"""Accepted-history binding for supplied balance observations."""
import copy
import json
import subprocess
import sys

import pytest

from nexus import paper_reconciliation_acceptance as subject
from nexus.paper_activity_acceptance_evidence import ACCOUNT, _scope
from nexus.paper_observation_review import _fixture_observation
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore


def prepared(tmp_path):
    pacing, activity, acceptance, scope = _scope(tmp_path)
    acceptance.append(scope, admission=lambda:(pacing.inspect()['head_sha256'],
        activity.inspect()['head_sha256']))
    opening = _fixture_observation(ACCOUNT,'1000','0',1800000000,1800000001)
    closing = _fixture_observation(ACCOUNT,'1000','0',1800000002,1800000003)
    options = dict(expected_account_id=ACCOUNT,
        pacing_path=tmp_path/'pacing.sqlite3',
        activity_store_path=tmp_path/'activity.sqlite3',
        acceptance_store_path=tmp_path/'acceptance.sqlite3',
        expected_activity_head=activity.inspect()['head_sha256'],
        opening_observation=opening, closing_observation=closing)
    return options


def snapshot(root):
    return tuple((path.name,path.read_bytes()) for path in sorted(root.iterdir()) if path.is_file())


def test_complete_accepted_history_binds_exact_observations_and_head_without_writes(tmp_path):
    options=prepared(tmp_path);before=snapshot(tmp_path);result=subject.review(**options)
    assert result['mode']=='PAPER_RECONCILIATION_ACCEPTANCE_REVIEW'
    assert result['complete_accepted_history_replayed'] is True
    assert result['legacy_baseline_absent'] is True
    assert result['complete_pacing_sessions_accepted'] is True
    assert result['complete_retained_batches_accepted'] is True
    assert result['complete_collection_observations_accepted'] is True
    assert result['journals_rechecked_unchanged'] is True
    assert result['accepted_transitions']==1
    assert result['activity_head_sha256']==options['expected_activity_head']
    assert result['opening_manifest_sha256']==options['opening_observation']['manifest_sha256']
    assert result['observation_review']['arithmetic_evaluated'] is True
    assert snapshot(tmp_path)==before
    for flag in ('admission_reserved','source_attested','source_coverage_verified',
        'activity_coverage_verified','fee_coverage_complete','fee_execution_links_verified',
        'balance_instants_verified','observation_windows_verified_quiet','holds_reconciled',
        'broker_account_reconciled','spendable_funds_verified','ledger_applied',
        'ready_for_routing','broker_connected','live_orders_enabled','money_movement_enabled',
        'automatic_retry','automatic_continuation','release_approved'):
        assert result[flag] is False


def test_unbound_or_missing_acceptance_history_fails_before_balance_review(tmp_path, monkeypatch):
    _, activity, _, _ = _scope(tmp_path)
    opening = _fixture_observation(ACCOUNT,'1000','0',1800000000,1800000001)
    called=False
    def forbidden(*args,**kwargs):
        nonlocal called;called=True;raise AssertionError
    monkeypatch.setattr(subject.paper_observation_review,'review',forbidden)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
        subject.review(expected_account_id=ACCOUNT,pacing_path=tmp_path/'pacing.sqlite3',
            activity_store_path=tmp_path/'activity.sqlite3',
            acceptance_store_path=tmp_path/'acceptance.sqlite3',
            expected_activity_head=activity.inspect()['head_sha256'],
            opening_observation=opening,closing_observation=opening)
    assert called is False


def test_wrong_explicit_activity_head_is_rejected(tmp_path):
    options=prepared(tmp_path);options['expected_activity_head']='0'*64
    with pytest.raises(ValueError,match='ACTIVITY_HEAD_DISAGREEMENT'):
        subject.review(**options)


def test_any_journal_change_during_composition_is_rejected(tmp_path,monkeypatch):
    options=prepared(tmp_path);original=subject.acceptance_status;calls=0
    def changed(**kwargs):
        nonlocal calls
        value=copy.deepcopy(original(**kwargs));calls+=1
        if calls==2:value['acceptance_head_sha256']='f'*64
        return value
    monkeypatch.setattr(subject,'acceptance_status',changed)
    with pytest.raises(ValueError,match='JOURNALS_CHANGED_DURING_REVIEW'):
        subject.review(**options)


@pytest.mark.parametrize('field,value',[('cross_journal_recovery_state','ACCEPTED_HISTORY_CONTENT_DISAGREEMENT_REVIEW_REQUIRED'),
    ('latest_acceptance_record_replayed',False),('all_accepted_transitions_replayed',False),
    ('accepted_transitions',0),('preexisting_history_unverified',True),('sessions',2),
    ('retained_batches',2),('retained_collection_observations',2)])
def test_every_accepted_history_requirement_fails_closed(tmp_path,monkeypatch,field,value):
    options=prepared(tmp_path);actual=subject.acceptance_status(**dict(
        expected_account_id=options['expected_account_id'],pacing_path=options['pacing_path'],
        activity_store_path=options['activity_store_path'],acceptance_store_path=options['acceptance_store_path']))
    actual[field]=value
    monkeypatch.setattr(subject,'acceptance_status',lambda **kwargs:actual)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
        subject.review(**options)


def test_accepted_tail_cannot_hide_unverified_legacy_baseline(tmp_path, monkeypatch):
    pacing,activity,_,_= _scope(tmp_path)
    (tmp_path/'acceptance.sqlite3').unlink()
    acceptance=PaperActivityAcceptanceStore(tmp_path/'acceptance.sqlite3',ACCOUNT)
    acceptance.bind(pacing_head=pacing.inspect()['head_sha256'],
        retention_head=activity.inspect()['head_sha256'],pacing_sessions=1,retained_batches=1)
    pacing,activity,acceptance,scope=_scope(tmp_path,2)
    acceptance.append(scope,admission=lambda:(pacing.inspect()['head_sha256'],
        activity.inspect()['head_sha256']))
    state=subject.acceptance_status(expected_account_id=ACCOUNT,
        pacing_path=tmp_path/'pacing.sqlite3',activity_store_path=tmp_path/'activity.sqlite3',
        acceptance_store_path=tmp_path/'acceptance.sqlite3')
    assert state['preexisting_history_unverified'] is True
    assert state['accepted_transitions']==1 and state['sessions']==state['retained_batches']==2
    assert state['all_accepted_transitions_replayed'] is True
    assert state['cross_journal_recovery_state']=='LEGACY_BASELINE_ONLY_REVIEW_REQUIRED'
    opening=_fixture_observation(ACCOUNT,'1000','0',1800000000,1800000001)
    called=False
    def forbidden(*args,**kwargs):
        nonlocal called;called=True;raise AssertionError
    monkeypatch.setattr(subject.paper_observation_review,'review',forbidden)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
        subject.review(expected_account_id=ACCOUNT,pacing_path=tmp_path/'pacing.sqlite3',
            activity_store_path=tmp_path/'activity.sqlite3',
            acceptance_store_path=tmp_path/'acceptance.sqlite3',
            expected_activity_head=activity.inspect()['head_sha256'],
            opening_observation=opening,closing_observation=opening)
    assert called is False


def test_cli_prints_private_review_but_never_writes_journals(tmp_path):
    options=prepared(tmp_path);opening=tmp_path/'opening.json';closing=tmp_path/'closing.json'
    opening.write_text(json.dumps(options['opening_observation']));closing.write_text(json.dumps(options['closing_observation']))
    before=snapshot(tmp_path)
    command=[sys.executable,'-m','nexus.paper_reconciliation_acceptance',
        '--expected-account',ACCOUNT,'--pacing',str(options['pacing_path']),
        '--activity-store',str(options['activity_store_path']),
        '--acceptance-store',str(options['acceptance_store_path']),
        '--activity-head',options['expected_activity_head'],'--opening',str(opening),'--closing',str(closing)]
    child=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert child.returncode==0 and json.loads(child.stdout)['complete_accepted_history_replayed'] is True
    assert snapshot(tmp_path)==before


def test_release_fixture_is_repeatable_vault_and_provider_silent():
    result=subject.fixture_report()
    assert result['passed'] and result['repeat_review_matches'] and result['journals_unchanged']
    assert result['unbound_history_rejected'] and result['private_scope_omitted']
    assert result['exact_activity_head_bound'] and result['complete_accepted_history_replayed']
    assert result['legacy_baseline_absent'] and result['complete_pacing_sessions_accepted']
    assert result['complete_retained_batches_accepted']
    assert result['complete_collection_observations_accepted']
    assert result['observation_arithmetic_evaluated'] and 'review' not in result
    assert result['actual_provider_access'] is result['actual_vault_access'] is False
