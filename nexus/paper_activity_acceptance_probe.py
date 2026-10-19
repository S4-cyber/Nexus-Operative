"""Explicit, paced Windows paper-activity read with private local retention.

Uses an already saved read-only credential pair and caller-selected private
files outside the source checkout.  It emits only aggregate counts and content
digests: broker/order/activity identities remain in the private input/store.
"""
import argparse
import json
import os
import hashlib
import re
from pathlib import Path
import sqlite3
import stat
import sys
import time

from nexus import alpaca_activity_normalizer as adapter
from nexus import paper_credentials
from nexus.alpaca_paper_contract import sha
from nexus.alpaca_paper_inspection import _uuid
from nexus.alpaca_activity_collection import PAGE_SIZE
from nexus.activity_collection_evidence import request_targets_match
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
        protected=[ROOT]
        if getattr(sys,'frozen',False):protected.append(Path(sys.executable).resolve().parent)
    except (OSError, RuntimeError):
        _fail()
    if any(absolute.is_relative_to(folder) or resolved.is_relative_to(folder) for folder in protected):
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



def _request_order_values(rows,account):
    orders=[]
    for row in rows:
        if row['lookup']['pending'] or row['lookup']['review_blocked']:_fail()
        if not row['submission_attempt_marked']:continue
        snapshot=row['snapshot']
        if snapshot is None:_fail()
        orders.append({k:snapshot[k] for k in ('broker_order_id','client_order_id','symbol','side',
            'status','filled_quantity','average_fill_price','updated_at')})
    _,order_map,_,_,duplicates=adapter.canonical_inputs([],orders,expected_account_id=account)
    if not order_map or duplicates:_fail()
    return orders,len(order_map),duplicates


def _request_orders(path,account,head):
    """Derive only allowlisted observed snapshots; no guessed order identities."""
    from nexus.broker_request_journal import BrokerRequestJournal
    if type(head) is not str or re.fullmatch('[0-9a-f]{64}',head) is None:_fail()
    journal=BrokerRequestJournal(path,account);retained=journal.recovery()
    if retained['head_sha256']!=head:_fail()
    result=_request_order_values(retained['requests'],account)
    if journal.recovery()!=retained:_fail()
    return result


def _request_orders_digest(orders,account,*,canonical_orders=False):
    # Canonical receipt orders have already passed full activity-store replay.
    if not canonical_orders:
        _,order_map,_,_,_=adapter.canonical_inputs([],orders,expected_account_id=account)
        orders=list(order_map.values())
    return sha(dict(account_sha256=hashlib.sha256(account.encode()).hexdigest(),
        orders=sorted(orders,key=lambda row:row['broker_order_id'])))


def _request_origin_replay(journal, retained, scopes, account):
    """Re-derive every persisted v3 request origin from its historical prefix."""
    from nexus.paper_activity_acceptance_store import REQUEST_PROFILE
    bound=[scope for scope in scopes if scope['profile']==REQUEST_PROFILE]
    legacy=len(scopes)-len(bound)
    prefixes=journal.observations_at_heads([scope['request_head_sha256'] for scope in bound])
    replayed=0;disagreements=0
    for scope in bound:
        rows=prefixes['observations_by_head'].get(scope['request_head_sha256'])
        receipt=retained['receipts'].get(scope['retained_receipt_sha256'])
        if rows is None:
            continue
        try:
            orders,_,_=_request_order_values(rows,account)
            matches=(receipt is not None and
                _request_orders_digest(orders,account)==scope['request_orders_sha256'] and
                _request_orders_digest(receipt['orders'],account,canonical_orders=True)==scope['request_orders_sha256'])
        except (ValueError,TypeError,KeyError):
            matches=False
        if matches:replayed+=1
        else:disagreements+=1
    verified=(bool(bound) and legacy==0 and replayed==len(bound) and
        not prefixes['missing_heads'] and prefixes['complete_current_tail_replayed'] is True)
    return dict(request_origin_bound_transitions=len(bound),legacy_unbound_request_transitions=legacy,
        retained_request_origin_prefixes_replayed=len(prefixes['observations_by_head']),
        replayed_request_origin_transitions=replayed,
        request_origin_missing_prefixes=len(prefixes['missing_heads']),
        request_origin_input_disagreements=disagreements,
        request_origin_current_tail_replayed=prefixes['complete_current_tail_replayed'] is True,
        request_origin_current_head_sha256=prefixes['current_head_sha256'],
        all_retained_request_origins_bound=verified,
        request_origin_history_verified=verified)


def _paths(expected_account_id, pacing_path, activity_store_path, acceptance_store_path):
    account = _uuid(expected_account_id)
    pacing, pacing_resolved = _private_path(pacing_path)
    activity, activity_resolved = _private_path(activity_store_path)
    acceptance, acceptance_resolved = _private_path(acceptance_store_path)
    if len({pacing_resolved,activity_resolved,acceptance_resolved}) != 3:
        _fail()
    from nexus.sqlite_journal_guard import check_path_set
    check_path_set((pacing_resolved,activity_resolved,acceptance_resolved),'PAPER_ACTIVITY_ACCEPTANCE_')
    return (account,PaperReadPacing(pacing,account),BrokerActivityStore(activity,account),
        PaperActivityAcceptanceStore(acceptance,account))


def _pacing_summary(before, after):
    return dict(account_sha256=after['account_sha256'],before_head_sha256=before['head_sha256'],
        after_head_sha256=after['head_sha256'],events=after['events'],sessions=after['sessions'],
        operation='ACTIVITY_COLLECTION',
        typed_activity_collection_sessions=after['operation_counts']['ACTIVITY_COLLECTION'],
        legacy_unspecified_sessions=after['operation_counts']['LEGACY_UNSPECIFIED'],
        next_allowed_at=after['next_allowed_at'],
        min_post_result_cooldown_seconds=after['min_post_result_cooldown_seconds'],
        coordination_scope='EXACT_LOCAL_JOURNAL_ONLY_NOT_BROKER_WIDE_OR_DISTRIBUTED')


def probe(*, expected_account_id, pacing_path, activity_store_path, acceptance_store_path, orders_path=None, after, until,
          confirm_read_only_paper=False, opener=None, wall_clock=time.time, elapsed_clock=time.monotonic,
          request_journal_path=None,expected_request_head=None):
    if confirm_read_only_paper is not True:
        _fail()
    account, pacing, store, acceptance = _paths(expected_account_id,pacing_path,activity_store_path,acceptance_store_path)
    request_mode=request_journal_path is not None
    if (orders_path is None)==(not request_mode) or (not request_mode and expected_request_head is not None):_fail()
    order_path,order_resolved=_private_path(request_journal_path if request_mode else orders_path)
    if order_resolved in (pacing.path.resolve(),store.path.resolve(),acceptance.path.resolve()):_fail()
    from nexus.sqlite_journal_guard import check_path_set
    check_path_set((pacing.path,store.path,acceptance.path,order_resolved),'PAPER_ACTIVITY_ACCEPTANCE_')
    orders,unique_orders,duplicate_orders=(_request_orders(order_path,account,expected_request_head)
        if request_mode else _orders(order_path,account))
    def input_guard():
        if request_mode and _request_orders(order_path,account,expected_request_head)!=(orders,unique_orders,duplicate_orders):_fail()
        return True
    input_guard()
    pacing_before = pacing.inspect(); store_before = store.inspect()
    acceptance.bind(pacing_head=pacing_before['head_sha256'],retention_head=store_before['head_sha256'],
        pacing_sessions=pacing_before['sessions'],retained_batches=store_before['retained_batches'])
    acceptance_before=acceptance.inspect()
    if (acceptance_before['pacing_bound_head_sha256']!=pacing_before['head_sha256'] or
        acceptance_before['retention_bound_head_sha256']!=store_before['head_sha256'] or
        acceptance_before['records']>=MAX_RECORDS):_fail()
    result = paper_credentials.collect_saved(orders, expected_account_id=account, after=after, until=until,
        confirm_read_only_paper=True, opener=opener, wall_clock=wall_clock, elapsed_clock=elapsed_clock,
        activity_store=store, pacing=pacing,**({'input_guard':input_guard} if request_mode else {}))
    input_guard()
    pacing_after = pacing.inspect(); store_after = store.inspect()
    try:
        if (pacing_after['sessions'] != pacing_before['sessions'] + 1 or
            pacing_after['events'] != pacing_before['events'] + 2 or
            pacing_after['operation_counts']['ACTIVITY_COLLECTION'] !=
                pacing_before['operation_counts']['ACTIVITY_COLLECTION'] + 1 or
            pacing_after['operation_counts']['LEGACY_UNSPECIFIED'] !=
                pacing_before['operation_counts']['LEGACY_UNSPECIFIED'] or
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
        if request_mode:
            from nexus.paper_activity_acceptance_store import REQUEST_PROFILE
            scope.update(profile=REQUEST_PROFILE,request_head_sha256=expected_request_head,
                request_orders_sha256=_request_orders_digest(orders,account))
        def admission():
            input_guard()
            current_pacing=pacing.inspect();current_store=store.inspect()
            operation=next((row for row in pacing.operation_history()
                if row['before_head_sha256']==scope['pacing_before_head_sha256'] and
                   row['after_head_sha256']==scope['pacing_after_head_sha256']),None)
            if not request_targets_match(operation,current_store['receipts'].get(scope['retained_receipt_sha256'])):_fail()
            return current_pacing['head_sha256'],current_store['head_sha256']
        accepted=acceptance.append(scope,admission=admission,**({'precommit_check':input_guard} if request_mode else {}))
        input_guard()
        acceptance_after=acceptance.inspect()
        if (accepted['duplicate'] is not False or accepted['record_sha256']!=sha(scope) or
            accepted['head_sha256']!=acceptance_after['head_sha256']):_fail()
        return dict(mode='PAPER_ACTIVITY_ACCEPTANCE_PROBE',status='REVIEW_REQUIRED',read_completed=True,
            source=result['source'],collection=collection,local_retention=retained,local_pacing=pacing_summary,
            order_input=dict(source='RETAINED_REQUEST_OBSERVATIONS' if request_mode else 'PRIVATE_SUPPLIED_ORDER_FILE',
                request_head_sha256=expected_request_head if request_mode else None,request_head_rechecked=request_mode,
                source_attested=False,request_head_persisted_in_acceptance_record=request_mode),
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


def status(*, expected_account_id, pacing_path, activity_store_path, acceptance_store_path,
           request_journal_path=None):
    account, pacing, store, acceptance = _paths(expected_account_id,pacing_path,activity_store_path,acceptance_store_path)
    if request_journal_path is not None:
        from nexus.sqlite_journal_guard import check_path_set
        request_resolved=_private_path(request_journal_path)[1]
        if request_resolved in {pacing.path.resolve(),store.path.resolve(),acceptance.path.resolve()}:_fail()
        check_path_set((pacing.path,store.path,acceptance.path,request_resolved),'PAPER_ACTIVITY_ACCEPTANCE_')
    pace=pacing.inspect();retained=store.inspect();accepted=acceptance.inspect()
    pending = pace['pending_token'] is not None
    scopes=acceptance.replay_scopes()
    completed={(item['before_head_sha256'],item['after_head_sha256']):item
        for item in pacing.operation_history() if item['outcome']=='SUCCESS'}
    records={row['activity_id']:row for row in retained['normalized_activities']}

    def replay_scope(scope):
        result=dict(accepted_receipt_replayed=False,collection_scope_replayed=False,
            account_fields_replayed=False,collection_observation_replayed=False,
            collection_cursor_sequence_complete=False,
            activity_input_replayed=False,pacing_transition_replayed=False,
            pacing_operation_typed=False,pacing_result_receipt_bound=False,pacing_request_targets_bound=False,
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
            result['collection_cursor_sequence_complete']=(isinstance(observation,dict) and
                bool(observation.get('pages')) and
                len(observation['pages'][-1].get('activity_ids',())) < PAGE_SIZE)
            result['activity_input_replayed']=(activities is not None and
                scope['activity_input_sha256']==sha(dict(account_sha256=retained['account_sha256'],
                    orders=receipt['orders'],activities=activities)))
            result['retention_transition_replayed']=(
                receipt['retention_before_head_sha256']==scope['retention_before_head_sha256'] and
                receipt['retention_after_head_sha256']==scope['retention_after_head_sha256'])
        if 'request_orders_sha256' in scope:
            result['request_order_input_replayed']=(receipt is not None and
                scope['request_orders_sha256']==_request_orders_digest(receipt['orders'],expected_account_id,canonical_orders=True))
        transition=(scope['pacing_before_head_sha256'],scope['pacing_after_head_sha256'])
        result['pacing_transition_replayed']=transition in completed
        operation=completed.get(transition)
        result['pacing_operation_typed']=operation is not None and operation['operation']=='ACTIVITY_COLLECTION'
        result['pacing_result_receipt_bound']=operation is not None and operation['result_receipt_bound'] is True
        result['pacing_request_targets_bound']=request_targets_match(operation,receipt)
        result['accepted_transition_replayed']=all(value for key,value in result.items()
            if key not in ('accepted_transition_replayed','collection_cursor_sequence_complete'))
        return result

    replayed=[replay_scope(scope) for scope in scopes]
    latest = accepted['last_record_scope']
    replay = dict(latest_accepted_receipt_replayed=False,latest_collection_scope_replayed=False,
        latest_account_fields_replayed=False,latest_collection_observation_replayed=False,
        latest_collection_cursor_sequence_complete=False,
        latest_activity_input_replayed=False,latest_pacing_transition_replayed=False,
        latest_pacing_operation_typed=False,latest_pacing_result_receipt_bound=False,latest_pacing_request_targets_bound=False,
        latest_retention_transition_replayed=False,latest_request_order_input_replayed=None,latest_acceptance_record_replayed=False)
    if latest is not None and replayed:
        current=replayed[-1]
        replay.update({f'latest_{key}':value for key,value in current.items()
            if key!='accepted_transition_replayed'})
        replay['latest_acceptance_record_replayed']=(current['accepted_transition_replayed'] and
            accepted['last_record_sha256']==sha(latest) and
            latest['pacing_after_head_sha256']==pace['head_sha256'] and
            latest['retention_after_head_sha256']==retained['head_sha256'])
    request_bound_count=sum('request_head_sha256' in scope for scope in scopes)
    origins=dict(request_journal_supplied=False,request_journal_present=False,
        request_origin_bound_transitions=request_bound_count,
        legacy_unbound_request_transitions=len(scopes)-request_bound_count,
        retained_request_origin_prefixes_replayed=0,replayed_request_origin_transitions=0,
        request_origin_missing_prefixes=0,request_origin_input_disagreements=0,
        request_origin_current_tail_replayed=False,request_origin_current_head_sha256=None,
        all_retained_request_origins_bound=False,request_origin_history_verified=False,
        request_journal_rechecked_unchanged=False,request_origin_recovery_state='NOT_REQUESTED')
    if request_journal_path is not None:
        from nexus.broker_request_journal import BrokerRequestJournal
        request_path,request_resolved=_private_path(request_journal_path)
        if request_resolved in {pacing.path.resolve(),store.path.resolve(),acceptance.path.resolve()}:_fail()
        journal=BrokerRequestJournal(request_path,account)
        origins.update(_request_origin_replay(journal,retained,scopes,account))
        final_request=journal.recovery()
        unchanged=final_request['head_sha256']==origins['request_origin_current_head_sha256']
        origins.update(request_journal_supplied=True,request_journal_present=request_path.is_file(),
            request_journal_rechecked_unchanged=unchanged)
        origins['request_origin_history_verified']=origins['request_origin_history_verified'] and unchanged
        origins['all_retained_request_origins_bound']=origins['request_origin_history_verified']
        if not request_bound_count:origin_state='NO_REQUEST_BOUND_TRANSITIONS'
        elif origins['legacy_unbound_request_transitions']:
            origin_state='LEGACY_REQUEST_ORIGIN_HISTORY_UNVERIFIED'
        elif origins['request_origin_history_verified']:
            origin_state='REQUEST_ORIGIN_HISTORY_REPLAYED_REVIEW_REQUIRED'
        else:origin_state='REQUEST_ORIGIN_HISTORY_DISAGREEMENT_REVIEW_REQUIRED'
        origins['request_origin_recovery_state']=origin_state
    historical=dict(replayed_accepted_transitions=sum(item['accepted_transition_replayed'] for item in replayed),
        all_accepted_receipts_replayed=bool(replayed) and all(item['accepted_receipt_replayed'] for item in replayed),
        all_collection_scopes_replayed=bool(replayed) and all(item['collection_scope_replayed'] for item in replayed),
        all_account_fields_replayed=bool(replayed) and all(item['account_fields_replayed'] for item in replayed),
        all_collection_observations_replayed=bool(replayed) and all(item['collection_observation_replayed'] for item in replayed),
        all_collection_cursor_sequences_complete=bool(replayed) and all(item['collection_cursor_sequence_complete'] for item in replayed),
        all_activity_inputs_replayed=bool(replayed) and all(item['activity_input_replayed'] for item in replayed),
        all_pacing_transitions_replayed=bool(replayed) and all(item['pacing_transition_replayed'] for item in replayed),
        all_pacing_operations_typed=bool(replayed) and all(item['pacing_operation_typed'] for item in replayed),
        all_pacing_result_receipts_bound=bool(replayed) and all(item['pacing_result_receipt_bound'] for item in replayed),
        all_pacing_request_targets_bound=bool(replayed) and all(item['pacing_request_targets_bound'] for item in replayed),
        all_retention_transitions_replayed=bool(replayed) and all(item['retention_transition_replayed'] for item in replayed),
        all_accepted_transitions_replayed=bool(replayed) and all(item['accepted_transition_replayed'] for item in replayed))
    if pending or pace['review_required']:recovery='PACING_UNCERTAINTY_REVIEW_REQUIRED'
    elif not accepted['initialized']:
        recovery='ACCEPTANCE_HISTORY_MISSING_REVIEW_REQUIRED' if pace['sessions'] or retained['retained_batches'] else 'NO_LOCAL_READ_HISTORY'
    elif (accepted['pacing_bound_head_sha256']!=pace['head_sha256'] or accepted['retention_bound_head_sha256']!=retained['head_sha256']):
        recovery='UNBOUND_CROSS_JOURNAL_TRANSITION_REVIEW_REQUIRED'
    elif accepted['preexisting_history_unverified']:
        recovery='LEGACY_BASELINE_ONLY_REVIEW_REQUIRED'
    elif accepted['records'] and not historical['all_pacing_operations_typed']:
        recovery='ACCEPTED_PACING_OPERATION_UNVERIFIED_REVIEW_REQUIRED'
    elif accepted['records'] and not historical['all_pacing_result_receipts_bound']:
        recovery='ACCEPTED_PACING_RESULT_RECEIPT_UNVERIFIED_REVIEW_REQUIRED'
    elif accepted['records'] and not historical['all_pacing_request_targets_bound']:
        recovery='ACCEPTED_PACING_REQUEST_TARGET_UNVERIFIED_REVIEW_REQUIRED'
    elif accepted['records'] and not replay['latest_acceptance_record_replayed']:
        recovery='ACCEPTED_TRANSITION_CONTENT_DISAGREEMENT_REVIEW_REQUIRED'
    elif accepted['records'] and not historical['all_accepted_transitions_replayed']:
        recovery='ACCEPTED_HISTORY_CONTENT_DISAGREEMENT_REVIEW_REQUIRED'
    elif accepted['records'] and not historical['all_collection_cursor_sequences_complete']:
        recovery='ACCEPTED_COLLECTION_CONTINUATION_REQUIRED'
    elif accepted['records']:recovery='BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED'
    else:recovery='NO_ACCEPTED_READ_TRANSITIONS'
    uncertain=recovery in ('PACING_UNCERTAINTY_REVIEW_REQUIRED','ACCEPTANCE_HISTORY_MISSING_REVIEW_REQUIRED','UNBOUND_CROSS_JOURNAL_TRANSITION_REVIEW_REQUIRED','ACCEPTED_TRANSITION_CONTENT_DISAGREEMENT_REVIEW_REQUIRED','ACCEPTED_HISTORY_CONTENT_DISAGREEMENT_REVIEW_REQUIRED','ACCEPTED_COLLECTION_CONTINUATION_REQUIRED','LEGACY_BASELINE_ONLY_REVIEW_REQUIRED','ACCEPTED_PACING_OPERATION_UNVERIFIED_REVIEW_REQUIRED','ACCEPTED_PACING_RESULT_RECEIPT_UNVERIFIED_REVIEW_REQUIRED','ACCEPTED_PACING_REQUEST_TARGET_UNVERIFIED_REVIEW_REQUIRED')
    return dict(request_bound_transitions=request_bound_count,mode='PAPER_ACTIVITY_ACCEPTANCE_STATUS',status='REVIEW_REQUIRED',read_only=True,
        pacing_journal_present=pacing.path.is_file(),activity_store_present=store.path.is_file(),acceptance_store_present=acceptance.path.is_file(),
        account_sha256=retained['account_sha256'],pacing_head_sha256=pace['head_sha256'],
        retention_head_sha256=retained['head_sha256'],sessions=pace['sessions'],
        typed_activity_collection_sessions=pace['operation_counts']['ACTIVITY_COLLECTION'],
        legacy_unspecified_sessions=pace['operation_counts']['LEGACY_UNSPECIFIED'],
        session_capacity_remaining=max(0,MAX_SESSIONS-pace['sessions']),next_allowed_at=pace['next_allowed_at'],
        pending_read=pending,retained_review_block=pace['review_required'],
        retained_batches=retained['retained_batches'],retained_activities=retained['retained_activities'],
        retained_collection_observations=retained['retained_collection_observations'],
        batch_capacity_remaining=max(0,MAX_BATCHES-retained['retained_batches']),
        acceptance_history_initialized=accepted['initialized'],acceptance_head_sha256=accepted['head_sha256'],
        accepted_transitions=accepted['records'],acceptance_record_capacity_remaining=max(0,MAX_RECORDS-accepted['records']),
        preexisting_history_unverified=accepted['preexisting_history_unverified'],cross_journal_recovery_state=recovery,
        cross_journal_heads_match=accepted['initialized'] and accepted['pacing_bound_head_sha256']==pace['head_sha256'] and accepted['retention_bound_head_sha256']==retained['head_sha256'],
        **historical,**replay,**origins,
        suggested_action=('COLLECT_REVIEWED_NARROWER_WINDOWS_NO_AUTOMATIC_CONTINUATION'
            if recovery=='ACCEPTED_COLLECTION_CONTINUATION_REQUIRED' else
            'RETAIN_LEGACY_HISTORY_AND_REVIEW_SEPARATE_TYPED_EVIDENCE'
            if recovery=='ACCEPTED_PACING_OPERATION_UNVERIFIED_REVIEW_REQUIRED' else
            'RETAIN_UNRECEIPTED_HISTORY_AND_REVIEW_SEPARATE_RECEIPT_BOUND_EVIDENCE'
            if recovery=='ACCEPTED_PACING_RESULT_RECEIPT_UNVERIFIED_REVIEW_REQUIRED' else
            'RETAIN_UNTARGETED_HISTORY_AND_REVIEW_SEPARATE_TARGET_BOUND_EVIDENCE'
            if recovery=='ACCEPTED_PACING_REQUEST_TARGET_UNVERIFIED_REVIEW_REQUIRED' else
            'REVIEW_ALL_RETAINED_JOURNALS_NO_AUTOMATIC_RETRY' if uncertain else
            'RESPECT_RETAINED_COOLDOWN_AND_SEPARATELY_CONFIRM_ONE_READ'),
        **{flag:False for flag in FLAGS},
        limitations='Local replay only: no order file, vault or provider access and no admission permission. Every accepted content record is re-derived from its exact retained receipt, normalized activities, collection observation and exact retained pacing/activity head transitions. Request-target binding additionally reconstructs the account/page/account sequence from each retained creation window and opaque cursor. When an explicit private request journal is supplied, persisted v3 origins are additionally re-derived from their exact historical request prefixes and the complete current request tail is sampled again; legacy v2 records remain unverified. A final short page establishes only bounded cursor-sequence completion for that declared creation window; it does not attest source, delayed/corrected activity coverage or fee posting. Matching content and heads establish only local cooperating-journal consistency, not causality or valid-tail completeness. Missing history does not prove no earlier read. Other journals/hosts and direct store writers are not coordinated. Never erase uncertain reads, receipts, acceptance records or cooldowns to bypass review.')


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_ACTIVITY_ACCEPTANCE_PROBE_USAGE_ERROR\n')


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument('--expected-account', required=True);parser.add_argument('--pacing', required=True)
    parser.add_argument('--activity-store', required=True);parser.add_argument('--acceptance-store',required=True);parser.add_argument('--orders')
    parser.add_argument('--request-journal');parser.add_argument('--request-head')
    parser.add_argument('--after');parser.add_argument('--until')
    parser.add_argument('--confirm-read-only-paper', action='store_true')
    parser.add_argument('--status-only', action='store_true', help='Replay local journals only; an optional request journal verifies persisted origins without credentials or provider data.')
    args = parser.parse_args()
    try:
        if args.status_only:
            if args.confirm_read_only_paper or any(value is not None for value in (args.orders,args.request_head,args.after,args.until)): _fail()
            result = status(expected_account_id=args.expected_account,pacing_path=args.pacing,
                activity_store_path=args.activity_store,acceptance_store_path=args.acceptance_store,
                request_journal_path=args.request_journal)
        else:
            if None in (args.after,args.until) or (args.orders is None)==(args.request_journal is None):_fail()
            if (args.request_journal is None)!=(args.request_head is None):_fail()
            result = probe(expected_account_id=args.expected_account,pacing_path=args.pacing,
                activity_store_path=args.activity_store,orders_path=args.orders,after=args.after,until=args.until,
                request_journal_path=args.request_journal,expected_request_head=args.request_head,
                acceptance_store_path=args.acceptance_store,
                confirm_read_only_paper=args.confirm_read_only_paper)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, MemoryError, RecursionError, KeyboardInterrupt):
        print('PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED');return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__ == '__main__':
    raise SystemExit(main())
