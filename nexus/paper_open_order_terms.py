"""Bounded supplied open IOC terms, never provider holds or execution authority."""
from decimal import Decimal, localcontext
import re

from nexus import alpaca_paper_contract as contract
from nexus.alpaca_activity_normalizer import _instant, _uuid, _budget, MAX_INPUT_BYTES

MAX_ORDERS = 128
SCOPE = 'QUANTITY_BTC_USD_SIMPLE_IOC_LIMIT_TERMS_NOT_PROVIDER_HOLDS_OR_EXECUTION_PRICES'
FLAGS = ('source_attested','order_coverage_verified','holds_reconciled','spendable_funds_verified',
         'ready_for_routing','ledger_applied','broker_connected','live_orders_enabled')


def _row(raw, account):
    if raw.get('account_id') is not None and _uuid(raw['account_id']) != account: raise ValueError('ACCOUNT')
    if raw.get('symbol') not in ('BTC/USD','BTCUSD') or raw.get('side') not in ('buy','sell') or raw.get('type') != 'limit' or raw.get('time_in_force') != 'ioc': raise ValueError('SCOPE')
    if raw.get('order_class') not in (None,'','simple') or (raw.get('extended_hours') is not None and raw['extended_hours'] is not False): raise ValueError('COMPLEX')
    if any(raw.get(k) is not None for k in ('notional','legs','replaces','replaced_by','stop_price','trail_price','trail_percent')): raise ValueError('COMPLEX')
    if any(raw.get(k) is not None and raw.get(k) is not False for k in ('correction','is_correction','reversal')) or any(raw.get(k) is not None for k in ('previous_id','previous_execution_id')): raise ValueError('CORRECTION')
    client = raw.get('client_order_id')
    if not isinstance(client,str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client) is None: raise ValueError('CLIENT')
    qty, price = contract.number(raw.get('qty')), contract.number(raw.get('limit_price'))
    payload = dict(client_order_id=client,symbol='BTC/USD',side=raw['side'],type='limit',time_in_force='ioc',
        qty=contract.text(qty),limit_price=contract.text(price))
    result = contract.normalize(raw,payload)
    if result['terminal']: raise ValueError('TERMINAL')
    with localcontext() as context:
        context.prec = 80
        remaining = qty-contract.number(result['filled_quantity'],zero=True)
    return dict(broker_order_id=result['broker_order_id'],client_order_id=client,side=raw['side'],status=result['status'],
        requested_quantity=contract.text(qty),filled_quantity=result['filled_quantity'],average_fill_price=result['average_fill_price'],remaining_quantity=contract.text(remaining),
        limit_price=contract.text(price),updated_at=_instant(raw['updated_at']))


def summarize(orders, *, expected_account_id):
    account = _uuid(expected_account_id)
    if type(orders) is not list or len(orders)>MAX_ORDERS: raise ValueError('PAPER_OPEN_ORDER_TERMS_INPUT_CAP')
    if any(type(row) is not dict for row in orders): raise ValueError('PAPER_OPEN_ORDER_TERMS_ROW_INVALID')
    by_id = {}; clients = {}; duplicates = 0; unsupported = 0; conflict = False; reasons = set(); input_bytes = 0
    for raw in orders:
        try:
            # Validate discarded fields too; nested/oversized private payloads
            # cannot leak through errors, summaries or normalized fingerprints.
            input_bytes += _budget([raw])
            if input_bytes > MAX_INPUT_BYTES: raise ValueError('BYTE_CAP')
            row = _row(raw,account)
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            unsupported += 1; reasons.add('UNSUPPORTED_OR_INVALID_OPEN_ORDER_TERMS'); continue
        oid, cid = row['broker_order_id'], row['client_order_id']
        if oid in by_id:
            if by_id[oid] == row: duplicates += 1
            else: conflict = True; reasons.add('CONFLICTING_OPEN_ORDER_IDENTITY_OR_TERMS')
            continue
        if cid in clients and clients[cid] != oid:
            conflict = True; reasons.add('CONFLICTING_OPEN_ORDER_IDENTITY_OR_TERMS')
        by_id[oid] = row; clients[cid] = oid
        if row['remaining_quantity']=='0': reasons.add('NONTERMINAL_ORDER_WITHOUT_REMAINDER')
    complete = unsupported == 0 and not conflict
    buy = sell = None
    if complete:
        with localcontext() as context:
            context.prec = 80
            buy = contract.text(sum((Decimal(r['remaining_quantity'])*Decimal(r['limit_price']) for r in by_id.values() if r['side']=='buy'),Decimal(0)))
            sell = contract.text(sum((Decimal(r['remaining_quantity']) for r in by_id.values() if r['side']=='sell'),Decimal(0)))
    result = dict(mode='REPORTED_OPEN_IOC_LIMIT_TERMS',status='REVIEW_REQUIRED' if reasons else 'TERMS_OBSERVED_SOURCE_UNVERIFIED',
        scope=SCOPE,reported_rows=len(orders),supported_unique_orders=len(by_id),duplicate_rows_ignored=duplicates,
        unsupported_rows=unsupported,terms_complete=complete,remaining_buy_limit_principal_cap_usd=buy,
        remaining_sell_quantity_btc=sell,normalized_terms_sha256=contract.sha([by_id[k] for k in sorted(by_id)]) if complete else None,
        review_reasons=sorted(reasons),**dict.fromkeys(FLAGS,False))
    result['summary_sha256'] = contract.sha(result)
    return result


def fixture_report():
    account='01234567-89ab-cdef-0123-456789abcdef'
    buy=dict(id='01234567-89ab-cdef-0123-456789abcde1',client_order_id='fixture_buy',symbol='BTCUSD',asset_class='crypto',
        side='buy',type='limit',time_in_force='ioc',qty='3',limit_price='100',filled_qty='0.5',filled_avg_price='99',
        status='pending_cancel',updated_at='2026-10-17T18:00:01.000000001Z')
    sell=dict(buy,id='01234567-89ab-cdef-0123-456789abcde2',client_order_id='fixture_sell',side='sell',qty='1',
        limit_price='110',filled_qty='0.5',filled_avg_price='111',status='partially_filled')
    observed=summarize([buy,sell,dict(buy)],expected_account_id=account)
    unsupported=summarize([buy,dict(sell,type='market')],expected_account_id=account)
    return dict(passed=observed['remaining_buy_limit_principal_cap_usd']=='250' and observed['remaining_sell_quantity_btc']=='0.5'
        and unsupported['remaining_buy_limit_principal_cap_usd'] is None,
        observed=observed,unsupported=unsupported,source='FIXED_SUPPLIED_OPEN_IOC_TERMS',broker_connected=False,live_orders_enabled=False)
