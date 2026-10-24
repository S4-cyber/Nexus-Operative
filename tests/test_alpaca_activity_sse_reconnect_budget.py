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
USE_RESERVED_TOKEN = object()


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


def test_emergency_stop_aborts_pending_reservation_without_external_access(
        tmp_path):
    cursor, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    cursor_before = cursor.inspect()
    samples = []
    result = subject.abort_pending_reconnect_for_emergency_stop(
        reconnect_budget=budget,
        expected_budget_head=reserved['head_sha256'], evaluated_at=1001,
        emergency_stop_check=lambda: samples.append(True) or True,
        confirm_emergency_stop_abort=True)
    state = budget.inspect()
    assert len(samples) == 2
    assert result['pending_reconnect_aborted']
    assert not result['duplicate_abort']
    assert not result['provider_request_performed']
    assert not result['credential_access_performed']
    assert not result['cursor_read_performed']
    assert not result['cursor_write_performed']
    assert state['pending_token'] is None and state['review_required']
    assert not state['completed']
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE'
    assert state['last_attempt_head_sha256'] == reserved['head_sha256']
    assert cursor.inspect() == cursor_before and not cursor.path.exists()
    assert all(result[flag] is False for flag in subject.FLAGS)


def test_emergency_stop_abort_lost_ack_replay_is_idempotent(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    options = dict(reconnect_budget=budget,
        expected_budget_head=reserved['head_sha256'], evaluated_at=1001,
        emergency_stop_check=lambda: True,
        confirm_emergency_stop_abort=True)
    first = subject.abort_pending_reconnect_for_emergency_stop(**options)
    saved = budget.path.read_bytes()
    options['evaluated_at'] = 1002
    replay = subject.abort_pending_reconnect_for_emergency_stop(**options)
    assert replay['duplicate_abort']
    assert replay['reconnect_budget_head_sha256'] == \
        first['reconnect_budget_head_sha256']
    assert budget.path.read_bytes() == saved


@pytest.mark.parametrize(('change','code'), [
    ({'confirm_emergency_stop_abort': False},
        'EMERGENCY_STOP_ABORT_CONFIRMATION_REQUIRED'),
    ({'emergency_stop_check': None}, 'EMERGENCY_STOP_CHECK_INVALID'),
    ({'emergency_stop_check': lambda: False},
        'EMERGENCY_STOP_NOT_ACTIVE'),
    ({'emergency_stop_check': lambda: (_ for _ in ()).throw(RuntimeError())},
        'EMERGENCY_STOP_NOT_ACTIVE'),
    ({'expected_budget_head': 'f' * 64}, 'HEAD_CHANGED'),
])
def test_emergency_stop_abort_denials_preserve_pending_state(
        tmp_path, change, code):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    saved = budget.path.read_bytes()
    options = dict(reconnect_budget=budget,
        expected_budget_head=reserved['head_sha256'], evaluated_at=1001,
        emergency_stop_check=lambda: True,
        confirm_emergency_stop_abort=True)
    options.update(change)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError, match=code):
        subject.abort_pending_reconnect_for_emergency_stop(**options)
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['pending_token'] == reserved['token']


def test_emergency_stop_must_remain_active_through_abort_admission(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    saved = budget.path.read_bytes()
    samples = iter((True, False))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EMERGENCY_STOP_NOT_ACTIVE'):
        subject.abort_pending_reconnect_for_emergency_stop(
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'], evaluated_at=1001,
            emergency_stop_check=lambda: next(samples),
            confirm_emergency_stop_abort=True)
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['pending_token'] == reserved['token']


def test_emergency_stop_abort_cannot_create_or_close_an_absent_episode(tmp_path):
    _, budget = stores(tmp_path)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='PENDING_RECONNECT_MISSING'):
        subject.abort_pending_reconnect_for_emergency_stop(
            reconnect_budget=budget, expected_budget_head=ZERO,
            evaluated_at=1000, emergency_stop_check=lambda: True,
            confirm_emergency_stop_abort=True)
    assert not budget.path.exists()


def waiting_retry(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    state = budget.finish(reserved['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE')
    return budget, reserved, state


def test_emergency_stop_closes_waiting_retry_without_new_attempt(tmp_path):
    budget, _, waiting = waiting_retry(tmp_path)
    samples = []
    result = subject.close_waiting_reconnect_for_emergency_stop(
        reconnect_budget=budget,
        expected_budget_head=waiting['head_sha256'], evaluated_at=1000.5,
        emergency_stop_check=lambda: samples.append(True) or True,
        confirm_emergency_stop_close=True)
    state = budget.inspect()
    assert len(samples) == 2
    assert result['waiting_retry_closed'] and not result['duplicate_close']
    assert not result['reconnect_attempt_reserved']
    assert not result['reconnect_attempt_consumed']
    assert not result['provider_request_performed']
    assert not result['credential_access_performed']
    assert not result['cursor_read_performed']
    assert not result['cursor_write_performed']
    assert state['attempts'] == 1 and state['pending_token'] is None
    assert state['review_required'] and not state['completed']
    assert state['next_allowed_at'] == 0
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE'
    assert state['emergency_stop_prior_head_sha256'] == waiting['head_sha256']
    assert all(result[flag] is False for flag in subject.FLAGS)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EPISODE_CLOSED'):
        budget.begin(evaluated_at=9999, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            confirm_reconnect_attempt=True)


def test_emergency_stop_waiting_close_lost_ack_replay_is_idempotent(tmp_path):
    budget, _, waiting = waiting_retry(tmp_path)
    options = dict(reconnect_budget=budget,
        expected_budget_head=waiting['head_sha256'], evaluated_at=1000.5,
        emergency_stop_check=lambda: True,
        confirm_emergency_stop_close=True)
    first = subject.close_waiting_reconnect_for_emergency_stop(**options)
    saved = budget.path.read_bytes()
    options['evaluated_at'] = 1001
    replay = subject.close_waiting_reconnect_for_emergency_stop(**options)
    assert replay['duplicate_close']
    assert replay['reconnect_budget_head_sha256'] == \
        first['reconnect_budget_head_sha256']
    assert budget.path.read_bytes() == saved


@pytest.mark.parametrize(('change','code'), [
    ({'confirm_emergency_stop_close': False},
        'EMERGENCY_STOP_CLOSE_CONFIRMATION_REQUIRED'),
    ({'emergency_stop_check': None}, 'EMERGENCY_STOP_CHECK_INVALID'),
    ({'emergency_stop_check': lambda: False},
        'EMERGENCY_STOP_NOT_ACTIVE'),
    ({'emergency_stop_check': lambda: (_ for _ in ()).throw(RuntimeError())},
        'EMERGENCY_STOP_NOT_ACTIVE'),
    ({'expected_budget_head': 'f' * 64}, 'HEAD_CHANGED'),
])
def test_emergency_stop_waiting_close_denials_preserve_backoff(
        tmp_path, change, code):
    budget, _, waiting = waiting_retry(tmp_path)
    saved = budget.path.read_bytes()
    options = dict(reconnect_budget=budget,
        expected_budget_head=waiting['head_sha256'], evaluated_at=1000.5,
        emergency_stop_check=lambda: True,
        confirm_emergency_stop_close=True)
    options.update(change)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError, match=code):
        subject.close_waiting_reconnect_for_emergency_stop(**options)
    assert budget.path.read_bytes() == saved
    state = budget.inspect()
    assert state['last_outcome'] == 'RETRY_REQUIRED'
    assert state['next_allowed_at'] == 1001


def test_emergency_stop_waiting_close_requires_stable_active_sample(tmp_path):
    budget, _, waiting = waiting_retry(tmp_path)
    saved = budget.path.read_bytes()
    samples = iter((True, False))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EMERGENCY_STOP_NOT_ACTIVE'):
        subject.close_waiting_reconnect_for_emergency_stop(
            reconnect_budget=budget,
            expected_budget_head=waiting['head_sha256'],
            evaluated_at=1000.5,
            emergency_stop_check=lambda: next(samples),
            confirm_emergency_stop_close=True)
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['last_outcome'] == 'RETRY_REQUIRED'


def test_emergency_stop_waiting_close_rejects_pending_or_absent_episode(
        tmp_path):
    _, pending = stores(tmp_path)
    reserved = pending.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    saved = pending.path.read_bytes()
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='WAITING_RETRY_MISSING'):
        subject.close_waiting_reconnect_for_emergency_stop(
            reconnect_budget=pending,
            expected_budget_head=reserved['head_sha256'], evaluated_at=1000,
            emergency_stop_check=lambda: True,
            confirm_emergency_stop_close=True)
    assert pending.path.read_bytes() == saved
    absent = subject.ActivitySSEReconnectBudget(
        tmp_path / 'absent.sqlite3', ACCOUNT)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='WAITING_RETRY_MISSING'):
        subject.close_waiting_reconnect_for_emergency_stop(
            reconnect_budget=absent, expected_budget_head=ZERO,
            evaluated_at=1000, emergency_stop_check=lambda: True,
            confirm_emergency_stop_close=True)
    assert not absent.path.exists()


def committed_pending_checkpoint(tmp_path,
        reservation_token=USE_RESERVED_TOKEN):
    initial, raw = segments()
    cursor, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, confirm_reconnect_attempt=True)
    token = (reserved['token'] if reservation_token is USE_RESERVED_TOKEN
        else reservation_token)
    opener = Opener([raw])
    checkpoint = subject.checkpoint.collect_and_checkpoint_once(
        KEY, SECRET, expected_account_id=ACCOUNT, initial_events=initial,
        reconnect_reason='CONNECTION_DROPPED', cursor_store=cursor,
        expected_cursor_head=ZERO, confirm_read_only_paper=True,
        confirm_cursor_checkpoint=True, opener=opener,
        wall_clock=lambda: 1000, elapsed_clock=lambda: 50,
        permission_check=lambda: True,
        emergency_stop_check=lambda: False,
        credential_check=lambda: True,
        reconnect_reservation_token=token)
    return cursor, budget, reserved, checkpoint, opener


def test_exact_bound_checkpoint_recovers_pending_result_without_network(tmp_path):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    assert budget.inspect()['pending_token'] == reserved['token']
    result = subject.reconcile_pending_checkpoint(cursor_store=
        ActivitySSECursorStore(cursor.path, ACCOUNT),
        reconnect_budget=subject.ActivitySSEReconnectBudget(
            budget.path, ACCOUNT), expected_budget_head=reserved['head_sha256'],
        expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
        evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert result['pending_checkpoint_reconciled']
    assert not result['provider_request_performed']
    assert not result['automatic_retry']
    assert state['completed'] and state['pending_token'] is None
    assert state['checkpoint_head_sha256'] == checkpoint['checkpoint_head_sha256']
    assert all(result[flag] is False for flag in subject.FLAGS)
    replay = subject.reconcile_pending_checkpoint(cursor_store=
        ActivitySSECursorStore(cursor.path, ACCOUNT),
        reconnect_budget=subject.ActivitySSEReconnectBudget(
            budget.path, ACCOUNT), expected_budget_head=reserved['head_sha256'],
        expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
        evaluated_at=1002, confirm_checkpoint_reconciliation=True)
    assert replay['duplicate_recovery']
    assert replay['reconnect_budget_head_sha256'] == state['head_sha256']
    assert len(opener.requests) == 1


@pytest.mark.parametrize(('token','code'), [
    ('b' * 64, 'CHECKPOINT_BINDING_DISAGREEMENT'),
    (None, 'CHECKPOINT_BINDING_DISAGREEMENT'),
])
def test_unbound_or_foreign_checkpoint_cannot_close_pending_budget(
        tmp_path, token, code):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path,
            reservation_token=token)
    saved = budget.path.read_bytes()
    with pytest.raises(subject.ActivitySSEReconnectBudgetError, match=code):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['pending_token'] == reserved['token']


def test_reconciliation_confirmation_and_heads_fail_before_budget_mutation(tmp_path):
    cursor, budget, reserved, checkpoint, _ = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    for changes, code in (
            ({'confirm_checkpoint_reconciliation': False},
                'RECONCILIATION_CONFIRMATION_REQUIRED'),
            ({'expected_budget_head': 'f' * 64}, 'HEAD_CHANGED'),
            ({'expected_checkpoint_head': 'f' * 64},
                'CHECKPOINT_BINDING_DISAGREEMENT')):
        options = dict(cursor_store=cursor, reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
        options.update(changes)
        with pytest.raises(subject.ActivitySSEReconnectBudgetError,
                match=code):
            subject.reconcile_pending_checkpoint(**options)
        assert budget.path.read_bytes() == saved


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
