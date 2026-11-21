"""Pure counterfactual binding from reviewed local-paper evidence to one preview.

The result is deliberately and permanently unsendable.  It grants no source,
broker, account, funds, routing, ledger, live-order, money-movement, or release
authority and performs no filesystem, credential, network, or clock access.
"""
from decimal import localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from nexus import alpaca_paper_contract as contract
from nexus.durable_orders import DurableOrderGateway
from nexus.forward_reconciliation_review import (
    MODELED_OUTCOME_PROFILE, RECEIPT_PROFILE, reconciliation_sha256)
from nexus.order_accounting import modeled_fill_sha256


PROFILE='nexus.forward-broker-intent.v1'
MODE='COUNTERFACTUAL_FORWARD_BROKER_INTENT_REVIEW'
RECONCILIATION_PROFILE='nexus.forward-broker-intent-reconciliation.v1'
RECONCILIATION_MODE='READ_ONLY_FORWARD_BROKER_INTENT_RECONCILIATION'
FALSE_FLAGS=('sendable','routing_authorized','source_attested','broker_connected',
    'broker_account_reconciled','funds_reconciled','ledger_applied',
    'live_orders_enabled','money_movement_enabled','release_approved')
BINDING_FIELDS=frozenset(('profile','session','cycle','product','side','order_id',
    'event_id','sequence','event_sequence','quote_evidence_sha256',
    'modeled_fill_sha256','forward_review_receipt_sha256',
    'event_reconciliation_sha256','initial_order_snapshot_sha256',
    'final_order_snapshot_sha256','source_mode','quantity','limit_price',
    'reference_notional','asset_metadata_sha256','preview_payload_sha256',
    'counterfactual_only',*FALSE_FLAGS,'intent_sha256'))
SOURCE_MODE='PUBLIC_READER_SINGLE_SOURCE_UNATTESTED'
LIMITATIONS=('Counterfactual binding only. The modeled local fill already changed the synthetic ledger; '
    'this preview can never be submitted or applied again. Caller-supplied asset metadata is unattested. '
    'No historical execution equivalence, source, broker, account, funds, fees, holdings, routing, live, '
    'performance, profitability, sustained-trial, or release acceptance is established.')
RECONCILIATION_LIMITATIONS=('One exact stopped local forward review is compared with one exact-head retained '
    'request plan between two fresh replay-validated order-journal snapshots. The caller-owned review is validated '
    'and detached once before journal access. One exact durable gateway plus the physical order journal and linked '
    'portfolio identity are pinned across both replays. The request guard spans the final order replay and a final request reread; '
    'both order snapshots must equal the reviewed non-halted snapshot, without holding an order-journal lock that '
    'could delay an emergency halt. Equality proves only sampled sequential local '
    'provenance continuity; a later mutation remains possible and it '
    'does not attest source, provider, broker account, funds, fees, holdings, market coverage, routing, sustained '
    'operation, performance, profitability, or release acceptance. The counterfactual plan remains unsendable.')
REQUEST_SUMMARY_FIELDS=frozenset(('client_order_id','payload_sha256',
    'forward_intent_bound','forward_intent_sha256','recovery_state','snapshot',
    'lookup_by_original_client_id_required','submission_attempt_marked',
    'submission_admission','lookup','automatic_retry','sendable',
    'broker_connected','ledger_applied','broker_account_reconciled'))
LOOKUP_SUMMARY_FIELDS=frozenset(('attempts','attempt_limit','pending',
    'next_allowed_at','review_blocked','interruptions','last_outcome'))


def _sha(value):
    try:payload=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError):raise ValueError('FORWARD_BROKER_INTENT_INPUT_INVALID') from None
    return hashlib.sha256(payload.encode()).hexdigest()


def _digest(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}',value) is not None


def _plain(value):
    """Reject subclass-based equality tricks recursively."""
    if value is None or type(value) in (str,int,bool) or (
            type(value) is float and math.isfinite(value)):return
    if type(value) is list:
        for item in value:_plain(item)
        return
    if type(value) is dict:
        if any(type(key) is not str for key in value):raise ValueError
        for item in value.values():_plain(item)
        return
    raise ValueError


def _detached_review(review):
    """Validate and detach one caller-owned review before durable reads."""
    if type(review) is not dict:
        raise ValueError('FORWARD_BROKER_INTENT_REVIEW_INVALID')
    try:
        _plain(review)
        detached=json.loads(json.dumps(review,sort_keys=True,
            separators=(',',':'),allow_nan=False))
        _plain(detached)
    except (ValueError,TypeError,OverflowError,RecursionError):
        raise ValueError('FORWARD_BROKER_INTENT_REVIEW_INVALID') from None
    return detached


def _asset_digest(asset,metadata_at,evaluated_at):
    fields=('symbol','class','status','tradable','fractionable','min_order_size',
        'min_trade_increment','price_increment')
    if type(asset) is not dict or set(asset)!=set(fields):
        raise ValueError('FORWARD_BROKER_INTENT_ASSET_INVALID')
    try:_plain(asset)
    except ValueError:raise ValueError('FORWARD_BROKER_INTENT_ASSET_INVALID') from None
    return _sha(dict(asset=asset,metadata_at=contract.text(contract.number(metadata_at,zero=True)),
        evaluated_at=contract.text(contract.number(evaluated_at,zero=True))))


def _receipt(review):
    if type(review) is not dict:
        raise ValueError('FORWARD_BROKER_INTENT_REVIEW_INVALID')
    try:_plain(review)
    except ValueError:raise ValueError('FORWARD_BROKER_INTENT_REVIEW_INVALID') from None
    receipt=review.get('review_receipt');events=review.get('durable_event_evidence')
    quotes=review.get('quote_evidence');snapshot=review.get('order_journal_snapshot')
    if (type(receipt) is not dict or type(events) is not dict or type(quotes) is not dict or
            type(snapshot) is not dict or receipt.get('profile')!=RECEIPT_PROFILE or
            receipt.get('read_only') is not True or receipt.get('local_consistency_ok') is not True or
            receipt.get('reason_count')!=0 or review.get('read_only') is not True or
            review.get('stored_state')!='STOPPED' or review.get('provisional_active_session') is not False or
            review.get('local_consistency_ok') is not True or review.get('reasons')!=[] or
            review.get('live_orders_enabled') is not False or review.get('release_approved') is not False or
            review.get('strategy_validated') is not False or snapshot.get('halted') is not False or
            snapshot.get('stable_during_review') is not True or
            receipt.get('order_snapshot_stable_during_review') is not True or
            receipt.get('journal_stable_during_review') is not True or
            receipt.get('portfolio_stable_during_review') is not True or
            receipt.get('initial_order_snapshot_sha256')!=receipt.get('final_order_snapshot_sha256') or
            receipt.get('initial_order_snapshot_sha256')!=snapshot.get('snapshot_sha256') or
            receipt.get('quote_head_sha256')!=quotes.get('head_sha256') or
            receipt.get('event_head_sha256')!=events.get('head_sha256') or
            receipt.get('event_reconciliation_sha256')!=events.get('reconciliation_sha256') or
            receipt.get('cut_state_before')!='CLEAR' or receipt.get('cut_state_after')!='CLEAR' or
            receipt.get('cut_requested_before') is not False or
            receipt.get('cut_requested_after') is not False or
            receipt.get('cut_binding_source')!='NONE' or
            receipt.get('cut_evidence_stable') is not True or
            receipt.get('independent_cut_binding_verified') is not False or
            receipt.get('cut_record_sha256') is not None or
            receipt.get('halted_before') is not False or receipt.get('halted_after') is not False or
            any(not _digest(receipt.get(key)) for key in (
                'receipt_sha256','event_reconciliation_sha256',
                'initial_order_snapshot_sha256','final_order_snapshot_sha256')) or
            _sha({key:value for key,value in receipt.items() if key!='receipt_sha256'})!=
                receipt.get('receipt_sha256')):
        raise ValueError('FORWARD_BROKER_INTENT_REVIEW_INVALID')
    required_true=('integrity_ok','event_trace_complete','quote_outcome_bijection',
        'durable_order_outcomes_reconciled','durable_fill_attempts_bound')
    if any(events.get(key) is not True for key in required_true):
        raise ValueError('FORWARD_BROKER_INTENT_REVIEW_INVALID')
    cut=dict(before_status=receipt['cut_state_before'],after_status=receipt['cut_state_after'],
        requested_before=receipt['cut_requested_before'],requested_after=receipt['cut_requested_after'],
        stable=receipt['cut_evidence_stable'],
        binding_verified=receipt['independent_cut_binding_verified'],
        binding_source=receipt['cut_binding_source'],record_sha256=receipt['cut_record_sha256'])
    expected=reconciliation_sha256(session=review.get('session'),state=review.get('stored_state'),
        quote_head_sha256=quotes.get('head_sha256'),event_head_sha256=events.get('head_sha256'),
        order_snapshot_sha256=snapshot.get('snapshot_sha256'),
        final_order_snapshot_sha256=receipt.get('final_order_snapshot_sha256'),
        cut_evidence=cut,reasons=review.get('reasons'),summary=events)
    if expected!=events.get('reconciliation_sha256'):
        raise ValueError('FORWARD_BROKER_INTENT_REVIEW_INVALID')
    return receipt,events


def _source(review,events):
    source=events.get('latest_modeled_outcome')
    if (type(source) is not dict or source.get('profile')!=MODELED_OUTCOME_PROFILE or
            source.get('session')!=review.get('session') or
            not _digest(events.get('latest_modeled_outcome_sha256')) or
            _sha(source)!=events.get('latest_modeled_outcome_sha256')):
        raise ValueError('FORWARD_BROKER_INTENT_SOURCE_INVALID')
    expected=frozenset(('profile','session','cycle','product','side','order_id',
        'event_id','sequence','event_sequence','quote_evidence_sha256',
        'modeled_fill','modeled_fill_sha256'))
    fill=source.get('modeled_fill')
    if (frozenset(source)!=expected or type(fill) is not dict or
            source.get('product')!='BTC-USD' or source.get('side') not in ('BUY','SELL') or
            type(source.get('cycle')) is not int or source['cycle']<1 or
            type(source.get('event_sequence')) is not int or source['event_sequence']<1 or
            not _digest(source.get('quote_evidence_sha256')) or
            not _digest(source.get('modeled_fill_sha256'))):
        raise ValueError('FORWARD_BROKER_INTENT_SOURCE_INVALID')
    try:digest=modeled_fill_sha256(fill)
    except ValueError:raise ValueError('FORWARD_BROKER_INTENT_SOURCE_INVALID') from None
    if (digest!=source['modeled_fill_sha256'] or fill.get('order_id')!=source.get('order_id') or
            fill.get('event_id')!=source.get('event_id') or fill.get('sequence')!=source.get('sequence') or
            fill.get('side')!=source.get('side') or fill.get('evidence_cycle')!=source.get('cycle') or
            fill.get('evidence_session')!=source.get('session') or
            fill.get('evidence_quote_sha256')!=source.get('quote_evidence_sha256')):
        raise ValueError('FORWARD_BROKER_INTENT_SOURCE_INVALID')
    return source,fill


def build(review,client_order_id,limit_price,asset,*,metadata_at,evaluated_at):
    """Return one deterministic unsendable plan from a stopped clean review."""
    receipt,events=_receipt(review);source,fill=_source(review,events)
    asset_sha256=_asset_digest(asset,metadata_at,evaluated_at)
    preview=contract.preview(client_order_id,source['product'],source['side'],
        fill['quantity'],limit_price,asset,metadata_at=metadata_at,evaluated_at=evaluated_at)
    payload=preview['payload']
    quantity=contract.number(payload['qty']);price=contract.number(payload['limit_price'])
    with localcontext() as context:
        context.prec=80;notional=quantity*price
    if (payload['qty']!=fill['quantity'] or payload['side']!=source['side'].lower() or
            payload['symbol']!='BTC/USD' or preview.get('payload_sha256')!=contract.sha(payload) or
            preview.get('sendable') is not False or preview.get('broker_connected') is not False or
            preview.get('live_orders_enabled') is not False or preview.get('ledger_applied') is not False or
            preview.get('reference_notional')!=contract.text(notional) or notional>200):
        raise ValueError('FORWARD_BROKER_INTENT_PREVIEW_INVALID')
    binding=dict(profile=PROFILE,session=source['session'],cycle=source['cycle'],
        product=source['product'],side=source['side'],order_id=source['order_id'],
        event_id=source['event_id'],sequence=source['sequence'],
        event_sequence=source['event_sequence'],
        quote_evidence_sha256=source['quote_evidence_sha256'],
        modeled_fill_sha256=source['modeled_fill_sha256'],
        forward_review_receipt_sha256=receipt['receipt_sha256'],
        event_reconciliation_sha256=events['reconciliation_sha256'],
        initial_order_snapshot_sha256=receipt['initial_order_snapshot_sha256'],
        final_order_snapshot_sha256=receipt['final_order_snapshot_sha256'],
        source_mode=SOURCE_MODE,quantity=payload['qty'],limit_price=payload['limit_price'],
        reference_notional=preview['reference_notional'],asset_metadata_sha256=asset_sha256,
        preview_payload_sha256=preview['payload_sha256'],counterfactual_only=True,
        **{key:False for key in FALSE_FLAGS})
    binding['intent_sha256']=_sha(binding)
    plan=dict(mode='DISCONNECTED_ALPACA_PAPER_PREVIEW',sendable=False,payload=payload,
        payload_sha256=preview['payload_sha256'],forward_intent_binding=binding)
    return dict(mode=MODE,profile=PROFILE,read_only=True,plan=plan,binding=binding,
        limitations=LIMITATIONS,**{key:False for key in FALSE_FLAGS})


def normalize_plan(value):
    """Validate and copy the exact compact journal representation."""
    if (type(value) is not dict or set(value)!={'mode','sendable','payload',
            'payload_sha256','forward_intent_binding'} or
            value.get('mode')!='DISCONNECTED_ALPACA_PAPER_PREVIEW' or
            value.get('sendable') is not False):
        raise ValueError('INVALID_FORWARD_BROKER_INTENT_PLAN')
    try:_plain(value)
    except ValueError:raise ValueError('INVALID_FORWARD_BROKER_INTENT_PLAN') from None
    binding=value.get('forward_intent_binding');payload=value.get('payload')
    try:tracker=contract.SnapshotTracker(dict(mode=value['mode'],sendable=False,
        payload=payload,payload_sha256=value['payload_sha256']))
    except (ValueError,TypeError):raise ValueError('INVALID_FORWARD_BROKER_INTENT_PLAN') from None
    if (type(binding) is not dict or frozenset(binding)!=BINDING_FIELDS or
            binding.get('profile')!=PROFILE or binding.get('counterfactual_only') is not True or
            any(binding.get(key) is not False for key in FALSE_FLAGS) or
            binding.get('source_mode')!=SOURCE_MODE or
            binding.get('preview_payload_sha256')!=value.get('payload_sha256') or
            binding.get('quantity')!=payload.get('qty') or
            binding.get('limit_price')!=payload.get('limit_price') or
            binding.get('side','').lower()!=payload.get('side') or
            binding.get('product')!='BTC-USD' or payload.get('symbol')!='BTC/USD' or
            _sha({key:item for key,item in binding.items() if key!='intent_sha256'})!=
                binding.get('intent_sha256') or
            any(not _digest(binding.get(key)) for key in (
                'quote_evidence_sha256','modeled_fill_sha256',
                'forward_review_receipt_sha256','event_reconciliation_sha256',
                'initial_order_snapshot_sha256','final_order_snapshot_sha256',
                'asset_metadata_sha256','preview_payload_sha256','intent_sha256')) or
            binding['initial_order_snapshot_sha256']!=binding['final_order_snapshot_sha256']):
        raise ValueError('INVALID_FORWARD_BROKER_INTENT_PLAN')
    quantity=contract.number(payload['qty']);price=contract.number(payload['limit_price'])
    with localcontext() as context:context.prec=80;notional=quantity*price
    if notional>200 or binding.get('reference_notional')!=contract.text(notional):
        raise ValueError('INVALID_FORWARD_BROKER_INTENT_PLAN')
    return dict(mode=value['mode'],sendable=False,payload=dict(tracker._payload),
        payload_sha256=value['payload_sha256'],forward_intent_binding=dict(binding))


def reconcile_current(review,request_snapshot,*,expected_account_sha256,
                      expected_head):
    """Purely compare one retained plan with a fresh stopped local review."""
    receipt,events=_receipt(review);source,fill=_source(review,events)
    if (type(request_snapshot) is not dict or
            set(request_snapshot)!={'account_sha256','head_sha256','plan','request'}):
        raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_INVALID')
    try:_plain(request_snapshot)
    except ValueError:
        raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_INVALID') from None
    account=request_snapshot.get('account_sha256');head=request_snapshot.get('head_sha256')
    request=request_snapshot.get('request')
    if (not _digest(account) or not _digest(head) or type(request) is not dict or
            frozenset(request)!=REQUEST_SUMMARY_FIELDS or
            account!=expected_account_sha256 or head!=expected_head):
        raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_INVALID')
    lookup=request.get('lookup')
    if (type(lookup) is not dict or frozenset(lookup)!=LOOKUP_SUMMARY_FIELDS or
            type(lookup.get('attempt_limit')) is not int or lookup['attempt_limit']<1 or
            lookup.get('attempts')!=0 or lookup.get('pending') is not False or
            lookup.get('next_allowed_at')!=0 or lookup.get('review_blocked') is not False or
            lookup.get('interruptions')!=0 or lookup.get('last_outcome') is not None):
        raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_INVALID')
    try:plan=normalize_plan(request_snapshot.get('plan'))
    except (ValueError,TypeError,KeyError):
        raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_INVALID') from None
    binding=plan['forward_intent_binding'];payload=plan['payload']
    if (request.get('client_order_id')!=payload.get('client_order_id') or
            request.get('payload_sha256')!=plan['payload_sha256'] or
            request.get('forward_intent_bound') is not True or
            request.get('forward_intent_sha256')!=binding['intent_sha256'] or
            request.get('recovery_state')!='PREPARED_UNSENT_DIAGNOSTIC' or
            request.get('snapshot') is not None or
            request.get('lookup_by_original_client_id_required') is not False or
            request.get('submission_attempt_marked') is not False or
            request.get('submission_admission') is not None or
            request.get('automatic_retry') is not False or
            request.get('sendable') is not False or
            request.get('broker_connected') is not False or
            request.get('ledger_applied') is not False or
            request.get('broker_account_reconciled') is not False):
        raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_INVALID')
    expected=dict(session=source['session'],cycle=source['cycle'],
        product=source['product'],side=source['side'],order_id=source['order_id'],
        event_id=source['event_id'],sequence=source['sequence'],
        event_sequence=source['event_sequence'],
        quote_evidence_sha256=source['quote_evidence_sha256'],
        modeled_fill_sha256=source['modeled_fill_sha256'],
        forward_review_receipt_sha256=receipt['receipt_sha256'],
        event_reconciliation_sha256=events['reconciliation_sha256'],
        initial_order_snapshot_sha256=receipt['initial_order_snapshot_sha256'],
        final_order_snapshot_sha256=receipt['final_order_snapshot_sha256'],
        quantity=fill['quantity'])
    if (any(binding.get(key)!=value for key,value in expected.items()) or
            payload.get('qty')!=fill['quantity']):
        raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_INVALID')
    seal=_sha(dict(account_sha256=account,request_head_sha256=head,
        plan=plan,forward_review_receipt_sha256=receipt['receipt_sha256'],
        latest_modeled_outcome_sha256=events['latest_modeled_outcome_sha256']))
    return dict(mode=RECONCILIATION_MODE,profile=RECONCILIATION_PROFILE,
        read_only=True,matched_local_snapshot=True,prepared_unsent=True,
        account_scope_bound=True,request_head_bound=True,plan_normalized=True,
        forward_source_revalidated=True,receipt_bound=True,
        event_reconciliation_bound=True,order_snapshots_bound=True,
        modeled_fill_bound=True,payload_bound=True,
        intent_sha256=binding['intent_sha256'],payload_sha256=plan['payload_sha256'],
        intent_reconciliation_sha256=seal,
        **{key:False for key in FALSE_FLAGS},limitations=RECONCILIATION_LIMITATIONS)


def _replayed_order_snapshot(review,order_gateway):
    """Require a fresh durable replay of the exact reviewed order snapshot."""
    snapshot=review.get('order_journal_snapshot') if type(review) is dict else None
    if type(snapshot) is not dict:
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SNAPSHOT_INVALID')
    try:
        _plain(snapshot)
        if type(order_gateway) is not DurableOrderGateway:
            raise ValueError('FORWARD_BROKER_INTENT_ORDER_GATEWAY_INVALID')
        current=DurableOrderGateway.session_orders_snapshot(
            order_gateway,review.get('session'))
        _plain(current)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SNAPSHOT_INVALID') from None
    fields=('journal_present','commands','portfolio_linked','halted',
        'portfolio_rows','snapshot_sha256')
    if (type(current) is not dict or current.get('read_only') is not True or
            current.get('live_orders_enabled') is not False or
            current.get('journal_present') is not True or
            type(current.get('commands')) is not int or current['commands']<1 or
            current.get('halted') is not False or
            not _digest(current.get('journal_sha256')) or
            not _digest(current.get('snapshot_sha256')) or
            any(current.get(key)!=snapshot.get(key) for key in fields)):
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SNAPSHOT_STALE')
    return current


def _order_file_identity(path):
    """Capture one canonical single-link regular-file identity."""
    try:
        path=Path(path);observed=path.lstat();resolved=path.resolve(strict=True)
        after=path.lstat()
    except (OSError,RuntimeError,TypeError,ValueError):
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SOURCE_CHANGED') from None
    if (stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode) or
            observed.st_nlink!=1 or stat.S_ISLNK(after.st_mode) or
            not stat.S_ISREG(after.st_mode) or after.st_nlink!=1 or
            not os.path.samestat(observed,after)):
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SOURCE_CHANGED')
    return str(resolved),observed.st_dev,observed.st_ino


def _order_source_binding(review,order_gateway):
    """Bind both replays to one real gateway and physical local source."""
    if type(order_gateway) is not DurableOrderGateway:
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_GATEWAY_INVALID')
    try:path=DurableOrderGateway._path(order_gateway)
    except (OSError,RuntimeError,TypeError,ValueError):
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SOURCE_CHANGED') from None
    order_identity=_order_file_identity(path)
    reviewed=review.get('order_journal_snapshot')
    portfolio_identity=None
    if type(reviewed) is dict and reviewed.get('portfolio_linked') is True:
        portfolio_identity=_order_file_identity(
            Path(order_identity[0]).parent/'paper_portfolio.sqlite3')
    return order_identity,portfolio_identity


def _require_order_source(order_gateway,binding):
    if type(order_gateway) is not DurableOrderGateway:
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_GATEWAY_INVALID')
    order_identity,portfolio_identity=binding
    try:path=DurableOrderGateway._path(order_gateway)
    except (OSError,RuntimeError,TypeError,ValueError):
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SOURCE_CHANGED') from None
    if _order_file_identity(path)!=order_identity:
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SOURCE_CHANGED')
    if (portfolio_identity is not None and _order_file_identity(
            Path(order_identity[0]).parent/'paper_portfolio.sqlite3')!=
            portfolio_identity):
        raise ValueError('FORWARD_BROKER_INTENT_ORDER_SOURCE_CHANGED')


def reconcile_persisted(review,journal,client_order_id,*,expected_head,
                        order_gateway):
    """Reconcile both journals while keeping the request head guarded."""
    frozen_review=_detached_review(review)
    order_source=_order_source_binding(frozen_review,order_gateway)
    _require_order_source(order_gateway,order_source)
    order_before=_replayed_order_snapshot(frozen_review,order_gateway)
    _require_order_source(order_gateway,order_source)
    with journal.locked_request_plan_snapshot(client_order_id,
            expected_head=expected_head) as read:
        first=read();result=reconcile_current(frozen_review,first,
            expected_account_sha256=journal.account_digest,
            expected_head=expected_head)
        if read()!=first:
            raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_CHANGED')
        # Keep the exact request head and file identity guarded while the
        # independent order journal is replayed a second time.  No order lock
        # is retained, so the emergency halt remains independently writable.
        _require_order_source(order_gateway,order_source)
        order_after=_replayed_order_snapshot(frozen_review,order_gateway)
        _require_order_source(order_gateway,order_source)
        if order_after!=order_before:
            raise ValueError('FORWARD_BROKER_INTENT_ORDER_SNAPSHOT_CHANGED')
        if read()!=first:
            raise ValueError('FORWARD_BROKER_INTENT_RECONCILIATION_CHANGED')
    return dict(result,review_input_detached=True,order_gateway_exact=True,
        order_journal_identity_rechecked=True,
        request_snapshot_replayed=True,
        journal_file_fingerprint_rechecked=True,
        request_guard_spans_final_order_replay=True,
        final_request_snapshot_replayed=True,
        order_snapshot_replayed=True,
        order_snapshot_stable_during_reconciliation=True,
        durable_halt_clear=True)
