"""Exact collection scope replay over real disposable journals; no broker IO."""
import copy
import json

import pytest

from nexus import paper_activity_acceptance_probe as probe
from nexus import paper_activity_acceptance_evidence as evidence
from nexus import paper_reconciliation_acceptance as balances
from nexus import paper_request_activity_acceptance as requests
from nexus import paper_read_pacing as pacing
from nexus.alpaca_activity_collection import activity_url
from nexus.alpaca_paper_inspection import URLS
from nexus.alpaca_paper_contract import sha
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore
from test_paper_activity_acceptance_probe import vault, run
from test_paper_credentials import save
from test_alpaca_paper_inspection import ACCOUNT, Opener, account
from test_alpaca_activity_collection import page, fee, START, END


def targets(urls):
    return [sha(dict(method='GET', target=url)) for url in urls]


def accepted(root, urls):
    gate, activity, acceptance, scope = evidence._scope(root, request_targets=targets(urls))
    acceptance.append(scope, admission=lambda:(gate.inspect()['head_sha256'], activity.inspect()['head_sha256']))
    options = dict(expected_account_id=ACCOUNT, pacing_path=gate.path,
        activity_store_path=activity.path, acceptance_store_path=acceptance.path)
    return options, gate, activity, acceptance


@pytest.mark.parametrize('changed', ['account_before', 'window', 'cursor', 'account_after'])
def test_self_consistent_v2_receipt_must_match_retained_scope_without_repair(tmp_path, changed):
    urls = [URLS[0], activity_url(evidence.AFTER, evidence.UNTIL), URLS[0]]
    if changed == 'window': urls[1] = activity_url('2026-10-16T00:00:00Z', '2026-10-17T00:00:00Z')
    elif changed == 'cursor': urls[1] = activity_url(evidence.AFTER, evidence.UNTIL, '20261017000000000::11234567-89AB-CDEF-0123-456789ABCDEF')
    else: urls[0 if changed == 'account_before' else 2] = URLS[1]
    options, gate, activity, acceptance = accepted(tmp_path, urls)
    before = evidence._sqlite_files_snapshot(tmp_path)
    operation = gate.operation_history()[0]
    assert operation['result_receipt_bound'] is operation['request_targets_bound'] is True
    assert operation['receipt_profile'] == pacing.RECEIPT_PROFILE
    state = probe.status(**options)
    assert state['cross_journal_heads_match'] is True
    assert state['all_pacing_operations_typed'] is state['all_pacing_result_receipts_bound'] is True
    assert state['all_collection_scopes_replayed'] is state['all_collection_observations_replayed'] is True
    assert state['latest_pacing_request_targets_bound'] is state['all_pacing_request_targets_bound'] is False
    assert state['latest_acceptance_record_replayed'] is state['all_accepted_transitions_replayed'] is False
    assert state['cross_journal_recovery_state'] == 'ACCEPTED_PACING_REQUEST_TARGET_UNVERIFIED_REVIEW_REQUIRED'
    assert all(state[flag] is False for flag in probe.FLAGS)
    assert probe.status(**options) == state
    assert evidence._sqlite_files_snapshot(tmp_path) == before
    assert acceptance.inspect()['records'] == gate.inspect()['sessions'] == activity.inspect()['retained_batches'] == 1
    # Both composed reviews must refuse before reading observations or requests.
    with pytest.raises(ValueError, match='ACCEPTED_HISTORY_REQUIRED'):
        balances.review(**options, expected_activity_head=activity.inspect()['head_sha256'],
            opening_observation={}, closing_observation={})
    with pytest.raises(ValueError, match='ACCEPTED_HISTORY_REQUIRED'):
        requests.review(**options, expected_activity_head=activity.inspect()['head_sha256'],
            request_journal_path=tmp_path/'absent.sqlite3', expected_request_head='0'*64)
    assert not (tmp_path/'absent.sqlite3').exists()
    assert evidence._sqlite_files_snapshot(tmp_path) == before


def test_later_valid_tail_cannot_hide_an_earlier_wrong_window(tmp_path):
    wrong = activity_url('2026-10-16T00:00:00Z', '2026-10-17T00:00:00Z')
    options, _, _, acceptance = accepted(tmp_path, [URLS[0], wrong, URLS[0]])
    gate, activity, _, scope = evidence._scope(tmp_path, 2)
    acceptance.append(scope, admission=lambda:(gate.inspect()['head_sha256'], activity.inspect()['head_sha256']))
    before = evidence._sqlite_files_snapshot(tmp_path)
    result = probe.status(**options)
    assert result['latest_acceptance_record_replayed'] is True
    assert result['latest_pacing_request_targets_bound'] is True
    assert result['all_pacing_request_targets_bound'] is False
    assert result['replayed_accepted_transitions'] == 1 and result['accepted_transitions'] == 2
    assert result['cross_journal_recovery_state'] == 'ACCEPTED_PACING_REQUEST_TARGET_UNVERIFIED_REVIEW_REQUIRED'
    cold = evidence._fresh_status(tmp_path)
    assert cold['all_request_targets_bound'] is False and cold['latest_content_replayed'] is True
    assert cold['external_connections'] == cold['credential_operations'] == 0
    assert evidence._sqlite_files_snapshot(tmp_path) == before


@pytest.mark.parametrize('pages,index', [(1,0),(1,1),(1,2),(2,0),(2,1),(2,2),(2,3)])
def test_new_probe_refuses_wrong_receipt_target_before_acceptance(vault,tmp_path,monkeypatch,pages,index):
    save()
    original = pacing.PaperReadPacing.finish
    def changed(self, token, **kwargs):
        receipt = copy.deepcopy(kwargs['receipt'])
        receipt['request_sha256'][index] = sha(dict(method='GET', target='wrong_fixture_target'))
        return original(self, token, **dict(kwargs, receipt=receipt))
    monkeypatch.setattr(pacing.PaperReadPacing, 'finish', changed)
    values = [account(), [fee()], account()] if pages == 1 else [account(), page(), [], account()]
    opener = Opener(values)
    with pytest.raises(ValueError, match='PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED'):
        run(tmp_path, opener)
    root = tmp_path/'private'
    gate = pacing.PaperReadPacing(root/'pacing.sqlite3', ACCOUNT)
    assert gate.inspect()['last_outcome'] == 'SUCCESS' and gate.inspect()['pending_token'] is None
    assert gate.operation_history()[0]['request_targets_bound'] is True
    assert len(opener.requests) == pages + 2
    acceptance = PaperActivityAcceptanceStore(root/'acceptance.sqlite3', ACCOUNT)
    assert acceptance.inspect()['records'] == 0
    state = probe.status(expected_account_id=ACCOUNT, pacing_path=gate.path,
        activity_store_path=root/'activity.sqlite3', acceptance_store_path=acceptance.path)
    assert state['retained_batches'] == 1
    assert state['cross_journal_recovery_state'] == 'UNBOUND_CROSS_JOURNAL_TRANSITION_REVIEW_REQUIRED'


@pytest.mark.parametrize('pages', [1,2])
def test_actual_collector_targets_match_opaque_cursor_and_window(vault,tmp_path,pages):
    save()
    first = page()
    first[-1]['id'] = first[-1]['id'].upper()  # Cursor source spelling is retained, not canonicalized.
    values = [account(), [fee()], account()] if pages == 1 else [account(), first, [], account()]
    opener = Opener(values)
    result, _, gate_path, activity_path, acceptance_path = run(tmp_path, opener)
    gate = pacing.PaperReadPacing(gate_path, ACCOUNT)
    assert gate.operation_history()[0]['request_sha256'] == tuple(targets([r.full_url for r,_ in opener.requests]))
    state = probe.status(expected_account_id=ACCOUNT, pacing_path=gate_path,
        activity_store_path=activity_path, acceptance_store_path=acceptance_path)
    assert state['all_pacing_request_targets_bound'] is state['all_accepted_transitions_replayed'] is True
    assert state['cross_journal_recovery_state'] == 'BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED'
    assert all(result[flag] is False for flag in probe.FLAGS)
    assert '20261017000000000::11234567-89AB-CDEF-0123-456789ABCDEF' not in json.dumps(state)


def test_second_page_cursor_spelling_cannot_be_substituted(vault,tmp_path,monkeypatch):
    save();first=page();first[-1]['id']=first[-1]['id'].upper()
    original=pacing.PaperReadPacing.finish
    def changed(self,token,**kwargs):
        receipt=copy.deepcopy(kwargs['receipt'])
        receipt['request_sha256'][2]=sha(dict(method='GET',target=activity_url(START,END,first[-1]['id'].lower())))
        return original(self,token,**dict(kwargs,receipt=receipt))
    monkeypatch.setattr(pacing.PaperReadPacing,'finish',changed)
    opener=Opener([account(),first,[],account()])
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED'):run(tmp_path,opener)
    assert len(opener.requests)==4
    assert PaperActivityAcceptanceStore(tmp_path/'private'/'acceptance.sqlite3',ACCOUNT).inspect()['records']==0
