"""Explicit, paced Windows paper-activity read with private local retention.

Uses an already saved read-only credential pair and caller-selected private
files outside the source checkout.  It emits only aggregate counts and content
digests: broker/order/activity identities remain in the private input/store.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from nexus import alpaca_activity_normalizer as adapter
from nexus import paper_credentials
from nexus.alpaca_paper_contract import sha
from nexus.alpaca_paper_inspection import _uuid
from nexus.alpaca_activity_collection import PAGE_SIZE
from nexus.broker_activity_store import BrokerActivityStore, MAX_BATCHES
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore, MAX_RECORDS
from nexus.paper_acceptance_probe import FLAGS, ROOT
from nexus.paper_read_pacing import PaperReadPacing, MAX_SESSIONS

MAX_ORDER_FILE_BYTES = adapter.MAX_INPUT_BYTES
COLLECTOR_FLAGS = ('collection_source_attested','automatic_continuation','automatic_retry',
    'activity_coverage_verified','fee_coverage_complete','broker_account_reconciled',
    'spendable_funds_verified','ready_for_routing','broker_connected','live_orders_enabled','ledger_applied')
COLLECTOR_FLAGS += ('atomic_snapshot',)


def _fail():
    raise ValueError('PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED') from None


def _private_path(value):
    path = Path(value)
    try:
        absolute = path.absolute()
        resolved = path.resolve()
    except (OSError, RuntimeError):
        _fail()
    if absolute.is_relative_to(ROOT) or resolved.is_relative_to(ROOT):
        _fail()
    return path, resolved


def _orders(path, account):
    path, _ = _private_path(path)
    try:
        observed = path.lstat()
        if (stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode) or
            observed.st_nlink != 1 or not 1 <= observed.st_size <= MAX_ORDER_FILE_BYTES):
            _fail()
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (observed.st_dev, observed.st_ino, observed.st_size):
                _fail()
            raw = stream.read(MAX_ORDER_FILE_BYTES + 1)
            closed = os.fstat(stream.fileno())
        final = path.lstat()
        if ((closed.st_dev, closed.st_ino, closed.st_size) != (opened.st_dev, opened.st_ino, opened.st_size) or
            (final.st_dev, final.st_ino, final.st_size) != (observed.st_dev, observed.st_ino, observed.st_size)):
            _fail()
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: _fail()
                result[key] = value
            return result
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=lambda _: _fail())
        _, order_map, _, _, duplicates = adapter.canonical_inputs([], value, expected_account_id=account)
        return value, len(order_map), duplicates
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError, MemoryError):
        _fail()


def _paths(expected_account_id, pacing_path, activity_store_path, acceptance_store_path):
    account = _uuid(expected_account_id)
    pacing, pacing_resolved = _private_path(pacing_path)
    activity, activity_resolved = _private_path(activity_store_path)
    acceptance, acceptance_resolved = _private_path(acceptance_store_path)
    if len({pacing_resolved,activity_resolved,acceptance_resolved}) != 3:
        _fail()
    return (account,PaperReadPacing(pacing,account),BrokerActivityStore(activity,account),
        PaperActivityAcceptanceStore(acceptance,account))


def _pacing_summary(before, after):
    return dict(account_sha256=after['account_sha256'],before_head_sha256=before['head_sha256'],
        after_head_sha256=after['head_sha256'],events=after['events'],sessions=after['sessions'],
        next_allowed_at=after['next_allowed_at'],
        min_post_result_cooldown_seconds=after['min_post_result_cooldown_seconds'],
        coordination_scope='EXACT_LOCAL_JOURNAL_ONLY_NOT_BROKER_WIDE_OR_DISTRIBUTED')


def probe(*, expected_account_id, pacing_path, activity_store_path, acceptance_store_path, orders_path, after, until,
          confirm_read_only_paper=False, opener=None, wall_clock=time.time, elapsed_clock=time.monotonic):
    if confirm_read_only_paper is not True:
        _fail()
    account, pacing, store, acceptance = _paths(expected_account_id,pacing_path,activity_store_path,acceptance_store_path)
    order_path, order_resolved = _private_path(orders_path)
    if order_resolved in (pacing.path.resolve(),store.path.resolve(),acceptance.path.resolve()):
        _fail()
    orders, unique_orders, duplicate_orders = _orders(order_path, account)
    pacing_before = pacing.inspect(); store_before = store.inspect()
    acceptance.bind(pacing_head=pacing_before['head_sha256'],retention_head=store_before['head_sha256'],
        pacing_sessions=pacing_before['sessions'],retained_batches=store_before['retained_batches'])
    acceptance_before=acceptance.inspect()
    if (acceptance_before['pacing_bound_head_sha256']!=pacing_before['head_sha256'] or
        acceptance_before['retention_bound_head_sha256']!=store_before['head_sha256'] or
        acceptance_before['records']>=MAX_RECORDS):_fail()
    result = paper_credentials.collect_saved(orders, expected_account_id=account, after=after, until=until,
        confirm_read_only_paper=True, opener=opener, wall_clock=wall_clock, elapsed_clock=elapsed_clock,
        activity_store=store, pacing=pacing)
    pacing_after = pacing.inspect(); store_after = store.inspect()
    try:
        if (pacing_after['sessions'] != pacing_before['sessions'] + 1 or
            pacing_after['events'] != pacing_before['events'] + 2 or
            pacing_after['pending_token'] is not None or pacing_after['review_required'] is not False or
            pacing_after['last_outcome'] != 'SUCCESS'):
            _fail()
        if (store_after['retained_batches'] != store_before['retained_batches'] + 1 or
            store_after['retained_collection_observations'] != store_before['retained_collection_observations'] + 1):
            _fail()
        if any(result[field] is not False for field in COLLECTOR_FLAGS):
            _fail()
        retention = result['local_retention']; normalization = result['activity_normalization']
        observation = result['collection_observation']
        if (retention['duplicate'] is not False or retention['head_sha256'] != store_after['head_sha256'] or
            retention['receipt_id'] not in store_after['receipts'] or
            store_after['receipts'][retention['receipt_id']]['collection_observation'] != observation or
            sum(result['page_counts']) != result['rows_received'] or
            normalization['duplicate_orders_ignored'] != duplicate_orders or
            normalization['account_scope'] != 'CALLER_DECLARED_WITH_OPTIONAL_ROW_ID_CHECKS'):
            _fail()
        counts = dict(order_snapshots=unique_orders,rows_received=result['rows_received'],
            normalized_activities=len(normalization['normalized_activities']),
            executions=len(normalization['executions']),fee_debits=len(normalization['fee_debits']))
        if counts['normalized_activities'] > counts['rows_received']:
            _fail()
        collection = dict(scope_sha256=result['scope_sha256'],account_fields_sha256=observation['account_fields_sha256'],
            observation_sha256=sha(observation),page_count=len(observation['pages']),page_counts=list(result['page_counts']),
            end_of_page_sequence_observed=result['end_of_page_sequence_observed'],
            collection_limit_reached=result['collection_limit_reached'],continuation_required=not result['end_of_page_sequence_observed'],
            activity_input_sha256=normalization['input_sha256'],counts=counts,
            recognized_fee_debits=dict(normalization['recognized_fee_debits']),
            review_reasons=list(normalization['review_reasons']))
        retained = dict(account_sha256=store_after['account_sha256'],
            before_head_sha256=store_before['head_sha256'],after_head_sha256=store_after['head_sha256'],
            receipt_sha256=retention['receipt_id'],retained_batches=store_after['retained_batches'],
            retained_activities=store_after['retained_activities'],
            retained_collection_observations=store_after['retained_collection_observations'],
            batch_capacity_remaining=MAX_BATCHES-store_after['retained_batches'])
        pacing_summary = _pacing_summary(pacing_before, pacing_after)
        scope = dict(profile='nexus.paper-activity-acceptance-record.v2',account_sha256=retained['account_sha256'],
            collection_scope_sha256=collection['scope_sha256'],account_fields_sha256=collection['account_fields_sha256'],
            collection_observation_sha256=collection['observation_sha256'],
            activity_input_sha256=collection['activity_input_sha256'],retained_receipt_sha256=retained['receipt_sha256'],
            retention_before_head_sha256=retained['before_head_sha256'],retention_after_head_sha256=retained['after_head_sha256'],
            pacing_before_head_sha256=pacing_summary['before_head_sha256'],
            pacing_after_head_sha256=pacing_summary['after_head_sha256'])
        accepted=acceptance.append(scope,admission=lambda:(pacing.inspect()['head_sha256'],store.inspect()['head_sha256']))
        acceptance_after=acceptance.inspect()
        if (accepted['duplicate'] is not False or accepted['record_sha256']!=sha(scope) or
            accepted['head_sha256']!=acceptance_after['head_sha256']):_fail()
        return dict(mode='PAPER_ACTIVITY_ACCEPTANCE_PROBE',status='REVIEW_REQUIRED',read_completed=True,
            source=result['source'],collection=collection,local_retention=retained,local_pacing=pacing_summary,
            acceptance_history=dict(before_head_sha256=acceptance_before['head_sha256'],
                after_head_sha256=acceptance_after['head_sha256'],records=acceptance_after['records'],
                record_capacity_remaining=MAX_RECORDS-acceptance_after['records'],
                preexisting_history_unverified=acceptance_after['preexisting_history_unverified']),
            acceptance_record_scope=scope,acceptance_record_sha256=sha(scope),credential_pair_rechecked=True,
            raw_activity_rows_included=False,raw_order_identities_included=False,
            **{flag:False for flag in FLAGS},
            limitations='One explicitly confirmed fixed-host paper GET session with an already saved Windows pair, exact account, caller-declared closed creation window, local durable pacing, replay-validated private activity retention and a sampled cross-journal acceptance-history binding. Counts and digests do not prove complete activity/fee coverage, source authenticity, posting times, execution links, broker-wide quota, reconciliation, spendable funds or routing authority. Raw activity/order identities stay in the private input/store and are intentionally omitted here. Only cooperating callers using these exact local journals are coordinated; a crash can leave a pacing/activity transition unbound and direct store writers are not locked out. Inspect all retained state and never auto-retry, reset or delete uncertain evidence. Keep the order input, report and journals outside Git.')
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        _fail()


def status(*, expected_account_id, pacing_path, activity_store_path, acceptance_store_path):
    _, pacing, store, acceptance = _paths(expected_account_id,pacing_path,activity_store_path,acceptance_store_path)
    pace=pacing.inspect();retained=store.inspect();accepted=acceptance.inspect()
    pending = pace['pending_token'] is not None
    scopes=acceptance.replay_scopes()
    completed={(item['before_head_sha256'],item['after_head_sha256']) for item in pacing.completed_sessions()
        if item['outcome']=='SUCCESS'}
    records={row['activity_id']:row for row in retained['normalized_activities']}

    def replay_scope(scope):
        result=dict(accepted_receipt_replayed=False,collection_scope_replayed=False,
            account_fields_replayed=False,collection_observation_replayed=False,
            activity_input_replayed=False,pacing_transition_replayed=False,
            retention_transition_replayed=False,accepted_transition_replayed=False)
        receipt=retained['receipts'].get(scope['retained_receipt_sha256'])
        result['accepted_receipt_replayed']=receipt is not None
        if receipt is not None:
            observation=receipt.get('collection_observation')
            try:activities=[records[key] for key in receipt['activity_ids']]
            except KeyError:activities=None
            result['collection_scope_replayed']=scope['collection_scope_sha256']==sha(dict(
                account_sha256=retained['account_sha256'],after=receipt['after'],until=receipt['until'],
                direction='asc',page_size=PAGE_SIZE))
            result['account_fields_replayed']=(isinstance(observation,dict) and
                scope['account_fields_sha256']==observation.get('account_fields_sha256'))
            result['collection_observation_replayed']=(isinstance(observation,dict) and
                scope['collection_observation_sha256']==sha(observation))
            result['activity_input_replayed']=(activities is not None and
                scope['activity_input_sha256']==sha(dict(account_sha256=retained['account_sha256'],
                    orders=receipt['orders'],activities=activities)))
            result['retention_transition_replayed']=(
                receipt['retention_before_head_sha256']==scope['retention_before_head_sha256'] and
                receipt['retention_after_head_sha256']==scope['retention_after_head_sha256'])
        result['pacing_transition_replayed']=(scope['pacing_before_head_sha256'],
            scope['pacing_after_head_sha256']) in completed
        result['accepted_transition_replayed']=all(value for key,value in result.items()
            if key!='accepted_transition_replayed')
        return result

    replayed=[replay_scope(scope) for scope in scopes]
    latest = accepted['last_record_scope']
    replay = dict(latest_accepted_receipt_replayed=False,latest_collection_scope_replayed=False,
        latest_account_fields_replayed=False,latest_collection_observation_replayed=False,
        latest_activity_input_replayed=False,latest_pacing_transition_replayed=False,
        latest_retention_transition_replayed=False,latest_acceptance_record_replayed=False)
    if latest is not None and replayed:
        current=replayed[-1]
        replay.update({f'latest_{key}':value for key,value in current.items()
            if key!='accepted_transition_replayed'})
        replay['latest_acceptance_record_replayed']=(current['accepted_transition_replayed'] and
            accepted['last_record_sha256']==sha(latest) and
            latest['pacing_after_head_sha256']==pace['head_sha256'] and
            latest['retention_after_head_sha256']==retained['head_sha256'])
    historical=dict(replayed_accepted_transitions=sum(item['accepted_transition_replayed'] for item in replayed),
        all_accepted_receipts_replayed=bool(replayed) and all(item['accepted_receipt_replayed'] for item in replayed),
        all_collection_scopes_replayed=bool(replayed) and all(item['collection_scope_replayed'] for item in replayed),
        all_account_fields_replayed=bool(replayed) and all(item['account_fields_replayed'] for item in replayed),
        all_collection_observations_replayed=bool(replayed) and all(item['collection_observation_replayed'] for item in replayed),
        all_activity_inputs_replayed=bool(replayed) and all(item['activity_input_replayed'] for item in replayed),
        all_pacing_transitions_replayed=bool(replayed) and all(item['pacing_transition_replayed'] for item in replayed),
        all_retention_transitions_replayed=bool(replayed) and all(item['retention_transition_replayed'] for item in replayed),
        all_accepted_transitions_replayed=bool(replayed) and all(item['accepted_transition_replayed'] for item in replayed))
    if pending or pace['review_required']:recovery='PACING_UNCERTAINTY_REVIEW_REQUIRED'
    elif not accepted['initialized']:
        recovery='ACCEPTANCE_HISTORY_MISSING_REVIEW_REQUIRED' if pace['sessions'] or retained['retained_batches'] else 'NO_LOCAL_READ_HISTORY'
    elif (accepted['pacing_bound_head_sha256']!=pace['head_sha256'] or accepted['retention_bound_head_sha256']!=retained['head_sha256']):
        recovery='UNBOUND_CROSS_JOURNAL_TRANSITION_REVIEW_REQUIRED'
    elif accepted['preexisting_history_unverified']:
        recovery='LEGACY_BASELINE_ONLY_REVIEW_REQUIRED'
    elif accepted['records'] and not replay['latest_acceptance_record_replayed']:
        recovery='ACCEPTED_TRANSITION_CONTENT_DISAGREEMENT_REVIEW_REQUIRED'
    elif accepted['records'] and not historical['all_accepted_transitions_replayed']:
        recovery='ACCEPTED_HISTORY_CONTENT_DISAGREEMENT_REVIEW_REQUIRED'
    elif accepted['records']:recovery='BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED'
    else:recovery='NO_ACCEPTED_READ_TRANSITIONS'
    uncertain=recovery in ('PACING_UNCERTAINTY_REVIEW_REQUIRED','ACCEPTANCE_HISTORY_MISSING_REVIEW_REQUIRED','UNBOUND_CROSS_JOURNAL_TRANSITION_REVIEW_REQUIRED','ACCEPTED_TRANSITION_CONTENT_DISAGREEMENT_REVIEW_REQUIRED','ACCEPTED_HISTORY_CONTENT_DISAGREEMENT_REVIEW_REQUIRED','LEGACY_BASELINE_ONLY_REVIEW_REQUIRED')
    return dict(mode='PAPER_ACTIVITY_ACCEPTANCE_STATUS',status='REVIEW_REQUIRED',read_only=True,
        pacing_journal_present=pacing.path.is_file(),activity_store_present=store.path.is_file(),acceptance_store_present=acceptance.path.is_file(),
        account_sha256=retained['account_sha256'],pacing_head_sha256=pace['head_sha256'],
        retention_head_sha256=retained['head_sha256'],sessions=pace['sessions'],
        session_capacity_remaining=max(0,MAX_SESSIONS-pace['sessions']),next_allowed_at=pace['next_allowed_at'],
        pending_read=pending,retained_review_block=pace['review_required'],
        retained_batches=retained['retained_batches'],retained_activities=retained['retained_activities'],
        retained_collection_observations=retained['retained_collection_observations'],
        batch_capacity_remaining=max(0,MAX_BATCHES-retained['retained_batches']),
        acceptance_history_initialized=accepted['initialized'],acceptance_head_sha256=accepted['head_sha256'],
        accepted_transitions=accepted['records'],acceptance_record_capacity_remaining=max(0,MAX_RECORDS-accepted['records']),
        preexisting_history_unverified=accepted['preexisting_history_unverified'],cross_journal_recovery_state=recovery,
        cross_journal_heads_match=accepted['initialized'] and accepted['pacing_bound_head_sha256']==pace['head_sha256'] and accepted['retention_bound_head_sha256']==retained['head_sha256'],
        **historical,**replay,
        suggested_action='REVIEW_ALL_RETAINED_JOURNALS_NO_AUTOMATIC_RETRY' if uncertain else 'RESPECT_RETAINED_COOLDOWN_AND_SEPARATELY_CONFIRM_ONE_READ',
        **{flag:False for flag in FLAGS},
        limitations='Local replay only: no order file, vault or provider access and no admission permission. Every accepted content record is re-derived from its exact retained receipt, normalized activities, collection observation and exact retained pacing/activity head transitions; matching content and heads establish only local cooperating-journal consistency, not causality, source, coverage or valid-tail completeness. Missing history does not prove no earlier read. Other journals/hosts and direct store writers are not coordinated. Never erase uncertain reads, receipts, acceptance records or cooldowns to bypass review.')


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_ACTIVITY_ACCEPTANCE_PROBE_USAGE_ERROR\n')


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument('--expected-account', required=True);parser.add_argument('--pacing', required=True)
    parser.add_argument('--activity-store', required=True);parser.add_argument('--acceptance-store',required=True);parser.add_argument('--orders')
    parser.add_argument('--after');parser.add_argument('--until')
    parser.add_argument('--confirm-read-only-paper', action='store_true')
    parser.add_argument('--status-only', action='store_true', help='Replay local journals only; never read orders, credentials or provider data.')
    args = parser.parse_args()
    try:
        if args.status_only:
            if args.confirm_read_only_paper or any(value is not None for value in (args.orders,args.after,args.until)): _fail()
            result = status(expected_account_id=args.expected_account,pacing_path=args.pacing,activity_store_path=args.activity_store,acceptance_store_path=args.acceptance_store)
        else:
            if None in (args.orders,args.after,args.until): _fail()
            result = probe(expected_account_id=args.expected_account,pacing_path=args.pacing,
                activity_store_path=args.activity_store,orders_path=args.orders,after=args.after,until=args.until,
                acceptance_store_path=args.acceptance_store,
                confirm_read_only_paper=args.confirm_read_only_paper)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, MemoryError, RecursionError, KeyboardInterrupt):
        print('PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED');return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__ == '__main__':
    raise SystemExit(main())
