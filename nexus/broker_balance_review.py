"""Pure supplied BTC/USD balance, availability and open-order review.

No provider, credentials, storage, ledger mutation or spendable-funds authority.
Sequential snapshots and caller-linked fees remain unverified even when matching.
"""
from datetime import timezone
from decimal import Decimal, localcontext
import hashlib
import re

from nexus import alpaca_paper_inspection as inspection
from nexus import alpaca_paper_contract as contract
from nexus import broker_fee_projection as projection
from nexus import broker_cash_path
from nexus.alpaca_activity_normalizer import _budget, MAX_INPUT_BYTES

MAX_OPEN_ORDERS = 128
MAX_POSITIONS = 128
MAX_FEES = 2000


def _fail(code):
    raise ValueError('BROKER_BALANCE_REVIEW_' + code)


def _stamp(value):
    # The older linked-fee projector groups at microsecond precision. Do not
    # silently round nanosecond REST evidence into that different profile.
    if not isinstance(value, str) or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})', value) is None:
        _fail('TIMESTAMP_PROFILE')
    try: return contract.timestamp(value).astimezone(timezone.utc)
    except (ValueError, OverflowError): _fail('TIMESTAMP_PROFILE')


def _canonical_record(normalizer, field):
    def normalize(raw):
        at = _stamp(raw.get(field))
        result = normalizer(raw)
        result[field] = at.isoformat()
        return result
    return normalize


def _open_order(raw):
    if raw.get('symbol') not in ('BTC/USD', 'BTCUSD') or raw.get('side') not in ('buy', 'sell') or raw.get('type') != 'limit' or raw.get('time_in_force') != 'ioc':
        _fail('OPEN_ORDER_SCOPE')
    if raw.get('order_class') not in (None, '', 'simple') or any(raw.get(k) is not None for k in ('notional', 'legs', 'replaces', 'replaced_by', 'stop_price', 'trail_price', 'trail_percent')):
        _fail('COMPLEX_OR_REPLACED_ORDER')
    if raw.get('extended_hours') is not None and raw['extended_hours'] is not False:
        _fail('OPEN_ORDER_SCOPE')
    cid = raw.get('client_order_id')
    if not isinstance(cid, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', cid) is None:
        _fail('CLIENT_ID')
    qty, price = contract.number(raw.get('qty')), contract.number(raw.get('limit_price'))
    payload = dict(client_order_id=cid, symbol='BTC/USD', side=raw['side'], type='limit',
                   time_in_force='ioc', qty=contract.text(qty), limit_price=contract.text(price))
    normalized = contract.normalize(raw, payload)
    normalized['updated_at'] = _stamp(raw['updated_at']).isoformat()
    if normalized['terminal']: _fail('TERMINAL_ORDER_IN_OPEN_SNAPSHOT')
    with localcontext() as context:
        context.prec = 80
        remaining = qty - contract.number(normalized['filled_quantity'], zero=True)
    return dict(snapshot=projection.order_record(normalized), requested_quantity=contract.text(qty),
                remaining_quantity=contract.text(remaining), limit_price=contract.text(price))


def review(orders, executions, fees, *, expected_account_id, opening_balances, opening_at,
           account_before, positions, open_orders, account_after, observed_at,
           fee_coverage_complete=False):
    """Review one bounded supplied snapshot against explicit linked history.

    Creation windows, source/account labels, fees and order lists are not attested.
    Missing availability is never inferred from total quantity or buying power.
    """
    expected = inspection._uuid(expected_account_id)
    if type(fee_coverage_complete) is not bool: _fail('FEE_DECLARATION')
    groups = ((orders, projection.MAX_ORDERS), (executions, projection.MAX_EXECUTIONS),
              (fees, MAX_FEES), (positions, MAX_POSITIONS), (open_orders, MAX_OPEN_ORDERS))
    for rows, limit in groups:
        if not isinstance(rows, list) or len(rows) > limit: _fail('INPUT_CAP')
    if not isinstance(account_before, dict) or not isinstance(account_after, dict): _fail('ACCOUNT_SHAPE')
    total = sum(_budget(rows) for rows, _ in groups) + _budget([account_before, account_after])
    if total > MAX_INPUT_BYTES: _fail('INPUT_BYTE_CAP')
    # Freeze bounded scalar-only source rows before comparing the snapshots.
    orders, executions, fees, positions, open_orders = ([dict(row) for row in rows] for rows, _ in groups)
    account_before, account_after = dict(account_before), dict(account_after)
    groups = ((orders, projection.MAX_ORDERS), (executions, projection.MAX_EXECUTIONS),
              (fees, MAX_FEES), (positions, MAX_POSITIONS), (open_orders, MAX_OPEN_ORDERS))
    if sum(_budget(rows) for rows, _ in groups) + _budget([account_before, account_after]) > MAX_INPUT_BYTES:
        _fail('INPUT_BYTE_CAP')
    for rows, _ in groups:
        for row in rows:
            if row.get('account_id') is not None and inspection._uuid(row['account_id']) != expected:
                _fail('ACCOUNT_DISAGREEMENT')
            if any(row.get(k) is not None and row.get(k) is not False for k in ('correction', 'is_correction', 'reversal')) or any(row.get(k) is not None for k in ('previous_id', 'previous_execution_id')):
                _fail('CORRECTION_REQUIRES_REVIEW')
    start, end = _stamp(opening_at), _stamp(observed_at)
    if end < start: _fail('INTERVAL_REGRESSION')
    opening = projection.balances(opening_balances)
    order_map, order_duplicates = projection.unique(orders, projection.MAX_ORDERS, 'broker_order_id',
        _canonical_record(projection.order_record, 'updated_at'))
    execution_map, execution_duplicates = projection.unique(executions, projection.MAX_EXECUTIONS, 'execution_id',
        _canonical_record(projection.execution_record, 'occurred_at'))
    fee_map, fee_duplicates = projection.unique(fees, MAX_FEES, 'activity_id',
        _canonical_record(projection.fee_record, 'occurred_at'))
    if len({r['client_order_id'] for r in order_map.values()}) != len(order_map): _fail('CLIENT_ID_CONFLICT')
    summary = inspection._report(account_before, positions, open_orders, account_after, expected, 'SUPPLIED_UNATTESTED')
    account = inspection._account(account_before)
    reported_cash = summary['observed_inventory']['cash']
    reported_buying_power = summary['observed_inventory']['non_marginable_buying_power']
    reasons = set(summary['review_reasons']) | {'ACCOUNT_SNAPSHOT_SOURCE_UNVERIFIED',
        'ACTIVITY_AND_FEE_COVERAGE_UNVERIFIED', 'ORDER_HOLD_POLICY_UNVERIFIED', 'SEQUENTIAL_SNAPSHOT_NOT_ATOMIC'}
    total_btc = Decimal(0)
    supported_positions = True
    normalized_positions = []; unsupported_scope = []
    for row in positions:
        if row.get('symbol') not in ('BTC/USD', 'BTCUSD') or row.get('asset_class') != 'crypto' or row.get('side') != 'long':
            supported_positions = False
            unsupported_scope.append({k:None if row.get(k) is None else str(row[k])
                for k in ('symbol','asset_class','side','qty','qty_available')})
            continue
        quantity = contract.number(row.get('qty'), zero=True)
        available = None if row.get('qty_available') is None else contract.number(row['qty_available'], zero=True)
        # _report already checks duplicate BTC rows and availability <= quantity.
        total_btc = quantity
        normalized_positions.append(dict(symbol='BTC/USD', side='long', quantity=contract.text(quantity),
            quantity_available=None if available is None else contract.text(available)))
    if not supported_positions:
        total_btc = None
        reasons.add('OBSERVED_TOTAL_INVENTORY_NOT_EVALUATED')
    available_btc = None if not supported_positions or summary['observed_btc_available'] is None else contract.number(summary['observed_btc_available'], zero=True)
    unavailable_btc = None
    if total_btc is not None and available_btc is not None:
        with localcontext() as context:
            context.prec = 80
            unavailable_btc = total_btc - available_btc
    open_map = {}; clients = {}; open_duplicates = 0
    for raw in open_orders:
        row = _open_order(raw); snapshot = row['snapshot']; oid = snapshot['broker_order_id']
        if _stamp(snapshot['updated_at']) > end: _fail('OPEN_SNAPSHOT_AFTER_OBSERVATION')
        if oid in open_map:
            if open_map[oid] != row: _fail('OPEN_ORDER_DUPLICATE_CONFLICT')
            open_duplicates += 1
            continue
        cid = snapshot['client_order_id']
        if cid in clients and clients[cid] != oid: _fail('CLIENT_ID_CONFLICT')
        clients[cid] = oid; open_map[oid] = row
        previous = order_map.get(oid)
        if previous is None: reasons.add('OPEN_ORDER_OUTSIDE_SUPPLIED_EXECUTION_HISTORY')
        elif previous != snapshot: reasons.add('OPEN_ORDER_HISTORY_SNAPSHOT_DISAGREEMENT')
        if row['remaining_quantity'] == '0': reasons.add('NONTERMINAL_ORDER_WITHOUT_REMAINDER')
    if any(r['status'] not in contract.TERMINAL and oid not in open_map for oid, r in order_map.items()):
        reasons.add('NONTERMINAL_HISTORY_ORDER_NOT_IN_OPEN_SNAPSHOT')
    with localcontext() as context:
        context.prec = 80
        buy_cap = sum((contract.number(r['remaining_quantity'], zero=True) * contract.number(r['limit_price'])
                       for r in open_map.values() if r['snapshot']['side'] == 'buy'), Decimal(0))
        sell_quantity = sum((contract.number(r['remaining_quantity'], zero=True)
                             for r in open_map.values() if r['snapshot']['side'] == 'sell'), Decimal(0))
        if reported_cash is not None and buy_cap > contract.number(reported_cash, zero=True):
            reasons.add('BUY_LIMIT_PRINCIPAL_CAP_EXCEEDS_REPORTED_CASH')
        if total_btc is not None and sell_quantity > total_btc:
            reasons.add('SELL_REMAINDERS_EXCEED_REPORTED_TOTAL_BTC')
        if unavailable_btc is not None and unavailable_btc != sell_quantity:
            reasons.add('OBSERVED_BTC_UNAVAILABLE_DIFFERS_FROM_SELL_REMAINDERS')
    projected = None
    if reported_cash is not None and total_btc is not None:
        projected = broker_cash_path.analyze_and_project(
            [dict(r, symbol='BTC/USD') for r in order_map.values()],
            [dict(r, product='BTC-USD') for r in execution_map.values()], list(fee_map.values()),
            opening_balances={k:contract.text(v) for k,v in opening.items()}, opening_at=start.isoformat(),
            observed_balances={'USD':reported_cash, 'BTC':contract.text(total_btc)}, observed_at=end.isoformat(),
            fee_coverage_complete=fee_coverage_complete)
        reasons.update(projected['reasons']); reasons.update(projected['cash_path']['reasons'])
    else: reasons.add('BALANCE_PROJECTION_NOT_EVALUATED')
    inventory = dict(reported_cash=reported_cash, reported_non_marginable_buying_power=reported_buying_power,
        reported_total_btc=None if total_btc is None else contract.text(total_btc),
        reported_available_btc=None if available_btc is None else contract.text(available_btc),
        observed_unavailable_btc=None if unavailable_btc is None else contract.text(unavailable_btc))
    normalized = dict(account_sha256=hashlib.sha256(expected.encode()).hexdigest(), account=account,
        opening_balances={k:contract.text(v) for k,v in opening.items()}, opening_at=start.isoformat(), observed_at=end.isoformat(),
        positions=normalized_positions, inventory=inventory, unsupported_positions=len(positions)-len(normalized_positions),
        unsupported_position_scope_sha256=contract.sha(unsupported_scope),
        orders=sorted(order_map.values(), key=lambda r:r['broker_order_id']),
        executions=sorted(execution_map.values(), key=lambda r:r['execution_id']),
        fees=sorted(fee_map.values(), key=lambda r:r['activity_id']),
        open_orders=[open_map[k] for k in sorted(open_map)], fee_coverage_declared=fee_coverage_complete)
    return dict(mode='SUPPLIED_BTC_USD_BALANCE_REVIEW', status='REVIEW_REQUIRED', read_only=True,
        source='SUPPLIED_UNATTESTED', account_sha256=normalized['account_sha256'], input_sha256=contract.sha(normalized),
        observed_inventory=inventory, open_order_terms=dict(unique_orders=len(open_map),
            remaining_buy_limit_principal_cap_usd=contract.text(buy_cap), remaining_sell_quantity_btc=contract.text(sell_quantity),
            scope='SUPPLIED_QUANTITY_IOC_LIMIT_TERMS_ONLY_NOT_PROVIDER_HOLDS_OR_FUTURE_PROCEEDS'),
        balance_projection_evaluated=projected is not None, linked_history_review=projected,
        duplicate_records_ignored=dict(orders=order_duplicates, executions=execution_duplicates, fees=fee_duplicates, open_orders=open_duplicates),
        review_reasons=sorted(reasons), atomic_snapshot=False, account_fields_double_checked=True,
        source_coverage_verified=False, fee_coverage_complete=False, holds_reconciled=False,
        broker_account_reconciled=False, spendable_funds_verified=False, ready_for_routing=False,
        broker_connected=False, ledger_applied=False, live_orders_enabled=False, automatic_retry=False,
        limitations='Supplied sequential consistency only. Compare explicit fills/linked fees with total inventory, never availability. Quantity-minus-availability is an observed difference, not an authenticated hold ledger. Buy limit principal caps are not broker holds, required execution prices or fee-inclusive spendable funds; incoming buys/sell proceeds are not credited as available. Missing orders, transfers, settlement, fees, corrections and source coverage require independent acceptance. Microsecond linked-history profile only; no REST fee links or posting instants are invented.')


def fixture_report():
    from nexus.broker_fee_projection import fixture_inputs
    data = fixture_inputs(); balances = data.pop('observed_balances')
    account = dict(id='01234567-89ab-cdef-0123-456789abcdef', currency='USD', status='ACTIVE', crypto_status='ACTIVE',
        account_blocked=False, trading_blocked=False, trade_suspended_by_user=False,
        cash=balances['USD'], non_marginable_buying_power=balances['USD'], pending_transfer_in='0', pending_transfer_out='0', accrued_fees='0')
    result = review(**data, expected_account_id=account['id'], account_before=account, account_after=dict(account),
                    positions=[], open_orders=[])
    missing = review(**data, expected_account_id=account['id'], account_before=account, account_after=dict(account),
        positions=[dict(symbol='BTCUSD', asset_class='crypto', side='long', qty='0', qty_available=None)], open_orders=[])
    changed = dict(account, cash='1000')
    mismatch = review(**data, expected_account_id=account['id'], account_before=changed, account_after=dict(changed),
                      positions=[], open_orders=[])
    checks = dict(missing_available_btc_is_unknown=missing['observed_inventory']['reported_available_btc'] is None
                  and 'POSITION_AVAILABILITY_MISSING' in missing['review_reasons'],
                  cash_disagreement_detected='DECLARED_BALANCE_DISAGREEMENT' in mismatch['review_reasons'])
    return dict(passed=result['linked_history_review']['cash_path']['declared_path_consistency_ok'] and all(checks.values()), review=result,
                negative_checks=checks,
                source='FIXED_SUPPLIED_LINKED_FEE_FIXTURE', broker_connected=False, live_orders_enabled=False)
