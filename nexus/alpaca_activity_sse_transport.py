"""Explicit single-attempt paper Activity SSE transport.

The callable in this module opens only the fixed Alpaca paper Activity SSE
``since_id`` target and returns the aggregate result of the existing strict
wire review.  It never retries, reconnects, advances a cursor, stores a
credential, applies a ledger entry, or submits an order.
"""
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from nexus import alpaca_activity_sse_wire_review as wire


POLICY = 'nexus.explicit-alpaca-paper-activity-sse-transport.v1'
REQUEST_TIMEOUT = 4
TOTAL_SECONDS = 20
READ_SIZE = 64 * 1024
MIN_DATA_FRAMES = 2
_CREDENTIAL = re.compile(r'[A-Za-z0-9_-]{16,256}')


class ActivitySSETransportError(ValueError):
    """Fixed diagnostics only; never echo credentials or response bytes."""


def _fail(code):
    raise ActivitySSETransportError(
        'ALPACA_ACTIVITY_SSE_TRANSPORT_' + code) from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        _fail('REDIRECT_DENIED')


def _complete_segment(body, content_length):
    if content_length is not None:
        return len(body) == content_length
    normalized = bytes(body).replace(b'\r\n', b'\n')
    if not normalized.endswith(b'\n\n'):
        return False
    return sum(line.startswith(b'data:') for line in normalized.splitlines()) >= MIN_DATA_FRAMES


def _headers(response):
    try:
        content_type = response.headers.get('Content-Type', '')
        encoding = response.headers.get('Content-Encoding', 'identity')
        length = response.headers.get('Content-Length')
    except (AttributeError, TypeError):
        _fail('HEADERS_INVALID')
    if type(content_type) is not str or type(encoding) is not str:
        _fail('HEADERS_INVALID')
    parsed_length = None
    if length is not None:
        if type(length) is not str or re.fullmatch(r'[0-9]{1,8}', length) is None:
            _fail('CONTENT_LENGTH_INVALID')
        parsed_length = int(length)
        if not 1 <= parsed_length <= wire.MAX_BYTES:
            _fail('BODY_CAP')
    return {
        'Content-Type': content_type,
        'Content-Encoding': encoding,
    }, parsed_length


def _account_id(value):
    if type(value) is not str or len(value) != 36:
        _fail('ACCOUNT_ID_INVALID')
    try:
        normalized = str(UUID(value))
    except ValueError:
        _fail('ACCOUNT_ID_INVALID')
    if normalized != value:
        _fail('ACCOUNT_ID_INVALID')
    return normalized


def collect_once(api_key, api_secret, *, expected_account_id, since_id,
        confirm_read_only_paper=False, opener=None, wall_clock=time.time,
        elapsed_clock=time.monotonic, permission_check=None,
        emergency_stop_check=None, credential_check=None):
    """Read one bounded finite segment and return aggregate review evidence.

    ``opener`` is an injected test seam.  Without it, proxies are disabled and
    redirects are denied.  The independent checks are sampled before and after
    opening, each read, and the final review.  Any non-``True`` permission or
    credential result, or any non-``False`` emergency-stop result, fails closed.
    """
    if confirm_read_only_paper is not True:
        _fail('EXPLICIT_AUTHORIZATION_REQUIRED')
    if any(type(value) is not str or _CREDENTIAL.fullmatch(value) is None
            for value in (api_key, api_secret)):
        _fail('CREDENTIAL_FORMAT_INVALID')
    account_id = _account_id(expected_account_id)
    target = wire.activity_url(since_id)
    if not all(callable(item) for item in (wall_clock, elapsed_clock,
            permission_check, emergency_stop_check, credential_check)):
        _fail('CHECK_INVALID')
    injected = opener is not None
    reader = opener or build_opener(ProxyHandler({}), _NoRedirect())
    started = previous = None

    def checkpoint():
        nonlocal started, previous
        try:
            if permission_check() is not True:
                _fail('PERMISSION_REVOKED')
            if emergency_stop_check() is not False:
                _fail('EMERGENCY_STOP_ACTIVE')
            if credential_check() is not True:
                _fail('CREDENTIALS_CHANGED')
            current = (wall_clock(), elapsed_clock())
        except ActivitySSETransportError:
            raise
        except Exception:
            _fail('CHECK_FAILED')
        if any(type(value) not in (int, float) or not math.isfinite(value) or
                not 0 <= value <= 1e12 for value in current):
            _fail('CLOCK_INVALID')
        if started is None:
            started = current
        if previous is not None and any(now < prior
                for now, prior in zip(current, previous)):
            _fail('CLOCK_REGRESSION')
        if abs((current[0] - started[0]) -
                (current[1] - started[1])) > 2:
            _fail('CLOCK_DISCONTINUITY')
        elapsed = current[1] - started[1]
        if elapsed >= TOTAL_SECONDS:
            _fail('TIME_BUDGET')
        previous = current
        return max(0.001, min(REQUEST_TIMEOUT, TOTAL_SECONDS - elapsed))

    timeout = checkpoint()
    request = Request(target, method='GET', headers={
        'APCA-API-KEY-ID': api_key,
        'APCA-API-SECRET-KEY': api_secret,
        'Accept': 'text/event-stream',
        'Accept-Encoding': 'identity',
        'Cache-Control': 'no-cache',
        'User-Agent': 'NexusOperativePaperActivitySSE/0.x',
    })
    chunks = []
    try:
        with reader.open(request, timeout=timeout) as response:
            checkpoint()
            if response.geturl() != target:
                _fail('RESPONSE_URL_CHANGED')
            if type(response.status) is not int or response.status != 200:
                _fail('HTTP_STATUS_REVIEW')
            response_headers, content_length = _headers(response)
            # Reuse the strict metadata grammar before reading any body bytes.
            try:
                wire._headers(response_headers)
            except wire.ActivitySSEWireReviewError:
                _fail('RESPONSE_METADATA_INVALID')
            read = getattr(response, 'read1', None)
            if not callable(read):
                read = getattr(response, 'read', None)
            if not callable(read):
                _fail('BODY_INVALID')
            total = 0
            body = bytearray()
            while not _complete_segment(body, content_length):
                if len(chunks) >= wire.MAX_CHUNKS:
                    _fail('CHUNK_CAP')
                checkpoint()
                chunk = read(min(READ_SIZE, wire.MAX_BYTES + 1 - total))
                checkpoint()
                if type(chunk) is not bytes:
                    _fail('BODY_INVALID')
                if not chunk:
                    _fail('SEGMENT_INCOMPLETE')
                total += len(chunk)
                if total > wire.MAX_BYTES:
                    _fail('BODY_CAP')
                chunks.append(chunk)
                body.extend(chunk)
            if content_length is not None and total != content_length:
                _fail('CONTENT_LENGTH_CHANGED')
            checkpoint()
            reviewed = wire.review(chunks,
                expected_account_id=account_id, since_id=since_id,
                response_url=response.geturl(), response_status=response.status,
                response_headers=response_headers)
            checkpoint()
    except ActivitySSETransportError:
        raise
    except wire.ActivitySSEWireReviewError:
        _fail('WIRE_REVIEW_FAILED')
    except HTTPError as error:
        error.close()
        _fail('HTTP_STATUS_REVIEW')
    except (TimeoutError, URLError, OSError):
        _fail('TRANSPORT_UNAVAILABLE')
    except Exception:
        _fail('TRANSPORT_FAILED')
    return dict(reviewed, mode='EXPLICIT_ALPACA_ACTIVITY_SSE_TRANSPORT',
        transport_policy=POLICY,
        source='INJECTED_UNATTESTED' if injected else 'PAPER_HTTPS_READS',
        explicit_authorization=True, single_get_attempt=True,
        bounded_segment_complete=True, stream_opened=True,
        actual_provider_access=not injected,
        actual_stream_connection=not injected,
        actual_vault_access=False, automatic_retry=False,
        automatic_reconnect_performed=False, cursor_written=False,
        ledger_applied=False, ready_for_routing=False,
        live_orders_enabled=False, money_movement_enabled=False,
        release_approved=False,
        limitations=reviewed['limitations'] +
            ' The transport performed one explicitly authorized bounded GET; '
            'it did not retry, reconnect, back off, write a cursor or grant '
            'trading, funding, routing or release authority.')


def fixture_report():
    """Injected aggregate-only transport fixture with zero provider access."""
    import io
    import json
    from nexus.alpaca_activity_sse_review import _event
    from nexus.alpaca_paper_contract import canonical

    account = '01234567-89ab-cdef-0123-456789abcdef'
    cursor = '01K00000000000000000000001'
    rows = [
        _event(cursor, '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event('01K00000000000000000000002',
            '22222222-2222-2222-2222-222222222222', 'FEE'),
    ]
    raw = (':heartbeat\n\n' + ''.join('data: ' + canonical(row) + '\n\n'
        for row in rows)).encode()

    class Response:
        status = 200
        headers = {'Content-Type': 'text/event-stream; charset=utf-8'}
        def __init__(self, target):
            self.target = target
            self.body = io.BytesIO(raw)
            self.closed = False
        def __enter__(self): return self
        def __exit__(self, *args): self.closed = True
        def geturl(self): return self.target
        def read(self, size): return self.body.read(min(size, 73))

    class Opener:
        def __init__(self): self.requests = []; self.response = None
        def open(self, request, timeout):
            self.requests.append((request, timeout))
            self.response = Response(request.full_url)
            return self.response

    opener = Opener()
    result = collect_once('fixture_key_only_123', 'fixture_secret_only_123',
        expected_account_id=account, since_id=cursor,
        confirm_read_only_paper=True, opener=opener,
        wall_clock=lambda: 1000, elapsed_clock=lambda: 50,
        permission_check=lambda: True,
        emergency_stop_check=lambda: False,
        credential_check=lambda: True)
    request, timeout = opener.requests[0]
    encoded = json.dumps(result, sort_keys=True)
    checks = dict(
        exact_single_get=request.get_method() == 'GET' and request.data is None
            and len(opener.requests) == 1,
        fixed_activity_target=request.full_url == wire.activity_url(cursor),
        fixed_stream_headers=request.get_header('Accept') == 'text/event-stream'
            and request.get_header('Accept-encoding') == 'identity',
        bounded_segment_reviewed=result['bounded_segment_complete']
            and result['events_reviewed'] == 2 and 0 < timeout <= REQUEST_TIMEOUT,
        injected_response_closed=opener.response.closed,
        no_identifier_or_credential_echo=all(token not in encoded for token in
            (account, cursor, 'fixture_key_only_123', 'fixture_secret_only_123',
             '11111111', '22222222')),
        no_automatic_continuation=not result['automatic_retry']
            and not result['automatic_reconnect_performed']
            and not result['cursor_written'],
        output_never_authoritative=all(result[key] is False for key in
            ('actual_provider_access', 'actual_vault_access', 'ledger_applied',
             'ready_for_routing', 'live_orders_enabled',
             'money_movement_enabled', 'release_approved')),
    )
    return dict(source='SYNTHETIC_INJECTED_ACTIVITY_SSE_TRANSPORT_FIXTURE',
        passed=all(checks.values()), **checks,
        injected_get_requests=len(opener.requests),
        actual_provider_access=False, actual_vault_access=False,
        actual_stream_connection=False,
        ready_for_routing=False, live_orders_enabled=False,
        money_movement_enabled=False, release_approved=False)
