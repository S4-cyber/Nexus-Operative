"""Pure, bounded Trading REST v2 BTC/USD activity normalization.

No fetch, storage, execution inference, automatic fee linking or account authority.
REST activity IDs are not assumed to be stream execution IDs. Date-only fee
records never become invented execution/posting timestamps.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import re
from uuid import UUID

from nexus import broker_fee_projection as projection
from nexus.alpaca_paper_contract import number, text, sha

MAX_ACTIVITIES = 2000
MAX_ROW_FIELDS = 64
MAX_FIELD_BYTES = 4096
MAX_INPUT_BYTES = 2 * 1024 * 1024
SYMBOLS = ('BTC/USD', 'BTCUSD')


def _fail(code):
    raise ValueError('ALPACA_ACTIVITY_' + code)


def _uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        _fail('UUID_INVALID')
    try: return str(UUID(value))
    except ValueError: _fail('UUID_INVALID')


def _id(value):
    if not isinstance(value, str) or len(value) != 55 or not re.fullmatch(r'[0-9]{17}::[A-Fa-f0-9-]{36}', value):
        _fail('REST_ID_INVALID')
    return value[:19] + _uuid(value[19:])


def _instant(value):
    """Canonical RFC3339 instant with up to nine fractional digits, without rounding."""
    if not isinstance(value, str): _fail('TIME_INVALID')
    match = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.([0-9]{1,9}))?(Z|[+-]\d\d:\d\d)', value, re.ASCII)
    if not match or match[3] == '-00:00': _fail('TIME_INVALID')
    try:
        base = datetime.fromisoformat(match[1] + ('+00:00' if match[3] == 'Z' else match[3])).astimezone(timezone.utc)
    except (ValueError, OverflowError): _fail('TIME_INVALID')
    fraction = (match[2] or '').rstrip('0')
    return base.isoformat(timespec='seconds')[:-6] + ('.' + fraction if fraction else '') + '+00:00'


def _date(value):
    if not isinstance(value, str): _fail('FEE_DATE_INVALID')
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        try:
            result = datetime.fromisoformat(value).date().isoformat()
            if result != value: _fail('FEE_DATE_INVALID')
            return result
        except ValueError: _fail('FEE_DATE_INVALID')
    return _instant(value)


def _debit(value):
    # Source fee amounts are signed. Never turn a refund/credit into a debit.
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or isinstance(value, str) and len(value) > 64:
        _fail('FEE_SIGN_OR_AMOUNT_INVALID')
    try: amount = Decimal(str(value))
    except (InvalidOperation, ValueError): _fail('FEE_SIGN_OR_AMOUNT_INVALID')
    if not amount.is_finite() or amount > 0: _fail('FEE_SIGN_OR_AMOUNT_INVALID')
    return number(amount.copy_negate(), zero=True)


def _budget(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ACTIVITIES: _fail('INPUT_CAP')
    total = 0
    # Bound/validate all fields, including discarded private descriptions, before
    # normalization. Reject nested/unbounded input instead of copying/hashing it.
    for row in rows:
        if not isinstance(row, dict) or len(row) > MAX_ROW_FIELDS: _fail('ROW_INVALID')
        for key, value in row.items():
            if not isinstance(key, str) or len(key) > 64: _fail('ROW_INVALID')
            if value is not None and type(value) not in (str, int, float, bool, Decimal): _fail('ROW_INVALID')
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                # Very large ints must not reach expensive string conversion.
                if isinstance(value, int) and value.bit_length() > 256: _fail('ROW_INVALID')
                try:
                    numeric = Decimal(str(value))
                    if not numeric.is_finite() or numeric.copy_abs() > Decimal('1e12'): _fail('ROW_INVALID')
                except (InvalidOperation, ValueError): _fail('ROW_INVALID')
            encoded = str(value) if value is not None else ''
            if len(encoded) > MAX_FIELD_BYTES: _fail('ROW_CAP')
            try:
                size = len(encoded.encode('utf-8')); key_size = len(key.encode('utf-8'))
            except UnicodeEncodeError: _fail('ROW_INVALID')
            if size > MAX_FIELD_BYTES: _fail('ROW_CAP')
            total += key_size + size + 8
            if total > MAX_INPUT_BYTES: _fail('INPUT_BYTE_CAP')
    return total


def _record(raw, orders, account):
    identity = _id(raw.get('id'))
    if raw.get('account_id') is not None and _uuid(raw['account_id']) != account:
        _fail('ACCOUNT_DISAGREEMENT')
    if any(key in raw for key in ('entry_type', 'ref_id', 'event_ulid', 'details')):
        _fail('REST_STREAM_SCHEMA_MIXED')
    if any(raw.get(key) is not None for key in ('previous_id', 'previous_execution_id')) or any(raw.get(key) is not None and raw.get(key) is not False for key in ('correction', 'is_correction', 'reversal')):
        _fail('CORRECTION_REQUIRES_REVIEW')
    if raw.get('currency') not in (None, 'USD'): _fail('CURRENCY_SCOPE')
    kind = raw.get('activity_type')
    if any(raw.get(key) is not None for key in ('commission', 'swap_rate', 'swap_fee_bps')):
        _fail('UNMODELED_FINANCIAL_FIELDS')
    if kind == 'FILL':
        if raw.get('net_amount') is not None: _fail('UNMODELED_FINANCIAL_FIELDS')
        if raw.get('symbol') not in SYMBOLS or raw.get('side') not in ('buy', 'sell') or raw.get('type') not in ('fill', 'partial_fill'):
            _fail('FILL_SCOPE')
        if raw.get('execution_type') not in (None, 'fill', 'partial_fill'):
            _fail('CORRECTION_REQUIRES_REVIEW')
        if raw.get('asset_class') not in (None, 'crypto') or raw.get('status') not in (None, 'executed'):
            _fail('FILL_SCOPE')
        oid = _uuid(raw.get('order_id')); order = orders.get(oid)
        if order is None or order['side'] != raw['side']:
            _fail('ORDER_LINK_DISAGREEMENT')
        if raw.get('client_order_id') is not None and raw['client_order_id'] != order['client_order_id']:
            _fail('ORDER_LINK_DISAGREEMENT')
        quantity = number(raw.get('qty')); cumulative = number(raw.get('cum_qty')); leaves = number(raw.get('leaves_qty'), zero=True)
        if cumulative < quantity or cumulative > number(order['filled_quantity'], zero=True): _fail('CUMULATIVE_DISAGREEMENT')
        return dict(activity_id=identity, kind=kind, fill_type=raw['type'], broker_order_id=oid, client_order_id=order['client_order_id'],
                    side=raw['side'].upper(), product='BTC-USD', quantity=text(quantity), price=text(number(raw.get('price'))),
                    occurred_at=_instant(raw.get('transaction_time')), cumulative_quantity=text(cumulative), leaves_quantity=text(leaves))
    if kind not in ('CFEE', 'FEE'): _fail('TYPE_REQUIRES_REVIEW')
    if any(raw.get(key) is not None for key in ('type', 'execution_type', 'event')):
        _fail('REST_STREAM_SCHEMA_MIXED')
    if raw.get('status') != 'executed': _fail('FEE_STATUS_REQUIRES_REVIEW')
    if raw.get('symbol') not in (None, '', *SYMBOLS): _fail('FEE_ASSET_SCOPE')
    if kind == 'CFEE':
        if raw.get('symbol') not in SYMBOLS or number(raw.get('net_amount'), zero=True) != 0: _fail('FEE_ASSET_SCOPE')
        amount = _debit(raw.get('qty')); asset = 'BTC'
    else:
        if raw.get('qty') is not None and number(raw['qty'], zero=True) != 0: _fail('FEE_ASSET_SCOPE')
        amount = _debit(raw.get('net_amount')); asset = 'USD'
    # Retain references as unverified hints only; never parse free text or link by
    # equal amounts, nearest time, order ID, or a one-fill coincidence.
    reference = None if raw.get('order_id') is None else _uuid(raw['order_id'])
    execution = None if raw.get('execution_id') is None else projection.identifier(raw['execution_id'])
    return dict(activity_id=identity, kind=kind, asset=asset, amount=text(amount),
                reported_date=_date(raw.get('date')), order_reference=reference,
                unverified_execution_reference=execution)


def normalize(activities, orders, *, expected_account_id):
    """Normalize a supplied REST activity set; never attest coverage or fee links."""
    account = _uuid(expected_account_id)
    if _budget(activities) + _budget(orders) > MAX_INPUT_BYTES: _fail('INPUT_BYTE_CAP')
    def order_record(raw):
        result = projection.order_record(raw)
        result['updated_at'] = _instant(raw['updated_at'])
        return result
    order_map, order_duplicates = projection.unique(orders, projection.MAX_ORDERS, 'broker_order_id', order_record)
    if len({r['client_order_id'] for r in order_map.values()}) != len(order_map): _fail('CLIENT_ID_CONFLICT')
    records = {}; duplicates = 0
    for raw in activities:
        record = _record(raw, order_map, account); identity = record['activity_id']
        if identity in records:
            if records[identity] != record: _fail('DUPLICATE_CONFLICT')
            duplicates += 1
        else: records[identity] = record
    ordered = [records[key] for key in sorted(records)]
    fills = [r for r in ordered if r['kind'] == 'FILL']; fees = [r for r in ordered if r['kind'] != 'FILL']
    executions = [dict(execution_id=r['activity_id'],broker_order_id=r['broker_order_id'],client_order_id=r['client_order_id'],
                       side=r['side'],product=r['product'],quantity=r['quantity'],price=r['price'],occurred_at=r['occurred_at']) for r in fills]
    reasons = {'ACTIVITY_COVERAGE_UNVERIFIED', 'FEE_LINK_AND_TIMING_UNVERIFIED'}
    quantities = {}; notionals = {}; by_order = {}; totals = {'BTC': Decimal(0), 'USD': Decimal(0)}
    with localcontext() as ctx:
        ctx.prec = 80
        for fill in fills:
            oid = fill['broker_order_id']; qty = number(fill['quantity'])
            by_order.setdefault(oid, []).append(fill)
            quantities[oid] = quantities.get(oid, Decimal(0)) + qty
            notionals[oid] = notionals.get(oid, Decimal(0)) + qty * number(fill['price'])
        for oid, order in order_map.items():
            chain = sorted(by_order.get(oid, []), key=lambda f:number(f['cumulative_quantity']))
            previous = Decimal(0); at = None; requested = None
            for fill in chain:
                cumulative = number(fill['cumulative_quantity']); leaves = number(fill['leaves_quantity'], zero=True)
                if cumulative != previous + number(fill['quantity']): reasons.add('CUMULATIVE_FILL_CHAIN_DISAGREEMENT')
                if requested is not None and requested != cumulative + leaves: reasons.add('REMAINING_QUANTITY_DISAGREEMENT')
                if at is not None and fill['occurred_at'] < at: reasons.add('CUMULATIVE_FILL_TIME_REGRESSION')
                if fill['occurred_at'] > order['updated_at']: reasons.add('FILL_AFTER_ORDER_SNAPSHOT')
                previous = cumulative; at = fill['occurred_at']; requested = cumulative + leaves
            qty = number(order['filled_quantity'], zero=True)
            if quantities.get(oid, 0) != qty: reasons.add('SUPPLIED_FILL_QUANTITY_DISAGREEMENT')
            if qty and notionals.get(oid, 0) != qty * number(order['average_fill_price']): reasons.add('SUPPLIED_FILL_AVERAGE_DISAGREEMENT')
        for fee in fees: totals[fee['asset']] += number(fee['amount'], zero=True)
    canonical = dict(account_sha256=hashlib.sha256(account.encode()).hexdigest(),
                     orders=sorted(order_map.values(), key=lambda r:r['broker_order_id']), activities=ordered)
    return dict(mode='SUPPLIED_ALPACA_REST_ACTIVITY_NORMALIZATION', status='REVIEW_REQUIRED',
                input_sha256=sha(canonical), account_scope='CALLER_DECLARED_WITH_OPTIONAL_ROW_ID_CHECKS',
                executions=executions, fee_debits=fees, normalized_activities=ordered,
                recognized_fee_debits={asset:text(value) for asset,value in totals.items()},
                duplicate_activities_ignored=duplicates, duplicate_orders_ignored=order_duplicates,
                review_reasons=sorted(reasons), fee_coverage_complete=False,
                execution_identity_scope='REST_ACTIVITY_ID_NOT_STREAM_EXECUTION_ID',
                broker_account_reconciled=False, spendable_funds_verified=False, ledger_applied=False,
                broker_connected=False, ready_for_routing=False, live_orders_enabled=False,
                limitations='Supplied REST records only; unsupported/corrected activities require review. Fee dates/references are not verified execution links or posting instants. No inferred fills, zero fees, pagination completeness, account attestation or synthetic-ledger changes.')


def fixture_report():
    """Fixed documentation-shaped synthetic example, not collected broker data."""
    data = projection.fixture_inputs(); rows = []
    def identity(index):
        return '20261017180000000::' + f'11234567-89ab-cdef-0123-{index:012d}'
    for index, execution in enumerate(data['executions'], 1):
        rows.append(dict(id=identity(index), activity_type='FILL', order_id=execution['broker_order_id'],
            symbol='BTCUSD', side=execution['side'].lower(), type='fill', qty=execution['quantity'],
            cum_qty=execution['quantity'], leaves_qty='0', price=execution['price'],
            transaction_time=execution['occurred_at']))
    rows.extend((dict(id=identity(3), activity_type='CFEE', symbol='BTCUSD', qty='-0.001',
                      net_amount='0', status='executed', date='2026-10-18'),
                 dict(id=identity(4), activity_type='FEE', symbol='BTCUSD', net_amount='-0.10989',
                      status='executed', date='2026-10-18')))
    result = normalize(rows, data['orders'], expected_account_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    result['source'] = 'FIXED_SYNTHETIC_REST_EXAMPLE'
    return result


if __name__ == '__main__':
    import json
    print(json.dumps(fixture_report(), sort_keys=True, indent=2))
