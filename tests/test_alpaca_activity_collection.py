"""Injected paper GET pages only; no actual broker, credential or account IO."""
import copy
from datetime import datetime
import json
from urllib.error import HTTPError
import io

import pytest
from nexus import alpaca_activity_collection as collector
from nexus import alpaca_paper_inspection as transport
from nexus import paper_credentials as credentials
from test_alpaca_paper_inspection import Opener, Response, account, ACCOUNT, OTHER, KEY, SECRET
from test_alpaca_activity_normalizer import inputs as fill_inputs
from test_paper_credentials import Vault

START = '2026-10-17T00:00:00Z'
END = '2026-10-18T00:00:00Z'
NOW = datetime.fromisoformat('2026-10-19T00:00:00+00:00').timestamp()


def fee(index=1, **changes):
    record = dict(id='20261017000000000::' + f'11234567-89ab-cdef-0123-{index:012d}',
                  activity_type='FEE', symbol='BTCUSD', net_amount='-0.01',
                  status='executed', date='2026-10-16', description=SECRET)
    record.update(changes)
    return record


def page(start=1, count=100): return [fee(index) for index in range(start, start + count)]


def collect(opener=None, orders=None, **changes):
    options = dict(expected_account_id=ACCOUNT, after=START, until=END, confirm_read_only_paper=True,
                   opener=opener or Opener([account(), [fee()], account()]),
                   wall_clock=lambda: NOW, elapsed_clock=lambda: 50)
    options.update(changes)
    return collector.collect_activities(KEY, SECRET, [] if orders is None else orders, **options)


def test_short_page_is_observed_not_verified_coverage_and_returns_only_normalized_data():
    opener = Opener([account(), [fee()], account()]); result = collect(opener)
    assert [r.full_url for r, _ in opener.requests] == [transport.URLS[0], collector.activity_url(START, END), transport.URLS[0]]
    assert result['end_of_page_sequence_observed'] is True and result['continuation_hint'] is None
    assert result['collection_limit_reached'] is False and result['page_counts'] == [1]
    assert result['source'] == 'INJECTED_UNATTESTED' and result['account_fields_double_checked']
    for key in ('automatic_continuation', 'automatic_retry', 'activity_coverage_verified', 'fee_coverage_complete',
                'broker_account_reconciled', 'spendable_funds_verified', 'ready_for_routing', 'broker_connected',
                'live_orders_enabled', 'ledger_applied', 'atomic_snapshot'):
        assert result[key] is False
    report = result['activity_normalization']
    assert report['recognized_fee_debits']['USD'] == '0.01'
    assert report['fee_debits'][0]['reported_date'] == '2026-10-16'  # Not filtered as creation/execution time.
    assert KEY not in json.dumps(result) and SECRET not in json.dumps(result)
    assert all(r.closed for r in opener.responses)
    for request, timeout in opener.requests:
        assert request.get_method() == 'GET' and request.data is None and 0 < timeout <= 4
        assert request.get_header('Apca-api-secret-key') == SECRET


def test_two_full_pages_stop_without_third_probe_or_automatic_continuation():
    first, second = page(), page(101)
    opener = Opener([account(), first, second, account()]); result = collect(opener)
    assert result['page_counts'] == [100, 100] and result['rows_received'] == 200
    assert len(opener.requests) == 4 and result['collection_limit_reached']
    assert not result['end_of_page_sequence_observed']
    assert result['continuation_hint'] == dict(scope_sha256=result['scope_sha256'], page_token=second[-1]['id'])
    assert opener.requests[2][0].full_url == collector.activity_url(START, END, first[-1]['id'])
    assert result['activity_normalization']['recognized_fee_debits']['USD'] == '2'
    assert result['activity_coverage_verified'] is False


def test_empty_second_page_and_overlapping_semantic_duplicates_do_not_invent_coverage():
    first = page()
    for second in ([], [copy.deepcopy(first[-1]), fee(101)]):
        opener = Opener([account(), first, second, account()]); result = collect(opener)
        assert len(opener.requests) == 4 and result['end_of_page_sequence_observed']
        assert result['activity_coverage_verified'] is False
        assert result['activity_normalization']['duplicate_activities_ignored'] == (1 if second else 0)


def test_empty_first_page_does_not_infer_account_or_fee_acceptance():
    opener = Opener([account(), [], account()]); result = collect(opener)
    assert result['rows_received'] == 0 and len(opener.requests) == 3
    assert result['activity_normalization']['status'] == 'REVIEW_REQUIRED'
    assert result['activity_coverage_verified'] is False


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_confirmation_is_strict_before_network(confirmation):
    opener = Opener()
    with pytest.raises(transport.PaperInspectionError, match='EXPLICIT_AUTHORIZATION_REQUIRED'):
        collect(opener, confirm_read_only_paper=confirmation)
    assert not opener.requests


@pytest.mark.parametrize('start,end', [(START, START), (END, START), (START, '2026-10-18T00:00:01Z'),
    ('2026-10-17', END), ('2026-10-17T00:00:00+00:00', END), ('2026-10-17T00:00:00.1Z', END),
    ('2026-02-30T00:00:00Z', END), ('1969-12-31T00:00:00Z', '1970-01-01T00:00:00Z'), (None, END)])
def test_invalid_or_unbounded_windows_are_denied_before_io(start, end):
    opener = Opener()
    with pytest.raises(ValueError): collect(opener, after=start, until=end)
    assert not opener.requests


def test_future_window_and_invalid_order_terms_deny_before_account_get():
    opener = Opener()
    with pytest.raises(transport.PaperInspectionError, match='FUTURE_CREATION_WINDOW'):
        collect(opener, wall_clock=lambda: 1000)
    assert not opener.requests
    with pytest.raises(ValueError): collect(opener, orders=[{'private': SECRET}])
    assert not opener.requests


@pytest.mark.parametrize('change', ['live', 'port', 'fragment', 'filter', 'direction', 'count', 'duplicate', 'order', 'token', 'unknown'])
def test_shared_reader_allows_only_canonical_activity_path_and_query(change):
    url = collector.activity_url(START, END)
    variants = dict(live=url.replace('paper-api.', 'api.'), port=url.replace('.markets/', '.markets:443/'),
        fragment=url + '#secret', filter=url + '&activity_types=FILL', direction=url.replace('direction=asc', 'direction=desc'),
        count=url.replace('page_size=100', 'page_size=500'), duplicate=url + '&after=bad',
        order=url.replace('after=', 'xafter=', 1), token=url + '&page_token=bad%26x%3Dy', unknown=url + '&x=1')
    opener = Opener()
    with transport._ReadSession(KEY, SECRET, confirm_read_only_paper=True, opener=opener) as session:
        with pytest.raises(transport.PaperInspectionError, match='PATH_DENIED'): session.get(variants[change])
    assert not opener.requests


@pytest.mark.parametrize('bad', [None, {}, [None], [fee()] * 101, [{**fee(), 'activity_type': 'CSD'}],
                                [{**fee(), 'private': ['nested']}], [{**fee(), 'id': 'stream-id'}]],
                         ids=['null', 'object', 'null-row', 'oversized', 'deposit', 'nested', 'bad-id'])
def test_first_page_failure_does_not_admit_next_request_or_partial_result(bad):
    opener = Opener([account(), bad])
    with pytest.raises(transport.PaperInspectionError): collect(opener)
    assert len(opener.requests) == 2 and opener.responses[-1].closed


@pytest.mark.parametrize('second', [[fee(100)], page(), page(1, 50)], ids=['repeat-cursor', 'repeat-page', 'backwards-overlap'])
def test_nonprogressing_cursor_is_rejected_even_when_last_page_is_short(second):
    opener = Opener([account(), page(), second])
    with pytest.raises(transport.PaperInspectionError, match='CURSOR_NO_PROGRESS'): collect(opener)
    assert len(opener.requests) == 3


def test_cross_page_conflicting_duplicate_is_not_overwritten_or_counted_twice():
    opener = Opener([account(), page(), [fee(100, net_amount='-1'), fee(101)]])
    with pytest.raises(transport.PaperInspectionError): collect(opener)
    assert len(opener.requests) == 3


@pytest.mark.parametrize('phase', ['first', 'last-id', 'last-cash'])
def test_account_identity_or_stability_failure_discards_the_page_set(phase):
    first, last = account(), account()
    if phase == 'first': first['id'] = OTHER
    elif phase == 'last-id': last['id'] = OTHER
    else: last['cash'] = '999'
    opener = Opener([first, [fee()], last])
    with pytest.raises(transport.PaperInspectionError): collect(opener)
    assert len(opener.requests) == (1 if phase == 'first' else 3)


@pytest.mark.parametrize('code', [401, 403, 429, 500, 302])
def test_error_body_is_closed_unread_no_retry_or_extra_identity_request(code):
    stream = io.BytesIO(SECRET.encode()); url = collector.activity_url(START, END)
    error = HTTPError(url, code, SECRET, {'Retry-After': '30'}, stream)
    opener = Opener([account(), error])
    with pytest.raises(transport.PaperInspectionError) as caught: collect(opener)
    assert len(opener.requests) == 2 and stream.closed and SECRET not in str(caught.value)


@pytest.mark.parametrize('phase', ['second-page', 'final-account'])
def test_late_timeout_does_not_return_earlier_pages_as_success(phase):
    values = [account(), page(), TimeoutError(SECRET)] if phase == 'second-page' else [account(), [fee()], TimeoutError(SECRET)]
    opener = Opener(values)
    with pytest.raises(transport.PaperInspectionError, match='TRANSPORT_UNAVAILABLE'): collect(opener)
    assert len(opener.requests) == 3


def test_first_page_oversized_duplicate_json_or_url_change_is_closed_before_continuation():
    for response in (Response(None, raw=b' ' * (transport.MAX_BODY + 1)),
                     Response(None, raw=b'[{"id":1,"id":2}]'), Response([], url='https://api.alpaca.markets/v2/account/activities')):
        opener = Opener([account(), response])
        with pytest.raises(transport.PaperInspectionError): collect(opener)
        assert len(opener.requests) == 2 and response.closed


def test_caller_order_mutation_during_io_does_not_change_captured_order_links():
    rows, orders = fill_inputs(); first = [rows[0]]
    opener = Opener([account(), first, account()]); original = opener.open
    def mutate(request, timeout):
        value = original(request, timeout)
        orders[0]['client_order_id'] = 'changed_after_admission'
        return value
    opener.open = mutate
    result = collect(opener, orders=orders)
    assert result['activity_normalization']['executions'][0]['client_order_id'] == 'fixture_1'


def test_clock_discontinuity_and_revocation_discard_after_page_response():
    for failure in ('clock', 'permission'):
        opener = Opener([account(), page()]); original = opener.open; wall = [NOW]; allowed = [True]
        def changed(request, timeout):
            value = original(request, timeout)
            if len(opener.requests) == 2:
                if failure == 'clock': wall[0] += 10
                else: allowed[0] = False
            return value
        opener.open = changed
        with pytest.raises(transport.PaperInspectionError):
            collect(opener, wall_clock=lambda: wall[0], permission_check=lambda: allowed[0])
        assert len(opener.requests) == 2 and opener.responses[-1].closed


def test_final_credential_check_failure_never_returns_collected_rows():
    with pytest.raises(transport.PaperInspectionError, match='CREDENTIALS_CHANGED'):
        collect(credential_check=lambda: False)


def test_saved_key_remove_or_external_entry_change_fences_collection(monkeypatch):
    vault = Vault(); monkeypatch.setattr(credentials, '_vault', lambda: vault)
    monkeypatch.setattr(credentials, '_epoch', 0); monkeypatch.setattr(credentials, '_blocked', False)
    for action in ('remove', 'external'):
        credentials.install(KEY, SECRET, confirm_store=True)
        opener = Opener([account(), [fee()], account()]); original = opener.open
        def changed(request, timeout):
            value = original(request, timeout)
            if len(opener.requests) == 2:
                if action == 'remove': credentials.remove(confirm_remove=True)
                else:
                    key = (credentials.SERVICE, credentials.ACCOUNT)
                    record = json.loads(vault.data[key]); record['api_key'] = 'changed_fixture_1234'
                    vault.data[key] = json.dumps(record)
            return value
        opener.open = changed
        with pytest.raises(ValueError):
            credentials.collect_saved([], expected_account_id=ACCOUNT, after=START, until=END,
                confirm_read_only_paper=True, opener=opener, wall_clock=lambda: NOW, elapsed_clock=lambda: 50)
        assert len(opener.requests) == (2 if action == 'remove' else 3)


def test_repeated_calls_do_not_cache_or_automatically_resume_and_share_read_admission():
    first, second = Opener([account(), [fee()], account()]), Opener([account(), [], account()])
    assert collect(first)['rows_received'] == 1
    assert collect(second)['rows_received'] == 0
    with transport._ReadSession(KEY, SECRET, confirm_read_only_paper=True, opener=Opener()):
        opener = Opener()
        with pytest.raises(transport.PaperInspectionError, match='BUSY'): collect(opener)
        assert not opener.requests


def test_no_new_route_or_startup_vault_or_network_access(monkeypatch):
    from nexus.app import app
    from fastapi.testclient import TestClient
    def forbidden(*args, **kwargs): pytest.fail('Startup accessed activity credentials/transport')
    monkeypatch.setattr(credentials, '_vault', forbidden)
    monkeypatch.setattr(collector, 'collect_activities', forbidden)
    client = TestClient(app); client.get('/api/status'); client.get('/portal'); client.get('/api/onboarding/tasks')
    assert not any('activity-collection' in route.path for route in app.routes)


def test_opaque_cursor_spelling_is_preserved_while_duplicate_id_checks_are_semantic():
    first = page(); first[-1]['id'] = '20261017000000000::ABCDEF01-2345-6789-ABCD-000000000100'
    opener = Opener([account(), first, [fee(101)], account()]); result = collect(opener)
    assert opener.requests[2][0].full_url == collector.activity_url(START, END, first[-1]['id'])
    assert 'ABCDEF01-2345-6789-ABCD-' in opener.requests[2][0].full_url
    assert result['activity_normalization']['duplicate_activities_ignored'] == 0
    second = [{**first[-1], 'id': first[-1]['id'].lower()}]
    with pytest.raises(transport.PaperInspectionError, match='CURSOR_NO_PROGRESS'):
        collect(Opener([account(), first, second]))


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_saved_collection_requires_strict_confirmation_before_vault_access(monkeypatch, confirmation):
    def forbidden(): pytest.fail('Unconfirmed collection accessed the credential store')
    monkeypatch.setattr(credentials, '_vault', forbidden)
    with pytest.raises(credentials.PaperVaultError, match='READ_CONFIRMATION_REQUIRED'):
        credentials.collect_saved([], expected_account_id=ACCOUNT, after=START, until=END,
                                  confirm_read_only_paper=confirmation)


def test_saved_collection_success_still_returns_no_account_or_trading_authority(monkeypatch):
    vault = Vault(); monkeypatch.setattr(credentials, '_vault', lambda: vault)
    monkeypatch.setattr(credentials, '_epoch', 0); monkeypatch.setattr(credentials, '_blocked', False)
    credentials.install(KEY, SECRET, confirm_store=True)
    result = credentials.collect_saved([], expected_account_id=ACCOUNT, after=START, until=END,
        confirm_read_only_paper=True, opener=Opener([account(), [fee()], account()]),
        wall_clock=lambda: NOW, elapsed_clock=lambda: 50)
    assert result['rows_received'] == 1 and result['source'] == 'INJECTED_UNATTESTED'
    assert result['activity_coverage_verified'] is False and result['ready_for_routing'] is False
    assert SECRET not in json.dumps(result)
