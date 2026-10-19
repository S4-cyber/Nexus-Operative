"""Bind original request/fill review to complete accepted activity history.

This is a private, read-only composition over caller-selected local journals. It
does not read credentials, contact a provider, write evidence or grant order,
funding, ledger, source, reconciliation or release authority.
"""
import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from nexus import alpaca_activity_normalizer as adapter
from nexus.activity_collection_evidence import SCHEMA, page_observation, validate
from nexus.alpaca_activity_collection import PAGE_SIZE
from nexus.alpaca_paper_contract import sha
from nexus.alpaca_paper_contract import preview
from nexus.broker_activity_store import BrokerActivityStore
from nexus.broker_request_journal import BrokerRequestJournal
from nexus import broker_request_activity_review as request_review
from nexus.paper_activity_acceptance_probe import status as acceptance_status
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore, PROFILE
from nexus.paper_read_pacing import PaperReadPacing

POLICY = 'nexus.paper-request-activity-acceptance.v1'
FLAGS = tuple(dict.fromkeys((*request_review.FLAGS, 'admission_reserved',
    'automatic_continuation', 'requested_window_boundaries_verified')))


def _fail(code):
    raise ValueError('PAPER_REQUEST_ACTIVITY_ACCEPTANCE_' + code)


def _complete(value):
    """Require all cooperating activity history, not merely the latest tail."""
    return (
        value['cross_journal_recovery_state'] == 'BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED'
        and value['cross_journal_heads_match'] is True
        and value['accepted_transitions'] > 0
        and value['preexisting_history_unverified'] is False
        and value['accepted_transitions'] == value['sessions']
        and value['accepted_transitions'] == value['retained_batches']
        and value['accepted_transitions'] == value['retained_collection_observations']
        and value['all_collection_cursor_sequences_complete'] is True
        and value['replayed_accepted_transitions'] == value['accepted_transitions']
        and value['latest_acceptance_record_replayed'] is True
        and value['all_accepted_transitions_replayed'] is True
    )



def _request_origins(journal,store,acceptance_path,account,expected_head):
    """Re-derive v3 request inputs from historical prefixes, never current guesses."""
    from nexus.paper_activity_acceptance_probe import _request_origin_replay
    scopes=PaperActivityAcceptanceStore(acceptance_path,account).replay_scopes()
    result=_request_origin_replay(journal,store.inspect(),scopes,account)
    if (result['request_origin_bound_transitions'] and
        (result['request_origin_missing_prefixes'] or
        result['request_origin_current_head_sha256']!=expected_head or
        result['request_origin_current_tail_replayed'] is not True)):
        _fail('HISTORICAL_REQUEST_PREFIX_REQUIRED')
    if (result['request_origin_input_disagreements'] or
        result['replayed_request_origin_transitions']!=result['request_origin_bound_transitions']):
        _fail('HISTORICAL_REQUEST_INPUT_DISAGREEMENT')
    return result


def review(*, expected_account_id, request_journal_path, pacing_path,
           activity_store_path, acceptance_store_path,
           expected_request_head, expected_activity_head):
    """Review immutable request terms/fills only after full accepted-history replay."""
    from nexus.sqlite_journal_guard import check_path_set
    check_path_set((request_journal_path,pacing_path,activity_store_path,acceptance_store_path),'PAPER_REQUEST_ACTIVITY_ACCEPTANCE_')
    before = acceptance_status(expected_account_id=expected_account_id,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path)
    if not _complete(before):
        _fail('ACCEPTED_HISTORY_REQUIRED')
    if before['retention_head_sha256'] != expected_activity_head:
        _fail('ACTIVITY_HEAD_DISAGREEMENT')

    journal = BrokerRequestJournal(request_journal_path, expected_account_id)
    store = BrokerActivityStore(activity_store_path, expected_account_id)
    origins=_request_origins(journal,store,acceptance_store_path,expected_account_id,expected_request_head)
    component = request_review.review(journal, store,
        expected_request_head=expected_request_head,
        expected_activity_head=expected_activity_head)

    after = acceptance_status(expected_account_id=expected_account_id,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path)
    recovered = journal.recovery()
    if before != after or recovered['head_sha256'] != expected_request_head:
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    if component['activity_head_sha256'] != after['retention_head_sha256']:
        _fail('COMPONENT_HEAD_DISAGREEMENT')

    scope = dict(policy=POLICY, account_sha256=after['account_sha256'],
        request_head_sha256=expected_request_head,
        activity_head_sha256=expected_activity_head,
        pacing_head_sha256=after['pacing_head_sha256'],
        acceptance_head_sha256=after['acceptance_head_sha256'],
        accepted_transitions=after['accepted_transitions'],
        request_activity_review_sha256=component['input_sha256'])
    return dict(mode='PAPER_REQUEST_ACTIVITY_ACCEPTANCE_REVIEW',
        status='REVIEW_REQUIRED', read_only=True, **scope, input_sha256=sha(scope),**origins,
        journals_rechecked_unchanged=True, complete_accepted_history_replayed=True,
        legacy_baseline_absent=True, complete_pacing_sessions_accepted=True,
        complete_retained_batches_accepted=True,
        complete_collection_observations_accepted=True,
        all_collection_cursor_sequences_complete=True,
        acceptance_recovery_state=after['cross_journal_recovery_state'],
        local_request_activity_agreement=component['local_request_activity_agreement'],
        request_activity_review=component,
        **{flag:False for flag in FLAGS},
        limitations='Complete local accepted-history replay binds this exact request head to one exact retained activity head and rechecks all cooperating journals. This is sampled local consistency only: it does not attest provider source or complete activity/fee coverage, make the request journal atomic with collection, verify holds or spendable funds, authorize transport, write a ledger or approve release. Keep all private journals and reports outside Git.')


def _fixture(root):
    """Build one accepted synthetic fill collection; never provider or vault IO."""
    account='01234567-89ab-cdef-0123-456789abcdef'
    after='2026-10-17T00:00:00Z';until='2026-10-18T00:00:00Z'
    pacing=PaperReadPacing(root/'pacing.sqlite3',account)
    activity=BrokerActivityStore(root/'activity.sqlite3',account)
    acceptance=PaperActivityAcceptanceStore(root/'acceptance.sqlite3',account)
    requests=BrokerRequestJournal(root/'requests.sqlite3',account)
    pace_before=pacing.inspect();activity_before=activity.inspect()
    acceptance.bind(pacing_head=pace_before['head_sha256'],
        retention_head=activity_before['head_sha256'],pacing_sessions=0,retained_batches=0)
    token=pacing.begin(admission=lambda:1000)
    pacing.finish(token,admission=lambda:1000,status_code=200)
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,
        fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    plan=preview('nx_accept_fixture','BTC-USD','BUY','2','100',asset,
        metadata_at=1000,evaluated_at=1001)
    broker_id='11234567-89ab-cdef-0123-456789abcdef'
    raw=dict(**plan['payload'],id=broker_id,asset_class='crypto',status='filled',
        filled_qty='2',filled_avg_price='99',updated_at='2026-10-17T18:00:02+00:00')
    requests.prepare(plan);requests.mark_submission_uncertain('nx_accept_fixture')
    requests.observe('nx_accept_fixture',raw)
    order=dict(broker_order_id=broker_id,client_order_id='nx_accept_fixture',symbol='BTC/USD',
        side='buy',status='filled',filled_quantity='2',average_fill_price='99',
        updated_at=raw['updated_at'])
    fills=[dict(id='20261017180000000::'+f'01234567-89ab-cdef-0123-{i:012d}',
        activity_type='FILL',order_id=broker_id,symbol='BTCUSD',side='buy',
        type='partial_fill' if i==1 else 'fill',qty='1',price=price,cum_qty=str(i),
        leaves_qty=str(2-i),transaction_time=f'2026-10-17T18:00:0{i}Z')
        for i,price in ((1,'98'),(2,'100'))]
    normalized=adapter.normalize(fills,[order],expected_account_id=account)
    observed_at=1792281600
    observation=validate(dict(schema=SCHEMA,declared_transport='INJECTED_UNATTESTED',
        read_started_at=observed_at,read_completed_at=observed_at,
        account_fields_sha256=sha(dict(fixture='accepted-request-fill')),
        pages=[page_observation(fills,normalized['normalized_activities'],after=after,until=until,
            request_cursor=None,requested_at=observed_at,received_at=observed_at)]),
        dict(activities=normalized['normalized_activities']),after=after,until=until)
    receipt=activity.retain(fills,[order],after=after,until=until,
        collection_observation=observation)
    pace_after=pacing.inspect();activity_after=activity.inspect()
    scope=dict(profile=PROFILE,account_sha256=activity_after['account_sha256'],
        collection_scope_sha256=sha(dict(account_sha256=activity_after['account_sha256'],
            after=after,until=until,direction='asc',page_size=PAGE_SIZE)),
        account_fields_sha256=observation['account_fields_sha256'],
        collection_observation_sha256=sha(observation),
        activity_input_sha256=normalized['input_sha256'],
        retained_receipt_sha256=receipt['receipt_id'],
        pacing_before_head_sha256=pace_before['head_sha256'],
        pacing_after_head_sha256=pace_after['head_sha256'],
        retention_before_head_sha256=activity_before['head_sha256'],
        retention_after_head_sha256=activity_after['head_sha256'])
    acceptance.append(scope,admission=lambda:(pacing.inspect()['head_sha256'],activity.inspect()['head_sha256']))
    return dict(expected_account_id=account,request_journal_path=requests.path,
        pacing_path=pacing.path,activity_store_path=activity.path,
        acceptance_store_path=acceptance.path,
        expected_request_head=requests.recovery()['head_sha256'],
        expected_activity_head=activity.inspect()['head_sha256']),activity


def fixture_report():
    with tempfile.TemporaryDirectory(prefix='nexus-request-activity-acceptance-') as directory:
        root=Path(directory);options,activity=_fixture(root)
        before=tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()))
        result=review(**options)
        after=tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()))
        activity.retain([],[],after='2026-10-18T00:00:00Z',until='2026-10-19T00:00:00Z')
        unbound=False
        try:review(**options)
        except ValueError as error:
            unbound=str(error)=='PAPER_REQUEST_ACTIVITY_ACCEPTANCE_ACCEPTED_HISTORY_REQUIRED'
        component=result['request_activity_review']
        passed=(result['complete_accepted_history_replayed'] and
            result['local_request_activity_agreement'] and
            component['request_reviews'][0]['explicit_fill_count']==2 and
            before==after and unbound)
        return dict(source='SYNTHETIC_LOCAL_ACCEPTED_REQUEST_FILL_FIXTURE',passed=passed,
            accepted_history_bound=result['complete_accepted_history_replayed'],
            original_request_linked=result['local_request_activity_agreement'],
            explicit_fills_checked=component['request_reviews'][0]['explicit_fill_count']==2,
            journals_unchanged=before==after,unbound_activity_rejected=unbound,
            actual_provider_access=False,actual_vault_access=False,
            **{flag:False for flag in FLAGS})


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_REQUEST_ACTIVITY_ACCEPTANCE_USAGE_ERROR\n')


def main():
    parser = _Parser(description=__doc__)
    for name in ('expected-account','request-journal','pacing','activity-store',
                 'acceptance-store','request-head','activity-head'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    try:
        result = review(expected_account_id=args.expected_account,
            request_journal_path=args.request_journal, pacing_path=args.pacing,
            activity_store_path=args.activity_store,
            acceptance_store_path=args.acceptance_store,
            expected_request_head=args.request_head,
            expected_activity_head=args.activity_head)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error,
            MemoryError, RecursionError, KeyboardInterrupt):
        print('PAPER_REQUEST_ACTIVITY_ACCEPTANCE_FAILED')
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
