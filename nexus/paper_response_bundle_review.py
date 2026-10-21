"""Offline exact-byte review of one account/activity response bundle.

The bundle composes the existing account and activity normalization reviews at
one exact current pacing head.  It requires an ACCOUNT_INVENTORY session to be
followed immediately by the current ACTIVITY_COLLECTION session, binds every
private response body and the portable account observation, and rechecks the
pacing and activity journals unchanged.  It never opens credentials, contacts
a provider, writes evidence or grants account, source, funds, routing or
release authority.
"""
import argparse
from contextlib import closing
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re
import sqlite3

from nexus.alpaca_paper_contract import canonical, sha
from nexus import alpaca_paper_contract as contract
from nexus import paper_account_response_review as account_review
from nexus import paper_activity_response_review as activity_review
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_read_pacing import PaperReadPacing
from nexus.broker_activity_store import BrokerActivityStore

MODE='PAPER_EXACT_RESPONSE_BUNDLE_REVIEW'
FLAGS=tuple(dict.fromkeys((*account_review.FLAGS,*activity_review.FLAGS)))
MAX_ADJACENT_START_GAP_SECONDS=300


def _fail(code):
    raise ValueError('PAPER_RESPONSE_BUNDLE_REVIEW_'+code) from None


def _digest(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:
        _fail('HEAD_INVALID')
    return value


def _order_identity_maps(open_orders,activity_orders,activity_records,account_id):
    """Compare identity, progress and fill deltas without exposing private IDs."""
    from nexus.paper_open_order_terms import _row
    open_by_broker={};open_by_client={};open_rows={}
    try:
        for raw in open_orders:
            row=_row(raw,account_id)
            identity=(row['broker_order_id'],row['client_order_id'],row['side'])
            if (row['broker_order_id'] in open_by_broker and
                    open_by_broker[row['broker_order_id']]!=identity):
                _fail('OPEN_ORDER_IDENTITY_CONFLICT')
            if (row['client_order_id'] in open_by_client and
                    open_by_client[row['client_order_id']]!=identity):
                _fail('OPEN_ORDER_IDENTITY_CONFLICT')
            open_by_broker[row['broker_order_id']]=identity
            open_by_client[row['client_order_id']]=identity
            open_rows[row['broker_order_id']]=row
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
        _fail('OPEN_ORDER_TERMS_UNSUPPORTED')
    activity_by_broker={};activity_by_client={};activity_rows={}
    try:
        for row in activity_orders:
            identity=(row['broker_order_id'],row['client_order_id'],row['side'])
            if (row['broker_order_id'] in activity_by_broker and
                    activity_by_broker[row['broker_order_id']]!=identity):
                _fail('ACTIVITY_ORDER_IDENTITY_CONFLICT')
            if (row['client_order_id'] in activity_by_client and
                    activity_by_client[row['client_order_id']]!=identity):
                _fail('ACTIVITY_ORDER_IDENTITY_CONFLICT')
            activity_by_broker[row['broker_order_id']]=identity
            activity_by_client[row['client_order_id']]=identity
            activity_rows[row['broker_order_id']]=row
    except (TypeError,KeyError):
        _fail('ACTIVITY_ORDER_IDENTITY_INVALID')
    shared_brokers=set(open_by_broker)&set(activity_by_broker)
    shared_clients=set(open_by_client)&set(activity_by_client)
    if (any(open_by_broker[key]!=activity_by_broker[key] for key in shared_brokers) or
            any(open_by_client[key]!=activity_by_client[key] for key in shared_clients)):
        _fail('CROSS_RESPONSE_ORDER_IDENTITY_CHANGED')
    open_identities=sorted(set(open_by_broker.values()))
    activity_identities=sorted(set(activity_by_broker.values()))
    overlap=len(set(open_identities)&set(activity_identities))
    progress=[];fill_deltas=[];fill_activity_count=0
    fill_partitions=[];historical_fill_activity_count=0
    fills_by_broker={}
    try:
        for record in activity_records:
            if record['kind']=='FILL':
                fills_by_broker.setdefault(record['broker_order_id'],[]).append(record)
    except (TypeError,KeyError):
        _fail('ACTIVITY_FILL_RECORD_INVALID')
    active_transitions={
        'accepted':contract.STATUSES,
        'pending_new':contract.STATUSES-{'accepted'},
        'new':contract.STATUSES-{'accepted','pending_new'},
        'partially_filled':{'partially_filled','pending_cancel','filled','canceled','expired'},
        'pending_cancel':{'pending_cancel','partially_filled','filled','canceled','expired'},
    }
    for broker_id in sorted(shared_brokers):
        before=open_rows[broker_id];after=activity_rows[broker_id]
        before_at=contract.timestamp_order_ns(before['updated_at'])
        after_at=contract.timestamp_order_ns(after['updated_at'])
        before_qty=contract.number(before['filled_quantity'],zero=True)
        after_qty=contract.number(after['filled_quantity'],zero=True)
        if after_at<before_at:_fail('CROSS_RESPONSE_ORDER_UPDATE_TIME_REGRESSION')
        if (after_at==before_at and (after['status'],after['filled_quantity'],
                after['average_fill_price'])!=(before['status'],before['filled_quantity'],
                before['average_fill_price'])):
            _fail('CROSS_RESPONSE_ORDER_UPDATE_TIME_CONFLICT')
        if after_qty<before_qty:_fail('CROSS_RESPONSE_ORDER_FILL_REGRESSION')
        if after_qty==before_qty and after['average_fill_price']!=before['average_fill_price']:
            _fail('CROSS_RESPONSE_ORDER_FILL_CORRECTION')
        if after['status'] not in active_transitions[before['status']]:
            _fail('CROSS_RESPONSE_ORDER_STATUS_REGRESSION')
        fills=fills_by_broker.get(broker_id,())
        try:
            parsed=sorted(((contract.number(fill['cumulative_quantity']),
                    contract.timestamp_order_ns(fill['occurred_at']),fill) for fill in fills),
                key=lambda item:(item[0],item[1],item[2]['activity_id']))
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_INVALID')
        if any(cumulative>after_qty for cumulative,_,_ in parsed):
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_AHEAD')
        historical=[]
        for cumulative,occurred_at,fill in parsed:
            if cumulative<=before_qty:
                if occurred_at>before_at:
                    _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_LATE_HISTORICAL')
                historical.append(dict(activity_id=fill['activity_id'],
                    cumulative_quantity=fill['cumulative_quantity'],
                    occurred_at=fill['occurred_at']))
        delta=[]
        if after_qty>before_qty:
            delta=[item for item in parsed if item[0]>before_qty]
            if not delta:_fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_MISSING')
            running=before_qty
            try:
                with localcontext() as context:
                    context.prec=80
                    notional=(Decimal(0) if before_qty==0 else
                        before_qty*contract.number(before['average_fill_price']))
                    for cumulative,occurred_at,fill in delta:
                        quantity=contract.number(fill['quantity'])
                        notional+=quantity*contract.number(fill['price'])
                        if cumulative!=running+quantity:_fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_GAP')
                        if not before_at<occurred_at<=after_at:_fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_TIME')
                        running=cumulative
                    after_average=contract.number(after['average_fill_price'])
                    expected_average=notional/after_qty
            except (TypeError,KeyError,OverflowError,AttributeError,ArithmeticError,InvalidOperation):
                _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_INVALID')
            if running!=after_qty:_fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_INCOMPLETE')
            if after_average!=expected_average:
                _fail('CROSS_RESPONSE_ORDER_FILL_AVERAGE_DISAGREEMENT')
            fill_activity_count+=len(delta)
            fill_deltas.append(dict(identity=open_by_broker[broker_id],
                before_filled_quantity=contract.text(before_qty),
                after_filled_quantity=contract.text(after_qty),
                before_average_fill_price=before['average_fill_price'],
                after_average_fill_price=after['average_fill_price'],
                fills=[dict(activity_id=fill['activity_id'],quantity=fill['quantity'],
                    price=fill['price'],cumulative_quantity=fill['cumulative_quantity'],
                    occurred_at=fill['occurred_at']) for _,_,fill in delta]))
        historical_fill_activity_count+=len(historical)
        fill_partitions.append(dict(identity=open_by_broker[broker_id],
            historical=historical,
            interval=[dict(activity_id=fill['activity_id'],
                cumulative_quantity=fill['cumulative_quantity'],occurred_at=fill['occurred_at'])
                for _,_,fill in delta]))
        progress.append(dict(identity=open_by_broker[broker_id],
            before_status=before['status'],after_status=after['status'],
            before_filled_quantity=contract.text(before_qty),
            after_filled_quantity=contract.text(after_qty),
            before_average_fill_price=before['average_fill_price'],
            after_average_fill_price=after['average_fill_price'],
            before_updated_at=before['updated_at'],after_updated_at=after['updated_at']))
    return (open_identities,activity_identities,overlap,progress,
        fill_deltas,fill_activity_count,fill_partitions,historical_fill_activity_count)


def _reported_fee_reference_links(activity_records,activity_orders):
    """Validate explicit reference pairs without promoting them to attested links."""
    try:
        fills={row['activity_id']:row for row in activity_records if row['kind']=='FILL'}
        order_ids={row['broker_order_id'] for row in activity_orders}
    except (TypeError,KeyError):
        _fail('ACTIVITY_FEE_REFERENCE_INVALID')
    links=[]
    for fee in activity_records:
        if fee.get('kind')=='FILL':continue
        try:
            order_id=fee['order_reference']
            execution_id=fee['unverified_execution_reference']
        except (TypeError,KeyError):
            _fail('ACTIVITY_FEE_REFERENCE_INVALID')
        if (order_id is None)!=(execution_id is None):
            _fail('CROSS_RESPONSE_FEE_REFERENCE_PARTIAL')
        if order_id is None:continue
        fill=fills.get(execution_id)
        if fill is None:_fail('CROSS_RESPONSE_FEE_EXECUTION_REFERENCE_UNKNOWN')
        if order_id not in order_ids:_fail('CROSS_RESPONSE_FEE_ORDER_REFERENCE_UNKNOWN')
        if fill['broker_order_id']!=order_id:
            _fail('CROSS_RESPONSE_FEE_REFERENCE_DISAGREEMENT')
        links.append(dict(fee_activity_id=fee['activity_id'],fee_kind=fee['kind'],
            fee_asset=fee['asset'],fee_amount=fee['amount'],reported_date=fee['reported_date'],
            fill_activity_id=execution_id,broker_order_id=order_id))
    return sorted(links,key=lambda row:row['fee_activity_id'])


def review(*,pacing_path,activity_store_path,expected_account_id,
        expected_pacing_head,expected_activity_head,account_session_head,
        account_before_path,positions_path,orders_path,account_after_path,
        account_observation_path,activity_session_head,
        activity_account_before_path,activity_page_paths,
        activity_account_after_path):
    """Bind adjacent retained account/activity sessions to exact private bytes."""
    expected_pacing_head=_digest(expected_pacing_head)
    expected_activity_head=_digest(expected_activity_head)
    account_session_head=_digest(account_session_head)
    activity_session_head=_digest(activity_session_head)
    if type(activity_page_paths) not in (tuple,list) or not 1<=len(activity_page_paths)<=2:
        _fail('PAGE_COUNT_INVALID')
    paths=[pacing_path,activity_store_path,account_before_path,positions_path,
        orders_path,account_after_path,account_observation_path,
        activity_account_before_path,*activity_page_paths,activity_account_after_path]
    checked=[_private_path(value)[1] for value in paths]
    if len(set(checked))!=len(checked):_fail('DISTINCT_PATHS_REQUIRED')

    gate=PaperReadPacing(pacing_path,expected_account_id)
    store=BrokerActivityStore(activity_store_path,expected_account_id)
    pacing_before=gate.inspect();activity_before=store.inspect()
    if pacing_before['head_sha256']!=expected_pacing_head:_fail('PACING_HEAD_CHANGED')
    if activity_before['head_sha256']!=expected_activity_head:_fail('ACTIVITY_HEAD_CHANGED')
    history=gate.operation_history()
    indices={row['after_head_sha256']:index for index,row in enumerate(history)}
    if account_session_head not in indices or activity_session_head not in indices:
        _fail('SESSION_NOT_FOUND')
    account_index=indices[account_session_head];activity_index=indices[activity_session_head]
    if (activity_index!=account_index+1 or activity_index!=len(history)-1 or
            activity_session_head!=expected_pacing_head):
        _fail('SESSION_SEQUENCE_INVALID')
    if (history[account_index]['operation']!='ACCOUNT_INVENTORY' or
            history[activity_index]['operation']!='ACTIVITY_COLLECTION'):
        _fail('SESSION_SEQUENCE_INVALID')
    adjacent_start_gap=history[activity_index]['started_at']-history[account_index]['completed_at']
    if not 0<=adjacent_start_gap<=MAX_ADJACENT_START_GAP_SECONDS:
        _fail('ADJACENT_SESSION_GAP_EXCEEDED')

    account=account_review.review(pacing_path=pacing_path,
        expected_account_id=expected_account_id,expected_head=expected_pacing_head,
        session_head=account_session_head,account_before_path=account_before_path,
        positions_path=positions_path,orders_path=orders_path,
        account_after_path=account_after_path,observation_path=account_observation_path)
    activity=activity_review.review(pacing_path=pacing_path,
        activity_store_path=activity_store_path,expected_account_id=expected_account_id,
        expected_pacing_head=expected_pacing_head,
        expected_activity_head=expected_activity_head,session_head=activity_session_head,
        account_before_path=activity_account_before_path,
        activity_page_paths=activity_page_paths,
        account_after_path=activity_account_after_path)
    if (account['account_sha256']!=activity['account_sha256'] or
            account['pacing_head_sha256']!=activity['pacing_head_sha256'] or
            account['session_timing_envelope_match'] is not True or
            activity['session_timing_envelope_match'] is not True or
            any(account[flag] is not False or activity[flag] is not False for flag in FLAGS)):
        _fail('COMPONENT_DISAGREEMENT')
    if account['account_fields_sha256']!=activity['account_fields_sha256']:
        _fail('ADJACENT_ACCOUNT_FIELDS_CHANGED')
    account_raw_values=[account_review._raw(path) for path in
        (account_before_path,positions_path,orders_path,account_after_path)]
    account_manifest=sha([hashlib.sha256(raw).hexdigest() for raw,_ in account_raw_values])
    if account_manifest!=account['response_manifest_sha256']:
        _fail('ACCOUNT_RESPONSE_BYTES_CHANGED')
    retained_activity=activity_before['receipts'].get(activity['retained_receipt_sha256'])
    if retained_activity is None:_fail('ACTIVITY_RECEIPT_CHANGED')
    retained_by_id={row['activity_id']:row for row in activity_before['normalized_activities']}
    try:retained_records=[retained_by_id[key] for key in retained_activity['activity_ids']]
    except KeyError:_fail('ACTIVITY_RECEIPT_CHANGED')
    (open_identities,activity_identities,overlap,order_progress,
        order_fill_deltas,order_fill_delta_activities,order_fill_partitions,
        order_historical_fill_activities)=_order_identity_maps(
        account_raw_values[2][1],retained_activity['orders'],retained_records,gate.account)
    reported_fee_links=_reported_fee_reference_links(
        retained_records,retained_activity['orders'])
    if gate.inspect()!=pacing_before:_fail('PACING_HEAD_CHANGED')
    if store.inspect()!=activity_before:_fail('ACTIVITY_HEAD_CHANGED')

    scope=dict(mode=MODE,status='REVIEW_REQUIRED',read_only=True,
        account_sha256=account['account_sha256'],
        pacing_head_sha256=expected_pacing_head,
        activity_head_sha256=expected_activity_head,
        account_session_head_sha256=account_session_head,
        activity_session_head_sha256=activity_session_head,
        account_response_manifest_sha256=account['response_manifest_sha256'],
        account_observation_manifest_sha256=account['portable_manifest_sha256'],
        adjacent_account_fields_sha256=account['account_fields_sha256'],
        reported_open_order_identity_sha256=sha(open_identities),
        activity_order_identity_sha256=sha(activity_identities),
        overlapping_order_identities=overlap,
        overlapping_order_progress_sha256=sha(order_progress),
        overlapping_order_progressions=len(order_progress),
        overlapping_order_fill_delta_sha256=sha(order_fill_deltas),
        overlapping_order_fill_deltas=len(order_fill_deltas),
        overlapping_order_fill_delta_activities=order_fill_delta_activities,
        overlapping_order_fill_partition_sha256=sha(order_fill_partitions),
        overlapping_order_historical_fill_activities=order_historical_fill_activities,
        overlapping_order_retained_fill_activities=(order_historical_fill_activities+
            order_fill_delta_activities),
        reported_fee_reference_sha256=sha(reported_fee_links),
        reported_fee_reference_links=len(reported_fee_links),
        activity_response_manifest_sha256=activity['response_manifest_sha256'],
        activity_receipt_sha256=activity['retained_receipt_sha256'],
        account_review_sha256=account['review_sha256'],
        activity_review_sha256=activity['review_sha256'],
        response_bodies=account['response_bodies']+activity['response_bodies'],
        activity_pages=activity['activity_pages'],
        exact_response_bytes_match=True,account_normalization_replayed=True,
        account_observation_replayed=True,activity_normalization_replayed=True,
        activity_observation_replayed=True,activity_receipt_replayed=True,
        sessions_adjacent=True,activity_session_is_current_tail=True,
        adjacent_session_start_gap_seconds=adjacent_start_gap,
        adjacent_session_start_gap_limit_seconds=MAX_ADJACENT_START_GAP_SECONDS,
        bounded_adjacent_session_start_gap=True,
        component_session_timing_envelopes_match=True,
        adjacent_account_fields_match=True,
        overlapping_order_identities_match=True,
        overlapping_order_progress_monotonic=True,
        overlapping_order_fill_deltas_bound=True,
        overlapping_order_fill_activities_partitioned=True,
        reported_fee_references_consistent=True,
        journals_rechecked_unchanged=True,admission_reserved=False,
        **{flag:False for flag in FLAGS})
    scope['review_sha256']=sha(scope)
    scope['limitations']=('Exact private bytes locally replay one adjacent ACCOUNT_INVENTORY and current-tail '
        'ACTIVITY_COLLECTION pair at shared journal heads, requires their normalized account fields to match, '
        'requires each retained read timing to stay inside its paced session, requires immutable broker/client/side '
        'identity agreement and non-regressing update time, cumulative fill, unchanged-quantity average price and '
        'status progression for orders visible in both responses, binds every increased cumulative fill to an '
        'exact contiguous retained fill-activity delta with bounded event time and weighted average price, '
        'requires every retained fill at or below the earlier cumulative quantity to occur no later than the '
        'earlier order update, and '
        'requires every explicit fee order/execution reference pair to resolve to the same retained fill. It limits the retained start gap '
        'to five minutes. This detects an adjacent discontinuity but does not '
        'make the reads atomic or prove a quiet interval. Hash agreement is not provider identity, TLS capture, '
        'account ownership, source or received-activity coverage, creation/execution/fee coverage, verified '
        'clocks, semantic fee links, fee coverage/posting, holds, settled or spendable funds, broker-wide pacing, routing permission or '
        'release acceptance. Keep all response bodies, observations and output outside Git.')
    return scope


def _fixture(root,two_pages=False,activity_account_cash='1000',activity_wall_clock=1792368000,
        account_order_client_id='fixture_1',account_order_changes=None,activity_order_changes=None,
        activity_fill_changes=None,activity_fee_changes=None,activity_include_fill=True):
    """Create disposable injected responses for one exact adjacent session pair."""
    from pathlib import Path
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    open_order=dict(id='01234567-89ab-cdef-0123-456789abcde1',
        client_order_id=account_order_client_id,
        account_id='01234567-89ab-cdef-0123-456789abcdef',symbol='BTCUSD',
        asset_class='crypto',side='buy',type='limit',time_in_force='ioc',qty='1',
        limit_price='101',filled_qty='0',filled_avg_price=None,status='new',
        updated_at='2026-10-17T17:59:59Z')
    open_order.update({} if account_order_changes is None else account_order_changes)
    account=account_review._fixture(root/'account',wall_clock_value=1792367940,
        open_orders=[open_order])
    activity=activity_review._fixture(root/'activity',two_pages=two_pages,
        pacing_path=account['pacing_path'],account_cash=activity_account_cash,
        wall_clock_value=activity_wall_clock,order_changes=activity_order_changes,
        fill_changes=activity_fill_changes,fee_changes=activity_fee_changes,
        include_fill=activity_include_fill)
    return dict(pacing_path=account['pacing_path'],
        activity_store_path=activity['activity_store_path'],
        expected_account_id=account['expected_account_id'],
        expected_pacing_head=activity['expected_pacing_head'],
        expected_activity_head=activity['expected_activity_head'],
        account_session_head=account['session_head'],
        account_before_path=account['account_before_path'],
        positions_path=account['positions_path'],orders_path=account['orders_path'],
        account_after_path=account['account_after_path'],
        account_observation_path=account['observation_path'],
        activity_session_head=activity['session_head'],
        activity_account_before_path=activity['account_before_path'],
        activity_page_paths=activity['activity_page_paths'],
        activity_account_after_path=activity['account_after_path'])


def _retime_fixture_pacing(options,*,account_times=None,activity_times=None):
    """Rewrite a disposable fixture chain to exercise timing-envelope rejection."""
    from nexus.paper_read_pacing import RECEIPT_PROFILE_V3, ZERO
    changes={}
    if account_times is not None:changes.update({1:account_times[0],2:account_times[1]})
    if activity_times is not None:changes.update({3:activity_times[0],4:activity_times[1]})
    # A sqlite3.Connection context manager commits or rolls back but does not
    # close the handle.  Close it explicitly so Windows can remove the
    # disposable timing-negative-control directory immediately after review.
    with closing(sqlite3.connect(options['pacing_path'])) as db:
        rows=db.execute('SELECT seq,body FROM events ORDER BY seq').fetchall()
        if [row[0] for row in rows]!=list(range(5)):_fail('FIXTURE_SHAPE_INVALID')
        previous=ZERO;heads=[];begin_head=None;before_head=None;operation=None
        for seq,encoded in rows:
            body=json.loads(encoded)
            if seq in changes:body['evaluated_at']=changes[seq]
            if body.get('kind')=='BEGIN':
                before_head=ZERO if seq==1 else previous
                operation=body['operation']
            elif body.get('kind')=='RESULT':
                body['token']=begin_head
                if body.get('receipt_profile')!=RECEIPT_PROFILE_V3:_fail('FIXTURE_SHAPE_INVALID')
                receipt=dict(profile=body['receipt_profile'],operation=operation,
                    before_head_sha256=before_head,begin_head_sha256=begin_head,
                    evaluated_at=body['evaluated_at'],outcome=body['outcome'],
                    request_kinds=body['request_kinds'],response_sha256=body['response_sha256'],
                    request_sha256=body['request_sha256'],
                    result_projection_sha256=body['result_projection_sha256'])
                body['receipt_sha256']=sha(receipt)
            digest=sha(dict(seq=seq,body=body,previous=previous))
            db.execute('UPDATE events SET body=?,previous=?,digest=? WHERE seq=?',
                (canonical(body),previous,digest,seq))
            previous=digest;heads.append(digest)
            if body.get('kind')=='BEGIN':begin_head=digest
        db.commit()
    options.update(account_session_head=heads[2],activity_session_head=heads[4],
        expected_pacing_head=heads[4])
    return options


def fixture_report():
    """Bounded injected bundle evidence for release trials; no external IO."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='nexus-response-bundle-') as directory:
        root=Path(directory);options=_fixture(root/'base',two_pages=True)
        before=sorted((str(path.relative_to(root/'base')),path.read_bytes())
            for path in (root/'base').rglob('*') if path.is_file())
        result=review(**options);repeated=review(**options)
        after=sorted((str(path.relative_to(root/'base')),path.read_bytes())
            for path in (root/'base').rglob('*') if path.is_file())
        changed=_fixture(root/'changed')
        changed['activity_page_paths'][0].write_bytes(
            changed['activity_page_paths'][0].read_bytes()+b' ')
        changed_rejected=False
        try:review(**changed)
        except ValueError as error:
            changed_rejected='RESPONSE_DIGEST_DISAGREEMENT' in str(error)
        discontinuous=_fixture(root/'discontinuous',activity_account_cash='999')
        discontinuity_rejected=False
        try:review(**discontinuous)
        except ValueError as error:
            discontinuity_rejected='ADJACENT_ACCOUNT_FIELDS_CHANGED' in str(error)
        order_conflict=_fixture(root/'order-conflict',account_order_client_id='different_client')
        order_identity_conflict_rejected=False
        try:review(**order_conflict)
        except ValueError as error:
            order_identity_conflict_rejected='CROSS_RESPONSE_ORDER_IDENTITY_CHANGED' in str(error)
        time_regression=_fixture(root/'time-regression',
            activity_order_changes={'updated_at':'2026-10-17T17:59:58+00:00'})
        order_time_regression_rejected=False
        try:review(**time_regression)
        except ValueError as error:
            order_time_regression_rejected='CROSS_RESPONSE_ORDER_UPDATE_TIME_REGRESSION' in str(error)
        fill_regression=_fixture(root/'fill-regression',
            account_order_changes={'filled_qty':'0.5','filled_avg_price':'99',
                'status':'partially_filled'},
            activity_order_changes={'filled_quantity':'0.4','average_fill_price':'100',
                'status':'partially_filled'},
            activity_fill_changes={'qty':'0.4','cum_qty':'0.4','leaves_qty':'0.6'})
        order_fill_regression_rejected=False
        try:review(**fill_regression)
        except ValueError as error:
            order_fill_regression_rejected='CROSS_RESPONSE_ORDER_FILL_REGRESSION' in str(error)
        status_regression=_fixture(root/'status-regression',
            account_order_changes={'status':'pending_cancel'},
            activity_order_changes={'status':'new','filled_quantity':'0','average_fill_price':None},
            activity_include_fill=False)
        order_status_regression_rejected=False
        try:review(**status_regression)
        except ValueError as error:
            order_status_regression_rejected='CROSS_RESPONSE_ORDER_STATUS_REGRESSION' in str(error)
        missing_fill=_fixture(root/'missing-fill',activity_include_fill=False)
        order_fill_activity_missing_rejected=False
        try:review(**missing_fill)
        except ValueError as error:
            order_fill_activity_missing_rejected='CROSS_RESPONSE_ORDER_FILL_ACTIVITY_MISSING' in str(error)
        fill_gap=_fixture(root/'fill-gap',
            activity_fill_changes={'qty':'0.5','cum_qty':'1','leaves_qty':'0.5'})
        order_fill_activity_gap_rejected=False
        try:review(**fill_gap)
        except ValueError as error:
            order_fill_activity_gap_rejected='CROSS_RESPONSE_ORDER_FILL_ACTIVITY_GAP' in str(error)
        fill_time=_fixture(root/'fill-time',
            activity_fill_changes={'transaction_time':'2026-10-17T17:59:58Z'})
        order_fill_activity_time_rejected=False
        try:review(**fill_time)
        except ValueError as error:
            order_fill_activity_time_rejected='CROSS_RESPONSE_ORDER_FILL_ACTIVITY_TIME' in str(error)
        fill_average=_fixture(root/'fill-average',activity_fill_changes={'price':'99'})
        order_fill_average_disagreement_rejected=False
        try:review(**fill_average)
        except ValueError as error:
            order_fill_average_disagreement_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_AVERAGE_DISAGREEMENT' in str(error))
        late_historical_fill=_fixture(root/'late-historical-fill',
            account_order_changes={'filled_qty':'0.5','filled_avg_price':'99',
                'status':'partially_filled'},
            activity_order_changes={'filled_quantity':'0.5','average_fill_price':'99',
                'status':'partially_filled'},
            activity_fill_changes={'qty':'0.5','cum_qty':'0.5','leaves_qty':'0.5',
                'price':'99','transaction_time':'2026-10-17T18:00:00Z'})
        late_historical_fill_rejected=False
        try:review(**late_historical_fill)
        except ValueError as error:
            late_historical_fill_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_ACTIVITY_LATE_HISTORICAL' in str(error))
        fee_execution=_fixture(root/'fee-execution',two_pages=True,
            activity_fee_changes={'execution_id':
                '20261017180000000::11234567-89ab-cdef-0123-999999999999'})
        fee_execution_reference_unknown_rejected=False
        try:review(**fee_execution)
        except ValueError as error:
            fee_execution_reference_unknown_rejected=(
                'CROSS_RESPONSE_FEE_EXECUTION_REFERENCE_UNKNOWN' in str(error))
        fee_order=_fixture(root/'fee-order',two_pages=True,
            activity_fee_changes={'order_id':'01234567-89ab-cdef-0123-456789abcde2'})
        fee_reference_disagreement_rejected=False
        try:review(**fee_order)
        except ValueError as error:
            fee_reference_disagreement_rejected=(
                'CROSS_RESPONSE_FEE_ORDER_REFERENCE_UNKNOWN' in str(error) or
                'CROSS_RESPONSE_FEE_REFERENCE_DISAGREEMENT' in str(error))
        fee_partial=_fixture(root/'fee-partial',two_pages=True,
            activity_fee_changes={'execution_id':None})
        fee_reference_partial_rejected=False
        try:review(**fee_partial)
        except ValueError as error:
            fee_reference_partial_rejected=(
                'CROSS_RESPONSE_FEE_REFERENCE_PARTIAL' in str(error))
        delayed=_fixture(root/'delayed',activity_wall_clock=1792368241)
        delayed_rejected=False
        try:review(**delayed)
        except ValueError as error:
            delayed_rejected='ADJACENT_SESSION_GAP_EXCEEDED' in str(error)
        timing=_fixture(root/'timing')
        _retime_fixture_pacing(timing,account_times=(1792367939,1792367939))
        timing_envelope_rejected=False
        try:review(**timing)
        except ValueError as error:
            timing_envelope_rejected='SESSION_TIMING_DISAGREEMENT' in str(error)
        tail=_fixture(root/'tail');gate=PaperReadPacing(tail['pacing_path'],tail['expected_account_id'])
        with gate.ownership() as owner:
            token=gate.begin(admission=lambda:1792454401,ownership=owner,
                operation='ACCOUNT_INVENTORY')
            gate.finish(token,admission=lambda:1792454402,status_code=401,ownership=owner,
                receipt=dict(operation='ACCOUNT_INVENTORY',request_kinds=['ACCOUNT'],
                    request_sha256=['a'*64],response_sha256=[]))
        tail_rejected=False
        try:review(**dict(tail,expected_pacing_head=gate.inspect()['head_sha256']))
        except ValueError as error:
            tail_rejected='SESSION_SEQUENCE_INVALID' in str(error)
        checks=dict(exact_adjacent_sessions_bound=result['sessions_adjacent'] and
                result['activity_session_is_current_tail'],
            exact_account_response_replayed=result['account_normalization_replayed'] and
                result['account_observation_replayed'],
            exact_activity_response_replayed=result['activity_normalization_replayed'] and
                result['activity_observation_replayed'] and result['activity_receipt_replayed'],
            bounded_adjacent_session_start_gap=result['bounded_adjacent_session_start_gap'],
            component_session_timing_envelopes_match=
                result['component_session_timing_envelopes_match'],
            adjacent_account_fields_match=result['adjacent_account_fields_match'],
            overlapping_order_identities_match=result['overlapping_order_identities_match'],
            shared_order_identity_bound=result['overlapping_order_identities']==1,
            overlapping_order_progress_monotonic=result['overlapping_order_progress_monotonic'],
            shared_order_progress_bound=result['overlapping_order_progressions']==1 and
                len(result['overlapping_order_progress_sha256'])==64,
            overlapping_order_fill_deltas_bound=result['overlapping_order_fill_deltas_bound'],
            shared_order_fill_delta_bound=result['overlapping_order_fill_deltas']==1 and
                result['overlapping_order_fill_delta_activities']==1 and
                len(result['overlapping_order_fill_delta_sha256'])==64,
            overlapping_order_fill_activities_partitioned=
                result['overlapping_order_fill_activities_partitioned'],
            shared_order_fill_partition_bound=
                result['overlapping_order_historical_fill_activities']==0 and
                result['overlapping_order_retained_fill_activities']==1 and
                len(result['overlapping_order_fill_partition_sha256'])==64,
            reported_fee_references_consistent=result['reported_fee_references_consistent'],
            reported_fee_reference_bound=result['reported_fee_reference_links']==1 and
                len(result['reported_fee_reference_sha256'])==64,
            repeat_review_matches=result==repeated,changed_activity_response_rejected=changed_rejected,
            adjacent_account_discontinuity_rejected=discontinuity_rejected,
            cross_response_order_identity_conflict_rejected=order_identity_conflict_rejected,
            cross_response_order_time_regression_rejected=order_time_regression_rejected,
            cross_response_order_fill_regression_rejected=order_fill_regression_rejected,
            cross_response_order_status_regression_rejected=order_status_regression_rejected,
            cross_response_order_fill_activity_missing_rejected=
                order_fill_activity_missing_rejected,
            cross_response_order_fill_activity_gap_rejected=order_fill_activity_gap_rejected,
            cross_response_order_fill_activity_time_rejected=order_fill_activity_time_rejected,
            cross_response_order_fill_average_disagreement_rejected=
                order_fill_average_disagreement_rejected,
            late_historical_fill_activity_rejected=late_historical_fill_rejected,
            fee_execution_reference_unknown_rejected=
                fee_execution_reference_unknown_rejected,
            fee_reference_disagreement_rejected=fee_reference_disagreement_rejected,
            fee_reference_partial_rejected=fee_reference_partial_rejected,
            delayed_adjacent_session_rejected=delayed_rejected,
            outside_session_timing_rejected=timing_envelope_rejected,
            session_tail_required=tail_rejected,journals_unchanged=before==after)
        return dict(source='SYNTHETIC_EXACT_RESPONSE_BUNDLE_FIXTURE',passed=all(checks.values()),
            **checks,actual_provider_access=False,actual_vault_access=False,
            **{flag:False for flag in FLAGS})


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_RESPONSE_BUNDLE_REVIEW_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    for name in ('pacing','activity-store','expected-account','expected-pacing-head',
            'expected-activity-head','account-session-head','account-before','positions',
            'orders','account-after','account-observation','activity-session-head',
            'activity-account-before','activity-account-after'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--activity-page',required=True,action='append')
    args=parser.parse_args()
    try:
        result=review(pacing_path=args.pacing,activity_store_path=args.activity_store,
            expected_account_id=args.expected_account,
            expected_pacing_head=args.expected_pacing_head,
            expected_activity_head=args.expected_activity_head,
            account_session_head=args.account_session_head,
            account_before_path=args.account_before,positions_path=args.positions,
            orders_path=args.orders,account_after_path=args.account_after,
            account_observation_path=args.account_observation,
            activity_session_head=args.activity_session_head,
            activity_account_before_path=args.activity_account_before,
            activity_page_paths=args.activity_page,
            activity_account_after_path=args.activity_account_after)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,
            OverflowError,MemoryError,RecursionError,RuntimeError,KeyboardInterrupt):
        print('PAPER_RESPONSE_BUNDLE_REVIEW_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
