"""Durable caller-driven Activity SSE reconnect budget tests."""
import copy
import io
import json

import pytest

from nexus import alpaca_activity_sse_reconnect_budget as subject
from nexus.alpaca_activity_sse_cursor_store import ZERO, ActivitySSECursorStore
from nexus.alpaca_activity_sse_review import _event
from nexus.alpaca_paper_contract import canonical


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
KEY = 'fixture_key_only_123'
SECRET = 'fixture_secret_only_123'


def segments():
    first_trade = _event('01K00000000000000000000001',
        '11111111-1111-1111-1111-111111111111', 'TRD')
    first_fee = _event('01K00000000000000000000002',
        '22222222-2222-2222-2222-222222222222', 'FEE')
    later_trade = _event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333', 'TRD',
        details={**first_trade['details'],
            'order_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id': 'fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    later_fee = _event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444', 'FEE',
        details={'parent_id': later_trade['ref_id'],
            'system_date': '2026-10-18'},
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    initial = [first_trade, first_fee, later_trade]
    replay = [copy.deepcopy(later_trade), later_fee]
    raw = ''.join('data: ' + canonical(row) + '\n\n'
        for row in replay).encode()
    return initial, raw


class Response:
    status = 200
    headers = {'Content-Type': 'text/event-stream; charset=utf-8'}
    def __init__(self, raw, target):
        self.body = io.BytesIO(raw)
        self.target = target
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def geturl(self): return self.target
    def read(self, size): return self.body.read(size)


class Opener:
    def __init__(self, values):
        self.values = iter(values)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return Response(value, request.full_url)


def stores(tmp_path):
    return (ActivitySSECursorStore(tmp_path / 'cursor.sqlite3', ACCOUNT),
        subject.ActivitySSEReconnectBudget(tmp_path / 'budget.sqlite3', ACCOUNT))


def call(tmp_path, opener, at=1000, **changes):
    initial, _ = segments()
    cursor, budget = changes.pop('stores', stores(tmp_path))
    options = dict(expected_account_id=ACCOUNT, initial_events=initial,
        reconnect_reason='CONNECTION_DROPPED', cursor_store=cursor,
        expected_cursor_head=ZERO, reconnect_budget=budget,
        expected_budget_head=budget.inspect()['head_sha256'],
        evaluated_at=at, confirm_reconnect_attempt=True,
        confirm_read_only_paper=True, confirm_cursor_checkpoint=True,
        opener=opener, wall_clock=lambda: at,
        elapsed_clock=lambda: 50, permission_check=lambda: True,
        emergency_stop_check=lambda: False,
        credential_check=lambda: True)
    options.update(changes)
    result = subject.collect_and_checkpoint_due(KEY, SECRET, **options)
    return result, cursor, budget


def test_success_reserves_before_one_get_and_closes_episode(tmp_path):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = opener.open
    def guarded(request, timeout):
        state = subject.ActivitySSEReconnectBudget(
            budget.path, ACCOUNT).inspect()
        assert state['attempts'] == 1 and state['pending_token']
        return original(request, timeout)
    opener.open = guarded
    result, cursor, budget = call(tmp_path, opener,
        stores=(cursor, budget))
    state = subject.ActivitySSEReconnectBudget(
        budget.path, ACCOUNT).inspect()
    assert len(opener.requests) == 1
    assert result['reconnect_attempt_number'] == 1
    assert result['bounded_backoff_enforced']
    assert not result['automatic_retry']
    assert state['completed'] and state['last_outcome'] == 'CHECKPOINT_COMMITTED'
    assert state['checkpoint_head_sha256'] == cursor.inspect()['head_sha256']
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EPISODE_CLOSED'):
        budget.begin(evaluated_at=1001, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            confirm_reconnect_attempt=True)


def test_transport_failure_records_backoff_without_sleep_or_second_get(tmp_path):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    with pytest.raises(ValueError, match='TRANSPORT_UNAVAILABLE'):
        call(tmp_path, opener, stores=(cursor, budget))
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert state['last_outcome'] == 'RETRY_REQUIRED'
    assert state['next_allowed_at'] == 1001
    assert not state['automatic_retry'] and not state['automatic_sleep']
    assert not cursor.path.exists()
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='BACKOFF_ACTIVE'):
        budget.begin(evaluated_at=1000.5, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            confirm_reconnect_attempt=True)
    second = budget.begin(evaluated_at=1001,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=state['head_sha256'], confirm_reconnect_attempt=True)
    assert second['attempt'] == 2


@pytest.mark.parametrize(('change','code'), [
    ({'confirm_reconnect_attempt': False}, 'CONFIRMATION_REQUIRED'),
    ({'confirm_read_only_paper': False}, 'READ_AUTHORIZATION_REQUIRED'),
    ({'confirm_cursor_checkpoint': False}, 'CHECKPOINT_AUTHORIZATION_REQUIRED'),
    ({'expected_budget_head': 'f' * 64}, 'HEAD_CHANGED'),
    ({'expected_cursor_head': 'f' * 64}, 'CURSOR_HEAD_CHANGED'),
])
def test_preflight_denials_are_network_silent(tmp_path, change, code):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    with pytest.raises(ValueError, match=code):
        call(tmp_path, opener, stores=(cursor, budget), **change)
    assert not opener.requests
    assert not budget.path.exists() and not cursor.path.exists()


def test_nonretryable_failure_closes_for_review(tmp_path):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    with pytest.raises(ValueError, match='ADMISSION_REVOKED'):
        call(tmp_path, Opener([raw]), stores=(cursor, budget),
            permission_check=lambda: False)
    state = budget.inspect()
    assert state['review_required']
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert not cursor.path.exists()


def test_crash_after_begin_is_never_expired_or_retried(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    cold = subject.ActivitySSEReconnectBudget(budget.path, ACCOUNT)
    assert cold.inspect()['pending_token'] == reserved['token']
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='PENDING_REVIEW'):
        cold.begin(evaluated_at=999999,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=reserved['head_sha256'],
            confirm_reconnect_attempt=True)


def test_fourth_retryable_failure_closes_for_review_at_fixed_cap(tmp_path):
    cursor, budget = stores(tmp_path)
    for attempt, at in enumerate((1000, 1001, 1003, 1007), start=1):
        opener = Opener([TimeoutError('private')])
        with pytest.raises(ValueError, match='TRANSPORT_UNAVAILABLE'):
            call(tmp_path, opener, at=at, stores=(cursor, budget))
        state = budget.inspect()
        assert len(opener.requests) == 1
        assert state['attempts'] == attempt
        if attempt < subject.MAX_ATTEMPTS:
            assert state['last_outcome'] == 'RETRY_REQUIRED'
            assert state['next_allowed_at'] == at + subject.BACKOFF_SECONDS[attempt - 1]
        else:
            assert state['last_outcome'] == 'REVIEW_REQUIRED'
            assert state['review_required'] and state['next_allowed_at'] == 0
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EPISODE_CLOSED'):
        budget.begin(evaluated_at=999999, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            confirm_reconnect_attempt=True)


def test_account_binding_and_private_values_never_reach_result(tmp_path):
    _, raw = segments()
    result, _, budget = call(tmp_path, Opener([raw]))
    encoded = json.dumps([result, budget.inspect()], sort_keys=True)
    assert all(value not in encoded for value in (
        ACCOUNT, KEY, SECRET, '01K0000', 'fixture_client'))
    assert all(result[flag] is False for flag in subject.FLAGS)
