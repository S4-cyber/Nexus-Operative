"""Bounded GET-only paper reader shared by explicit inspection and recovery.

No credential storage, environment lookup, order methods or retries.
Durable local pacing is optional and requires an explicit account/path journal.
Injected transports are trusted test seams and never attest a broker account.
"""
import json
from decimal import Decimal, localcontext
import math
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import UUID

from nexus.alpaca_paper_contract import number, text, sha

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


def _collection_rows(rows, account_id):
    if not isinstance(rows, list):
        _fail('PAPER_INSPECTION_COLLECTION_INVALID')
    if len(rows) > MAX_ITEMS:
        _fail('PAPER_INSPECTION_COLLECTION_CAP_REVIEW')
    if not all(isinstance(item, dict) for item in rows):
        _fail('PAPER_INSPECTION_COLLECTION_INVALID')
    for item in rows:
        if item.get('account_id') is not None and _uuid(item['account_id']) != account_id:
            _fail('PAPER_INSPECTION_COLLECTION_ACCOUNT_DISAGREEMENT')


def _position_inventory(positions):
    reasons = []
    available = '0'; total_btc = '0'; inventory_supported = True
    seen_btc = False
    for position in positions:
        if position.get('symbol') not in ('BTC/USD', 'BTCUSD') or position.get('asset_class') != 'crypto' or position.get('side') != 'long':
            reasons.append('UNSUPPORTED_POSITION_REVIEW')
            inventory_supported = False
            continue
        if seen_btc:
            _fail('PAPER_INSPECTION_DUPLICATE_BTC_POSITION')
        seen_btc = True
        quantity = number(position.get('qty'), zero=True)
        total_btc = text(quantity)
        if position.get('qty_available') is None:
            available = None
            reasons.append('POSITION_AVAILABILITY_MISSING')
        else:
            qty_available = number(position['qty_available'], zero=True)
            if qty_available > quantity:
                _fail('PAPER_INSPECTION_POSITION_AVAILABILITY_INVALID')
            available = text(qty_available)
    if not inventory_supported:
        total_btc = available = None
    unavailable = None
    if total_btc is not None and available is not None:
        with localcontext() as context:
            context.prec = 80
            unavailable = text(number(total_btc, zero=True)-number(available, zero=True))
    return total_btc, available, unavailable, inventory_supported, reasons


def _report(before, positions, orders, after, expected, source):
    first, last = _account(before), _account(after)
    if first['id'] != last['id'] or expected is not None and first['id'] != expected:
        _fail('PAPER_INSPECTION_ACCOUNT_ID_CHANGED')
    if first != last:
        _fail('PAPER_INSPECTION_ACCOUNT_CHANGED_DURING_READ')
    _collection_rows(positions, first['id'])
    _collection_rows(orders, first['id'])
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
    total_btc, available, unavailable, inventory_supported, position_reasons = _position_inventory(positions)
    reasons.extend(position_reasons)
    if orders:
        reasons.append('OPEN_ORDER_RESERVATIONS_REQUIRE_RECONCILIATION')
    cash = first['cash'] if first['currency'] == 'USD' else None
    buying = first['non_marginable_buying_power'] if first['currency'] == 'USD' else None
    ceiling = None if cash is None or buying is None else text(min(number(cash, zero=True), number(buying, zero=True)))
    inventory = dict(cash=cash, non_marginable_buying_power=buying, btc_total=total_btc,
        btc_available=available, btc_unavailable_difference=unavailable, scope_supported=inventory_supported)
    return dict(mode='READ_ONLY_PAPER_INSPECTION', source=source, account_id=first['id'],
                checks='REVIEW_REQUIRED' if reasons else 'OBSERVED_FIELDS_COVERED',
                review_reasons=sorted(set(reasons)), observed_usd_ceiling=ceiling,
                observed_btc_available=available, observed_inventory=inventory,
                account_fields_sha256=sha(first), reported_inventory_sha256=sha(inventory),
                fingerprint_scope='NORMALIZED_ACCOUNT_AND_REPORTED_INVENTORY_ONLY_NOT_RAW_SOURCE_ATTESTATION',
                position_count=len(positions), open_order_count=len(orders),
                sequential_account_fields_stable=True, atomic_snapshot=False,
                broker_account_reconciled=False, spendable_funds_verified=False,
                ready_for_routing=False, ledger_applied=False, broker_connected=False,
                live_orders_enabled=False, automatic_retry=False,
                limitations='Sequential observations only. Matching account fields do not prove positions/orders were atomic or complete. No asset entitlement, fees, settled funds, journal reconciliation or trading permission is verified.')


class _ReadSession:
    """Private fixed-path GET session shared by account inspection and recovery."""
    def __init__(self, api_key, api_secret, *, confirm_read_only_paper=False,
                 opener=None, wall_clock=time.time, elapsed_clock=time.monotonic,
                 permission_check=lambda: True, pacing=None, expected_account_id=None):
        if confirm_read_only_paper is not True:
            _fail('PAPER_INSPECTION_EXPLICIT_AUTHORIZATION_REQUIRED')
        for value in (api_key, api_secret):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', value):
                _fail('PAPER_INSPECTION_CREDENTIAL_FORMAT_INVALID')
        if pacing is not None:
            from nexus.paper_read_pacing import PaperReadPacing
            if type(pacing) is not PaperReadPacing or expected_account_id is None or pacing.account != _uuid(expected_account_id):
                _fail('PAPER_INSPECTION_PACING_ACCOUNT_OR_TYPE')
            pacing.inspect()  # Corrupt/foreign history blocks all IO; missing stays missing.
        self.pacing = pacing
        self.pacing_owner = None
        self.pacing_token = None
        self.pacing_finished = False
        self.key, self.secret = api_key, api_secret
        self.injected = opener is not None
        self.reader = opener
        self.wall_clock, self.elapsed_clock = wall_clock, elapsed_clock
        self.permission_check = permission_check
        self.start = self.previous = None
        self.requests = 0
        self.active = False
        self.entered = False

    def __enter__(self):
        if self.entered:
            _fail('PAPER_INSPECTION_SESSION_REUSE_DENIED')
        if not _lock.acquire(blocking=False):
            _fail('PAPER_INSPECTION_BUSY')
        try:
            self.checkpoint()
            if self.pacing is not None:
                self.pacing_owner = self.pacing.ownership()
                self.pacing_owner.__enter__()
                self.pacing_token = self.pacing.begin(admission=self.evaluated_at, ownership=self.pacing_owner)
                self.checkpoint()  # A lost/revoked acknowledgment never releases a reservation.
            if self.reader is None:
                self.reader = build_opener(ProxyHandler({}), _NoRedirect())
            self.active = self.entered = True
            return self
        except BaseException as exc:
            try:
                if self.pacing_owner is not None: self.pacing_owner.__exit__(None, None, None)
            finally:
                _lock.release()
            if isinstance(exc, Exception):
                _sanitize(exc)
            raise

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.checkpoint()
                self._pacing_result(status_code=200)
            if kind is not None and issubclass(kind, Exception):
                _sanitize(value)
        except Exception as exc:
            _sanitize(exc)
        finally:
            self.active = False
            try:
                if self.pacing_owner is not None: self.pacing_owner.__exit__(None, None, None)
            finally:
                _lock.release()

    def _pacing_result(self, **outcome):
        if self.pacing is not None and not self.pacing_finished:
            try:
                self.pacing.finish(self.pacing_token, admission=self.evaluated_at, **outcome)
            except Exception as exc:
                _sanitize(exc)  # Storage faults must never enter the HTTP retry/outcome branch.
            self.pacing_finished = True
            self.checkpoint()

    def checkpoint(self):
        try:
            if self.pacing_owner is not None:
                self.pacing_owner.validate()
            if self.permission_check() is not True:
                _fail('PAPER_INSPECTION_PERMISSION_REVOKED')
            current = (self.wall_clock(), self.elapsed_clock())
            if any(type(v) not in (int, float) or not 0 <= v <= 1e12 or not math.isfinite(v) for v in current):
                _fail('PAPER_INSPECTION_CLOCK_INVALID')
            if self.start is None:
                self.start = current
            if self.previous is not None and any(a < b for a, b in zip(current, self.previous)):
                _fail('PAPER_INSPECTION_CLOCK_REGRESSION')
            if abs((current[0] - self.start[0]) - (current[1] - self.start[1])) > 2:
                _fail('PAPER_INSPECTION_CLOCK_DISCONTINUITY')
            if current[1] - self.start[1] >= TOTAL_SECONDS:
                _fail('PAPER_INSPECTION_TIME_BUDGET')
            self.previous = current
            return max(0.001, min(REQUEST_TIMEOUT, TOTAL_SECONDS - (current[1] - self.start[1])))
        except Exception as exc:
            _sanitize(exc)

    def evaluated_at(self):
        self.checkpoint()
        return self.previous[0]

    def get(self, url, *, lookup=False):
        if not self.active:
            _fail('PAPER_INSPECTION_SESSION_NOT_ACTIVE')
        if self.pacing_finished:
            _fail('PAPER_INSPECTION_PACING_SESSION_FINISHED')
        from nexus.broker_lookup_policy import LOOKUP_URL
        lookup_path = isinstance(url, str) and re.fullmatch(re.escape(LOOKUP_URL) + r'\?client_order_id=[A-Za-z0-9_-]{1,64}', url)
        from nexus.alpaca_activity_collection import is_activity_url
        if lookup and not lookup_path or not lookup and url not in URLS and not is_activity_url(url):
            _fail('PAPER_INSPECTION_PATH_DENIED')
        if self.requests >= 4:
            _fail('PAPER_INSPECTION_REQUEST_CAP')
        timeout = self.checkpoint()
        request = Request(url, method='GET', headers={
            'APCA-API-KEY-ID': self.key, 'APCA-API-SECRET-KEY': self.secret,
            'Accept': 'application/json', 'Accept-Encoding': 'identity',
            'User-Agent': 'NexusOperativePaperInspection/0.x'})
        self.requests += 1
        try:
            with self.reader.open(request, timeout=timeout) as response:
                self.checkpoint()
                if response.geturl() != url:
                    _fail('PAPER_INSPECTION_RESPONSE_URL_CHANGED')
                if response.status != 200:
                    outcome = self._outcome(response.status, response.headers)
                    self._pacing_result(**outcome)
                    if lookup:
                        return outcome
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
                    self.checkpoint()
                    chunk = response.read(min(65536, MAX_BODY + 1 - len(body)))
                    self.checkpoint()
                    if not isinstance(chunk, bytes):
                        _fail('PAPER_INSPECTION_BODY_INVALID')
                    if len(body) + len(chunk) > MAX_BODY:
                        _fail('PAPER_INSPECTION_BODY_CAP')
                    body.extend(chunk)
                    if not chunk:
                        break
                if length is not None and len(body) != int(length):
                    _fail('PAPER_INSPECTION_BODY_LENGTH_CHANGED')
                value = json.loads(body.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_nonfinite, parse_float=Decimal)
                return dict(status_code=200, response=value) if lookup else value
        except HTTPError as exc:
            try:
                self.checkpoint()
                if exc.geturl() != url:
                    _fail('PAPER_INSPECTION_RESPONSE_URL_CHANGED')
                outcome = self._outcome(exc.code, exc.headers)
                self._pacing_result(**outcome)
                if lookup:
                    return outcome
            finally:
                exc.close()
            _sanitize(exc)
        except (TimeoutError, URLError, OSError) as exc:
            self.checkpoint()
            timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError)
            outcome = dict(transport_error='TIMEOUT' if timed_out else 'NETWORK_ERROR')
            self._pacing_result(**outcome)
            if lookup:
                return outcome
            _sanitize(exc)

    def _outcome(self, code, headers):
        if type(code) is not int or not 100 <= code <= 599 or 300 <= code <= 399:
            _fail('PAPER_INSPECTION_HTTP_STATUS_REVIEW')
        # Never read error bodies. Duplicate/oversized headers become review,
        # never an abbreviated cooldown or retained private header content.
        values = headers.get_all('Retry-After') if hasattr(headers, 'get_all') else [headers.get('Retry-After')]
        value = values[0] if values and len(values) == 1 else None
        if values and len(values) > 1 or value is not None and (not isinstance(value, str) or len(value) > 128):
            value = 'INVALID'
        return dict(status_code=code, retry_after=value)


def _sanitize(exc):
    if isinstance(exc, PaperInspectionError):
        raise exc
    if isinstance(exc, HTTPError):
        reason = 'AUTH_DENIED' if exc.code in (401, 403) else 'RATE_LIMIT' if exc.code == 429 else 'HTTP_STATUS_REVIEW'
        raise PaperInspectionError('PAPER_INSPECTION_' + reason) from None
    if isinstance(exc, (TimeoutError, URLError, OSError)):
        raise PaperInspectionError('PAPER_INSPECTION_TRANSPORT_UNAVAILABLE') from None
    raise PaperInspectionError('PAPER_INSPECTION_RESPONSE_INVALID') from None


def inspect_account(api_key, api_secret, *, confirm_read_only_paper=False,
                    expected_account_id=None, opener=None, wall_clock=time.time,
                    elapsed_clock=time.monotonic, permission_check=lambda: True, pacing=None):
    """Four bounded account GETs after explicit authorization; no order methods."""
    # Validate authorization and credentials before even inspecting caller scope.
    session = _ReadSession(api_key, api_secret, confirm_read_only_paper=confirm_read_only_paper,
        opener=opener, wall_clock=wall_clock, elapsed_clock=elapsed_clock, permission_check=permission_check,
        pacing=pacing, expected_account_id=expected_account_id)
    expected = None if expected_account_id is None else _uuid(expected_account_id)
    with session:
        values = []
        for url in URLS:
            value = session.get(url)
            if url == URLS[0]:
                normalized = _account(value)
                if expected is not None and normalized['id'] != expected:
                    _fail('PAPER_INSPECTION_ACCOUNT_ID_CHANGED')
            if url in URLS[1:3]:
                _collection_rows(value, normalized['id'])
                if url == URLS[1]: _position_inventory(value)
            values.append(value)
        result = _report(*values, expected, 'INJECTED_UNATTESTED' if session.injected else 'PAPER_HTTPS_READS')
        completed = session.evaluated_at()
        result['read_timing'] = dict(started_at=session.start[0], completed_at=completed,
                                    elapsed_seconds=session.previous[1]-session.start[1])
        result['observation_sha256'] = sha(dict(profile='nexus.paper-account-observation.v1',
            account_fields_sha256=result['account_fields_sha256'], reported_inventory_sha256=result['reported_inventory_sha256'],
            position_count=result['position_count'], open_order_count=result['open_order_count'],
            source=result['source'], read_timing=result['read_timing']))
        result['observation_digest_scope'] = 'NORMALIZED_ACCOUNT_INVENTORY_COUNTS_SOURCE_LABEL_AND_SAMPLED_TIMING_ONLY'
        from nexus.portable_paper_observation import create
        result['portable_observation'] = create(result)
        return result
