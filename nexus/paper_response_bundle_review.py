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


def _retained_activity_order_fill_semantics(
        activity_orders,activity_records,*,excluded_brokers=()):
    """Fail closed on retained order/fill semantics even without open-order overlap."""
    orders={};fills_by_broker={}
    try:
        for order in activity_orders:
            broker_id=order['broker_order_id']
            if broker_id in orders and orders[broker_id]!=order:
                _fail('RETAINED_ACTIVITY_ORDER_CONFLICT')
            orders[broker_id]=order
        for record in activity_records:
            if record['kind']=='FILL':
                fills_by_broker.setdefault(record['broker_order_id'],[]).append(record)
    except (TypeError,KeyError):
        _fail('RETAINED_ACTIVITY_ORDER_FILL_INVALID')
    excluded_brokers=set(excluded_brokers)
    summaries=[];fill_count=0
    for broker_id in sorted(orders):
        if broker_id in excluded_brokers:continue
        order=orders[broker_id]
        try:
            requested=contract.number(order['requested_quantity'])
            filled=contract.number(order['filled_quantity'],zero=True)
            limit=contract.number(order['limit_price'])
            average=(contract.number(order['average_fill_price'])
                if filled else None)
            parsed=sorted(((contract.number(fill['cumulative_quantity']),
                    contract.timestamp_order_ns(fill['occurred_at']),fill)
                for fill in fills_by_broker.get(broker_id,())),
                key=lambda item:(item[0],item[1],item[2]['activity_id']))
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('RETAINED_ACTIVITY_ORDER_FILL_INVALID')
        if filled>requested:
            _fail('RETAINED_ACTIVITY_ORDER_OVERFILLED')
        if order['status']=='filled' and filled!=requested:
            _fail('RETAINED_ACTIVITY_ORDER_FILLED_QUANTITY_DISAGREEMENT')
        if filled==requested and order['status']!='filled':
            _fail('RETAINED_ACTIVITY_ORDER_COMPLETION_STATUS_DISAGREEMENT')
        requested_quantities=set()
        for previous,current in zip(parsed,parsed[1:]):
            if current[0]==previous[0]:
                _fail('RETAINED_ACTIVITY_ORDER_FILL_CUMULATIVE_CONFLICT')
            if current[1]<previous[1]:
                _fail('RETAINED_ACTIVITY_ORDER_FILL_TIME_REGRESSION')
            try:current_quantity=contract.number(current[2]['quantity'])
            except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
                _fail('RETAINED_ACTIVITY_ORDER_FILL_INVALID')
            if current[0]!=previous[0]+current_quantity:
                _fail('RETAINED_ACTIVITY_ORDER_FILL_CHAIN_GAP')
        retained=[]
        for cumulative,_,fill in parsed:
            try:
                leaves=contract.number(fill['leaves_quantity'],zero=True)
                price=contract.number(fill['price'])
            except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
                _fail('RETAINED_ACTIVITY_ORDER_FILL_INVALID')
            if cumulative>filled:
                _fail('RETAINED_ACTIVITY_ORDER_FILL_AHEAD')
            requested_quantities.add(cumulative+leaves)
            if ((order['side']=='buy' and price>limit) or
                    (order['side']=='sell' and price<limit)):
                _fail('RETAINED_ACTIVITY_ORDER_FILL_LIMIT_PRICE_DISAGREEMENT')
            if ((fill['fill_type']=='partial_fill' and leaves==0) or
                    (fill['fill_type']=='fill' and leaves!=0)):
                _fail('RETAINED_ACTIVITY_ORDER_FILL_TYPE_REMAINDER_DISAGREEMENT')
            retained.append(dict(activity_id=fill['activity_id'],
                quantity=fill['quantity'],
                cumulative_quantity=fill['cumulative_quantity'],
                leaves_quantity=fill['leaves_quantity'],fill_type=fill['fill_type']))
        if len(requested_quantities)>1:
            _fail('RETAINED_ACTIVITY_ORDER_FILL_REMAINING_CONFLICT')
        if requested_quantities and requested_quantities!={requested}:
            _fail('RETAINED_ACTIVITY_ORDER_FILL_REQUESTED_QUANTITY_DISAGREEMENT')
        if filled and ((order['side']=='buy' and average>limit) or
                (order['side']=='sell' and average<limit)):
            _fail('RETAINED_ACTIVITY_ORDER_AVERAGE_LIMIT_PRICE_DISAGREEMENT')
        if filled:
            try:
                with localcontext() as context:
                    context.prec=80
                    retained_quantity=sum((contract.number(fill['quantity'])
                        for _,_,fill in parsed),Decimal(0))
                    retained_notional=sum((contract.number(fill['quantity'])*
                        contract.number(fill['price']) for _,_,fill in parsed),
                        Decimal(0))
                    unobserved_quantity=filled-retained_quantity
                    unobserved_notional=average*filled-retained_notional
                    unobserved_limit_notional=limit*unobserved_quantity
            except (ValueError,TypeError,KeyError,OverflowError,AttributeError,
                    ArithmeticError,InvalidOperation):
                _fail('RETAINED_ACTIVITY_ORDER_FILL_INVALID')
            if unobserved_quantity<0:
                _fail('RETAINED_ACTIVITY_ORDER_FILL_INVALID')
            if unobserved_quantity==0:
                if unobserved_notional!=0:
                    _fail('RETAINED_ACTIVITY_ORDER_FILL_AVERAGE_DISAGREEMENT')
            else:
                if unobserved_notional<=0:
                    _fail('RETAINED_ACTIVITY_ORDER_UNOBSERVED_FILL_AVERAGE_INVALID')
                if ((order['side']=='buy' and
                        unobserved_notional>unobserved_limit_notional) or
                        (order['side']=='sell' and
                        unobserved_notional<unobserved_limit_notional)):
                    _fail('RETAINED_ACTIVITY_ORDER_UNOBSERVED_FILL_AVERAGE_LIMIT_PRICE_DISAGREEMENT')
        fill_count+=len(parsed)
        summaries.append(dict(broker_order_id=broker_id,status=order['status'],
            requested_quantity=order['requested_quantity'],
            filled_quantity=order['filled_quantity'],fills=retained))
    return dict(orders=len(summaries),fills=fill_count,summary_sha256=sha(summaries))


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
        requested_qty=contract.number(before['requested_quantity'])
        requested_limit=contract.number(before['limit_price'])
        try:activity_requested_qty=contract.number(after['requested_quantity'])
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('CROSS_RESPONSE_ORDER_REQUESTED_QUANTITY_INVALID')
        try:activity_limit_price=contract.number(after['limit_price'])
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('CROSS_RESPONSE_ORDER_LIMIT_PRICE_INVALID')
        if after_at<before_at:_fail('CROSS_RESPONSE_ORDER_UPDATE_TIME_REGRESSION')
        if (after_at==before_at and (after['status'],after['filled_quantity'],
                after['average_fill_price'])!=(before['status'],before['filled_quantity'],
                before['average_fill_price'])):
            _fail('CROSS_RESPONSE_ORDER_UPDATE_TIME_CONFLICT')
        if activity_requested_qty!=requested_qty:
            _fail('CROSS_RESPONSE_ORDER_REQUESTED_QUANTITY_CHANGED')
        if activity_limit_price!=contract.number(before['limit_price']):
            _fail('CROSS_RESPONSE_ORDER_LIMIT_PRICE_CHANGED')
        if after_qty<before_qty:_fail('CROSS_RESPONSE_ORDER_FILL_REGRESSION')
        if after_qty==before_qty and after['average_fill_price']!=before['average_fill_price']:
            _fail('CROSS_RESPONSE_ORDER_FILL_CORRECTION')
        if after['status'] not in active_transitions[before['status']]:
            _fail('CROSS_RESPONSE_ORDER_STATUS_REGRESSION')
        if after['status']=='filled' and after_qty!=requested_qty:
            _fail('CROSS_RESPONSE_ORDER_FILLED_QUANTITY_DISAGREEMENT')
        fills=fills_by_broker.get(broker_id,())
        try:
            parsed=sorted(((contract.number(fill['cumulative_quantity']),
                    contract.timestamp_order_ns(fill['occurred_at']),fill) for fill in fills),
                key=lambda item:(item[0],item[1],item[2]['activity_id']))
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_INVALID')
        try:
            requested_quantities={cumulative+contract.number(
                fill['leaves_quantity'],zero=True) for cumulative,_,fill in parsed}
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_INVALID')
        try:
            fill_limit_disagreement=any(
                (before['side']=='buy' and contract.number(fill['price'])>requested_limit) or
                (before['side']=='sell' and contract.number(fill['price'])<requested_limit)
                for _,_,fill in parsed)
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_INVALID')
        if fill_limit_disagreement:
            _fail('CROSS_RESPONSE_ORDER_FILL_LIMIT_PRICE_DISAGREEMENT')
        if len(requested_quantities)>1:
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_REMAINING_CONFLICT')
        if any(cumulative>after_qty for cumulative,_,_ in parsed):
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_AHEAD')
        for previous,current in zip(parsed,parsed[1:]):
            if current[0]==previous[0]:
                _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_CUMULATIVE_CONFLICT')
            if current[1]<previous[1]:
                _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_TIME_REGRESSION')
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
        if requested_quantities and requested_quantities!={requested_qty}:
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_REQUESTED_QUANTITY_DISAGREEMENT')
        if after_qty==requested_qty and after['status']!='filled':
            _fail('CROSS_RESPONSE_ORDER_COMPLETION_STATUS_DISAGREEMENT')
        try:
            fill_type_remainder_disagreement=any(
                (fill['fill_type']=='partial_fill' and
                    contract.number(fill['leaves_quantity'],zero=True)==0) or
                (fill['fill_type']=='fill' and
                    contract.number(fill['leaves_quantity'],zero=True)!=0)
                for _,_,fill in parsed)
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('CROSS_RESPONSE_ORDER_FILL_ACTIVITY_INVALID')
        if fill_type_remainder_disagreement:
            _fail('CROSS_RESPONSE_ORDER_FILL_TYPE_REMAINDER_DISAGREEMENT')
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


def _reported_fee_reference_links(activity_records,activity_orders,read_completed_at):
    """Validate fee capture times and explicit pairs without attesting links."""
    try:
        read_completed_ns=int(Decimal(str(read_completed_at))*Decimal(1_000_000_000))
    except (InvalidOperation,ValueError,TypeError,OverflowError):
        _fail('ACTIVITY_FEE_REFERENCE_INVALID')
    try:
        fills={row['activity_id']:row for row in activity_records if row['kind']=='FILL'}
        order_ids={row['broker_order_id'] for row in activity_orders}
    except (TypeError,KeyError):
        _fail('ACTIVITY_FEE_REFERENCE_INVALID')
    links=[];timestamped=0;date_only=0
    retained=0;retained_timestamped=0;retained_date_only=0
    unreferenced=0;unreferenced_timestamped=0;unreferenced_date_only=0
    for fee in activity_records:
        if fee.get('kind')=='FILL':continue
        retained+=1
        try:
            order_id=fee['order_reference']
            execution_id=fee['unverified_execution_reference']
        except (TypeError,KeyError):
            _fail('ACTIVITY_FEE_REFERENCE_INVALID')
        if (order_id is None)!=(execution_id is None):
            _fail('CROSS_RESPONSE_FEE_REFERENCE_PARTIAL')
        fill=None
        if order_id is not None:
            fill=fills.get(execution_id)
            if fill is None:_fail('CROSS_RESPONSE_FEE_EXECUTION_REFERENCE_UNKNOWN')
            if order_id not in order_ids:_fail('CROSS_RESPONSE_FEE_ORDER_REFERENCE_UNKNOWN')
            if fill['broker_order_id']!=order_id:
                _fail('CROSS_RESPONSE_FEE_REFERENCE_DISAGREEMENT')
        reported=fee['reported_date']
        try:
            if 'T' in reported:
                fee_at=contract.timestamp_order_ns(reported)
                retained_timestamped+=1
            else:
                # A calendar date has no accepted timezone or posting instant.
                # Retain it as an unverified reference without inventing time.
                retained_date_only+=1
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('ACTIVITY_FEE_REFERENCE_INVALID')
        if 'T' in reported and fee_at>read_completed_ns:
            _fail('CROSS_RESPONSE_FEE_TIMESTAMP_AFTER_CAPTURE')
        if order_id is None:
            unreferenced+=1
            if 'T' in reported:unreferenced_timestamped+=1
            else:unreferenced_date_only+=1
            continue
        if 'T' in reported:
            fill_at=contract.timestamp_order_ns(fill['occurred_at'])
            timestamped+=1
            if fee_at<fill_at:_fail('CROSS_RESPONSE_FEE_TIMESTAMP_PRECEDES_FILL')
        else:date_only+=1
        links.append(dict(fee_activity_id=fee['activity_id'],fee_kind=fee['kind'],
            fee_asset=fee['asset'],fee_amount=fee['amount'],reported_date=fee['reported_date'],
            fill_activity_id=execution_id,broker_order_id=order_id))
    return (sorted(links,key=lambda row:row['fee_activity_id']),timestamped,date_only,
        dict(retained=retained,timestamped=retained_timestamped,date_only=retained_date_only,
            unreferenced=unreferenced,unreferenced_timestamped=unreferenced_timestamped,
            unreferenced_date_only=unreferenced_date_only))


def _retained_order_fill_capture_scope(
        activity_records,activity_orders,read_started_at,read_completed_at):
    """Fence frozen order snapshots at start and fills at completion."""
    try:
        read_started_ns=int(Decimal(str(read_started_at))*Decimal(1_000_000_000))
        read_completed_ns=int(Decimal(str(read_completed_at))*Decimal(1_000_000_000))
    except (InvalidOperation,ValueError,TypeError,OverflowError):
        _fail('ACTIVITY_ORDER_FILL_TIME_INVALID')
    if read_started_ns>read_completed_ns:_fail('ACTIVITY_ORDER_FILL_TIME_INVALID')
    orders=0
    for order in activity_orders:
        try:updated_at=contract.timestamp_order_ns(order['updated_at'])
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('ACTIVITY_ORDER_FILL_TIME_INVALID')
        orders+=1
        if updated_at>read_completed_ns:
            _fail('CROSS_RESPONSE_ORDER_UPDATE_AFTER_CAPTURE')
        if updated_at>read_started_ns:
            _fail('CROSS_RESPONSE_ORDER_UPDATE_AFTER_COLLECTION_START')
    fills=0
    for record in activity_records:
        try:kind=record['kind']
        except (TypeError,KeyError):_fail('ACTIVITY_ORDER_FILL_TIME_INVALID')
        if kind!='FILL':continue
        try:occurred_at=contract.timestamp_order_ns(record['occurred_at'])
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('ACTIVITY_ORDER_FILL_TIME_INVALID')
        fills+=1
        if occurred_at>read_completed_ns:
            _fail('CROSS_RESPONSE_FILL_TIMESTAMP_AFTER_CAPTURE')
    return dict(orders=orders,fills=fills)


def _reported_account_order_capture_scope(open_orders,read_completed_at):
    """Fence exact account-response order updates at read completion."""
    try:
        read_completed_ns=int(Decimal(str(read_completed_at))*Decimal(1_000_000_000))
    except (InvalidOperation,ValueError,TypeError,OverflowError):
        _fail('ACCOUNT_ORDER_TIME_INVALID')
    orders=0
    for order in open_orders:
        try:updated_at=contract.timestamp_order_ns(order['updated_at'])
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            _fail('ACCOUNT_ORDER_TIME_INVALID')
        orders+=1
        if updated_at>read_completed_ns:
            _fail('CROSS_RESPONSE_ACCOUNT_ORDER_UPDATE_AFTER_CAPTURE')
    return orders


def _retained_activity_page_capture_scope(activity_records,pages):
    """Fence each exact activity time at its earliest retained page receipt."""
    try:
        records={row['activity_id']:row for row in activity_records}
        if len(records)!=len(activity_records):_fail('ACTIVITY_PAGE_TIME_INVALID')
        received_by_id={}
        for page in pages:
            received_ns=int(Decimal(str(page['received_at']))*Decimal(1_000_000_000))
            for activity_id in page['activity_ids']:
                if activity_id not in records:_fail('ACTIVITY_PAGE_TIME_INVALID')
                previous=received_by_id.get(activity_id)
                if previous is None or received_ns<previous:
                    received_by_id[activity_id]=received_ns
        if set(received_by_id)!=set(records):_fail('ACTIVITY_PAGE_TIME_INVALID')
    except (InvalidOperation,ValueError,TypeError,KeyError,OverflowError):
        _fail('ACTIVITY_PAGE_TIME_INVALID')
    fills=0;timestamped_fees=0;date_only_fees=0
    for activity_id,record in records.items():
        try:kind=record['kind']
        except (TypeError,KeyError):_fail('ACTIVITY_PAGE_TIME_INVALID')
        if kind=='FILL':
            try:activity_at=contract.timestamp_order_ns(record['occurred_at'])
            except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
                _fail('ACTIVITY_PAGE_TIME_INVALID')
            fills+=1
            if activity_at>received_by_id[activity_id]:
                _fail('CROSS_RESPONSE_FILL_TIMESTAMP_AFTER_PAGE_RECEIPT')
            continue
        try:reported=record['reported_date']
        except (TypeError,KeyError):_fail('ACTIVITY_PAGE_TIME_INVALID')
        if 'T' not in reported:
            date_only_fees+=1
            continue
        try:activity_at=contract.timestamp_order_ns(reported)
        except (ValueError,TypeError,OverflowError,AttributeError):
            _fail('ACTIVITY_PAGE_TIME_INVALID')
        timestamped_fees+=1
        if activity_at>received_by_id[activity_id]:
            _fail('CROSS_RESPONSE_FEE_TIMESTAMP_AFTER_PAGE_RECEIPT')
    return dict(fills=fills,timestamped_fees=timestamped_fees,
        date_only_fees=date_only_fees)


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
    try:
        account_observation_file,_=_private_path(account_observation_path)
        account_observation_raw=account_review._read(account_observation_file)
        account_observation=account_review.portable.loads(account_observation_raw)
        account_observation_manifest=account_observation['manifest_sha256']
        account_read_completed_at=account_observation['read_timing']['completed_at']
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
        _fail('ACCOUNT_ORDER_TIME_INVALID')
    if account_observation_manifest!=account['portable_manifest_sha256']:
        _fail('ACCOUNT_RESPONSE_BYTES_CHANGED')
    if (hashlib.sha256(account_observation_raw).hexdigest()!=
            account['portable_observation_file_sha256']):
        _fail('ACCOUNT_RESPONSE_BYTES_CHANGED')
    reported_account_orders=_reported_account_order_capture_scope(
        account_raw_values[2][1],account_read_completed_at)
    retained_activity=activity_before['receipts'].get(activity['retained_receipt_sha256'])
    if retained_activity is None:_fail('ACTIVITY_RECEIPT_CHANGED')
    retained_by_id={row['activity_id']:row for row in activity_before['normalized_activities']}
    try:retained_records=[retained_by_id[key] for key in retained_activity['activity_ids']]
    except KeyError:_fail('ACTIVITY_RECEIPT_CHANGED')
    retained_order_fill_scope=_retained_order_fill_capture_scope(
        retained_records,retained_activity['orders'],
        retained_activity['collection_observation']['read_started_at'],
        retained_activity['collection_observation']['read_completed_at'])
    (open_identities,activity_identities,overlap,order_progress,
        order_fill_deltas,order_fill_delta_activities,order_fill_partitions,
        order_historical_fill_activities)=_order_identity_maps(
        account_raw_values[2][1],retained_activity['orders'],retained_records,gate.account)
    shared_brokers={identity[0] for identity in set(open_identities)&set(activity_identities)}
    retained_order_fill_semantics=_retained_activity_order_fill_semantics(
        retained_activity['orders'],retained_records,excluded_brokers=shared_brokers)
    (reported_fee_links,reported_fee_timestamped_links,
        reported_fee_date_only_links,retained_fee_scope)=_reported_fee_reference_links(
        retained_records,retained_activity['orders'],
        retained_activity['collection_observation']['read_completed_at'])
    retained_page_capture_scope=_retained_activity_page_capture_scope(
        retained_records,retained_activity['collection_observation']['pages'])
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
        reported_fee_timestamped_links=reported_fee_timestamped_links,
        reported_fee_date_only_links=reported_fee_date_only_links,
        retained_fee_records=retained_fee_scope['retained'],
        retained_fee_timestamped_records=retained_fee_scope['timestamped'],
        retained_fee_date_only_records=retained_fee_scope['date_only'],
        unreferenced_fee_records=retained_fee_scope['unreferenced'],
        unreferenced_fee_timestamped_records=
            retained_fee_scope['unreferenced_timestamped'],
        unreferenced_fee_date_only_records=retained_fee_scope['unreferenced_date_only'],
        retained_activity_order_snapshots=retained_order_fill_scope['orders'],
        retained_activity_order_fill_semantics_sha256=
            retained_order_fill_semantics['summary_sha256'],
        activity_only_orders_fill_semantics_bound=
            retained_order_fill_semantics['orders'],
        activity_only_fills_semantics_bound=retained_order_fill_semantics['fills'],
        reported_account_order_snapshots=reported_account_orders,
        retained_fill_records=retained_order_fill_scope['fills'],
        page_receipt_bounded_fill_records=retained_page_capture_scope['fills'],
        page_receipt_bounded_fee_exact_timestamp_records=
            retained_page_capture_scope['timestamped_fees'],
        page_receipt_temporally_unverified_fee_date_records=
            retained_page_capture_scope['date_only_fees'],
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
        retained_fill_limit_prices_checked=True,
        activity_only_order_average_limit_prices_checked=True,
        complete_activity_only_fill_averages_checked=True,
        unobserved_activity_fill_average_limits_checked=True,
        retained_fill_type_remainders_checked=True,
        retained_activity_fill_deltas_checked=True,
        retained_activity_order_fill_semantics_bound=True,
        overlapping_order_fill_activities_partitioned=True,
        reported_fee_references_consistent=True,
        reported_fee_exact_timestamp_chronology_checked=True,
        reported_fee_exact_timestamp_capture_upper_bound_checked=True,
        retained_fee_exact_timestamp_capture_upper_bound_checked=True,
        retained_order_update_capture_upper_bound_checked=True,
        reported_account_order_update_capture_upper_bound_checked=True,
        retained_order_update_collection_start_upper_bound_checked=True,
        retained_fill_timestamp_capture_upper_bound_checked=True,
        retained_exact_activity_capture_upper_bound_checked=True,
        retained_fill_timestamp_page_receipt_upper_bound_checked=True,
        retained_fee_exact_timestamp_page_receipt_upper_bound_checked=True,
        retained_exact_activity_page_receipt_upper_bound_checked=True,
        journals_rechecked_unchanged=True,admission_reserved=False,
        **{flag:False for flag in FLAGS})
    scope['review_sha256']=sha(scope)
    scope['limitations']=('Exact private bytes locally replay one adjacent ACCOUNT_INVENTORY and current-tail '
        'ACTIVITY_COLLECTION pair at shared journal heads, requires their normalized account fields to match, '
        'requires each retained read timing to stay inside its paced session, requires immutable broker/client/side '
        'identity agreement and non-regressing update time, cumulative fill, unchanged-quantity average price and '
        'status progression for orders visible in both responses, binds retained activity-order completion, '
        'requested/remainder quantities, adjacent cumulative fill deltas, fill type, order-average limit '
        'compliance, exact retained-versus-unobserved notional decomposition, complete retained-chain average '
        'arithmetic and individual limit-price semantics even when an order is '
        'not present in the preceding open-order snapshot, binds every increased cumulative fill to an '
        'exact contiguous retained fill-activity delta with bounded event time, an individual execution price '
        'that respects the immutable order limit, fill/partial-fill type consistent with the retained remainder, '
        'and weighted average price, '
        'requires every retained fill at or below the earlier cumulative quantity to occur no later than the '
        'earlier order update, and '
        'requires every exact account-response order update to be no later than that account read completion, '
        'requires every explicit fee order/execution reference pair to resolve to the same retained fill, refuses a referenced exact fee timestamp that precedes that fill, requires every caller-supplied order snapshot frozen before collection to be updated no later than the retained collection start, refuses any retained fill or exact fee timestamp beyond collection completion, and requires every exact retained fill or fee timestamp to be no later than the earliest page receipt that contained it. Date-only fee records remain temporally unverified. It limits the retained start gap '
        'to five minutes. This detects an adjacent discontinuity but does not '
        'make the reads atomic or prove a quiet interval. Hash agreement is not provider identity, TLS capture, '
        'account ownership, source or received-activity coverage, creation/execution/fee coverage, verified '
        'clocks, semantic fee links, fee coverage/posting, holds, settled or spendable funds, broker-wide pacing, routing permission or '
        'release acceptance. Keep all response bodies, observations and output outside Git.')
    return scope


def _fixture(root,two_pages=False,activity_account_cash='1000',activity_wall_clock=1792368000,
        account_order_client_id='fixture_1',account_order_changes=None,activity_order_changes=None,
        activity_fill_changes=None,activity_extra_fill_changes=None,
        activity_fee_changes=None,activity_include_fill=True,
        activity_response_time_offsets=None):
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
    activity_order={'limit_price':'101'}
    activity_order.update({} if activity_order_changes is None else activity_order_changes)
    activity=activity_review._fixture(root/'activity',two_pages=two_pages,
        pacing_path=account['pacing_path'],account_cash=activity_account_cash,
        wall_clock_value=activity_wall_clock,order_changes=activity_order,
        fill_changes=activity_fill_changes,extra_fill_changes=activity_extra_fill_changes,
        fee_changes=activity_fee_changes,
        include_fill=activity_include_fill,response_time_offsets=activity_response_time_offsets)
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
        filled_quantity_disagreement=_fixture(root/'filled-quantity-disagreement',
            account_order_changes={'filled_qty':'0.5','filled_avg_price':'99',
                'status':'partially_filled'},
            activity_order_changes={'status':'filled','filled_quantity':'0.5',
                'average_fill_price':'99'},activity_include_fill=False)
        filled_status_quantity_disagreement_rejected=False
        try:review(**filled_quantity_disagreement)
        except ValueError as error:
            filled_status_quantity_disagreement_rejected=(
                'CROSS_RESPONSE_ORDER_FILLED_QUANTITY_DISAGREEMENT' in str(error))
        completion_status_disagreement=_fixture(root/'completion-status-disagreement',
            account_order_changes={'filled_qty':'0.5','filled_avg_price':'99',
                'status':'partially_filled'},
            activity_order_changes={'status':'partially_filled','filled_quantity':'1',
                'average_fill_price':'100'},
            activity_fill_changes={'qty':'0.5','cum_qty':'1','leaves_qty':'0',
                'price':'101'})
        completion_status_disagreement_rejected=False
        try:review(**completion_status_disagreement)
        except ValueError as error:
            completion_status_disagreement_rejected=(
                'CROSS_RESPONSE_ORDER_COMPLETION_STATUS_DISAGREEMENT' in str(error))
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
        cumulative_conflict=_fixture(root/'cumulative-conflict',
            activity_fill_changes={'qty':'0.5','cum_qty':'0.5','leaves_qty':'0.5',
                'transaction_time':'2026-10-17T18:00:00Z'},
            activity_extra_fill_changes={'qty':'0.5','cum_qty':'0.5',
                'leaves_qty':'0.5','transaction_time':'2026-10-17T18:00:01Z'})
        repeated_cumulative_fill_state_rejected=False
        try:review(**cumulative_conflict)
        except ValueError as error:
            repeated_cumulative_fill_state_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_ACTIVITY_CUMULATIVE_CONFLICT' in str(error))
        fill_time_regression=_fixture(root/'fill-time-regression',
            activity_fill_changes={'qty':'0.5','cum_qty':'0.5','leaves_qty':'0.5',
                'transaction_time':'2026-10-17T18:00:01Z'},
            activity_extra_fill_changes={'qty':'0.5','cum_qty':'1','leaves_qty':'0',
                'transaction_time':'2026-10-17T18:00:00Z'})
        cumulative_fill_time_regression_rejected=False
        try:review(**fill_time_regression)
        except ValueError as error:
            cumulative_fill_time_regression_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_ACTIVITY_TIME_REGRESSION' in str(error))
        remaining_conflict=_fixture(root/'remaining-conflict',
            activity_fill_changes={'qty':'0.5','cum_qty':'0.5','leaves_qty':'0.5',
                'transaction_time':'2026-10-17T18:00:00Z'},
            activity_extra_fill_changes={'qty':'0.5','cum_qty':'1','leaves_qty':'0.5',
                'transaction_time':'2026-10-17T18:00:01Z'})
        cumulative_fill_remaining_conflict_rejected=False
        try:review(**remaining_conflict)
        except ValueError as error:
            cumulative_fill_remaining_conflict_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_ACTIVITY_REMAINING_CONFLICT' in str(error))
        requested_quantity_disagreement=_fixture(root/'requested-quantity-disagreement',
            activity_fill_changes={'leaves_qty':'1'})
        fill_requested_quantity_disagreement_rejected=False
        try:review(**requested_quantity_disagreement)
        except ValueError as error:
            fill_requested_quantity_disagreement_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_ACTIVITY_REQUESTED_QUANTITY_DISAGREEMENT'
                in str(error))
        activity_requested_quantity_changed=_fixture(
            root/'activity-requested-quantity-changed',
            activity_order_changes={'requested_quantity':'2'})
        activity_requested_quantity_changed_rejected=False
        try:review(**activity_requested_quantity_changed)
        except ValueError as error:
            activity_requested_quantity_changed_rejected=(
                'CROSS_RESPONSE_ORDER_REQUESTED_QUANTITY_CHANGED' in str(error))
        activity_limit_price_changed=_fixture(
            root/'activity-limit-price-changed',
            activity_order_changes={'limit_price':'102'})
        activity_limit_price_changed_rejected=False
        try:review(**activity_limit_price_changed)
        except ValueError as error:
            activity_limit_price_changed_rejected=(
                'CROSS_RESPONSE_ORDER_LIMIT_PRICE_CHANGED' in str(error))
        fill_limit_price_disagreement=_fixture(
            root/'fill-limit-price-disagreement',
            activity_order_changes={'status':'filled','filled_quantity':'1',
                'average_fill_price':'101'},
            activity_fill_changes={'type':'partial_fill','qty':'0.5',
                'cum_qty':'0.5','leaves_qty':'0.5','price':'100',
                'transaction_time':'2026-10-17T18:00:00Z'},
            activity_extra_fill_changes={'type':'fill','qty':'0.5','cum_qty':'1',
                'leaves_qty':'0','price':'102',
                'transaction_time':'2026-10-17T18:00:01Z'})
        fill_limit_price_disagreement_rejected=False
        try:review(**fill_limit_price_disagreement)
        except ValueError as error:
            fill_limit_price_disagreement_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_LIMIT_PRICE_DISAGREEMENT' in str(error))
        partial_fill_without_remainder=_fixture(
            root/'partial-fill-without-remainder',
            activity_fill_changes={'type':'partial_fill','qty':'1','cum_qty':'1',
                'leaves_qty':'0','price':'100'})
        partial_fill_without_remainder_rejected=False
        try:review(**partial_fill_without_remainder)
        except ValueError as error:
            partial_fill_without_remainder_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_TYPE_REMAINDER_DISAGREEMENT'
                in str(error))
        terminal_fill_with_remainder=_fixture(
            root/'terminal-fill-with-remainder',
            account_order_changes={'filled_qty':'0.25','filled_avg_price':'100',
                'status':'partially_filled'},
            activity_order_changes={'status':'partially_filled',
                'filled_quantity':'0.5','average_fill_price':'100'},
            activity_fill_changes={'type':'fill','qty':'0.25','cum_qty':'0.5',
                'leaves_qty':'0.5','price':'100'})
        terminal_fill_with_remainder_rejected=False
        try:review(**terminal_fill_with_remainder)
        except ValueError as error:
            terminal_fill_with_remainder_rejected=(
                'CROSS_RESPONSE_ORDER_FILL_TYPE_REMAINDER_DISAGREEMENT'
                in str(error))
        activity_only_semantic_disagreement=_fixture(
            root/'activity-only-semantic-disagreement',
            account_order_client_id='other_client',
            account_order_changes={'id':'01234567-89ab-cdef-0123-456789abcde9'},
            activity_order_changes={'status':'filled','filled_quantity':'0.5',
                'average_fill_price':'100'},
            activity_fill_changes={'type':'fill','qty':'0.5','cum_qty':'0.5',
                'leaves_qty':'0.5','price':'100'})
        activity_only_order_fill_semantic_disagreement_rejected=False
        try:review(**activity_only_semantic_disagreement)
        except ValueError as error:
            activity_only_order_fill_semantic_disagreement_rejected=(
                'RETAINED_ACTIVITY_ORDER_FILLED_QUANTITY_DISAGREEMENT'
                in str(error))
        activity_only_fill_chain_gap=_fixture(
            root/'activity-only-fill-chain-gap',
            account_order_client_id='other_client',
            account_order_changes={'id':'01234567-89ab-cdef-0123-456789abcde9'},
            activity_order_changes={'status':'filled','filled_quantity':'1',
                'average_fill_price':'100'},
            activity_fill_changes={'type':'partial_fill','qty':'0.5',
                'cum_qty':'0.5','leaves_qty':'0.5','price':'100',
                'transaction_time':'2026-10-17T18:00:00Z'},
            activity_extra_fill_changes={'type':'fill','qty':'0.25',
                'cum_qty':'1','leaves_qty':'0','price':'100',
                'transaction_time':'2026-10-17T18:00:01Z'})
        activity_only_fill_chain_gap_rejected=False
        try:review(**activity_only_fill_chain_gap)
        except ValueError as error:
            activity_only_fill_chain_gap_rejected=(
                'RETAINED_ACTIVITY_ORDER_FILL_CHAIN_GAP' in str(error))
        activity_only_average_limit_disagreement=_fixture(
            root/'activity-only-average-limit-disagreement',
            account_order_client_id='other_client',
            account_order_changes={'id':'01234567-89ab-cdef-0123-456789abcde9'},
            activity_order_changes={'status':'partially_filled',
                'filled_quantity':'0.5','average_fill_price':'102'},
            activity_fill_changes={'type':'partial_fill','qty':'0.25',
                'cum_qty':'0.5','leaves_qty':'0.5','price':'100'})
        activity_only_average_limit_disagreement_rejected=False
        try:review(**activity_only_average_limit_disagreement)
        except ValueError as error:
            activity_only_average_limit_disagreement_rejected=(
                'RETAINED_ACTIVITY_ORDER_AVERAGE_LIMIT_PRICE_DISAGREEMENT'
                in str(error))
        activity_only_complete_fill_average_disagreement=_fixture(
            root/'activity-only-complete-fill-average-disagreement',
            account_order_client_id='other_client',
            account_order_changes={'id':'01234567-89ab-cdef-0123-456789abcde9'},
            activity_order_changes={'status':'filled','filled_quantity':'1',
                'average_fill_price':'99'},
            activity_fill_changes={'type':'fill','qty':'1','cum_qty':'1',
                'leaves_qty':'0','price':'100'})
        activity_only_complete_fill_average_disagreement_rejected=False
        try:review(**activity_only_complete_fill_average_disagreement)
        except ValueError as error:
            activity_only_complete_fill_average_disagreement_rejected=(
                'RETAINED_ACTIVITY_ORDER_FILL_AVERAGE_DISAGREEMENT'
                in str(error))
        activity_only_unobserved_average_limit_disagreement=_fixture(
            root/'activity-only-unobserved-average-limit-disagreement',
            account_order_client_id='other_client',
            account_order_changes={'id':'01234567-89ab-cdef-0123-456789abcde9'},
            activity_order_changes={'status':'filled','filled_quantity':'1',
                'average_fill_price':'90'},
            activity_fill_changes={'type':'fill','qty':'0.5','cum_qty':'1',
                'leaves_qty':'0','price':'78'})
        activity_only_unobserved_average_limit_disagreement_rejected=False
        try:review(**activity_only_unobserved_average_limit_disagreement)
        except ValueError as error:
            activity_only_unobserved_average_limit_disagreement_rejected=(
                'RETAINED_ACTIVITY_ORDER_UNOBSERVED_FILL_AVERAGE_LIMIT_PRICE_DISAGREEMENT'
                in str(error))
        activity_only_unobserved_average_invalid=_fixture(
            root/'activity-only-unobserved-average-invalid',
            account_order_client_id='other_client',
            account_order_changes={'id':'01234567-89ab-cdef-0123-456789abcde9'},
            activity_order_changes={'status':'filled','filled_quantity':'1',
                'average_fill_price':'50'},
            activity_fill_changes={'type':'fill','qty':'0.5','cum_qty':'1',
                'leaves_qty':'0','price':'100'})
        activity_only_unobserved_average_invalid_rejected=False
        try:review(**activity_only_unobserved_average_invalid)
        except ValueError as error:
            activity_only_unobserved_average_invalid_rejected=(
                'RETAINED_ACTIVITY_ORDER_UNOBSERVED_FILL_AVERAGE_INVALID'
                in str(error))
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
        fee_early=_fixture(root/'fee-early',two_pages=True,
            activity_fee_changes={'date':'2026-10-17T18:00:00.999999999Z'})
        fee_timestamp_before_fill_rejected=False
        try:review(**fee_early)
        except ValueError as error:
            fee_timestamp_before_fill_rejected=(
                'CROSS_RESPONSE_FEE_TIMESTAMP_PRECEDES_FILL' in str(error))
        fee_after_capture=_fixture(root/'fee-after-capture',two_pages=True,
            activity_fee_changes={'date':'2026-10-19T00:00:00.000000001Z'})
        fee_timestamp_after_capture_rejected=False
        try:review(**fee_after_capture)
        except ValueError as error:
            fee_timestamp_after_capture_rejected=(
                'CROSS_RESPONSE_FEE_TIMESTAMP_AFTER_CAPTURE' in str(error))
        order_after_capture=_fixture(root/'order-after-capture',
            activity_order_changes={'updated_at':'2026-10-19T00:00:00.000000001Z'})
        order_update_after_capture_rejected=False
        try:review(**order_after_capture)
        except ValueError as error:
            order_update_after_capture_rejected=(
                'CROSS_RESPONSE_ORDER_UPDATE_AFTER_CAPTURE' in str(error))
        account_order_after_capture=_fixture(root/'account-order-after-capture',
            account_order_changes={'updated_at':'2026-10-18T23:59:00.000000001Z'},
            activity_order_changes={'updated_at':'2026-10-19T00:00:00Z'},
            activity_fill_changes={'transaction_time':'2026-10-19T00:00:00Z'})
        account_order_update_after_capture_rejected=False
        try:review(**account_order_after_capture)
        except ValueError as error:
            account_order_update_after_capture_rejected=(
                'CROSS_RESPONSE_ACCOUNT_ORDER_UPDATE_AFTER_CAPTURE' in str(error))
        order_after_start=_fixture(root/'order-after-start',
            activity_response_time_offsets=(0,0,2,3),
            activity_order_changes={'updated_at':'2026-10-19T00:00:01Z'},
            activity_fill_changes={'transaction_time':'2026-10-19T00:00:00Z'})
        order_update_after_collection_start_rejected=False
        try:review(**order_after_start)
        except ValueError as error:
            order_update_after_collection_start_rejected=(
                'CROSS_RESPONSE_ORDER_UPDATE_AFTER_COLLECTION_START' in str(error))
        fill_after_capture=_fixture(root/'fill-after-capture',
            activity_order_changes={'updated_at':'2026-10-19T00:00:00Z'},
            activity_fill_changes={'transaction_time':'2026-10-19T00:00:00.000000001Z'})
        fill_timestamp_after_capture_rejected=False
        try:review(**fill_after_capture)
        except ValueError as error:
            fill_timestamp_after_capture_rejected=(
                'CROSS_RESPONSE_FILL_TIMESTAMP_AFTER_CAPTURE' in str(error))
        fill_timestamp_after_page_receipt_rejected=False
        try:_retained_activity_page_capture_scope(
            [dict(activity_id='fill-1',kind='FILL',
                occurred_at='2026-10-19T00:00:02Z')],
            [dict(received_at='1792368001',activity_ids=['fill-1'])])
        except ValueError as error:
            fill_timestamp_after_page_receipt_rejected=(
                'CROSS_RESPONSE_FILL_TIMESTAMP_AFTER_PAGE_RECEIPT' in str(error))
        fee_after_page=_fixture(root/'fee-after-page',two_pages=True,
            activity_response_time_offsets=(0,0,1,3,4),
            activity_fee_changes={'date':'2026-10-19T00:00:02Z'})
        fee_timestamp_after_page_receipt_rejected=False
        try:review(**fee_after_page)
        except ValueError as error:
            fee_timestamp_after_page_receipt_rejected=(
                'CROSS_RESPONSE_FEE_TIMESTAMP_AFTER_PAGE_RECEIPT' in str(error))
        page_boundary=_fixture(root/'page-boundary',two_pages=True,
            activity_response_time_offsets=(0,0,0,3,4),
            activity_order_changes={'updated_at':'2026-10-19T00:00:00Z'},
            activity_fill_changes={'transaction_time':'2026-10-19T00:00:00Z'},
            activity_fee_changes={'date':'2026-10-19T00:00:00Z'})
        page_boundary_result=review(**page_boundary)
        page_receipt_exact_activity_scope_bound=(
            page_boundary_result['page_receipt_bounded_fill_records']==1 and
            page_boundary_result[
                'page_receipt_bounded_fee_exact_timestamp_records']==1 and
            page_boundary_result[
                'page_receipt_temporally_unverified_fee_date_records']==99 and
            page_boundary_result[
                'retained_exact_activity_page_receipt_upper_bound_checked'] is True)
        unreferenced_fee=_fixture(root/'unreferenced-fee',two_pages=True,
            activity_fee_changes={'order_id':None,'execution_id':None,
                'date':'2026-10-19T00:00:00Z'})
        unreferenced_result=review(**unreferenced_fee)
        unreferenced_fee_capture_scope_bound=(
            unreferenced_result['retained_fee_records']==100 and
            unreferenced_result['retained_fee_timestamped_records']==1 and
            unreferenced_result['retained_fee_date_only_records']==99 and
            unreferenced_result['unreferenced_fee_records']==100 and
            unreferenced_result['unreferenced_fee_timestamped_records']==1 and
            unreferenced_result['unreferenced_fee_date_only_records']==99 and
            unreferenced_result['reported_fee_reference_links']==0 and
            unreferenced_result[
                'retained_fee_exact_timestamp_capture_upper_bound_checked'] is True)
        unreferenced_fee_after_capture=_fixture(root/'unreferenced-fee-after-capture',
            two_pages=True,activity_fee_changes={'order_id':None,'execution_id':None,
                'date':'2026-10-19T00:00:00.000000001Z'})
        unreferenced_fee_timestamp_after_capture_rejected=False
        try:review(**unreferenced_fee_after_capture)
        except ValueError as error:
            unreferenced_fee_timestamp_after_capture_rejected=(
                'CROSS_RESPONSE_FEE_TIMESTAMP_AFTER_CAPTURE' in str(error))
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
            retained_fill_limit_prices_checked=
                result['retained_fill_limit_prices_checked'],
            activity_only_order_average_limit_prices_checked=
                result['activity_only_order_average_limit_prices_checked'],
            complete_activity_only_fill_averages_checked=
                result['complete_activity_only_fill_averages_checked'],
            unobserved_activity_fill_average_limits_checked=
                result['unobserved_activity_fill_average_limits_checked'],
            retained_fill_type_remainders_checked=
                result['retained_fill_type_remainders_checked'],
            retained_activity_fill_deltas_checked=
                result['retained_activity_fill_deltas_checked'],
            retained_activity_order_fill_semantics_bound=
                result['retained_activity_order_fill_semantics_bound'],
            shared_order_fill_delta_bound=result['overlapping_order_fill_deltas']==1 and
                result['overlapping_order_fill_delta_activities']==1 and
                len(result['overlapping_order_fill_delta_sha256'])==64,
            overlapping_order_fill_activities_partitioned=
                result['overlapping_order_fill_activities_partitioned'],
            shared_order_fill_partition_bound=
                result['overlapping_order_historical_fill_activities']==0 and
                result['overlapping_order_retained_fill_activities']==1 and
                len(result['overlapping_order_fill_partition_sha256'])==64,
            repeated_cumulative_fill_state_rejected=
                repeated_cumulative_fill_state_rejected,
            cumulative_fill_time_regression_rejected=
                cumulative_fill_time_regression_rejected,
            cumulative_fill_remaining_conflict_rejected=
                cumulative_fill_remaining_conflict_rejected,
            fill_requested_quantity_disagreement_rejected=
                fill_requested_quantity_disagreement_rejected,
            activity_requested_quantity_changed_rejected=
                activity_requested_quantity_changed_rejected,
            activity_limit_price_changed_rejected=
                activity_limit_price_changed_rejected,
            fill_limit_price_disagreement_rejected=
                fill_limit_price_disagreement_rejected,
            partial_fill_without_remainder_rejected=
                partial_fill_without_remainder_rejected,
            terminal_fill_with_remainder_rejected=
                terminal_fill_with_remainder_rejected,
            activity_only_order_fill_semantic_disagreement_rejected=
                activity_only_order_fill_semantic_disagreement_rejected,
            activity_only_fill_chain_gap_rejected=
                activity_only_fill_chain_gap_rejected,
            activity_only_average_limit_disagreement_rejected=
                activity_only_average_limit_disagreement_rejected,
            activity_only_complete_fill_average_disagreement_rejected=
                activity_only_complete_fill_average_disagreement_rejected,
            activity_only_unobserved_average_limit_disagreement_rejected=
                activity_only_unobserved_average_limit_disagreement_rejected,
            activity_only_unobserved_average_invalid_rejected=
                activity_only_unobserved_average_invalid_rejected,
            filled_status_quantity_disagreement_rejected=
                filled_status_quantity_disagreement_rejected,
            completion_status_disagreement_rejected=
                completion_status_disagreement_rejected,
            reported_fee_references_consistent=result['reported_fee_references_consistent'],
            reported_fee_reference_bound=result['reported_fee_reference_links']==1 and
                len(result['reported_fee_reference_sha256'])==64,
            reported_fee_exact_timestamp_chronology_checked=
                result['reported_fee_exact_timestamp_chronology_checked'],
            reported_fee_exact_timestamp_capture_upper_bound_checked=
                result['reported_fee_exact_timestamp_capture_upper_bound_checked'],
            retained_fee_exact_timestamp_capture_upper_bound_checked=
                result['retained_fee_exact_timestamp_capture_upper_bound_checked'],
            retained_order_update_capture_upper_bound_checked=
                result['retained_order_update_capture_upper_bound_checked'],
            reported_account_order_update_capture_upper_bound_checked=
                result['reported_account_order_update_capture_upper_bound_checked'],
            retained_order_update_collection_start_upper_bound_checked=
                result['retained_order_update_collection_start_upper_bound_checked'],
            retained_fill_timestamp_capture_upper_bound_checked=
                result['retained_fill_timestamp_capture_upper_bound_checked'],
            retained_exact_activity_capture_upper_bound_checked=
                result['retained_exact_activity_capture_upper_bound_checked'],
            retained_fill_timestamp_page_receipt_upper_bound_checked=
                result['retained_fill_timestamp_page_receipt_upper_bound_checked'],
            retained_fee_exact_timestamp_page_receipt_upper_bound_checked=
                result['retained_fee_exact_timestamp_page_receipt_upper_bound_checked'],
            retained_exact_activity_page_receipt_upper_bound_checked=
                result['retained_exact_activity_page_receipt_upper_bound_checked'],
            page_receipt_exact_activity_scope_bound=
                page_receipt_exact_activity_scope_bound,
            unreferenced_fee_capture_scope_bound=unreferenced_fee_capture_scope_bound,
            reported_fee_date_only_scope_preserved=
                result['reported_fee_timestamped_links']==0 and
                result['reported_fee_date_only_links']==1,
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
            fee_timestamp_before_fill_rejected=fee_timestamp_before_fill_rejected,
            fee_timestamp_after_capture_rejected=fee_timestamp_after_capture_rejected,
            order_update_after_capture_rejected=order_update_after_capture_rejected,
            account_order_update_after_capture_rejected=
                account_order_update_after_capture_rejected,
            order_update_after_collection_start_rejected=
                order_update_after_collection_start_rejected,
            fill_timestamp_after_capture_rejected=fill_timestamp_after_capture_rejected,
            fill_timestamp_after_page_receipt_rejected=
                fill_timestamp_after_page_receipt_rejected,
            fee_timestamp_after_page_receipt_rejected=
                fee_timestamp_after_page_receipt_rejected,
            unreferenced_fee_timestamp_after_capture_rejected=
                unreferenced_fee_timestamp_after_capture_rejected,
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
