"""Durable caller-driven Activity SSE reconnect budget tests."""
import copy
import io
import json
import sqlite3
from email.message import Message
from urllib.error import HTTPError

import pytest

from nexus import alpaca_activity_sse_reconnect_budget as subject
from nexus import alpaca_activity_sse_wire_review as wire
from nexus.alpaca_activity_sse_cursor_store import ZERO, ActivitySSECursorStore
from nexus.alpaca_activity_sse_review import _event
from nexus.alpaca_paper_contract import canonical, sha


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
KEY = 'fixture_key_only_123'
SECRET = 'fixture_secret_only_123'
USE_RESERVED_TOKEN = object()
STOP_CLEAR = lambda: False


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
    if 'expected_budget_head' in changes:
        expected_budget_head = changes.pop('expected_budget_head')
    else:
        expected_budget_head = budget.inspect()['head_sha256']
    options = dict(expected_account_id=ACCOUNT, initial_events=initial,
        reconnect_reason='CONNECTION_DROPPED', cursor_store=cursor,
        expected_cursor_head=ZERO, reconnect_budget=budget,
        expected_budget_head=expected_budget_head,
        evaluated_at=at, confirm_reconnect_attempt=True,
        confirm_read_only_paper=True, confirm_cursor_checkpoint=True,
        opener=opener, wall_clock=lambda: at,
        elapsed_clock=lambda: 50, outcome_clock=lambda: at,
        permission_check=lambda: True,
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
    assert result['emergency_stop_admission_fenced']
    assert result['emergency_stop_sampled_before_reservation']
    assert not result['automatic_retry']
    assert state['completed'] and state['last_outcome'] == 'CHECKPOINT_COMMITTED'
    assert state['checkpoint_head_sha256'] == cursor.inspect()['head_sha256']
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EPISODE_CLOSED'):
        budget.begin(evaluated_at=1001, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


def test_initial_cursor_inspection_failure_is_sanitized_before_reservation(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    monkeypatch.setattr(cursor, 'inspect',
        lambda: (_ for _ in ()).throw(
            RuntimeError('private cursor storage detail')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget))
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_CURSOR_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


@pytest.mark.parametrize('malformed', (None, {}, [],
    {'head_sha256': None}))
def test_malformed_initial_cursor_view_fails_closed_before_reservation(
        tmp_path, monkeypatch, malformed):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    monkeypatch.setattr(cursor, 'inspect', lambda: malformed)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='CURSOR_STATE_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


def test_initial_budget_inspection_failure_is_sanitized_before_reservation(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    monkeypatch.setattr(budget, 'inspect',
        lambda: (_ for _ in ()).throw(
            RuntimeError('private budget storage detail')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_ADMISSION_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


@pytest.mark.parametrize('malformed', (None, {}, [],
    {'head_sha256': ZERO}))
def test_malformed_initial_budget_view_fails_closed_before_reservation(
        tmp_path, monkeypatch, malformed):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    monkeypatch.setattr(budget, 'inspect', lambda: malformed)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='ADMISSION_STATE_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


@pytest.mark.parametrize('field', subject.FLAGS)
def test_initial_budget_view_cannot_promote_authority_before_reservation(
        tmp_path, monkeypatch, field):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    original = budget.inspect
    monkeypatch.setattr(budget, 'inspect',
        lambda: {**original(), field: True})
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='ADMISSION_STATE_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


@pytest.mark.parametrize('kind', ('extra_field', 'mapping_subclass',
    'mutable_backoff'))
def test_initial_budget_view_requires_exact_total_shape(tmp_path,
        monkeypatch, kind):
    class BudgetMapping(dict):
        pass

    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    original = budget.inspect

    def altered():
        value = original()
        if kind == 'extra_field':
            return {**value, 'private_extension': False}
        if kind == 'mapping_subclass':
            return BudgetMapping(value)
        return {**value, 'backoff_seconds': list(value['backoff_seconds'])}

    monkeypatch.setattr(budget, 'inspect', altered)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='ADMISSION_STATE_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


def test_reservation_failure_before_commit_is_sanitized(tmp_path,
        monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    monkeypatch.setattr(budget, 'begin',
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError('private reservation storage detail')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


def test_reservation_lost_ack_preserves_pending_without_provider_io(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    original_begin = budget.begin
    original_inspect = budget.inspect

    def lost_ack(**kwargs):
        original_begin(**kwargs)
        raise RuntimeError('private reservation acknowledgement detail')

    monkeypatch.setattr(budget, 'begin', lost_ack)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert not opener.requests and not cursor.path.exists()
    state = original_inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['attempts'] == 1 and state['last_outcome'] is None
    assert state['next_allowed_at'] == 0 and not state['review_required']


@pytest.mark.parametrize('malformed', (None, {}, [],
    {'token': ZERO}, {'duplicate_reservation': False}))
def test_malformed_reservation_ack_fails_closed_before_provider_io(
        tmp_path, monkeypatch, malformed):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    monkeypatch.setattr(budget, 'begin', lambda **kwargs: malformed)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


def test_committed_reservation_corrupt_ack_preserves_pending_without_io(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    original_begin = budget.begin
    original_inspect = budget.inspect

    def corrupt_ack(**kwargs):
        reservation = original_begin(**kwargs)
        return {**reservation, 'provider_request_authorized': 'private'}

    monkeypatch.setattr(budget, 'begin', corrupt_ack)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert not opener.requests and not cursor.path.exists()
    state = original_inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['attempts'] == 1 and state['last_outcome'] is None
    assert state['next_allowed_at'] == 0 and not state['review_required']


def test_well_formed_uncommitted_reservation_ack_has_no_request_authority(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    token = 'a' * 64
    monkeypatch.setattr(budget, 'begin', lambda **kwargs: dict(
        token=token, head_sha256=token, attempt=1,
        expected_cursor_head_sha256=ZERO,
        duplicate_reservation=False, provider_request_authorized=True))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert not opener.requests
    assert not cursor.path.exists() and not budget.path.exists()


@pytest.mark.parametrize('changed', (
    {'token': 'a' * 64, 'head_sha256': 'a' * 64},
    {'attempt': 2}))
def test_structurally_valid_corrupt_ack_must_match_durable_reservation(
        tmp_path, monkeypatch, changed):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    original_begin = budget.begin

    def corrupt_ack(**kwargs):
        return {**original_begin(**kwargs), **changed}

    monkeypatch.setattr(budget, 'begin', corrupt_ack)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert not opener.requests and not cursor.path.exists()
    state = subject.ActivitySSEReconnectBudget(
        budget.path, ACCOUNT).inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['attempts'] == 1 and state['last_outcome'] is None
    assert state['next_allowed_at'] == 0 and not state['review_required']


def test_post_reservation_inspection_failure_blocks_provider_io(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    original_begin = budget.begin

    def unreadable_after_commit(**kwargs):
        reservation = original_begin(**kwargs)
        monkeypatch.setattr(budget, 'inspect', lambda: (_ for _ in ()).throw(
            RuntimeError('private post-reservation inspection detail')))
        return reservation

    monkeypatch.setattr(budget, 'begin', unreadable_after_commit)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert not opener.requests and not cursor.path.exists()
    state = subject.ActivitySSEReconnectBudget(
        budget.path, ACCOUNT).inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['attempts'] == 1 and state['last_outcome'] is None
    assert state['next_allowed_at'] == 0 and not state['review_required']


def test_concurrent_reservation_result_at_preopen_fence_blocks_provider_io(
        tmp_path):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    samples = []

    def permission():
        samples.append(True)
        # The first permission fence is checkpoint admission.  Change the
        # durable reservation at the transport's final pre-open fence.
        if len(samples) == 2:
            state = budget.inspect()
            budget.finish(state['pending_token'], evaluated_at=1000,
                outcome='REVIEW_REQUIRED',
                detail_code='CONCURRENT_OWNER_REVIEW')
        return True

    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            permission_check=permission)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert samples == [True, True]
    assert not opener.requests and not cursor.path.exists()
    state = subject.ActivitySSEReconnectBudget(
        budget.path, ACCOUNT).inspect()
    assert state['pending_token'] is None and state['review_required']
    assert state['last_detail_code'] == 'CONCURRENT_OWNER_REVIEW'


def test_unreadable_reservation_at_preopen_fence_blocks_provider_io(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    original_inspect = budget.inspect
    samples = []

    def permission():
        samples.append(True)
        if len(samples) == 2:
            monkeypatch.setattr(budget, 'inspect',
                lambda: (_ for _ in ()).throw(
                    RuntimeError('private preopen storage detail')))
        return True

    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget),
            permission_check=permission)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESERVATION_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert samples == [True, True]
    assert not opener.requests and not cursor.path.exists()
    state = original_inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['attempts'] == 1 and state['last_outcome'] is None
    assert state['next_allowed_at'] == 0 and not state['review_required']


def test_emergency_stop_callback_cannot_retarget_cursor_store(tmp_path):
    cursor, budget = stores(tmp_path)
    original_cursor_path = cursor.path
    retargeted = tmp_path / 'retargeted-cursor.sqlite3'
    opener = Opener([b'private provider response'])

    def stop_clear():
        cursor.path = retargeted
        return False

    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='STORE_BINDING_CHANGED'):
        call(tmp_path, opener, stores=(cursor, budget),
            emergency_stop_check=stop_clear)
    assert not opener.requests
    assert not original_cursor_path.exists()
    assert not retargeted.exists() and not budget.path.exists()


def test_permission_callback_cannot_retarget_budget_store(tmp_path):
    cursor, budget = stores(tmp_path)
    original_budget_path = budget.path
    retargeted = tmp_path / 'retargeted-budget.sqlite3'
    opener = Opener([b'private provider response'])
    samples = []

    def permission():
        samples.append(True)
        if len(samples) == 2:
            budget.path = retargeted
        return True

    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='STORE_BINDING_CHANGED'):
        call(tmp_path, opener, stores=(cursor, budget),
            permission_check=permission)
    assert samples == [True, True]
    assert not opener.requests and not cursor.path.exists()
    assert not retargeted.exists()
    state = subject.ActivitySSEReconnectBudget(
        original_budget_path, ACCOUNT).inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None


@pytest.mark.parametrize('store_name', ('cursor', 'budget'))
def test_completed_checkpoint_call_cannot_retarget_store_before_result(
        tmp_path, monkeypatch, store_name):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    original_cursor_path = cursor.path
    original_budget_path = budget.path
    retargeted = tmp_path / f'retargeted-{store_name}.sqlite3'
    opener = Opener([raw])
    original = subject.checkpoint.collect_and_checkpoint_once

    def retarget_after_checkpoint(*args, **kwargs):
        result = original(*args, **kwargs)
        setattr(cursor if store_name == 'cursor' else budget,
            'path', retargeted)
        return result

    monkeypatch.setattr(subject.checkpoint, 'collect_and_checkpoint_once',
        retarget_after_checkpoint)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='STORE_BINDING_CHANGED'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert len(opener.requests) == 1 and not retargeted.exists()
    binding = ActivitySSECursorStore(
        original_cursor_path, ACCOUNT).latest_checkpoint_binding()
    assert binding['reconnect_reservation_bound']
    state = subject.ActivitySSEReconnectBudget(
        original_budget_path, ACCOUNT).inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None and not state['completed']


@pytest.mark.parametrize('store_name', ('cursor', 'budget'))
def test_result_commit_cannot_retarget_store_before_stability_check(
        tmp_path, monkeypatch, store_name):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    original_cursor_path = cursor.path
    original_budget_path = budget.path
    retargeted = tmp_path / f'retargeted-result-{store_name}.sqlite3'
    opener = Opener([raw])
    original_finish = budget.finish

    def retarget_after_finish(*args, **kwargs):
        retained = original_finish(*args, **kwargs)
        setattr(cursor if store_name == 'cursor' else budget,
            'path', retargeted)
        return retained

    monkeypatch.setattr(budget, 'finish', retarget_after_finish)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='STORE_BINDING_CHANGED'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert len(opener.requests) == 1 and not retargeted.exists()
    assert ActivitySSECursorStore(
        original_cursor_path, ACCOUNT).inspect()['initialized']
    state = subject.ActivitySSEReconnectBudget(
        original_budget_path, ACCOUNT).inspect()
    assert state['completed'] and state['pending_token'] is None
    assert state['last_outcome'] == 'CHECKPOINT_COMMITTED'


def test_provider_failure_cannot_retarget_budget_before_failure_record(
        tmp_path):
    cursor, budget = stores(tmp_path)
    original_budget_path = budget.path
    retargeted = tmp_path / 'retargeted-failure-budget.sqlite3'
    opener = Opener([TimeoutError('private')])
    original_open = opener.open

    def retarget_then_fail(request, timeout):
        budget.path = retargeted
        return original_open(request, timeout)

    opener.open = retarget_then_fail
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='STORE_BINDING_CHANGED'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert len(opener.requests) == 1 and not retargeted.exists()
    state = subject.ActivitySSEReconnectBudget(
        original_budget_path, ACCOUNT).inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None and state['next_allowed_at'] == 0


def test_checkpoint_ack_requires_exact_durable_cursor_binding(tmp_path,
        monkeypatch):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = subject.checkpoint.collect_and_checkpoint_once

    def corrupt_ack(*args, **kwargs):
        result = original(*args, **kwargs)
        return {**result, 'checkpoint_receipt_sha256': 'f' * 64}

    monkeypatch.setattr(subject.checkpoint, 'collect_and_checkpoint_once',
        corrupt_ack)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget))
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_CHECKPOINT_STATE_UNCERTAIN'
    assert len(opener.requests) == 1
    state = budget.inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None and not state['completed']
    binding = cursor.latest_checkpoint_binding()
    assert binding['reconnect_reservation_bound']
    recovered = subject.reconcile_pending_checkpoint(cursor_store=cursor,
        reconnect_budget=budget, expected_budget_head=state['head_sha256'],
        expected_checkpoint_head=binding['head_sha256'], evaluated_at=1001,
        confirm_checkpoint_reconciliation=True)
    assert recovered['pending_checkpoint_reconciled']
    assert budget.inspect()['completed']


@pytest.mark.parametrize('field', subject.FLAGS)
def test_checkpoint_ack_cannot_promote_authority_flags(tmp_path, monkeypatch,
        field):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = subject.checkpoint.collect_and_checkpoint_once

    def promoted_ack(*args, **kwargs):
        result = original(*args, **kwargs)
        return {**result, field: True}

    monkeypatch.setattr(subject.checkpoint, 'collect_and_checkpoint_once',
        promoted_ack)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget))
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_CHECKPOINT_STATE_UNCERTAIN'
    assert len(opener.requests) == 1
    state = budget.inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None and not state['completed']
    assert cursor.latest_checkpoint_binding()['reconnect_reservation_bound']


@pytest.mark.parametrize(('field', 'value'), [
    *[(field, False) for field in subject.CHECKPOINT_ACK_TRUE],
    *[(field, True) for field in subject.CHECKPOINT_ACK_FALSE],
    ('duplicate_checkpoint', 1),
    ('actual_provider_access', 1),
    ('actual_stream_connection', 1),
    ('actual_reconnect_performed', 1),
    ('source', 'PAPER_HTTPS_READS'),
])
def test_checkpoint_ack_requires_exact_safety_shape(tmp_path, monkeypatch,
        field, value):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = subject.checkpoint.collect_and_checkpoint_once

    def malformed_ack(*args, **kwargs):
        result = original(*args, **kwargs)
        return {**result, field: value}

    monkeypatch.setattr(subject.checkpoint, 'collect_and_checkpoint_once',
        malformed_ack)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='CHECKPOINT_STATE_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert len(opener.requests) == 1
    state = budget.inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None and not state['completed']


def test_forged_checkpoint_ack_cannot_complete_reconnect_budget(tmp_path,
        monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([b'private provider response'])
    monkeypatch.setattr(subject.checkpoint, 'collect_and_checkpoint_once',
        lambda *args, **kwargs: dict(
            mode='EXPLICIT_ALPACA_ACTIVITY_SSE_CHECKPOINT',
            status='REVIEW_REQUIRED', account_sha256=cursor.account_sha256,
            checkpoint_head_sha256='a' * 64,
            checkpoint_receipt_sha256='b' * 64,
            checkpoint_cursor_sha256='c' * 64,
            local_cursor_checkpoint_committed=True,
            process_restart_recoverable=True,
            reconnect_reservation_bound=True))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget))
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_CHECKPOINT_STATE_UNCERTAIN'
    assert not opener.requests and not cursor.path.exists()
    state = budget.inspect()
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None and not state['completed']


def test_checkpoint_binding_must_remain_stable_across_result_commit(tmp_path,
        monkeypatch):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = cursor.latest_checkpoint_binding
    samples = []

    def changed_binding():
        samples.append(True)
        retained = original()
        if len(samples) == 2:
            return {**retained, 'head_sha256': 'f' * 64}
        return retained

    monkeypatch.setattr(cursor, 'latest_checkpoint_binding', changed_binding)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        call(tmp_path, opener, stores=(cursor, budget))
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RESULT_RECORD_UNCERTAIN'
    assert samples == [True, True] and len(opener.requests) == 1
    state = budget.inspect()
    binding = original()
    assert state['completed'] and state['pending_token'] is None
    assert state['checkpoint_head_sha256'] == binding['head_sha256']
    assert state['checkpoint_receipt_sha256'] == \
        binding['checkpoint_receipt_sha256']


@pytest.mark.parametrize('field', subject.FLAGS)
def test_durable_checkpoint_binding_cannot_promote_authority(tmp_path,
        monkeypatch, field):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = cursor.latest_checkpoint_binding

    def promoted_binding():
        return {**original(), field: True}

    monkeypatch.setattr(cursor, 'latest_checkpoint_binding',
        promoted_binding)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='CHECKPOINT_STATE_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget))
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert state['pending_token'] == state['head_sha256']
    assert state['last_outcome'] is None and not state['completed']


def test_post_result_binding_rejects_custom_equality_mapping(tmp_path,
        monkeypatch):
    class EqualMapping(dict):
        def __eq__(self, other): return True
        def __ne__(self, other): return False

    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = cursor.latest_checkpoint_binding
    samples = []

    def forged_stability_view():
        samples.append(True)
        retained = original()
        return retained if len(samples) == 1 else EqualMapping(retained)

    monkeypatch.setattr(cursor, 'latest_checkpoint_binding',
        forged_stability_view)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_RECORD_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert samples == [True, True] and len(opener.requests) == 1
    state = budget.inspect()
    assert state['completed'] and state['pending_token'] is None


def test_success_result_and_exact_replay_bind_observed_completion_time(
        tmp_path):
    _, raw = segments()
    result, _, budget = call(tmp_path, Opener([raw]),
        outcome_clock=lambda: 1007)
    state = budget.inspect()
    assert state['completed'] and state['last_evaluated_at'] == 1007
    saved = budget.path.read_bytes()
    replay = budget.finish(state['last_attempt_head_sha256'],
        evaluated_at=1007, outcome='CHECKPOINT_COMMITTED',
        detail_code='SUCCESS',
        checkpoint_head_sha256=result['checkpoint_head_sha256'],
        checkpoint_receipt_sha256=result['checkpoint_receipt_sha256'])
    assert replay['duplicate_result']
    assert budget.path.read_bytes() == saved


def test_success_recovers_only_exact_lost_result_acknowledgement(
        tmp_path, monkeypatch):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original = budget.finish

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('private lost acknowledgement')

    monkeypatch.setattr(budget, 'finish', lost_ack)
    result, _, _ = call(tmp_path, opener, stores=(cursor, budget),
        outcome_clock=lambda: 1007)
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert result['result_acknowledgement_recovered']
    assert state['completed'] and state['pending_token'] is None
    assert state['last_evaluated_at'] == 1007
    assert state['last_attempt_head_sha256'] != state['head_sha256']
    assert state['last_outcome'] == state['last_requested_outcome'] == \
        'CHECKPOINT_COMMITTED'
    assert state['last_detail_code'] == \
        state['last_requested_detail_code'] == 'SUCCESS'


def test_success_rejects_uncommitted_result_acknowledgement_failure(
        tmp_path, monkeypatch):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    monkeypatch.setattr(budget, 'finish',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('private uncommitted failure')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_RECORD_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert len(opener.requests) == 1 and cursor.inspect()['initialized']
    state = budget.inspect()
    assert state['pending_token'] and not state['completed']


@pytest.mark.parametrize('kind', ('mapping_subclass', 'extra_field',
    'promoted_authority'))
def test_success_rejects_noncanonical_budget_result_acknowledgement(
        tmp_path, monkeypatch, kind):
    class BudgetMapping(dict):
        pass

    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    original_finish = budget.finish
    original_inspect = budget.inspect

    def altered_finish(*args, **kwargs):
        value = original_finish(*args, **kwargs)
        if kind == 'mapping_subclass':
            return BudgetMapping(value)
        if kind == 'extra_field':
            return {**value, 'private_extension': False}
        return {**value, 'release_approved': True}

    monkeypatch.setattr(budget, 'finish', altered_finish)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_RECORD_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert len(opener.requests) == 1
    state = original_inspect()
    assert state['completed'] and state['pending_token'] is None


def test_success_with_uncertain_outcome_clock_uses_no_network_recovery(
        tmp_path):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='OUTCOME_CLOCK_REGRESSION'):
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: 999)
    cursor_state = cursor.inspect()
    budget_state = budget.inspect()
    assert len(opener.requests) == 1 and cursor_state['initialized']
    assert budget_state['pending_token'] and budget_state['last_outcome'] is None
    recovered = subject.reconcile_pending_checkpoint(cursor_store=cursor,
        reconnect_budget=budget,
        expected_budget_head=budget_state['head_sha256'],
        expected_checkpoint_head=cursor_state['head_sha256'],
        evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert recovered['pending_checkpoint_reconciled']
    assert not recovered['provider_request_performed']
    assert len(opener.requests) == 1 and budget.inspect()['completed']


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
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)
    second = budget.begin(evaluated_at=1001,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=state['head_sha256'], emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    assert second['attempt'] == 2


def test_failure_recovers_exact_lost_result_acknowledgement(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    original = budget.finish

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('private lost acknowledgement')

    monkeypatch.setattr(budget, 'finish', lost_ack)
    with pytest.raises(ValueError, match='TRANSPORT_UNAVAILABLE'):
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: 1007)
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert state['pending_token'] is None and not state['review_required']
    assert state['last_outcome'] == 'RETRY_REQUIRED'
    assert state['last_evaluated_at'] == 1007
    assert state['last_applied_backoff_seconds'] == 1
    assert state['next_allowed_at'] == 1008


def test_failure_rejects_uncommitted_result_acknowledgement_failure(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    monkeypatch.setattr(budget, 'finish',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('private uncommitted failure')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_RECORD_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget))
    assert len(opener.requests) == 1 and not cursor.path.exists()
    state = budget.inspect()
    assert state['pending_token'] and state['last_outcome'] is None


def test_failure_lost_ack_accepts_exact_emergency_stop_conversion(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    original = budget.finish
    samples = []

    def check():
        value = len(samples) >= 6
        samples.append(value)
        return value

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('private lost acknowledgement')

    monkeypatch.setattr(budget, 'finish', lost_ack)
    with pytest.raises(ValueError, match='TRANSPORT_UNAVAILABLE'):
        call(tmp_path, opener, stores=(cursor, budget),
            emergency_stop_check=check)
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert samples == [False] * 6 + [True]
    assert state['pending_token'] is None and state['review_required']
    assert state['last_requested_outcome'] == 'RETRY_REQUIRED'
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE'
    assert state['last_applied_backoff_seconds'] == 0
    assert state['next_allowed_at'] == 0


def test_slow_failure_starts_fixed_backoff_when_outcome_is_observed(tmp_path):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    with pytest.raises(ValueError, match='TRANSPORT_UNAVAILABLE'):
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: 1007)
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert state['last_evaluated_at'] == 1007
    assert state['last_applied_backoff_seconds'] == 1
    assert state['next_allowed_at'] == 1008
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='BACKOFF_ACTIVE'):
        budget.begin(evaluated_at=1007.999, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


@pytest.mark.parametrize(('clock','code'), [
    (lambda: 999, 'OUTCOME_CLOCK_REGRESSION'),
    (lambda: 'private', 'OUTCOME_CLOCK_INVALID'),
])
def test_failed_attempt_with_uncertain_outcome_clock_closes_for_review(
        tmp_path, clock, code):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    with pytest.raises(subject.ActivitySSEReconnectBudgetError, match=code):
        call(tmp_path, opener, stores=(cursor, budget), outcome_clock=clock)
    state = budget.inspect()
    assert len(opener.requests) == 1
    assert state['attempts'] == 1 and state['pending_token'] is None
    assert state['review_required'] and not state['completed']
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'OUTCOME_CLOCK_UNCERTAIN'
    assert state['last_evaluated_at'] == 1000
    assert state['next_allowed_at'] == 0 and not cursor.path.exists()
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EPISODE_CLOSED'):
        budget.begin(evaluated_at=2000, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


def test_uncertain_outcome_can_be_explicitly_closed_without_retry_or_io(
        tmp_path):
    _, budget = stores(tmp_path)
    pending = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    result = subject.close_pending_reconnect_for_outcome_uncertainty(
        reconnect_budget=budget,
        expected_budget_head=pending['head_sha256'], evaluated_at=1001,
        confirm_outcome_uncertain=True, confirm_no_retry=True)
    state = budget.inspect()
    assert result['pending_outcome_closed'] and not result['duplicate_close']
    assert result['outcome_uncertainty_preserved']
    assert not result['provider_request_performed']
    assert not result['credential_access_performed']
    assert not result['cursor_read_performed']
    assert not result['cursor_write_performed']
    assert not result['reconnect_attempt_reserved']
    assert not result['reconnect_attempt_consumed']
    assert not result['retry_scheduled'] and not result['automatic_retry']
    assert state['attempts'] == 1 and state['pending_token'] is None
    assert state['review_required'] and not state['completed']
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'OUTCOME_CLOCK_UNCERTAIN'
    assert state['next_allowed_at'] == 0
    assert all(result[flag] is False for flag in subject.FLAGS)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EPISODE_CLOSED'):
        budget.begin(evaluated_at=9999, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


def test_failed_clock_uncertainty_recovers_lost_result_acknowledgement(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    original = budget.finish

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('private lost acknowledgement')

    monkeypatch.setattr(budget, 'finish', lost_ack)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='OUTCOME_CLOCK_UNAVAILABLE'):
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: (_ for _ in ()).throw(
                RuntimeError('private clock failure')))
    state = budget.inspect()
    assert len(opener.requests) == 1 and not cursor.path.exists()
    assert state['review_required'] and state['pending_token'] is None
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'OUTCOME_CLOCK_UNCERTAIN'
    assert state['last_evaluated_at'] == 1000


@pytest.mark.parametrize('change', [
    {'head_sha256': 'f' * 64},
    {'last_result_head_sha256': 'f' * 64},
    {'last_requested_detail_code': 'NONRETRYABLE_FAILURE'},
    {'checkpoint_head_sha256': 'f' * 64},
])
def test_failed_clock_lost_ack_requires_exact_current_result(
        tmp_path, monkeypatch, change):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    original_finish = budget.finish
    original_inspect = budget.inspect
    committed = []

    def lost_ack(*args, **kwargs):
        original_finish(*args, **kwargs)
        committed.append(True)
        raise RuntimeError('private lost acknowledgement')

    def mismatched_inspect():
        retained = original_inspect()
        if committed:
            retained.update(change)
        return retained

    monkeypatch.setattr(budget, 'finish', lost_ack)
    monkeypatch.setattr(budget, 'inspect', mismatched_inspect)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_RECORD_UNCERTAIN'):
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: (_ for _ in ()).throw(
                RuntimeError('private clock failure')))
    assert len(opener.requests) == 1 and not cursor.path.exists()
    state = original_inspect()
    assert state['review_required'] and state['pending_token'] is None
    assert state['last_detail_code'] == 'OUTCOME_CLOCK_UNCERTAIN'


def test_failed_clock_result_inspection_failure_is_sanitized(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private')])
    original_inspect = budget.inspect
    original_finish = budget.finish
    committed = []

    def lost_ack(*args, **kwargs):
        original_finish(*args, **kwargs)
        committed.append(True)
        raise RuntimeError('private lost acknowledgement')

    def failed_inspect():
        if committed:
            raise RuntimeError('private path')
        return original_inspect()

    monkeypatch.setattr(budget, 'finish', lost_ack)
    monkeypatch.setattr(budget, 'inspect', failed_inspect)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_RECORD_UNCERTAIN') as error:
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: (_ for _ in ()).throw(
                RuntimeError('private clock failure')))
    assert 'private' not in str(error.value)
    assert len(opener.requests) == 1 and not cursor.path.exists()
    assert original_inspect()['last_detail_code'] == \
        'OUTCOME_CLOCK_UNCERTAIN'


@pytest.mark.parametrize('post_attempt', ['failure', 'malformed'])
def test_provider_failure_cursor_recovery_inspection_is_fail_closed(
        tmp_path, monkeypatch, post_attempt):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private provider')])
    original_inspect = cursor.inspect

    def inspect():
        if not opener.requests:
            return original_inspect()
        if post_attempt == 'failure':
            raise RuntimeError('private cursor path')
        return {'head_sha256': 'private cursor value'}

    monkeypatch.setattr(cursor, 'inspect', inspect)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='CURSOR_STATE_UNCERTAIN') as error:
        call(tmp_path, opener, stores=(cursor, budget))
    assert 'private' not in str(error.value)
    assert len(opener.requests) == 1
    state = budget.inspect()
    assert state['pending_token'] and state['last_outcome'] is None
    assert state['next_allowed_at'] == 0


def test_failed_clock_cursor_recovery_inspection_cannot_terminalize(
        tmp_path, monkeypatch):
    cursor, budget = stores(tmp_path)
    opener = Opener([TimeoutError('private provider')])
    original_inspect = cursor.inspect

    def inspect():
        if opener.requests:
            raise RuntimeError('private cursor path')
        return original_inspect()

    monkeypatch.setattr(cursor, 'inspect', inspect)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='CURSOR_STATE_UNCERTAIN') as error:
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: (_ for _ in ()).throw(
                RuntimeError('private clock failure')))
    assert 'private' not in str(error.value)
    assert len(opener.requests) == 1
    state = budget.inspect()
    assert state['pending_token'] and state['last_outcome'] is None
    assert not state['review_required'] and state['next_allowed_at'] == 0


def test_uncertain_outcome_close_lost_ack_replay_is_byte_stable(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    options = dict(reconnect_budget=budget,
        expected_budget_head=reserved['head_sha256'], evaluated_at=1001,
        confirm_outcome_uncertain=True, confirm_no_retry=True)
    first = subject.close_pending_reconnect_for_outcome_uncertainty(
        **options)
    saved = budget.path.read_bytes()
    replay = subject.close_pending_reconnect_for_outcome_uncertainty(
        **options)
    assert not first['duplicate_close'] and replay['duplicate_close']
    assert replay['reconnect_budget_head_sha256'] == \
        first['reconnect_budget_head_sha256']
    assert budget.path.read_bytes() == saved
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_REPLAY_DISAGREEMENT'):
        subject.close_pending_reconnect_for_outcome_uncertainty(
            **dict(options, evaluated_at=1002))
    assert budget.path.read_bytes() == saved


@pytest.mark.parametrize(('change','code'), [
    ({'confirm_outcome_uncertain': False},
        'OUTCOME_UNCERTAIN_CONFIRMATION_REQUIRED'),
    ({'confirm_no_retry': False},
        'OUTCOME_UNCERTAIN_NO_RETRY_CONFIRMATION_REQUIRED'),
    ({'expected_budget_head': 'f' * 64}, 'HEAD_CHANGED'),
    ({'evaluated_at': 999}, 'CLOCK_REGRESSION'),
])
def test_uncertain_outcome_close_denials_preserve_pending_attempt(
        tmp_path, change, code):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    saved = budget.path.read_bytes()
    options = dict(reconnect_budget=budget,
        expected_budget_head=reserved['head_sha256'], evaluated_at=1001,
        confirm_outcome_uncertain=True, confirm_no_retry=True)
    options.update(change)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError, match=code):
        subject.close_pending_reconnect_for_outcome_uncertainty(**options)
    assert budget.path.read_bytes() == saved
    state = budget.inspect()
    assert state['pending_token'] == reserved['token']
    assert state['last_outcome'] is None and not state['review_required']


@pytest.mark.parametrize(('status','detail'), [
    (429, 'ALPACA_ACTIVITY_SSE_TRANSPORT_RATE_LIMITED'),
    (503, 'ALPACA_ACTIVITY_SSE_TRANSPORT_SERVICE_UNAVAILABLE'),
])
def test_provider_retry_after_is_durable_and_never_shortened(
        tmp_path, status, detail):
    cursor, budget = stores(tmp_path)
    headers = Message()
    headers['Retry-After'] = '120'
    body = io.BytesIO(SECRET.encode())
    error = HTTPError(
        wire.activity_url('01K00000000000000000000003'),
        status, SECRET, headers, body)
    opener = Opener([error])
    with pytest.raises(ValueError,
            match='RATE_LIMITED|SERVICE_UNAVAILABLE'):
        call(tmp_path, opener, stores=(cursor, budget),
            outcome_clock=lambda: 1007)
    state = budget.inspect()
    assert body.closed and len(opener.requests) == 1
    assert state['last_outcome'] == 'RETRY_REQUIRED'
    assert state['last_detail_code'] == detail
    assert state['last_requested_retry_after_seconds'] == 120
    assert state['last_applied_backoff_seconds'] == 120
    assert state['provider_directed_backoff_observed']
    assert state['last_evaluated_at'] == 1007
    assert state['next_allowed_at'] == 1127
    saved = budget.path.read_bytes()
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='BACKOFF_ACTIVE'):
        budget.begin(evaluated_at=1126.999, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)
    assert budget.path.read_bytes() == saved
    second = budget.begin(evaluated_at=1127,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=state['head_sha256'],
        emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    assert second['attempt'] == 2


@pytest.mark.parametrize('status', [429, 503])
def test_provider_backoff_without_exact_retry_after_closes_for_review(
        tmp_path, status):
    cursor, budget = stores(tmp_path)
    body = io.BytesIO(SECRET.encode())
    error = HTTPError(
        wire.activity_url('01K00000000000000000000003'),
        status, SECRET, {}, body)
    with pytest.raises(ValueError, match='HTTP_STATUS_REVIEW'):
        call(tmp_path, Opener([error]), stores=(cursor, budget))
    state = budget.inspect()
    assert body.closed
    assert state['review_required'] and state['next_allowed_at'] == 0
    assert not state['provider_directed_backoff_observed']


@pytest.mark.parametrize(('change','code'), [
    ({'confirm_reconnect_attempt': False}, 'CONFIRMATION_REQUIRED'),
    ({'confirm_read_only_paper': False}, 'READ_AUTHORIZATION_REQUIRED'),
    ({'confirm_cursor_checkpoint': False}, 'CHECKPOINT_AUTHORIZATION_REQUIRED'),
    ({'outcome_clock': 0}, 'OUTCOME_CLOCK_INVALID'),
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


@pytest.mark.parametrize(('check','code'), [
    (lambda: True, 'EMERGENCY_STOP_ACTIVE'),
    (lambda: None, 'EMERGENCY_STOP_ACTIVE'),
    (lambda: (_ for _ in ()).throw(RuntimeError()),
        'EMERGENCY_STOP_ACTIVE'),
    (None, 'EMERGENCY_STOP_CHECK_INVALID'),
])
def test_emergency_stop_blocks_before_reconnect_storage_or_network(
        tmp_path, check, code):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    opener = Opener([raw])
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match=code):
        call(tmp_path, opener, stores=(cursor, budget),
            emergency_stop_check=check)
    assert not opener.requests
    assert not budget.path.exists() and not cursor.path.exists()


def test_budget_rechecks_clear_stop_inside_reservation_transaction(tmp_path):
    _, budget = stores(tmp_path)
    samples = []
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO,
        emergency_stop_check=lambda: samples.append(False) or False,
        confirm_reconnect_attempt=True)
    assert len(samples) == 2
    assert reserved['attempt'] == 1
    assert budget.inspect()['pending_token'] == reserved['token']


def test_budget_stop_raised_during_reservation_consumes_no_attempt(
        tmp_path):
    _, budget = stores(tmp_path)
    samples = iter((False, True))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EMERGENCY_STOP_ACTIVE'):
        budget.begin(evaluated_at=1000, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: next(samples),
            confirm_reconnect_attempt=True)
    state = budget.inspect()
    assert state['attempts'] == 0 and state['pending_token'] is None
    assert not state['review_required'] and state['head_sha256'] != ZERO
    retry = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=state['head_sha256'], emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    assert retry['attempt'] == 1


def test_reservation_lost_ack_exact_replay_is_byte_stable(tmp_path):
    _, budget = stores(tmp_path)
    samples = []
    options = dict(evaluated_at=1000, expected_cursor_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
        emergency_stop_check=lambda: samples.append(False) or False,
        confirm_reconnect_attempt=True)
    first = budget.begin(**options)
    saved = budget.path.read_bytes()
    replay = budget.begin(**options)
    assert not first['duplicate_reservation']
    assert first['provider_request_authorized']
    assert replay['duplicate_reservation']
    assert not replay['provider_request_authorized']
    assert replay['token'] == first['token']
    assert replay['attempt'] == first['attempt'] == 1
    assert budget.path.read_bytes() == saved
    assert samples == [False, False, False, False]


@pytest.mark.parametrize('change', [
    {'evaluated_at': 1001},
    {'expected_cursor_head': 'a' * 64},
    {'reconnect_reason': 'SLOW_CONSUMER'},
])
def test_reservation_replay_requires_exact_original_inputs(tmp_path, change):
    _, budget = stores(tmp_path)
    options = dict(evaluated_at=1000, expected_cursor_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
        emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    budget.begin(**options)
    saved = budget.path.read_bytes()
    options.update(change)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='BEGIN_REPLAY_DISAGREEMENT'):
        budget.begin(**options)
    assert budget.path.read_bytes() == saved


def test_reservation_replay_stop_race_preserves_pending_attempt(tmp_path):
    _, budget = stores(tmp_path)
    options = dict(evaluated_at=1000, expected_cursor_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
        emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    reserved = budget.begin(**options)
    saved = budget.path.read_bytes()
    samples = iter((False, True))
    options['emergency_stop_check'] = lambda: next(samples)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EMERGENCY_STOP_ACTIVE'):
        budget.begin(**options)
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['pending_token'] == reserved['token']


def test_coordinator_refuses_replayed_reservation_before_provider_io(
        tmp_path):
    _, raw = segments()
    cursor, budget = stores(tmp_path)
    budget.begin(evaluated_at=1000, expected_cursor_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
        emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    saved = budget.path.read_bytes()
    opener = Opener([raw])
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESERVATION_REPLAY_REQUIRES_REVIEW'):
        call(tmp_path, opener, stores=(cursor, budget),
            expected_budget_head=ZERO)
    assert not opener.requests
    assert budget.path.read_bytes() == saved
    assert not cursor.path.exists()


def test_legacy_pending_reservation_remains_readable_without_replay(
        tmp_path):
    _, budget = stores(tmp_path)
    budget.begin(evaluated_at=1000, expected_cursor_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
        emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    with sqlite3.connect(budget.path) as db:
        encoded, parent = db.execute(
            'SELECT body,previous FROM events WHERE seq=1').fetchone()
        body = json.loads(encoded)
        body.pop('expected_budget_head_sha256')
        digest = sha(dict(seq=1, body=body, previous=parent))
        db.execute('UPDATE events SET body=?,digest=? WHERE seq=1',
            (canonical(body), digest))
    legacy = budget.inspect()
    assert legacy['pending_token'] == legacy['head_sha256']
    assert legacy['last_begin_expected_head_sha256'] is None
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='HEAD_CHANGED'):
        budget.begin(evaluated_at=1000, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


def test_budget_stop_raised_at_due_retry_preserves_exact_backoff_state(
        tmp_path):
    _, budget = stores(tmp_path)
    first = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    waiting = budget.finish(first['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=STOP_CLEAR)
    saved = budget.path.read_bytes()
    samples = iter((False, True))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EMERGENCY_STOP_ACTIVE'):
        budget.begin(evaluated_at=1001, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=waiting['head_sha256'],
            emergency_stop_check=lambda: next(samples),
            confirm_reconnect_attempt=True)
    assert budget.path.read_bytes() == saved
    state = budget.inspect()
    assert state['attempts'] == 1
    assert state['last_outcome'] == 'RETRY_REQUIRED'
    assert state['next_allowed_at'] == 1001


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


def test_retry_result_requires_emergency_stop_check(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    saved = budget.path.read_bytes()
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EMERGENCY_STOP_CHECK_INVALID'):
        budget.finish(reserved['token'], evaluated_at=1000,
            outcome='RETRY_REQUIRED',
            detail_code=
                'ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE')
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['pending_token'] == reserved['token']


def test_retry_result_rechecks_clear_stop_inside_transaction(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    samples = []
    state = budget.finish(reserved['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=lambda: samples.append(False) or False)
    assert samples == [False, False]
    assert state['last_outcome'] == 'RETRY_REQUIRED'
    assert state['next_allowed_at'] == 1001


def test_retry_result_lost_ack_exact_replay_is_byte_stable(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    options = dict(evaluated_at=1000, outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=STOP_CLEAR)
    first = budget.finish(reserved['token'], **options)
    saved = budget.path.read_bytes()
    replay = budget.finish(reserved['token'], **options)
    assert not first['duplicate_result'] and replay['duplicate_result']
    assert replay['head_sha256'] == first['head_sha256']
    assert budget.path.read_bytes() == saved
    assert replay['attempts'] == 1 and replay['next_allowed_at'] == 1001


@pytest.mark.parametrize('detail', [
    'ALPACA_ACTIVITY_SSE_TRANSPORT_RATE_LIMITED',
    'ALPACA_ACTIVITY_SSE_TRANSPORT_SERVICE_UNAVAILABLE',
])
def test_provider_backoff_result_replay_requires_exact_delay(
        tmp_path, detail):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    options = dict(evaluated_at=1000, outcome='RETRY_REQUIRED',
        detail_code=detail,
        provider_retry_after_seconds=120,
        emergency_stop_check=STOP_CLEAR)
    first = budget.finish(reserved['token'], **options)
    saved = budget.path.read_bytes()
    replay = budget.finish(reserved['token'], **options)
    assert replay['duplicate_result']
    assert replay['head_sha256'] == first['head_sha256']
    assert budget.path.read_bytes() == saved
    options['provider_retry_after_seconds'] = 121
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_REPLAY_DISAGREEMENT'):
        budget.finish(reserved['token'], **options)
    assert budget.path.read_bytes() == saved


@pytest.mark.parametrize('change', [
    {'evaluated_at': 1001},
    {'detail_code': 'ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_FAILED'},
    {'outcome': 'REVIEW_REQUIRED', 'detail_code': 'NONRETRYABLE_FAILURE'},
    {'checkpoint_head_sha256': 'a' * 64},
])
def test_retry_result_replay_requires_exact_original_inputs(
        tmp_path, change):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    options = dict(evaluated_at=1000, outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=STOP_CLEAR)
    budget.finish(reserved['token'], **options)
    saved = budget.path.read_bytes()
    options.update(change)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RESULT_REPLAY_DISAGREEMENT'):
        budget.finish(reserved['token'], **options)
    assert budget.path.read_bytes() == saved


def test_converted_retry_result_exact_replay_recovers_lost_ack(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    options = dict(evaluated_at=1000, outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE')
    samples = iter((False, True))
    first = budget.finish(reserved['token'],
        emergency_stop_check=lambda: next(samples), **options)
    saved = budget.path.read_bytes()
    replay = budget.finish(reserved['token'],
        emergency_stop_check=lambda: True, **options)
    assert first['last_outcome'] == 'REVIEW_REQUIRED'
    assert first['last_requested_outcome'] == 'RETRY_REQUIRED'
    assert replay['duplicate_result']
    assert budget.path.read_bytes() == saved


def test_stop_conversion_retains_provider_instruction_but_schedules_nothing(
        tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    options = dict(evaluated_at=1000, outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_RATE_LIMITED',
        provider_retry_after_seconds=120)
    samples = iter((False, True))
    first = budget.finish(reserved['token'],
        emergency_stop_check=lambda: next(samples), **options)
    saved = budget.path.read_bytes()
    assert first['last_outcome'] == 'REVIEW_REQUIRED'
    assert first['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE'
    assert first['last_requested_retry_after_seconds'] == 120
    assert first['last_applied_backoff_seconds'] == 0
    assert first['provider_directed_backoff_observed']
    assert first['next_allowed_at'] == 0 and first['review_required']
    replay = budget.finish(reserved['token'],
        emergency_stop_check=lambda: True, **options)
    assert replay['duplicate_result']
    assert budget.path.read_bytes() == saved


def test_old_result_record_remains_replayable_after_upgrade(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    state = budget.finish(reserved['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=STOP_CLEAR)
    with sqlite3.connect(budget.path) as db:
        encoded, parent = db.execute(
            'SELECT body,previous FROM events WHERE seq=2').fetchone()
        body = json.loads(encoded)
        body.pop('requested_outcome')
        body.pop('requested_detail_code')
        body.pop('requested_retry_after_seconds')
        body.pop('applied_backoff_seconds')
        digest = sha(dict(seq=2, body=body, previous=parent))
        db.execute('UPDATE events SET body=?,digest=? WHERE seq=2',
            (canonical(body), digest))
    legacy = subject.ActivitySSEReconnectBudget(
        budget.path, ACCOUNT).inspect()
    assert legacy['head_sha256'] != state['head_sha256']
    assert legacy['last_requested_outcome'] == 'RETRY_REQUIRED'
    assert legacy['last_requested_detail_code'] == \
        'ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE'
    saved = budget.path.read_bytes()
    replay = budget.finish(reserved['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=STOP_CLEAR)
    assert replay['duplicate_result']
    assert budget.path.read_bytes() == saved


def test_old_result_cannot_be_replayed_after_next_attempt_begins(tmp_path):
    _, budget = stores(tmp_path)
    first = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    waiting = budget.finish(first['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=STOP_CLEAR)
    second = budget.begin(evaluated_at=1001,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=waiting['head_sha256'],
        emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    saved = budget.path.read_bytes()
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='TOKEN_CHANGED'):
        budget.finish(first['token'], evaluated_at=1000,
            outcome='RETRY_REQUIRED',
            detail_code=
                'ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
            emergency_stop_check=STOP_CLEAR)
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['pending_token'] == second['token']


@pytest.mark.parametrize('check', [
    lambda: True,
    lambda: None,
    lambda: (_ for _ in ()).throw(RuntimeError()),
])
def test_active_or_indeterminate_stop_closes_retry_result_for_review(
        tmp_path, check):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    state = budget.finish(reserved['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=check)
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE'
    assert state['next_allowed_at'] == 0 and state['review_required']
    assert state['pending_token'] is None


def test_stop_raised_inside_retry_result_transaction_schedules_no_retry(
        tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    samples = iter((False, True))
    state = budget.finish(reserved['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=lambda: next(samples))
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE'
    assert state['next_allowed_at'] == 0 and state['review_required']
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='EPISODE_CLOSED'):
        budget.begin(evaluated_at=9999, expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=state['head_sha256'],
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


def test_coordinator_stop_raised_during_failure_result_schedules_no_retry(
        tmp_path):
    cursor, budget = stores(tmp_path)
    samples = []
    def check():
        # The first six samples cover preflight, reservation, and transport;
        # the seventh is the in-transaction retry-result fence.
        value = len(samples) >= 6
        samples.append(value)
        return value
    with pytest.raises(ValueError, match='TRANSPORT_UNAVAILABLE'):
        call(tmp_path, Opener([TimeoutError('private')]),
            stores=(cursor, budget), emergency_stop_check=check)
    state = budget.inspect()
    assert samples == [False] * 6 + [True]
    assert state['last_outcome'] == 'REVIEW_REQUIRED'
    assert state['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE'
    assert state['next_allowed_at'] == 0 and state['review_required']
    assert state['pending_token'] is None


def test_crash_after_begin_is_never_expired_or_retried(tmp_path):
    _, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    cold = subject.ActivitySSEReconnectBudget(budget.path, ACCOUNT)
    assert cold.inspect()['pending_token'] == reserved['token']
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='PENDING_REVIEW'):
        cold.begin(evaluated_at=999999,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED',
            expected_head=reserved['head_sha256'],
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


def test_emergency_stop_aborts_pending_reservation_without_external_access(
        tmp_path):
    cursor, budget = stores(tmp_path)
    reserved = budget.begin(evaluated_at=1000,
        expected_cursor_head=ZERO, reconnect_reason='CONNECTION_DROPPED',
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
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
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
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
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
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
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
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
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
    state = budget.finish(reserved['token'], evaluated_at=1000,
        outcome='RETRY_REQUIRED',
        detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
        emergency_stop_check=STOP_CLEAR)
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
            emergency_stop_check=STOP_CLEAR,
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
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
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
        expected_head=ZERO, emergency_stop_check=STOP_CLEAR,
        confirm_reconnect_attempt=True)
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


def test_reconciliation_recovers_exact_lost_result_acknowledgement(
        tmp_path, monkeypatch):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    original = budget.finish

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('private lost acknowledgement')

    monkeypatch.setattr(budget, 'finish', lost_ack)
    result = subject.reconcile_pending_checkpoint(cursor_store=cursor,
        reconnect_budget=budget,
        expected_budget_head=reserved['head_sha256'],
        expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
        evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert result['pending_checkpoint_reconciled']
    assert result['result_acknowledgement_recovered']
    assert not result['duplicate_recovery']
    assert budget.inspect()['completed']


def test_reconciliation_lost_ack_inspection_failure_is_sanitized(
        tmp_path, monkeypatch):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    original_finish = budget.finish
    original_inspect = budget.inspect
    committed = False

    def lost_ack(*args, **kwargs):
        nonlocal committed
        original_finish(*args, **kwargs)
        committed = True
        raise RuntimeError('private lost acknowledgement')

    def failed_recovery_inspect():
        if committed:
            raise RuntimeError('private recovery inspection detail')
        return original_inspect()

    monkeypatch.setattr(budget, 'finish', lost_ack)
    monkeypatch.setattr(budget, 'inspect', failed_recovery_inspect)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RECOVERY_RESULT_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert len(opener.requests) == 1
    assert original_inspect()['completed']


def test_reconciliation_final_result_inspection_failure_is_sanitized(
        tmp_path, monkeypatch):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    original_finish = budget.finish
    original_inspect = budget.inspect
    committed = False

    def finish(*args, **kwargs):
        nonlocal committed
        result = original_finish(*args, **kwargs)
        committed = True
        return result

    def failed_stable_inspect():
        if committed:
            raise RuntimeError('private stable inspection detail')
        return original_inspect()

    monkeypatch.setattr(budget, 'finish', finish)
    monkeypatch.setattr(budget, 'inspect', failed_stable_inspect)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RECOVERY_RESULT_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert len(opener.requests) == 1
    assert original_inspect()['completed']


@pytest.mark.parametrize('malformed', (None, {}, [], {'head_sha256': None}))
def test_reconciliation_lost_ack_malformed_result_view_fails_closed(
        tmp_path, monkeypatch, malformed):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    original_finish = budget.finish
    original_inspect = budget.inspect
    committed = False

    def lost_ack(*args, **kwargs):
        nonlocal committed
        original_finish(*args, **kwargs)
        committed = True
        raise RuntimeError('private lost acknowledgement')

    def malformed_recovery_inspect():
        return malformed if committed else original_inspect()

    monkeypatch.setattr(budget, 'finish', lost_ack)
    monkeypatch.setattr(budget, 'inspect', malformed_recovery_inspect)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RECOVERY_RESULT_UNCERTAIN'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert original_inspect()['completed']


def test_reconciliation_rejects_uncommitted_result_acknowledgement_failure(
        tmp_path, monkeypatch):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    monkeypatch.setattr(budget, 'finish',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('private uncommitted failure')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RECOVERY_RESULT_UNCERTAIN'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved
    assert budget.inspect()['pending_token'] == reserved['token']


def test_reconciliation_initial_budget_inspection_failure_is_sanitized(
        tmp_path, monkeypatch):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    original_inspect = budget.inspect
    monkeypatch.setattr(budget, 'inspect',
        lambda: (_ for _ in ()).throw(
            RuntimeError('private initial inspection detail')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_RECOVERY_STATE_UNCERTAIN'
    assert 'private' not in str(raised.value)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved
    assert original_inspect()['pending_token'] == reserved['token']


@pytest.mark.parametrize('malformed', (None, {}, [], {'head_sha256': None}))
def test_reconciliation_malformed_initial_budget_view_fails_closed(
        tmp_path, monkeypatch, malformed):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    original_inspect = budget.inspect
    monkeypatch.setattr(budget, 'inspect', lambda: malformed)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RECOVERY_STATE_UNCERTAIN'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved
    assert original_inspect()['pending_token'] == reserved['token']


@pytest.mark.parametrize('field', subject.FLAGS)
def test_reconciliation_budget_view_cannot_promote_authority(tmp_path,
        monkeypatch, field):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    original = budget.inspect
    monkeypatch.setattr(budget, 'inspect',
        lambda: {**original(), field: True})
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RECOVERY_STATE_UNCERTAIN'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved
    assert original()['pending_token'] == reserved['token']


@pytest.mark.parametrize('kind', ('mapping_subclass', 'extra_field',
    'promoted_authority'))
def test_reconciliation_rejects_noncanonical_budget_result_acknowledgement(
        tmp_path, monkeypatch, kind):
    class BudgetMapping(dict):
        pass

    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    original_finish = budget.finish
    original_inspect = budget.inspect

    def altered_finish(*args, **kwargs):
        value = original_finish(*args, **kwargs)
        if kind == 'mapping_subclass':
            return BudgetMapping(value)
        if kind == 'extra_field':
            return {**value, 'private_extension': False}
        return {**value, 'release_approved': True}

    monkeypatch.setattr(budget, 'finish', altered_finish)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RECOVERY_RESULT_UNCERTAIN'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    state = original_inspect()
    assert state['completed'] and state['pending_token'] is None


def test_reconciliation_cursor_binding_failure_is_sanitized(
        tmp_path, monkeypatch):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    monkeypatch.setattr(cursor, 'latest_checkpoint_binding',
        lambda: (_ for _ in ()).throw(
            RuntimeError('private cursor binding detail')))
    with pytest.raises(subject.ActivitySSEReconnectBudgetError) as raised:
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert str(raised.value) == \
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_CURSOR_BINDING_REVIEW_FAILED'
    assert 'private' not in str(raised.value)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved


@pytest.mark.parametrize('malformed', (None, {}, [], {'head_sha256': None}))
def test_reconciliation_malformed_cursor_binding_fails_closed(
        tmp_path, monkeypatch, malformed):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    monkeypatch.setattr(cursor, 'latest_checkpoint_binding',
        lambda: malformed)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='CURSOR_BINDING_REVIEW_FAILED'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved


@pytest.mark.parametrize('field', subject.FLAGS)
def test_reconciliation_binding_cannot_promote_authority(tmp_path,
        monkeypatch, field):
    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    saved = budget.path.read_bytes()
    original = cursor.latest_checkpoint_binding
    monkeypatch.setattr(cursor, 'latest_checkpoint_binding',
        lambda: {**original(), field: True})
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='CURSOR_BINDING_REVIEW_FAILED'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert len(opener.requests) == 1
    assert budget.path.read_bytes() == saved


def test_reconciliation_post_result_binding_rejects_custom_equality(
        tmp_path, monkeypatch):
    class EqualMapping(dict):
        def __eq__(self, other): return True
        def __ne__(self, other): return False

    cursor, budget, reserved, checkpoint, opener = \
        committed_pending_checkpoint(tmp_path)
    original = cursor.latest_checkpoint_binding
    samples = []

    def forged_stability_view():
        samples.append(True)
        retained = original()
        return retained if len(samples) == 1 else EqualMapping(retained)

    monkeypatch.setattr(cursor, 'latest_checkpoint_binding',
        forged_stability_view)
    with pytest.raises(subject.ActivitySSEReconnectBudgetError,
            match='RECOVERY_RESULT_UNCERTAIN'):
        subject.reconcile_pending_checkpoint(cursor_store=cursor,
            reconnect_budget=budget,
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=checkpoint['checkpoint_head_sha256'],
            evaluated_at=1001, confirm_checkpoint_reconciliation=True)
    assert samples == [True, True] and len(opener.requests) == 1
    state = budget.inspect()
    assert state['completed'] and state['pending_token'] is None


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
            emergency_stop_check=STOP_CLEAR,
            confirm_reconnect_attempt=True)


def test_account_binding_and_private_values_never_reach_result(tmp_path):
    _, raw = segments()
    result, _, budget = call(tmp_path, Opener([raw]))
    encoded = json.dumps([result, budget.inspect()], sort_keys=True)
    assert all(value not in encoded for value in (
        ACCOUNT, KEY, SECRET, '01K0000', 'fixture_client'))
    assert all(result[flag] is False for flag in subject.FLAGS)
