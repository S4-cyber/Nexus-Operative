"""Opt-in GET-only paper inspection primitive; not registered with the runtime.

No credential storage, environment lookup, order methods, journal writes or retries.
Injected transports are trusted test seams and never attest a broker account.
"""
import json
from decimal import Decimal
import math
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import UUID

from nexus.alpaca_paper_contract import number, text

ROOT = 'https://paper-api.alpaca.markets'
URLS = (ROOT + '/v2/account', ROOT + '/v2/positions',
        ROOT + '/v2/orders?status=open&limit=129&direction=asc', ROOT + '/v2/account')
MAX_BODY = 1024 * 1024
MAX_ITEMS = 128
REQUEST_TIMEOUT = 4
TOTAL_SECONDS = 20
_lock = threading.Lock()


class PaperInspectionError(ValueError):
    """Only fixed diagnostic codes may cross the inspection boundary."""


def _fail(code):
    raise PaperInspectionError(code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        _fail('PAPER_INSPECTION_REDIRECT_DENIED')


def _uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        _fail('PAPER_INSPECTION_ACCOUNT_ID_INVALID')
    try:
        return str(UUID(value))
    except ValueError:
        _fail('PAPER_INSPECTION_ACCOUNT_ID_INVALID')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail('PAPER_INSPECTION_JSON_DUPLICATE_KEY')
        result[key] = value
    return result


def _nonfinite(value):
    _fail('PAPER_INSPECTION_JSON_NONFINITE')


def _account(raw):
    if not isinstance(raw, dict):
        _fail('PAPER_INSPECTION_ACCOUNT_INVALID')
    result = {'id': _uuid(raw.get('id'))}
    # Missing, unknown and noncanonical flags must never become an implicit pass.
    for field in ('currency', 'status', 'crypto_status'):
        value = raw.get(field)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[A-Z_]{1,40}', value)):
            _fail('PAPER_INSPECTION_ACCOUNT_INVALID')
        result[field] = value
    for field in ('account_blocked', 'trading_blocked', 'trade_suspended_by_user'):
        value = raw.get(field)
        if value is not None and type(value) is not bool:
            _fail('PAPER_INSPECTION_ACCOUNT_INVALID')
        result[field] = value
    for field in ('cash', 'non_marginable_buying_power', 'pending_transfer_in', 'pending_transfer_out', 'accrued_fees'):
        value = raw.get(field)
        result[field] = None if value is None else text(number(value, zero=True))
    return result


def _report(before, positions, orders, after, expected, source):
    first, last = _account(before), _account(after)
    if first['id'] != last['id'] or expected is not None and first['id'] != expected:
        _fail('PAPER_INSPECTION_ACCOUNT_ID_CHANGED')
    if first != last:
        _fail('PAPER_INSPECTION_ACCOUNT_CHANGED_DURING_READ')
    if not isinstance(positions, list) or not isinstance(orders, list):
        _fail('PAPER_INSPECTION_COLLECTION_INVALID')
    if len(positions) > MAX_ITEMS or len(orders) > MAX_ITEMS:
        _fail('PAPER_INSPECTION_COLLECTION_CAP_REVIEW')
    if not all(isinstance(item, dict) for item in positions + orders):
        _fail('PAPER_INSPECTION_COLLECTION_INVALID')
    reasons = []
    if any(first[f] != v for f, v in (('currency', 'USD'), ('status', 'ACTIVE'), ('crypto_status', 'ACTIVE'))):
        reasons.append('ACCOUNT_CURRENCY_OR_STATUS_REVIEW')
    if any(first[f] is not False for f in ('account_blocked', 'trading_blocked', 'trade_suspended_by_user')):
        reasons.append('ACCOUNT_PERMISSION_FLAGS_REVIEW')
    fields = ('cash', 'non_marginable_buying_power', 'pending_transfer_in', 'pending_transfer_out', 'accrued_fees')
    if any(first[f] is None for f in fields):
        reasons.append('ACCOUNT_FUNDS_FIELDS_INCOMPLETE')
    if any(first[f] is not None and number(first[f], zero=True) != 0 for f in fields[2:]):
        reasons.append('PENDING_TRANSFERS_OR_FEES_REVIEW')
    available = '0'
    seen_btc = False
    for position in positions:
        if position.get('symbol') not in ('BTC/USD', 'BTCUSD') or position.get('asset_class') != 'crypto' or position.get('side') != 'long':
            reasons.append('UNSUPPORTED_POSITION_REVIEW')
            continue
        if seen_btc:
            _fail('PAPER_INSPECTION_DUPLICATE_BTC_POSITION')
        seen_btc = True
        quantity = number(position.get('qty'), zero=True)
        if position.get('qty_available') is None:
            available = None
            reasons.append('POSITION_AVAILABILITY_MISSING')
        else:
            qty_available = number(position['qty_available'], zero=True)
            if qty_available > quantity:
                _fail('PAPER_INSPECTION_POSITION_AVAILABILITY_INVALID')
            available = text(qty_available)
    if orders:
        reasons.append('OPEN_ORDER_RESERVATIONS_REQUIRE_RECONCILIATION')
    cash, buying = first['cash'], first['non_marginable_buying_power']
    ceiling = None if cash is None or buying is None else text(min(number(cash, zero=True), number(buying, zero=True)))
    return dict(mode='READ_ONLY_PAPER_INSPECTION', source=source, account_id=first['id'],
                checks='REVIEW_REQUIRED' if reasons else 'OBSERVED_FIELDS_COVERED',
                review_reasons=sorted(set(reasons)), observed_usd_ceiling=ceiling,
                observed_btc_available=available, position_count=len(positions), open_order_count=len(orders),
                sequential_account_fields_stable=True, atomic_snapshot=False,
                broker_account_reconciled=False, spendable_funds_verified=False,
                ready_for_routing=False, ledger_applied=False, broker_connected=False,
                live_orders_enabled=False, automatic_retry=False,
                limitations='Sequential observations only. Matching account fields do not prove positions/orders were atomic or complete. No asset entitlement, fees, settled funds, journal reconciliation or trading permission is verified.')


def inspect_account(api_key, api_secret, *, confirm_read_only_paper=False,
                    expected_account_id=None, opener=None, wall_clock=time.time,
                    elapsed_clock=time.monotonic, permission_check=lambda: True):
    """Perform four bounded GETs only after explicit per-call authorization.

    Not wired to PORTAL, startup, Engage, vaults or environment variables. The
    injected opener is trusted code, not an untrusted plugin boundary. Deadlines
    are checked at IO boundaries; synchronous reads cannot be forcibly canceled.
    """
    if confirm_read_only_paper is not True:
        _fail('PAPER_INSPECTION_EXPLICIT_AUTHORIZATION_REQUIRED')
    for value in (api_key, api_secret):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', value):
            _fail('PAPER_INSPECTION_CREDENTIAL_FORMAT_INVALID')
    expected = None if expected_account_id is None else _uuid(expected_account_id)
    if not _lock.acquire(blocking=False):
        _fail('PAPER_INSPECTION_BUSY')
    try:
        start = previous = None

        def checkpoint():
            nonlocal start, previous
            if permission_check() is not True:
                _fail('PAPER_INSPECTION_PERMISSION_REVOKED')
            current = (wall_clock(), elapsed_clock())
            if any(type(v) not in (int, float) or not 0 <= v <= 1e12 or not math.isfinite(v) for v in current):
                _fail('PAPER_INSPECTION_CLOCK_INVALID')
            if start is None:
                start = current
            if previous is not None and any(a < b for a, b in zip(current, previous)):
                _fail('PAPER_INSPECTION_CLOCK_REGRESSION')
            if abs((current[0] - start[0]) - (current[1] - start[1])) > 2:
                _fail('PAPER_INSPECTION_CLOCK_DISCONTINUITY')
            if current[1] - start[1] >= TOTAL_SECONDS:
                _fail('PAPER_INSPECTION_TIME_BUDGET')
            previous = current
            return max(0.001, min(REQUEST_TIMEOUT, TOTAL_SECONDS - (current[1] - start[1])))

        checkpoint()
        injected = opener is not None
        reader = opener if injected else build_opener(ProxyHandler({}), _NoRedirect())
        values = []
        for url in URLS:
            timeout = checkpoint()
            request = Request(url, method='GET', headers={
                'APCA-API-KEY-ID': api_key, 'APCA-API-SECRET-KEY': api_secret,
                'Accept': 'application/json', 'Accept-Encoding': 'identity',
                'User-Agent': 'NexusOperativePaperInspection/0.x'})
            with reader.open(request, timeout=timeout) as response:
                checkpoint()
                if response.geturl() != url:
                    _fail('PAPER_INSPECTION_RESPONSE_URL_CHANGED')
                if response.status != 200:
                    _fail('PAPER_INSPECTION_HTTP_STATUS_REVIEW')
                if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    _fail('PAPER_INSPECTION_CONTENT_TYPE_INVALID')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    _fail('PAPER_INSPECTION_ENCODING_DENIED')
                length = response.headers.get('Content-Length')
                if length is not None and (not re.fullmatch(r'[0-9]{1,8}', length) or int(length) > MAX_BODY):
                    _fail('PAPER_INSPECTION_BODY_CAP')
                body = bytearray()
                while True:
                    checkpoint()
                    chunk = response.read(min(65536, MAX_BODY + 1 - len(body)))
                    checkpoint()
                    if not isinstance(chunk, bytes):
                        _fail('PAPER_INSPECTION_BODY_INVALID')
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        _fail('PAPER_INSPECTION_BODY_CAP')
                    if not chunk:
                        break
                if length is not None and len(body) != int(length):
                    _fail('PAPER_INSPECTION_BODY_LENGTH_CHANGED')
                value = json.loads(body.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_nonfinite, parse_float=Decimal)
                if url == URLS[0]:
                    normalized = _account(value)
                    if expected is not None and normalized['id'] != expected:
                        _fail('PAPER_INSPECTION_ACCOUNT_ID_CHANGED')
                values.append(value)
        result = _report(*values, expected, 'INJECTED_UNATTESTED' if injected else 'PAPER_HTTPS_READS')
        checkpoint()
        return result
    except HTTPError as exc:
        code = exc.code
        exc.close()
        reason = 'AUTH_DENIED' if code in (401, 403) else 'RATE_LIMIT' if code == 429 else 'HTTP_STATUS_REVIEW'
        raise PaperInspectionError('PAPER_INSPECTION_' + reason) from None
    except PaperInspectionError:
        raise
    except (TimeoutError, URLError, OSError):
        raise PaperInspectionError('PAPER_INSPECTION_TRANSPORT_UNAVAILABLE') from None
    except Exception:
        raise PaperInspectionError('PAPER_INSPECTION_RESPONSE_INVALID') from None
    finally:
        _lock.release()
