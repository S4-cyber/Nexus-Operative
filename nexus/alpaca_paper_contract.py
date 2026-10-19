"""Disconnected Alpaca paper contract study. No network, credentials or ledger writes.

Only quantity-based IOC limit previews are admitted. Broker observations remain
unattested diagnostics, never existing synthetic fills or reconciled holdings.
"""
import copy
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation,localcontext
import hashlib
import json
import re
from uuid import UUID

PAPER_ORDERS_URL='https://paper-api.alpaca.markets/v2/orders'
PRODUCTS={'BTC-USD':'BTC/USD'}
TERMINAL=frozenset(('filled','canceled','expired','rejected'))
STATUSES=TERMINAL|frozenset(('accepted','pending_new','new','partially_filled','pending_cancel'))
MAX_SNAPSHOTS=256
SOURCES=('https://docs.alpaca.markets/us/docs/crypto-trading',
         'https://docs.alpaca.markets/us/docs/orders-at-alpaca',
         'https://docs.alpaca.markets/us/reference/postorder',
         'https://docs.alpaca.markets/us/reference/getorderbyclientorderid')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def number(value,zero=False):
    if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)) or isinstance(value,str) and len(value)>64:raise ValueError('INVALID_BROKER_CONTRACT_NUMBER')
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('INVALID_BROKER_CONTRACT_NUMBER')
    if not result.is_finite() or result<0 or result>Decimal('1e12') or (not zero and result==0) or (result!=0 and result<Decimal('1e-18')) or result.as_tuple().exponent < -18:
        raise ValueError('INVALID_BROKER_CONTRACT_NUMBER')
    return result


def text(value):
    if value==0:return '0'
    result=format(value,'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def timestamp(value):
    if not isinstance(value,str) or len(value)>64:raise ValueError('INVALID_BROKER_UPDATE_TIME')
    try:stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('INVALID_BROKER_UPDATE_TIME')
    if stamp.tzinfo is None:raise ValueError('INVALID_BROKER_UPDATE_TIME')
    return stamp


def timestamp_order_ns(value):
    """Exact RFC3339 nanosecond ordering; legacy <=microsecond ISO stays readable.

    This is supplied event ordering, never clock synchronization or latency proof.
    Raw source spelling remains unchanged in normalized snapshots and journals.
    """
    stamp=timestamp(value)
    match=re.fullmatch(r'([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})',value)
    fraction=None
    if match:
        stamp=timestamp(match[1]+match[3]);fraction=int((match[2] or '').ljust(9,'0'))
    elif re.search(r'[.,][0-9]{7,}',value):
        raise ValueError('BROKER_UPDATE_TIME_PRECISION_UNSUPPORTED')
    try:delta=stamp.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    except (ValueError,OverflowError):raise ValueError('BROKER_UPDATE_TIME_PRECISION_UNSUPPORTED') from None
    return (delta.days*86400+delta.seconds)*1_000_000_000+(delta.microseconds*1000 if fraction is None else fraction)


def preview(client_order_id,product,side,quantity,limit_price,asset,*,metadata_at,evaluated_at):
    if not isinstance(client_order_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client_order_id):raise ValueError('INVALID_BROKER_CLIENT_ID')
    if not isinstance(product,str) or product not in PRODUCTS or not isinstance(side,str) or side not in ('BUY','SELL'):raise ValueError('BROKER_CONTRACT_SCOPE_NOT_ADMITTED')
    if not isinstance(asset,dict) or asset.get('symbol')!=PRODUCTS[product] or asset.get('class')!='crypto' or asset.get('status')!='active' or asset.get('tradable') is not True or asset.get('fractionable') is not True:
        raise ValueError('BROKER_ASSET_METADATA_NOT_ELIGIBLE')
    if not 0<=number(evaluated_at)-number(metadata_at)<=60:raise ValueError('BROKER_ASSET_METADATA_STALE_OR_FUTURE')
    qty=number(quantity);price=number(limit_price)
    minimum=number(asset.get('min_order_size'));step=number(asset.get('min_trade_increment'));tick=number(asset.get('price_increment'))
    with localcontext() as ctx:
        ctx.prec=80
        if qty<minimum or qty%step:raise ValueError('BROKER_QUANTITY_INCREMENT_OR_MINIMUM')
        if price%tick:raise ValueError('BROKER_PRICE_INCREMENT')
        notional=qty*price
        if notional>200:raise ValueError('BROKER_PREVIEW_LOCAL_NOTIONAL_CAP')
    payload=dict(client_order_id=client_order_id,symbol=PRODUCTS[product],side=side.lower(),qty=text(qty),
                 type='limit',time_in_force='ioc',limit_price=text(price))
    return dict(mode='DISCONNECTED_ALPACA_PAPER_PREVIEW',payload=payload,payload_sha256=sha(payload),
                hypothetical_method='POST',hypothetical_url=PAPER_ORDERS_URL,sendable=False,
                broker_connected=False,live_orders_enabled=False,ledger_applied=False,
                reference_notional=text(notional),metadata_scope='CALLER_SUPPLIED_UNATTESTED',
                blockers=['EXPLICIT_PAPER_CONNECTION_AUTHORIZATION_REQUIRED','ACCOUNT_AND_ASSET_ELIGIBILITY_NOT_ATTESTED',
                          'AUTHENTICATED_BROKER_ID_RECOVERY_REQUIRED','FEE_ASSET_AND_POSITION_RECONCILIATION_REQUIRED'],
                limitations='Preview only. Metadata increments are caller supplied; no rounding. The local $200 quantity-times-limit screen is not a market-fill notional guarantee. No broker account, balance, entitlement, fee or fill is verified.')


def normalize(response,payload):
    if not isinstance(response,dict):raise ValueError('INVALID_BROKER_ORDER_SNAPSHOT')
    try:broker_id=str(UUID(response['id']))
    except (ValueError,KeyError,TypeError,AttributeError):raise ValueError('INVALID_BROKER_ORDER_ID')
    for field in ('client_order_id','side','type','time_in_force'):
        if response.get(field)!=payload[field]:raise ValueError('BROKER_ORDER_IDENTITY_DISAGREEMENT')
    symbols={payload['symbol'],payload['symbol'].replace('/','')}
    if not isinstance(response.get('symbol'),str) or response.get('symbol') not in symbols or response.get('asset_class')!='crypto':raise ValueError('BROKER_ORDER_IDENTITY_DISAGREEMENT')
    if number(response.get('qty'))!=number(payload['qty']) or number(response.get('limit_price'))!=number(payload['limit_price']):
        raise ValueError('BROKER_ORDER_TERMS_DISAGREEMENT')
    status=response.get('status')
    if not isinstance(status,str) or status not in STATUSES:raise ValueError('BROKER_ORDER_STATE_REQUIRES_REVIEW')
    qty=number(response.get('filled_qty'),zero=True);requested=number(payload['qty'])
    if qty>requested:raise ValueError('BROKER_CUMULATIVE_OVERFILL')
    average=response.get('filled_avg_price')
    if qty:
        average=number(average)
        if (payload['side']=='buy' and average>number(payload['limit_price'])) or (payload['side']=='sell' and average<number(payload['limit_price'])):
            raise ValueError('BROKER_FILL_LIMIT_DISAGREEMENT')
    else:
        if average is not None and number(average,zero=True)!=0:raise ValueError('BROKER_AVERAGE_WITHOUT_FILL')
        average=None
    if status=='filled' and qty!=requested or status=='partially_filled' and not 0<qty<requested or status in ('accepted','pending_new','new','rejected') and qty:
        raise ValueError('BROKER_STATUS_QUANTITY_DISAGREEMENT')
    updated=response.get('updated_at');timestamp_order_ns(updated)
    return dict(broker_order_id=broker_id,client_order_id=payload['client_order_id'],symbol=payload['symbol'],
                side=payload['side'],status=status,filled_quantity=text(qty),average_fill_price=text(average) if average is not None else None,
                updated_at=updated,terminal=status in TERMINAL,cancellation_confirmed=status=='canceled',
                fee_reconciled=False,position_reconciled=False,ledger_applied=False)


class SnapshotTracker:
    """Bounded in-memory fixture tracker; no durable broker recovery authority."""
    def __init__(self,plan):
        if not isinstance(plan,dict) or plan.get('mode')!='DISCONNECTED_ALPACA_PAPER_PREVIEW' or plan.get('sendable') is not False or plan.get('payload_sha256')!=sha(plan.get('payload')):
            raise ValueError('INVALID_BROKER_PREVIEW')
        payload=plan.get('payload')
        if not isinstance(payload,dict) or set(payload)!={'client_order_id','symbol','side','qty','type','time_in_force','limit_price'} or payload.get('symbol') not in PRODUCTS.values() or payload.get('side') not in ('buy','sell') or payload.get('type')!='limit' or payload.get('time_in_force')!='ioc':
            raise ValueError('INVALID_BROKER_PREVIEW')
        self._payload=copy.deepcopy(payload);self._latest=None;self._seen=set()

    def observe(self,response):
        current=normalize(response,self._payload);digest=sha(current)
        # Only the current observation can replay. An older seen event must not
        # appear to restore an earlier authoritative quantity or order status.
        if self._latest and current==self._latest:return self._result(current,Decimal(0),True)
        previous=self._latest
        if previous:
            if current['broker_order_id']!=previous['broker_order_id']:raise ValueError('BROKER_ASSIGNED_ID_CHANGED')
            before=timestamp_order_ns(previous['updated_at']);after=timestamp_order_ns(current['updated_at'])
            if after<before:raise ValueError('BROKER_UPDATE_TIME_REGRESSION')
            if after==before:raise ValueError('BROKER_UPDATE_TIME_CONFLICT')
            old=number(previous['filled_quantity'],zero=True);new=number(current['filled_quantity'],zero=True)
            if new<old:raise ValueError('BROKER_CUMULATIVE_FILL_REGRESSION')
            if new==old and current['average_fill_price']!=previous['average_fill_price']:raise ValueError('BROKER_FILL_CORRECTION_REQUIRES_REVIEW')
            if previous['terminal'] and (current['status']!=previous['status'] or new!=old):raise ValueError('BROKER_TERMINAL_STATE_CHANGED')
            active_transitions={
                'accepted':STATUSES,
                'pending_new':STATUSES-{'accepted'},
                'new':STATUSES-{'accepted','pending_new'},
                'partially_filled':{'partially_filled','pending_cancel','filled','canceled','expired'},
                'pending_cancel':{'pending_cancel','partially_filled','filled','canceled','expired'},
            }
            if not previous['terminal'] and current['status'] not in active_transitions[previous['status']]:
                raise ValueError('BROKER_STATUS_TRANSITION_REQUIRES_REVIEW')
        if len(self._seen)>=MAX_SNAPSHOTS:raise ValueError('BROKER_SNAPSHOT_RETENTION_CAP')
        delta=number(current['filled_quantity'],zero=True)-(number(previous['filled_quantity'],zero=True) if previous else Decimal(0))
        self._latest=copy.deepcopy(current);self._seen.add(digest)
        return self._result(current,delta,False)

    def _result(self,current,delta,duplicate):
        return dict(snapshot=copy.deepcopy(current),duplicate=duplicate,observed_quantity_increase=text(delta),
                    observation_digests_retained=len(self._seen),observation_digest_limit=MAX_SNAPSHOTS,
                    broker_connected=False,ledger_applied=False,live_orders_enabled=False,
                    scope='Cumulative quantity diagnostics only; average price is not an incremental fill price. Fees may be in the received asset; no holdings or cash are credited.')

    def status(self):return dict(latest=copy.deepcopy(self._latest),observation_digests_retained=len(self._seen),automatic_submission=False,automatic_resume=False)


def catalogue():
    return dict(mode='DISCONNECTED_ALPACA_PAPER_CONTRACT',read_only=True,broker_connected=False,
                external_submission_supported=False,live_orders_enabled=False,products=list(PRODUCTS),
                request_scope='Quantity-based IOC limit previews only; no credentials, network, account mutation or strategy routing.',
                observation_scope='Latest allowlisted snapshot and bounded historical digests only; identity/status/cumulative-quantity diagnostics, not durable broker recovery or fee/position reconciliation.',
                unknown_submission_action='Look up the original client order ID and reconcile before considering a retry; never infer no fill from a timeout.',
                pending_cancel_is_terminal=False,fee_reconciled=False,position_reconciled=False,
                durable_request_journal_scope='Bounded replay-validated request journal and explicitly authorized original-ID GET recovery coordinator exist with retained budgets/cooldowns, post-lock clock/permission guards, saved-key fencing and interrupted-ticket review; injected validation only, no automatic retry, account reconciliation or routing permission.',
                fee_projection_scope='Disconnected explicit-execution/received-asset-fee projection and chronological fee-reserved funding audit available; account and spendable funds remain unverified.',
                activity_normalization_scope='Bounded supplied Trading REST BTC/USD FILL/CFEE/FEE normalization with semantic deduplication and explicit fill-chain checks; fee debits retain unverified references/dates, never inferred links, timestamps, zero-fee coverage or account authority.',
                activity_collection_scope='Internal explicitly confirmed paper GET collection for a closed creation window up to one day, at most two 100-row pages plus stable account reads; no startup/UI route, persistence, verified coverage, automatic continuation or routing authority. Opaque cursors preserve source spelling.',
                account_inspection_scope='Explicit Windows-only local credential save/remove/status and separately confirmed fixed-host GET-only paper inspection controls exist; injected tests only, no authenticated acceptance, atomic snapshot, spendable-funds authority or order adapter.',
                documentation_verified_at='2026-10-17',sources=list(SOURCES))


TIME_CHECKS=('nanosecond_updates_ordered','regression_retains_latest','equal_instant_conflicts','raw_spelling_retained')
TIME_FLAGS=('source_attested','clock_synchronized','execution_latency_verified','broker_connected',
            'ready_for_routing','ledger_applied','live_orders_enabled','money_movement_enabled','release_approved')


def timestamp_fixture_report():
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
        min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    request=preview('nx_time_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    first=dict(**request['payload'],id='01234567-89ab-cdef-0123-456789abcdef',asset_class='crypto',
        status='new',filled_qty='0',filled_avg_price=None,updated_at='2026-10-17T18:00:00.123456788Z')
    second=dict(first,status='partially_filled',filled_qty='1',filled_avg_price='99',updated_at='2026-10-17T18:00:00.123456789Z')
    tracker=SnapshotTracker(request);tracker.observe(first);result=tracker.observe(second);before=tracker.status()
    regression=conflict=False
    try:tracker.observe(first)
    except ValueError as error:regression=str(error)=='BROKER_UPDATE_TIME_REGRESSION' and tracker.status()==before
    try:tracker.observe(dict(second,status='pending_cancel',updated_at='2026-10-17T11:00:00.123456789-07:00'))
    except ValueError as error:conflict=str(error)=='BROKER_UPDATE_TIME_CONFLICT' and tracker.status()==before
    checks=dict(nanosecond_updates_ordered=result['observed_quantity_increase']=='1' and timestamp_order_ns(second['updated_at'])-timestamp_order_ns(first['updated_at'])==1,
        regression_retains_latest=regression,equal_instant_conflicts=conflict,
        raw_spelling_retained=result['snapshot']['updated_at']==second['updated_at'] and tracker.observe(second)['duplicate'])
    return dict(source='SUPPLIED_EXACT_SNAPSHOT_TIME_FIXTURE',passed=all(checks.values()),**checks,**{k:False for k in TIME_FLAGS})


def fixture_report():
    """Repeatable in-memory partial/cancel exercise, no application state access."""
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
               min_order_size='0.5',min_trade_increment='0.5',price_increment='0.5')
    request=preview('nx_contract_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    tracker=SnapshotTracker(request);results=[]
    for second,status,qty,average in ((0,'new','0',None),(1,'partially_filled','1','99'),(2,'pending_cancel','1','99'),(3,'canceled','1','99')):
        raw=dict(id='01234567-89ab-cdef-0123-456789abcdef',client_order_id='nx_contract_fixture',symbol='BTC/USD',
                 asset_class='crypto',side='buy',type='limit',time_in_force='ioc',qty='2',limit_price='100',
                 status=status,filled_qty=qty,filled_avg_price=average,updated_at=f'2026-10-17T18:00:0{second}+00:00')
        results.append(tracker.observe(raw))
    replay=tracker.observe(raw)
    return dict(mode='DISCONNECTED_BROKER_CONTRACT_FIXTURE',passed=True,steps=results,current_replay_duplicate=replay['duplicate'],
                preview_sha256=request['payload_sha256'],retained_quantity='1',ledger_applied=False,
                broker_connected=False,live_orders_enabled=False,fee_reconciled=False,position_reconciled=False,
                limitations='Injected in-memory observations, not broker acceptance, durable recovery, execution pacing or an account reconciliation.')


if __name__=='__main__':print(json.dumps(fixture_report(),sort_keys=True,indent=2))
