"""Strict supplied Activity SSE envelope/link review; no transport or writes.

This is a forward-compatible, narrow parser for BTC/USD ``TRD`` fills and
one-to-one USD ``FEE`` events from Alpaca's documented Activity SSE schema.
It never opens a stream, authenticates an account, persists a cursor, or
claims complete delivery.  Unsupported bulk fees, corrections, busts, and
other financial activity types fail closed instead of being discarded.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from nexus.alpaca_activity_normalizer import _instant, _uuid
from nexus.alpaca_paper_contract import sha, text, timestamp_order_ns


POLICY = 'nexus.supplied-alpaca-activity-sse-link-review.v1'
SOURCES = (
    'https://docs.alpaca.markets/us/docs/activity-sse',
)
MAX_EVENTS = 2000
MAX_FIELDS = 32
MAX_DETAILS_FIELDS = 24
MAX_FIELD_BYTES = 4096
MAX_INPUT_BYTES = 4 * 1024 * 1024
ULID = re.compile(r'[0-7][0-9A-HJKMNP-TV-Z]{25}', re.ASCII)
CLIENT_ID = re.compile(r'[A-Za-z0-9_-]{1,64}', re.ASCII)
COMMON = frozenset(('account_id', 'at', 'event_id', 'ref_id',
    'activity_type', 'activity_subtype', 'status', 'executed_at',
    'settle_date', 'qty', 'price', 'net_amount', 'currency', 'previous_id',
    'swap_rate', 'swap_fee_bps', 'details'))
FLAGS = ('provider_source_attested', 'activity_stream_entitled',
    'stream_delivery_verified', 'cursor_durability_verified',
    'reconnect_coverage_verified', 'parallel_legacy_equivalence_verified',
    'fee_coverage_complete', 'fee_posting_interval_verified',
    'holds_verified', 'settled_funds_verified', 'spendable_funds_verified',
    'broker_account_reconciled', 'ready_for_routing', 'live_orders_enabled',
    'money_movement_enabled', 'release_approved')


def _fail(code):
    raise ValueError('ALPACA_ACTIVITY_SSE_' + code) from None


def _signed(value, *, zero=True):
    if (isinstance(value, bool) or
            not isinstance(value, (str, int, float, Decimal)) or
            isinstance(value, str) and len(value) > 64):
        _fail('NUMBER_INVALID')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _fail('NUMBER_INVALID')
    if (not result.is_finite() or result.copy_abs() > Decimal('1e12') or
            result.as_tuple().exponent < -18 or
            result != 0 and result.copy_abs() < Decimal('1e-18') or
            not zero and result == 0):
        _fail('NUMBER_INVALID')
    return result


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\d', value):
        _fail('DATE_INVALID')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail('DATE_INVALID')
    if parsed.isoformat() != value:
        _fail('DATE_INVALID')
    return value


def _uuid_value(value, code='UUID_INVALID'):
    if not isinstance(value, str) or len(value) > 64:
        _fail(code)
    try:
        return _uuid(value)
    except (ValueError, TypeError, AttributeError):
        _fail(code)


def _budget(events):
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        _fail('EVENT_CAP')
    total = 0
    for event in events:
        if not isinstance(event, dict) or len(event) > MAX_FIELDS:
            _fail('EVENT_SHAPE')
        for key, value in event.items():
            if not isinstance(key, str) or len(key) > 64:
                _fail('EVENT_SHAPE')
            if key == 'details':
                if not isinstance(value, dict) or len(value) > MAX_DETAILS_FIELDS:
                    _fail('DETAILS_SHAPE')
                values = value.items()
            else:
                values = ((key, value),)
            for nested_key, nested_value in values:
                if (not isinstance(nested_key, str) or len(nested_key) > 64 or
                        nested_value is not None and type(nested_value) not in
                        (str, int, float, bool, Decimal)):
                    _fail('FIELD_INVALID')
                if type(nested_value) is int and nested_value.bit_length() > 256:
                    _fail('FIELD_INVALID')
                if type(nested_value) in (int, float, Decimal):
                    try:
                        numeric = Decimal(str(nested_value))
                    except (InvalidOperation, ValueError, TypeError):
                        _fail('FIELD_INVALID')
                    if not numeric.is_finite() or numeric.copy_abs() > Decimal('1e12'):
                        _fail('FIELD_INVALID')
                rendered = '' if nested_value is None else str(nested_value)
                try:
                    size = len(rendered.encode('utf-8'))
                except UnicodeEncodeError:
                    _fail('FIELD_INVALID')
                if size > MAX_FIELD_BYTES:
                    _fail('FIELD_CAP')
                total += len(nested_key.encode('utf-8')) + size + 8
                if total > MAX_INPUT_BYTES:
                    _fail('INPUT_BYTE_CAP')


def _common(event, account):
    if not set(event) <= COMMON or set(event) < {
            'account_id', 'at', 'event_id', 'ref_id', 'activity_type',
            'status', 'executed_at', 'settle_date', 'qty', 'price',
            'net_amount', 'currency', 'details'}:
        _fail('EVENT_SHAPE')
    if _uuid_value(event['account_id']) != account:
        _fail('ACCOUNT_DISAGREEMENT')
    event_id = event['event_id']
    if not isinstance(event_id, str) or ULID.fullmatch(event_id) is None:
        _fail('EVENT_ID_INVALID')
    if event.get('previous_id') is not None:
        _fail('CORRECTION_OR_BUST_REQUIRES_REVIEW')
    if event.get('swap_rate') is not None or event.get('swap_fee_bps') is not None:
        _fail('FX_SCOPE_UNSUPPORTED')
    if event['status'] != 'executed' or event['currency'] != 'USD':
        _fail('STATUS_OR_CURRENCY_SCOPE')
    try:
        occurred = _instant(event['at'])
        executed = _instant(event['executed_at'])
    except (ValueError, TypeError, AttributeError, OverflowError):
        _fail('TIME_INVALID')
    return dict(event_id=event_id, ref_id=_uuid_value(event['ref_id']),
        activity_type=event['activity_type'],
        activity_subtype=event.get('activity_subtype'), at=occurred,
        executed_at=executed, settle_date=_day(event['settle_date']))


def _trade(event, common):
    details = event['details']
    required = {'asset_id', 'order_id', 'client_order_id', 'side',
        'order_status', 'execution_type', 'symbol', 'cum_qty', 'leaves_qty'}
    if set(details) not in (required, required | {'commission'}):
        _fail('TRADE_DETAILS_SHAPE')
    if common['activity_subtype'] is not None or \
            details['execution_type'] != 'fill':
        _fail('TRADE_EXECUTION_SCOPE')
    if details['symbol'] not in ('BTCUSD', 'BTC/USD') or \
            details['side'] not in ('buy', 'sell') or \
            details['order_status'] not in ('filled', 'partially_filled'):
        _fail('TRADE_PRODUCT_OR_STATE_SCOPE')
    client = details['client_order_id']
    if not isinstance(client, str) or CLIENT_ID.fullmatch(client) is None:
        _fail('CLIENT_ID_INVALID')
    qty = _signed(event['qty'], zero=False)
    price = _signed(event['price'], zero=False)
    net = _signed(event['net_amount'], zero=False)
    cumulative = _signed(details['cum_qty'], zero=False)
    leaves = _signed(details['leaves_qty'])
    if qty <= 0 or price <= 0 or cumulative < qty or leaves < 0:
        _fail('TRADE_QUANTITY_INVALID')
    if details['side'] == 'buy' and net >= 0 or \
            details['side'] == 'sell' and net <= 0:
        _fail('TRADE_CASH_DIRECTION')
    if 'commission' in details and _signed(details['commission']) < 0:
        _fail('TRADE_COMMISSION_INVALID')
    return dict(**common, asset_id=_uuid_value(details['asset_id']),
        order_id=_uuid_value(details['order_id']), client_order_id=client,
        side=details['side'].upper(), symbol='BTC-USD', quantity=text(qty),
        price=text(price), net_amount=text(net), cumulative_quantity=text(cumulative),
        leaves_quantity=text(leaves))


def _fee(event, common):
    details = event['details']
    if set(details) != {'parent_id', 'system_date'}:
        _fail('FEE_DETAILS_OR_PARENT_SCOPE')
    if common['activity_subtype'] not in (None, 'COM', 'OCC'):
        _fail('FEE_SUBTYPE_SCOPE')
    if _signed(event['qty']) != 0 or _signed(event['price']) != 0 or \
            _signed(event['net_amount'], zero=False) >= 0:
        _fail('FEE_AMOUNT_OR_DIRECTION')
    return dict(**common, parent_ref_id=_uuid_value(details['parent_id']),
        system_date=_day(details['system_date']),
        amount=text(_signed(event['net_amount']).copy_negate()))


def _normalize(events, *, expected_account_id):
    """Return private canonical rows after bounded envelope validation."""
    _budget(events)
    try:
        account = _uuid_value(expected_account_id, 'ACCOUNT_INVALID')
    except (ValueError, TypeError, AttributeError):
        _fail('ACCOUNT_INVALID')
    normalized = []
    event_ids = set()
    ref_ids = set()
    previous_event_id = None
    for event in events:
        common = _common(event, account)
        if common['event_id'] in event_ids or common['ref_id'] in ref_ids:
            _fail('DUPLICATE_IDENTITY')
        if previous_event_id is not None and common['event_id'] <= previous_event_id:
            _fail('CURSOR_NOT_STRICTLY_INCREASING')
        previous_event_id = common['event_id']
        event_ids.add(common['event_id'])
        ref_ids.add(common['ref_id'])
        if common['activity_type'] == 'TRD':
            normalized.append(_trade(event, common))
        elif common['activity_type'] == 'FEE':
            normalized.append(_fee(event, common))
        else:
            _fail('ACTIVITY_TYPE_SCOPE')
    return account, normalized


def _linked(normalized):
    """Require every admitted fee parent to resolve in the supplied rows."""
    trades = {row['ref_id']: row for row in normalized
        if row['activity_type'] == 'TRD'}
    fees = [row for row in normalized if row['activity_type'] == 'FEE']
    if not trades or not fees:
        _fail('TRADES_AND_FEES_REQUIRED')
    linked_orders = set()
    for fee in fees:
        trade = trades.get(fee['parent_ref_id'])
        if trade is None:
            _fail('FEE_PARENT_UNKNOWN')
        if timestamp_order_ns(fee['executed_at']) < \
                timestamp_order_ns(trade['executed_at']):
            _fail('FEE_EXECUTION_PRECEDES_PARENT')
        linked_orders.add(trade['order_id'])
    return trades, fees, linked_orders


def review(events, *, expected_account_id):
    """Review a supplied finite event sequence and one-to-one fee links."""
    account, normalized = _normalize(events,
        expected_account_id=expected_account_id)
    trades, fees, linked_orders = _linked(normalized)
    canonical = dict(policy=POLICY,
        account_sha256=sha(account), sources=SOURCES, events=normalized)
    return dict(mode='SUPPLIED_ALPACA_ACTIVITY_SSE_LINK_REVIEW',
        status='REVIEW_REQUIRED', read_only=True,
        input_sha256=sha(canonical), account_sha256=sha(account),
        events_reviewed=len(normalized), trades_reviewed=len(trades),
        fees_reviewed=len(fees), linked_parent_trades=len({fee['parent_ref_id']
            for fee in fees}), linked_orders=len(linked_orders),
        event_ids_unique=True, ref_ids_unique=True,
        event_cursor_order_strict=True, all_admitted_fees_parent_linked=True,
        corrections_and_busts_admitted=False,
        bulk_or_unlinked_fees_admitted=False,
        schema_scope='BTC_USD_TRD_FILL_AND_ONE_TO_ONE_USD_FEE_ONLY',
        **{flag: False for flag in FLAGS},
        limitations='Supplied finite envelopes only. The parser performs no SSE connection, entitlement check, authentication, cursor persistence, reconnect, historical replay, parallel legacy comparison, provider-source attestation or complete delivery proof. Bulk fees, corrections, busts, FX and non-BTC/USD activity fail closed. Parent links establish only local schema consistency, not fee completeness, posting, holds, settlement, spendable funds, broker acceptance, routing, live trading or release approval.')


def _event(event_id, ref_id, kind, **changes):
    account = '01234567-89ab-cdef-0123-456789abcdef'
    trade = dict(account_id=account, event_id=event_id, ref_id=ref_id,
        activity_type='TRD', qty='2', price='100', currency='USD',
        net_amount='-200', status='executed',
        at='2026-10-18T18:00:00.100000000Z',
        executed_at='2026-10-18T18:00:00.000000000Z',
        settle_date='2026-10-18', details=dict(
            asset_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            order_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            client_order_id='fixture_client_1', side='buy',
            order_status='filled', execution_type='fill', symbol='BTCUSD',
            cum_qty='2', leaves_qty='0'))
    if kind == 'FEE':
        trade.update(activity_type='FEE', activity_subtype='COM', qty='0',
            price='0', net_amount='-1',
            at='2026-10-18T18:01:00.100000000Z',
            executed_at='2026-10-18T18:01:00.000000000Z',
            details=dict(parent_id='11111111-1111-1111-1111-111111111111',
                system_date='2026-10-18'))
    trade.update(changes)
    return trade


def fixture_report():
    """Synthetic aggregate-only linked controls; no provider or network I/O."""
    account = '01234567-89ab-cdef-0123-456789abcdef'
    events = [
        _event('01K00000000000000000000001',
            '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event('01K00000000000000000000002',
            '22222222-2222-2222-2222-222222222222', 'FEE')]
    result = review(events, expected_account_id=account)
    negative = {}
    cases = {
        'unlinked_parent': [events[0], _event(events[1]['event_id'],
            events[1]['ref_id'], 'FEE', details=dict(
                parent_id='33333333-3333-3333-3333-333333333333',
                system_date='2026-10-18'))],
        'cursor_regression': list(reversed(events)),
        'bulk_fee': [events[0], _event(events[1]['event_id'],
            events[1]['ref_id'], 'FEE', activity_subtype='TAF',
            details=dict(system_date='2026-10-18'))],
    }
    for name, values in cases.items():
        try:
            review(values, expected_account_id=account)
            negative[name] = False
        except ValueError:
            negative[name] = True
    checks = dict(strict_cursor_and_identity=result['event_cursor_order_strict']
        and result['event_ids_unique'] and result['ref_ids_unique'],
        exact_parent_link_bound=result['all_admitted_fees_parent_linked']
        and result['linked_parent_trades'] == 1 and result['linked_orders'] == 1,
        unsupported_corrections_and_bulk_fees_refused=
        result['corrections_and_busts_admitted'] is False and
        result['bulk_or_unlinked_fees_admitted'] is False and
        negative['bulk_fee'],
        unknown_parent_refused=negative['unlinked_parent'],
        cursor_regression_refused=negative['cursor_regression'],
        output_never_authoritative=all(result[flag] is False for flag in FLAGS))
    return dict(source='SYNTHETIC_SUPPLIED_ACTIVITY_SSE_LINK_FIXTURE',
        passed=all(checks.values()), **checks,
        actual_provider_access=False, actual_vault_access=False,
        **{flag: False for flag in FLAGS})
