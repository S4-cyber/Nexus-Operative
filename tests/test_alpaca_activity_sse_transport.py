"""Single-attempt injected tests for the bounded Activity SSE transport."""
import io
import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

import pytest

from nexus import alpaca_activity_sse_transport as subject
from nexus import alpaca_activity_sse_wire_review as wire_review
from nexus.alpaca_activity_sse_review import _event
from nexus.alpaca_paper_contract import canonical


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
CURSOR = '01K00000000000000000000001'
KEY = 'fixture_key_only_123'
SECRET = 'fixture_secret_only_123'


def payload(suffix=''):
    rows = [
        _event(CURSOR, '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event('01K00000000000000000000002',
            '22222222-2222-2222-2222-222222222222', 'FEE'),
    ]
    return (':heartbeat\n\n' + ''.join('data: ' + canonical(row) + '\n\n'
        for row in rows) + suffix).encode()


class Response:
    def __init__(self, raw=None, *, url=None, status=200, headers=None,
            chunk_size=79):
        self.body = io.BytesIO(payload() if raw is None else raw)
        self.url = url
        self.status = status
        self.headers = ({'Content-Type': 'text/event-stream; charset=utf-8'}
            if headers is None else headers)
        self.chunk_size = chunk_size
        self.closed = False
        self.reads = 0
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def geturl(self): return self.url
    def read(self, size):
        self.reads += 1
        return self.body.read(min(size, self.chunk_size))


class Opener:
    def __init__(self, value=None):
        self.value = Response() if value is None else value
        self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.value, BaseException):
            raise self.value
        if self.value.url is None:
            self.value.url = request.full_url
        return self.value


def collect(opener=None, **changes):
    options = dict(expected_account_id=ACCOUNT, since_id=CURSOR,
        confirm_read_only_paper=True, opener=opener or Opener(),
        wall_clock=lambda: 1000, elapsed_clock=lambda: 50,
        permission_check=lambda: True,
        emergency_stop_check=lambda: False,
        credential_check=lambda: True)
    options.update(changes)
    return subject.collect_once(KEY, SECRET, **options)


def test_single_get_binds_target_headers_and_returns_only_aggregate_review():
    response = Response(chunk_size=61); opener = Opener(response)
    result = collect(opener)
    assert result['mode'] == 'EXPLICIT_ALPACA_ACTIVITY_SSE_TRANSPORT'
    assert result['source'] == 'INJECTED_UNATTESTED'
    assert result['single_get_attempt'] and result['bounded_segment_complete']
    assert result['events_reviewed'] == 2 and result['heartbeat_comments'] == 1
    assert not result['automatic_retry'] and not result['cursor_written']
    assert len(opener.requests) == 1 and response.closed and response.reads > 1
    request, timeout = opener.requests[0]
    assert request.full_url == wire_review.activity_url(CURSOR)
    assert request.get_method() == 'GET' and request.data is None
    assert request.get_header('Apca-api-key-id') == KEY
    assert request.get_header('Apca-api-secret-key') == SECRET
    assert request.get_header('Accept') == 'text/event-stream'
    assert request.get_header('Accept-encoding') == 'identity'
    assert 0 < timeout <= subject.REQUEST_TIMEOUT
    encoded = json.dumps(result)
    assert all(value not in encoded for value in (ACCOUNT, CURSOR, KEY, SECRET,
        '11111111', '22222222'))


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_confirmation_is_strict_and_network_silent(confirmation):
    opener = Opener()
    with pytest.raises(subject.ActivitySSETransportError,
            match='EXPLICIT_AUTHORIZATION_REQUIRED'):
        collect(opener, confirm_read_only_paper=confirmation)
    assert not opener.requests


@pytest.mark.parametrize('credential', ['short', 'a' * 257,
    'a' * 20 + '\r\nX:1', 'é' * 20, None, 1])
def test_credentials_are_strict_and_never_echoed(credential):
    opener = Opener()
    with pytest.raises(subject.ActivitySSETransportError,
            match='CREDENTIAL_FORMAT_INVALID') as caught:
        subject.collect_once(credential, SECRET, expected_account_id=ACCOUNT,
            since_id=CURSOR, confirm_read_only_paper=True, opener=opener)
    assert str(credential) not in str(caught.value) and not opener.requests


def test_account_identity_and_required_safety_checks_fail_before_io():
    for changes, code in (({'expected_account_id': 'bad'}, 'ACCOUNT_ID_INVALID'),
            ({'permission_check': None}, 'CHECK_INVALID'),
            ({'emergency_stop_check': None}, 'CHECK_INVALID'),
            ({'credential_check': None}, 'CHECK_INVALID')):
        opener = Opener()
        with pytest.raises(subject.ActivitySSETransportError, match=code):
            collect(opener, **changes)
        assert not opener.requests


def test_default_opener_disables_proxies_and_redirects(monkeypatch):
    opener = Opener(); handlers = []
    monkeypatch.setattr(subject, 'build_opener',
        lambda *items: handlers.extend(items) or opener)
    result = subject.collect_once(KEY, SECRET, expected_account_id=ACCOUNT,
        since_id=CURSOR, confirm_read_only_paper=True,
        wall_clock=lambda: 1000, elapsed_clock=lambda: 50,
        permission_check=lambda: True,
        emergency_stop_check=lambda: False,
        credential_check=lambda: True)
    assert result['source'] == 'PAPER_HTTPS_READS'
    assert result['actual_provider_access'] is True
    assert len(handlers) == 2 and isinstance(handlers[0], ProxyHandler)
    assert handlers[0].proxies == {} and isinstance(handlers[1], subject._NoRedirect)
    stream = io.BytesIO(SECRET.encode())
    with pytest.raises(subject.ActivitySSETransportError,
            match='REDIRECT_DENIED'):
        handlers[1].redirect_request(opener.requests[0][0], stream, 302,
            SECRET, {}, wire_review.ENDPOINT)
    assert stream.closed


@pytest.mark.parametrize(('response','code'), [
    (Response(url='https://api.alpaca.markets/v2beta1/events/activities'),
        'RESPONSE_URL_CHANGED'),
    (Response(status=204), 'HTTP_STATUS_REVIEW'),
    (Response(headers={'Content-Type': 'application/json'}),
        'RESPONSE_METADATA_INVALID'),
    (Response(headers={'Content-Type': 'text/event-stream',
        'Content-Encoding': 'gzip'}), 'RESPONSE_METADATA_INVALID'),
    (Response(headers={'Content-Type': 'text/event-stream',
        'Content-Length': '-1'}), 'CONTENT_LENGTH_INVALID'),
    (Response(headers={'Content-Type': 'text/event-stream',
        'Content-Length': str(wire_review.MAX_BYTES + 1)}), 'BODY_CAP'),
])
def test_response_metadata_is_checked_before_body(response, code):
    opener = Opener(response)
    with pytest.raises((subject.ActivitySSETransportError,
            wire_review.ActivitySSEWireReviewError), match=code):
        collect(opener)
    assert response.closed and response.reads == 0


def test_declared_length_requires_exact_complete_body():
    raw = payload(); response = Response(raw,
        headers={'Content-Type': 'text/event-stream',
            'Content-Length': str(len(raw) + 1)})
    with pytest.raises(subject.ActivitySSETransportError,
            match='SEGMENT_INCOMPLETE'):
        collect(Opener(response))
    assert response.closed


@pytest.mark.parametrize(('failure','code'), [
    (TimeoutError(SECRET), 'TRANSPORT_UNAVAILABLE'),
    (URLError(SECRET), 'TRANSPORT_UNAVAILABLE'),
    (OSError(SECRET), 'TRANSPORT_UNAVAILABLE'),
])
def test_transport_failures_are_sanitized_and_never_retried(failure, code):
    opener = Opener(failure)
    with pytest.raises(subject.ActivitySSETransportError, match=code) as caught:
        collect(opener)
    assert SECRET not in str(caught.value) and len(opener.requests) == 1


def test_http_error_is_closed_unread_and_not_retried():
    body = io.BytesIO(SECRET.encode())
    error = HTTPError(wire_review.activity_url(CURSOR), 401, SECRET, {}, body)
    opener = Opener(error)
    with pytest.raises(subject.ActivitySSETransportError,
            match='HTTP_STATUS_REVIEW') as caught:
        collect(opener)
    assert body.closed and SECRET not in str(caught.value)
    assert len(opener.requests) == 1


@pytest.mark.parametrize(('option','value','code'), [
    ('permission_check', lambda: False, 'PERMISSION_REVOKED'),
    ('emergency_stop_check', lambda: True, 'EMERGENCY_STOP_ACTIVE'),
    ('credential_check', lambda: False, 'CREDENTIALS_CHANGED'),
])
def test_initial_safety_vetoes_are_network_silent(option, value, code):
    opener = Opener()
    with pytest.raises(subject.ActivitySSETransportError, match=code):
        collect(opener, **{option: value})
    assert not opener.requests


@pytest.mark.parametrize(('failure','code'), [
    ('permission', 'PERMISSION_REVOKED'),
    ('stop', 'EMERGENCY_STOP_ACTIVE'),
    ('credential', 'CREDENTIALS_CHANGED'),
])
def test_midstream_safety_veto_discards_partial_segment(failure, code):
    response = Response(chunk_size=17); opener = Opener(response)
    state = {'calls': 0}
    def permission():
        state['calls'] += 1
        return not (failure == 'permission' and state['calls'] >= 5)
    def stop(): return failure == 'stop' and state['calls'] >= 5
    def credential(): return not (failure == 'credential' and state['calls'] >= 5)
    with pytest.raises(subject.ActivitySSETransportError, match=code):
        collect(opener, permission_check=permission,
            emergency_stop_check=stop, credential_check=credential)
    assert len(opener.requests) == 1 and response.closed


def test_malformed_or_incomplete_body_is_discarded_with_fixed_code():
    for raw, code in ((b'data: {}\n', 'SEGMENT_INCOMPLETE'),
            (payload().replace(b'text-never-present', b'x'), None),
            (b'data: {}\n\ndata: {}\n\n', 'WIRE_REVIEW_FAILED')):
        if code is None:
            continue
        response = Response(raw)
        with pytest.raises(subject.ActivitySSETransportError, match=code):
            collect(Opener(response))
        assert response.closed


def test_size_and_chunk_caps_fail_closed(monkeypatch):
    monkeypatch.setattr(wire_review, 'MAX_BYTES', 100)
    response = Response(payload(), chunk_size=100)
    with pytest.raises(subject.ActivitySSETransportError, match='BODY_CAP'):
        collect(Opener(response))
    assert response.closed
    monkeypatch.setattr(wire_review, 'MAX_BYTES', 4 * 1024 * 1024)
    monkeypatch.setattr(wire_review, 'MAX_CHUNKS', 2)
    response = Response(payload(), chunk_size=1)
    with pytest.raises(subject.ActivitySSETransportError, match='CHUNK_CAP'):
        collect(Opener(response))
    assert response.closed


def test_clock_regression_discontinuity_and_budget_are_fail_closed():
    for wall_values, elapsed_values, code in (
            ([1000, 999], [50, 49], 'CLOCK_REGRESSION'),
            ([1000, 1000], [50, 53], 'CLOCK_DISCONTINUITY'),
            ([1000, 1020], [50, 70], 'TIME_BUDGET')):
        wall = iter(wall_values); elapsed = iter(elapsed_values)
        response = Response(chunk_size=1)
        with pytest.raises(subject.ActivitySSETransportError, match=code):
            collect(Opener(response), wall_clock=lambda: next(wall),
                elapsed_clock=lambda: next(elapsed))
        assert response.closed


def test_fixture_is_deterministic_aggregate_only_and_non_authoritative():
    result = subject.fixture_report()
    assert result == subject.fixture_report() and result['passed']
    assert all(result[key] for key in ('exact_single_get',
        'fixed_activity_target', 'fixed_stream_headers',
        'bounded_segment_reviewed', 'injected_response_closed',
        'no_identifier_or_credential_echo', 'no_automatic_continuation',
        'output_never_authoritative'))
    assert result['injected_get_requests'] == 1
    assert all(result[key] is False for key in ('actual_provider_access',
        'actual_vault_access', 'ready_for_routing', 'live_orders_enabled',
        'money_movement_enabled', 'release_approved'))
