"""Injected responses only; no provider, credentials, account or journal IO."""
import copy
import io
import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

import pytest

from nexus import alpaca_paper_inspection as module

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
OTHER = '11234567-89ab-cdef-0123-456789abcdef'
KEY = 'fixture_key_only_123'
SECRET = 'fixture_secret_only_123'


def account():
    return dict(id=ACCOUNT, currency='USD', status='ACTIVE', crypto_status='ACTIVE',
                account_blocked=False, trading_blocked=False, trade_suspended_by_user=False,
                cash='1000', non_marginable_buying_power='900', pending_transfer_in='0',
                pending_transfer_out='0', accrued_fees='0', buying_power='4000',
                account_number='PRIVATE_DO_NOT_RETURN', unrelated_private_field=SECRET)


def position(**changes):
    result = dict(symbol='BTC/USD', asset_class='crypto', side='long', qty='2', qty_available='1.9')
    result.update(changes)
    return result


class Response:
    def __init__(self, value, *, url=None, headers=None, raw=None, status=200):
        self.stream = io.BytesIO(json.dumps(value).encode() if raw is None else raw)
        self.url = url
        self.headers = {'Content-Type': 'application/json'} if headers is None else headers
        self.status = status
        self.closed = False
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def geturl(self):
        return self.url

    def read(self, size):
        assert 0 < size <= 65536
        self.reads += 1
        return self.stream.read(size)


class Opener:
    def __init__(self, values=None):
        self.values = values or [account(), [position()], [], account()]
        self.requests = []
        self.responses = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        item = self.values[len(self.requests) - 1]
        if isinstance(item, BaseException):
            raise item
        response = item if isinstance(item, Response) else Response(item)
        if response.url is None:
            response.url = request.full_url
        self.responses.append(response)
        return response


def inspect(opener=None, **kwargs):
    return module.inspect_account(KEY, SECRET, confirm_read_only_paper=True,
                                  opener=opener or Opener(), wall_clock=lambda: 1000,
                                  elapsed_clock=lambda: 50, **kwargs)


def test_observed_limits_are_allowlisted_not_authority_or_margin():
    opener = Opener()
    result = inspect(opener, expected_account_id=ACCOUNT)
    assert result['checks'] == 'OBSERVED_FIELDS_COVERED'
    assert result['observed_usd_ceiling'] == '900'
    assert result['observed_btc_available'] == '1.9'
    assert result['source'] == 'INJECTED_UNATTESTED'
    assert result['account_id'] == ACCOUNT
    for flag in ('broker_connected', 'spendable_funds_verified', 'broker_account_reconciled',
                 'ready_for_routing', 'ledger_applied', 'live_orders_enabled', 'automatic_retry', 'atomic_snapshot'):
        assert result[flag] is False
    serialized = json.dumps(result)
    assert KEY not in serialized and SECRET not in serialized and 'PRIVATE_DO_NOT_RETURN' not in serialized
    assert [r.full_url for r, _ in opener.requests] == list(module.URLS)
    for request, timeout in opener.requests:
        assert request.get_method() == 'GET' and request.data is None
        assert request.get_header('Apca-api-key-id') == KEY
        assert request.get_header('Apca-api-secret-key') == SECRET
        assert 0 < timeout <= 4
    assert all(r.closed for r in opener.responses)


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_authorization_is_strict_and_network_silent(confirmation):
    opener = Opener()
    with pytest.raises(module.PaperInspectionError, match='EXPLICIT_AUTHORIZATION_REQUIRED'):
        module.inspect_account(KEY, SECRET, confirm_read_only_paper=confirmation, opener=opener)
    assert not opener.requests


@pytest.mark.parametrize('bad', ['short', 'a' * 257, 'a' * 20 + '\r\nX:1', 'a' * 20 + ' ', None, 123, 'é' * 20],
                         ids=['short', 'long', 'crlf', 'space', 'none', 'number', 'unicode'])
def test_credentials_invalid_before_io(bad):
    opener = Opener()
    with pytest.raises(module.PaperInspectionError, match='CREDENTIAL_FORMAT_INVALID'):
        module.inspect_account(bad, SECRET, confirm_read_only_paper=True, opener=opener)
    assert not opener.requests


def test_default_opener_disables_proxies_and_installs_redirect_guard(monkeypatch):
    opener = Opener()
    handlers = []
    monkeypatch.setattr(module, 'build_opener', lambda *args: handlers.extend(args) or opener)
    result = module.inspect_account(KEY, SECRET, confirm_read_only_paper=True)
    assert result['source'] == 'PAPER_HTTPS_READS'
    assert len(handlers) == 2 and isinstance(handlers[0], ProxyHandler)
    assert handlers[0].proxies == {} and isinstance(handlers[1], module._NoRedirect)
    stream = io.BytesIO(b'private')
    with pytest.raises(module.PaperInspectionError, match='REDIRECT_DENIED'):
        handlers[1].redirect_request(opener.requests[0][0], stream, 302, 'secret', {}, module.URLS[0])
    assert stream.closed


@pytest.mark.parametrize('change', [
    {'url': 'https://api.alpaca.markets/v2/account'},
    {'url': module.URLS[0] + '?other=true'},
    {'status': 302},
    {'headers': {'Content-Type': 'text/html'}},
    {'headers': {'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}},
    {'headers': {'Content-Type': 'application/json', 'Content-Length': '1048577'}},
    {'headers': {'Content-Type': 'application/json', 'Content-Length': '-1'}},
    {'headers': {'Content-Type': 'application/json', 'Content-Length': '2'}},
    {'raw': b'{"id":1,"id":2}'}, {'raw': b'{"x":NaN}'}, {'raw': b'\xff'},
    {'raw': b'[' * 1500}, {'raw': b' ' * (module.MAX_BODY + 1)},
], ids=['live-host', 'changed-path', 'redirect-status', 'html', 'compressed', 'declared-cap',
        'bad-length', 'wrong-length', 'duplicate-key', 'nonfinite', 'utf8', 'depth', 'body-cap'])
def test_response_guards_close_and_do_not_follow_or_retry(change):
    response = Response(account(), **change)
    opener = Opener([response])
    with pytest.raises(module.PaperInspectionError):
        inspect(opener)
    assert len(opener.requests) == 1 and response.closed
    if 'url' in change or change.get('status') == 302:
        assert response.reads == 0


@pytest.mark.parametrize('code,reason', [(401, 'AUTH_DENIED'), (403, 'AUTH_DENIED'), (429, 'RATE_LIMIT'),
                                         (500, 'HTTP_STATUS_REVIEW'), (302, 'HTTP_STATUS_REVIEW')])
def test_http_failure_is_sanitized_closed_no_retry(code, reason):
    stream = io.BytesIO(SECRET.encode())
    error = HTTPError(module.URLS[0], code, SECRET, {}, stream)
    opener = Opener([error])
    with pytest.raises(module.PaperInspectionError, match=reason) as caught:
        inspect(opener)
    assert SECRET not in str(caught.value) and stream.closed and len(opener.requests) == 1


@pytest.mark.parametrize('failure', [TimeoutError(SECRET), URLError(SECRET), OSError(SECRET)])
def test_network_failure_is_sanitized_no_retry_and_lock_released(failure):
    opener = Opener([failure])
    with pytest.raises(module.PaperInspectionError, match='TRANSPORT_UNAVAILABLE') as caught:
        inspect(opener)
    assert SECRET not in str(caught.value) and len(opener.requests) == 1
    assert inspect()['checks'] == 'OBSERVED_FIELDS_COVERED'


@pytest.mark.parametrize('changes,reason', [
    ({'status': 'PAPER_ONLY'}, 'ACCOUNT_CURRENCY_OR_STATUS_REVIEW'),
    ({'crypto_status': None}, 'ACCOUNT_CURRENCY_OR_STATUS_REVIEW'),
    ({'currency': 'EUR'}, 'ACCOUNT_CURRENCY_OR_STATUS_REVIEW'),
    ({'account_blocked': True}, 'ACCOUNT_PERMISSION_FLAGS_REVIEW'),
    ({'trading_blocked': None}, 'ACCOUNT_PERMISSION_FLAGS_REVIEW'),
    ({'cash': None}, 'ACCOUNT_FUNDS_FIELDS_INCOMPLETE'),
    ({'non_marginable_buying_power': None}, 'ACCOUNT_FUNDS_FIELDS_INCOMPLETE'),
    ({'pending_transfer_out': '1'}, 'PENDING_TRANSFERS_OR_FEES_REVIEW'),
    ({'pending_transfer_in': '1'}, 'PENDING_TRANSFERS_OR_FEES_REVIEW'),
    ({'accrued_fees': '0.01'}, 'PENDING_TRANSFERS_OR_FEES_REVIEW'),
])
def test_incomplete_or_blocked_account_requires_review(changes, reason):
    raw = account(); raw.update(changes)
    result = inspect(Opener([raw, [], [], copy.deepcopy(raw)]))
    assert result['checks'] == 'REVIEW_REQUIRED' and reason in result['review_reasons']
    assert result['ready_for_routing'] is False


@pytest.mark.parametrize('changes', [{'cash': '-1'}, {'cash': 'NaN'}, {'cash': True},
                                     {'status': 'ACTIVE\nprivate'}, {'trading_blocked': 0}, {'id': SECRET}])
def test_invalid_account_never_becomes_a_pass(changes):
    raw = account(); raw.update(changes)
    with pytest.raises(module.PaperInspectionError) as caught:
        inspect(Opener([raw, [], [], raw]))
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize('changes,expected', [({'id': OTHER}, 'ACCOUNT_ID_CHANGED'),
                                             ({'cash': '999'}, 'ACCOUNT_CHANGED_DURING_READ'),
                                             ({'trading_blocked': True}, 'ACCOUNT_CHANGED_DURING_READ')])
def test_account_identity_and_cross_read_mutation_rejected(changes, expected):
    last = account(); last.update(changes)
    with pytest.raises(module.PaperInspectionError, match=expected):
        inspect(Opener([account(), [], [], last]))


def test_expected_account_binding_and_numerical_equivalence():
    with pytest.raises(module.PaperInspectionError, match='ACCOUNT_ID_CHANGED'):
        inspect(expected_account_id=OTHER)
    last = account(); last['cash'] = '1000.00'
    assert inspect(Opener([account(), [], [], last]))['checks'] == 'OBSERVED_FIELDS_COVERED'


@pytest.mark.parametrize('positions,reason', [([position(qty_available=None)], 'POSITION_AVAILABILITY_MISSING'),
                                            ([position(symbol='ETH/USD')], 'UNSUPPORTED_POSITION_REVIEW'),
                                            ([position(side='short')], 'UNSUPPORTED_POSITION_REVIEW')])
def test_position_availability_is_never_inferred(positions, reason):
    result = inspect(Opener([account(), positions, [], account()]))
    assert reason in result['review_reasons'] and result['checks'] == 'REVIEW_REQUIRED'
    if reason == 'POSITION_AVAILABILITY_MISSING':
        assert result['observed_btc_available'] is None


@pytest.mark.parametrize('positions', [[position(qty_available='3')], [position(), position()],
                                       [position(qty='-2')], [position(qty_available=True)]])
def test_invalid_or_duplicate_positions_fail(positions):
    with pytest.raises(module.PaperInspectionError):
        inspect(Opener([account(), positions, [], account()]))


def test_open_orders_require_reservation_review_not_assumed_free_cash():
    result = inspect(Opener([account(), [], [{'private': SECRET}], account()]))
    assert result['open_order_count'] == 1
    assert result['review_reasons'] == ['OPEN_ORDER_RESERVATIONS_REQUIRE_RECONCILIATION']
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('positions,orders', [([{}] * 129, []), ([], [{}] * 129), ({}, []), ([], {}), ([None], [])])
def test_collection_caps_and_shapes_fail_without_partial_success(positions, orders):
    with pytest.raises(module.PaperInspectionError):
        inspect(Opener([account(), positions, orders, account()]))


def test_permission_revoked_after_response_closes_and_discards():
    response = Response(account())
    opener = Opener([response])
    checks = iter([True, True, False])
    with pytest.raises(module.PaperInspectionError, match='PERMISSION_REVOKED'):
        inspect(opener, permission_check=lambda: next(checks))
    assert len(opener.requests) == 1 and response.closed and response.reads == 0


@pytest.mark.parametrize('wall,elapsed,reason', [([1000, 999], [50, 51], 'CLOCK_REGRESSION'),
    ([1000, 1010], [50, 51], 'CLOCK_DISCONTINUITY'), ([1000, 1020], [50, 70], 'TIME_BUDGET'),
    ([1000, 1001], [50, 49], 'CLOCK_REGRESSION'), ([float('nan')], [50], 'CLOCK_INVALID'),
    ([True], [50], 'CLOCK_INVALID'), ([1000], [10 ** 1000], 'CLOCK_INVALID')],
    ids=['wall-back', 'wall-jump', 'deadline', 'elapsed-back', 'nan', 'bool', 'huge'])
def test_clock_fences_before_network(wall, elapsed, reason):
    w, e = iter(wall), iter(elapsed)
    opener = Opener()
    with pytest.raises(module.PaperInspectionError, match=reason):
        module.inspect_account(KEY, SECRET, confirm_read_only_paper=True, opener=opener,
                               wall_clock=lambda: next(w), elapsed_clock=lambda: next(e))
    assert not opener.requests


def test_concurrent_inspection_is_rejected_without_queue_or_network():
    opener = Opener()
    assert module._lock.acquire(blocking=False)
    try:
        with pytest.raises(module.PaperInspectionError, match='BUSY'):
            inspect(opener)
        assert not opener.requests
    finally:
        module._lock.release()


def test_no_automatic_runtime_inspection_or_get_route():
    from nexus.app import app, status
    routes = [route for route in app.routes if route.path.endswith('/local-credentials/inspect')]
    assert len(routes) == 1 and routes[0].methods == {'POST'}
    assert status()['broker_connected'] is False


def test_wrong_bound_account_stops_before_positions_or_order_reads():
    opener = Opener()
    with pytest.raises(module.PaperInspectionError, match='ACCOUNT_ID_CHANGED'):
        inspect(opener, expected_account_id=OTHER)
    assert len(opener.requests) == 1 and opener.responses[0].closed


def test_unexpected_callback_failure_is_sanitized_and_unlocks():
    def broken():
        raise RuntimeError(SECRET)
    with pytest.raises(module.PaperInspectionError, match='RESPONSE_INVALID') as caught:
        inspect(permission_check=broken)
    assert SECRET not in str(caught.value)
    assert inspect()['checks'] == 'OBSERVED_FIELDS_COVERED'


def test_expired_or_revoked_during_read_cannot_return_result():
    opener = Opener()
    clock = [50]
    original = opener.open
    def slow(request, timeout):
        response = original(request, timeout)
        clock[0] += 20
        return response
    opener.open = slow
    with pytest.raises(module.PaperInspectionError, match='TIME_BUDGET'):
        module.inspect_account(KEY, SECRET, confirm_read_only_paper=True, opener=opener,
            wall_clock=lambda: 1000 + clock[0], elapsed_clock=lambda: clock[0])
    assert len(opener.requests) == 1 and opener.responses[0].closed


def test_real_overlap_rejects_second_inspection_and_releases_after_first():
    import threading
    entered, release = threading.Event(), threading.Event()
    first, second = Opener(), Opener()
    original = first.open
    outcomes = []
    def blocked(request, timeout):
        entered.set()
        assert release.wait(10)
        return original(request, timeout)
    first.open = blocked
    worker = threading.Thread(target=lambda: outcomes.append(inspect(first)))
    worker.start()
    try:
        assert entered.wait(10)
        with pytest.raises(module.PaperInspectionError, match='BUSY'):
            inspect(second)
        assert not second.requests
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and len(outcomes) == 1
    assert inspect()['checks'] == 'OBSERVED_FIELDS_COVERED'


def test_json_decimal_quantities_keep_exact_precision():
    raw = b'[{"symbol":"BTC/USD","asset_class":"crypto","side":"long","qty":2,"qty_available":1.234567890123456789}]'
    result = inspect(Opener([account(), Response(None, raw=raw), [], account()]))
    assert result['observed_btc_available'] == '1.234567890123456789'


def test_json_nonfinite_token_is_rejected_even_in_unused_fields():
    opener = Opener([Response(None, raw=b'{"private":Infinity}')])
    with pytest.raises(module.PaperInspectionError, match='JSON_NONFINITE'):
        inspect(opener)
    assert len(opener.requests) == 1
