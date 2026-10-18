"""Original offline execution-quality arithmetic for supplied BTC/USD evidence.

No proprietary implementation, transport, account lookup, files or routing.
Positive signed shortfall means worse than the supplied decision benchmark.
Integer nanoseconds preserve input precision, not verified clock accuracy or
nanosecond execution. Keep reports containing real amounts outside Git.
"""
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import re

POLICY='nexus.supplied-execution-quality.v1'
MAX_FILLS=2000
FLAGS=('source_attested','clock_domains_verified','actual_fee_coverage_verified',
    'market_impact_identified','queue_position_modeled','counterfactual_fill_verified',
    'profitability_verified','broker_latency_measured','ready_for_routing',
    'paper_orders_enabled','live_orders_enabled','money_movement_enabled','release_approved')
CHECKS=('buy_sell_signs_checked','decomposition_checked','partial_opportunity_separate',
    'missing_fees_remain_unknown','duplicates_not_double_counted','conflicting_duplicates_rejected',
    'nanosecond_ordering_checked','input_order_invariant','no_fill_not_promoted')


def _fail(code):raise ValueError('EXECUTION_QUALITY_'+code)


def _amount(value,*,zero=False):
    if type(value) is not str or re.fullmatch(r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,18})?',value) is None:
        _fail('AMOUNT')
    number=Decimal(value)
    if number<0 or (not zero and not number):_fail('AMOUNT')
    return number


def _ns(value):
    if type(value) is not int or not 0<=value<=2**63-1:_fail('TIMESTAMP')
    return value


def _text(value):
    if not value:return '0'
    result=format(value,'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def review(*,product,side,requested_quantity,decision_mid,decision_at_ns,
           arrival_bid,arrival_ask,arrival_at_ns,end_at_ns,fills,unfilled_mark=None):
    """Decompose one supplied order's benchmark shortfall; never infer P/L.

    All prices/quantities/fees are bounded decimal strings; fees are USD debit
    assumptions or None, never inferred from missing rows. Rebates/asset fees,
    order status, source coverage and actual fill eligibility are unsupported.
    Unfilled opportunity cost uses only an explicitly supplied end mark.
    """
    if product!='BTC-USD' or side not in ('BUY','SELL'):_fail('SCOPE')
    quantity=_amount(requested_quantity);benchmark=_amount(decision_mid)
    bid=_amount(arrival_bid);ask=_amount(arrival_ask)
    decision=_ns(decision_at_ns);arrival=_ns(arrival_at_ns);end=_ns(end_at_ns)
    if not decision<=arrival<=end:_fail('TIME_ORDER')
    if bid>ask:_fail('CROSSED_QUOTE')
    mark=None if unfilled_mark is None else _amount(unfilled_mark)
    if type(fills) is not list or len(fills)>MAX_FILLS:_fail('FILL_CAP')
    normalized={};duplicates=0
    for raw in fills:
        if type(raw) is not dict or set(raw)!={'execution_id','quantity','price','filled_at_ns','fee_usd'}:_fail('FILL_SHAPE')
        identity=raw['execution_id']
        if type(identity) is not str or re.fullmatch(r'[A-Za-z0-9_:-]{1,128}',identity) is None:_fail('IDENTITY')
        filled=_ns(raw['filled_at_ns'])
        if not arrival<=filled<=end:_fail('FILL_TIME_ORDER')
        row=dict(execution_id=identity,quantity=_text(_amount(raw['quantity'])),price=_text(_amount(raw['price'])),
            filled_at_ns=filled,fee_usd=None if raw['fee_usd'] is None else _text(_amount(raw['fee_usd'],zero=True)))
        if identity in normalized:
            if normalized[identity]!=row:_fail('DUPLICATE_CONFLICT')
            duplicates+=1
        else:normalized[identity]=row
    rows=sorted(normalized.values(),key=lambda r:(r['filled_at_ns'],r['execution_id']))
    with localcontext() as context:
        # Bounds above need <70 significant digits for finite sums/products.
        context.prec=96
        context.rounding=ROUND_HALF_EVEN
        filled=sum((Decimal(r['quantity']) for r in rows),Decimal(0))
        if filled>quantity:_fail('OVERFILL')
        unfilled=quantity-filled;direction=Decimal(1 if side=='BUY' else -1)
        notional=sum((Decimal(r['quantity'])*Decimal(r['price']) for r in rows),Decimal(0))
        mid=(bid+ask)/2;touch=ask if side=='BUY' else bid
        gross=direction*(notional-benchmark*filled)
        delay=direction*(mid-benchmark)*filled
        spread=direction*(touch-mid)*filled
        residual=direction*(notional-touch*filled)
        if delay+spread+residual!=gross:_fail('DECOMPOSITION')
        fees_known=bool(rows) and all(r['fee_usd'] is not None for r in rows)
        fees=sum((Decimal(r['fee_usd']) for r in rows),Decimal(0)) if fees_known else None
        explicit_total=None if fees is None else gross+fees
        opportunity=Decimal(0) if not unfilled else None if mark is None else direction*(mark-benchmark)*unfilled
        whole=None if explicit_total is None or opportunity is None else explicit_total+opportunity
        inputs=dict(policy=POLICY,product=product,side=side,requested_quantity=_text(quantity),
            decision_mid=_text(benchmark),decision_at_ns=decision,arrival_bid=_text(bid),arrival_ask=_text(ask),
            arrival_at_ns=arrival,end_at_ns=end,fills=rows,unfilled_mark=None if mark is None else _text(mark))
        digest=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return dict(mode='SUPPLIED_EXECUTION_QUALITY_REVIEW',policy=POLICY,status='RESEARCH_REVIEW_REQUIRED',
            input_sha256=digest,input_scope='NORMALIZED_UNIQUE_SUPPLIED_RECORDS_NOT_SOURCE_AUTHENTICATION',
            product=product,side=side,unique_fills=len(rows),duplicate_rows_ignored=duplicates,
            requested_quantity=_text(quantity),filled_quantity=_text(filled),unfilled_quantity=_text(unfilled),
            fill_fraction=_text(filled/quantity),fill_vwap=None if not filled else _text(notional/filled),
            signed_gross_shortfall_usd=_text(gross),delay_component_usd=_text(delay),
            arrival_spread_component_usd=_text(spread),post_arrival_residual_usd=_text(residual),
            decomposition_matches=True,all_supplied_fees_explicit=fees_known,
            supplied_fee_total_usd=None if fees is None else _text(fees),
            signed_shortfall_with_supplied_fees_usd=None if explicit_total is None else _text(explicit_total),
            executed_decision_notional_usd=_text(benchmark*filled),
            executed_shortfall_with_supplied_fees_bps=None if explicit_total is None else _text(explicit_total/(benchmark*filled)*10000),
            signed_unfilled_opportunity_usd=None if opportunity is None else _text(opportunity),
            signed_whole_order_shortfall_usd=None if whole is None else _text(whole),
            whole_order_decision_notional_usd=_text(benchmark*quantity),
            whole_order_shortfall_bps=None if whole is None else _text(whole/(benchmark*quantity)*10000),
            decision_to_arrival_ns=arrival-decision,
            arrival_to_first_supplied_fill_ns=None if not rows else rows[0]['filled_at_ns']-arrival,
            arrival_to_last_supplied_fill_ns=None if not rows else rows[-1]['filled_at_ns']-arrival,
            timestamp_scope='SUPPLIED_SAME_CLOCK_DOMAIN_ASSUMPTION_NOT_MEASURED_BROKER_LATENCY',
            **{k:False for k in FLAGS},
            limitations='Supplied BTC/USD observations only, no source/account/order binding or status/eligibility/coverage attestation. Positive shortfall is worse than the supplied decision benchmark, negative is better; neither is realized profit. Residual is not causally identified market impact. Fee totals use explicit USD debits only; None stays unknown and explicit zero does not prove actual zero fees. Unfilled opportunity uses a caller-supplied end mark, never a hypothetical executable fill. Nanosecond precision does not attest clock synchronization or fill speed. No routing or automatic policy promotion. Reports with actual amounts are private.')


def _fixture(**changes):
    epoch=1792195200000000000
    options=dict(product='BTC-USD',side='BUY',requested_quantity='3',decision_mid='100',
        decision_at_ns=epoch,arrival_bid='100',arrival_ask='102',arrival_at_ns=epoch+1,
        end_at_ns=epoch+4,unfilled_mark='104',fills=[
            dict(execution_id='fixture_a',quantity='1',price='102',filled_at_ns=epoch+2,fee_usd='0.1'),
            dict(execution_id='fixture_b',quantity='1',price='103',filled_at_ns=epoch+3,fee_usd='0.1')])
    options.update(changes);return options


def fixture_report():
    buy=review(**_fixture());sell=review(**_fixture(side='SELL'))
    unknown=_fixture();unknown['fills'][0]['fee_usd']=None;missing=review(**unknown)
    duplicate=_fixture();duplicate['fills'].append(dict(duplicate['fills'][0]));dedup=review(**duplicate)
    conflict=_fixture();conflict['fills'].append(dict(conflict['fills'][0],price='99'))
    denied=False
    try:review(**conflict)
    except ValueError as error:denied=str(error)=='EXECUTION_QUALITY_DUPLICATE_CONFLICT'
    reversed_input=_fixture();reversed_input['fills'].reverse();reordered=review(**reversed_input)
    empty=review(**_fixture(fills=[],unfilled_mark=None))
    checks=dict(buy_sell_signs_checked=buy['signed_gross_shortfall_usd']=='5' and sell['signed_gross_shortfall_usd']=='-5',
        decomposition_checked=buy['delay_component_usd']=='2' and buy['arrival_spread_component_usd']=='2' and buy['post_arrival_residual_usd']=='1',
        partial_opportunity_separate=buy['signed_unfilled_opportunity_usd']=='4' and buy['signed_whole_order_shortfall_usd']=='9.2',
        missing_fees_remain_unknown=missing['signed_shortfall_with_supplied_fees_usd'] is None and missing['signed_whole_order_shortfall_usd'] is None,
        duplicates_not_double_counted=dedup['duplicate_rows_ignored']==1 and dedup['signed_gross_shortfall_usd']==buy['signed_gross_shortfall_usd'],
        conflicting_duplicates_rejected=denied,nanosecond_ordering_checked=buy['decision_to_arrival_ns']==1 and buy['arrival_to_last_supplied_fill_ns']==2,
        input_order_invariant=buy==reordered,no_fill_not_promoted=empty['fill_vwap'] is None and empty['signed_shortfall_with_supplied_fees_usd'] is None)
    return dict(source='SYNTHETIC_EXECUTION_QUALITY_FIXTURE',passed=all(checks.values()),**checks,**{k:False for k in FLAGS})


if __name__=='__main__':
    print(json.dumps(fixture_report(),sort_keys=True))
