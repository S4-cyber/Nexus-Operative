"""Exact-head one-attempt Activity SSE checkpoint composition tests."""
import copy
import io
import json

import pytest

from nexus import alpaca_activity_sse_checkpoint as subject
from nexus.alpaca_activity_sse_cursor_store import (
    ZERO, ActivitySSECursorStore)
from nexus.alpaca_activity_sse_review import _event
from nexus.alpaca_paper_contract import canonical


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
KEY = 'fixture_key_only_123'
SECRET = 'fixture_secret_only_123'


def segments(suffix=''):
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
    raw = (''.join('data: ' + canonical(row) + '\n\n'
        for row in replay) + suffix).encode()
    return initial, replay, raw


class Response:
    status = 200
    headers = {'Content-Type': 'text/event-stream; charset=utf-8'}
    def __init__(self, raw, target=None):
        self.body = io.BytesIO(raw)
        self.target = target
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def geturl(self): return self.target
    def read(self, size): return self.body.read(size)


class Opener:
    def __init__(self, raw, callback=None):
        self.raw = raw
        self.callback = callback
        self.requests = []
        self.response = None
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.callback is not None:
            self.callback()
        self.response = Response(self.raw, request.full_url)
        return self.response


def run(tmp_path, **changes):
    initial, _, raw = segments()
    store = changes.pop('cursor_store',
        ActivitySSECursorStore(tmp_path / 'cursor.sqlite3', ACCOUNT))
    opener = changes.pop('opener', Opener(raw))
    options = dict(expected_account_id=ACCOUNT, initial_events=initial,
        reconnect_reason='CONNECTION_DROPPED', cursor_store=store,
        expected_cursor_head=ZERO, confirm_read_only_paper=True,
        confirm_cursor_checkpoint=True, opener=opener,
        wall_clock=lambda: 1000, elapsed_clock=lambda: 50,
        permission_check=lambda: True,
        emergency_stop_check=lambda: False,
        credential_check=lambda: True)
    options.update(changes)
    return subject.collect_and_checkpoint_once(KEY, SECRET, **options), store, opener


def test_single_get_review_and_exact_head_checkpoint_are_aggregate_only(tmp_path):
    result, store, opener = run(tmp_path)
    cold = ActivitySSECursorStore(store.path, ACCOUNT).inspect()
    assert result['mode'] == 'EXPLICIT_ALPACA_ACTIVITY_SSE_CHECKPOINT'
    assert result['single_get_attempt'] and result['single_reconnect_attempt']
    assert result['cursor_checkpoint_eligible'] and result['cursor_written']
    assert result['transport_input_bound']
    assert result['complete_review_counts_bound']
    assert result['redelivered_events_deduplicated'] == 1
    assert result['new_events_processed'] == 1
    assert len(opener.requests) == 1 and opener.response.closed
    assert cold['current_event_id'] == '01K00000000000000000000004'
    assert cold['head_sha256'] == result['checkpoint_head_sha256']
    binding = store.latest_checkpoint_binding()
    assert binding['transport_input_bound']
    assert binding['transport_input_sha256'] == \
        result['transport_input_sha256']
    encoded = json.dumps(result, sort_keys=True)
    assert all(token not in encoded for token in (ACCOUNT, KEY, SECRET,
        '01K0000', '11111111', 'fixture_client'))
    assert not result['automatic_retry']
    assert not result['automatic_reconnect_performed']
    assert not result['ledger_applied']
    assert not result['reconnect_reservation_bound']
    assert all(result[flag] is False for flag in subject.FLAGS)


@pytest.mark.parametrize(('option','value','code'), [
    ('confirm_read_only_paper', False, 'READ_AUTHORIZATION_REQUIRED'),
    ('confirm_cursor_checkpoint', False,
        'CHECKPOINT_AUTHORIZATION_REQUIRED'),
    ('cursor_store', object(), 'CURSOR_STORE_INVALID'),
    ('expected_cursor_head', 'bad', 'EXPECTED_HEAD_INVALID'),
    ('reconnect_reservation_token', 'bad',
        'RECONNECT_RESERVATION_INVALID'),
    ('permission_check', None, 'CHECK_INVALID'),
])
def test_invalid_admission_is_network_and_storage_silent(
        tmp_path, option, value, code):
    initial, _, raw = segments()
    path = tmp_path / 'cursor.sqlite3'
    store = ActivitySSECursorStore(path, ACCOUNT)
    opener = Opener(raw)
    options = dict(expected_account_id=ACCOUNT, initial_events=initial,
        reconnect_reason='CONNECTION_DROPPED', cursor_store=store,
        expected_cursor_head=ZERO, confirm_read_only_paper=True,
        confirm_cursor_checkpoint=True, opener=opener,
        permission_check=lambda: True,
        emergency_stop_check=lambda: False,
        credential_check=lambda: True)
    options[option] = value
    with pytest.raises(subject.ActivitySSECheckpointError, match=code):
        subject.collect_and_checkpoint_once(KEY, SECRET, **options)
    assert not opener.requests and not path.exists()


@pytest.mark.parametrize(('permission','stop','credential'), [
    (lambda: False, lambda: False, lambda: True),
    (lambda: True, lambda: True, lambda: True),
    (lambda: True, lambda: False, lambda: False),
])
def test_safety_veto_before_transport_is_network_silent(tmp_path,
        permission, stop, credential):
    initial, _, raw = segments()
    opener = Opener(raw)
    path = tmp_path / 'cursor.sqlite3'
    with pytest.raises(subject.ActivitySSECheckpointError,
            match='ADMISSION_REVOKED'):
        run(tmp_path, cursor_store=ActivitySSECursorStore(path, ACCOUNT),
            opener=opener, permission_check=permission,
            emergency_stop_check=stop, credential_check=credential)
    assert not opener.requests and not path.exists()


def test_stale_expected_head_and_account_binding_fail_before_io(tmp_path):
    initial, _, raw = segments()
    for store, code in (
            (ActivitySSECursorStore(tmp_path / 'a.sqlite3', ACCOUNT),
                'CURSOR_HEAD_CHANGED'),
            (ActivitySSECursorStore(tmp_path / 'b.sqlite3',
                'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
                'ACCOUNT_DISAGREEMENT')):
        opener = Opener(raw)
        with pytest.raises(subject.ActivitySSECheckpointError, match=code):
            run(tmp_path, cursor_store=store, opener=opener,
                expected_cursor_head='f' * 64)
        assert not opener.requests and not store.path.exists()


def test_initialized_store_must_match_supplied_prior_cursor(tmp_path):
    result, store, _ = run(tmp_path)
    initial, _, raw = segments()
    opener = Opener(raw)
    with pytest.raises(subject.ActivitySSECheckpointError,
            match='PERSISTED_CURSOR_DISAGREEMENT'):
        subject.collect_and_checkpoint_once(KEY, SECRET,
            expected_account_id=ACCOUNT, initial_events=initial,
            reconnect_reason='CONNECTION_DROPPED', cursor_store=store,
            expected_cursor_head=result['checkpoint_head_sha256'],
            confirm_read_only_paper=True, confirm_cursor_checkpoint=True,
            opener=opener, permission_check=lambda: True,
            emergency_stop_check=lambda: False,
            credential_check=lambda: True)
    assert not opener.requests


def test_interrupted_wire_segment_never_checkpoints(tmp_path):
    initial, _, raw = segments(
        ': you are reading too slowly, dropped 2 messages\n\n')
    path = tmp_path / 'cursor.sqlite3'
    opener = Opener(raw)
    with pytest.raises(subject.ActivitySSECheckpointError,
            match='INTERRUPTED_SEGMENT_INELIGIBLE'):
        run(tmp_path, initial_events=initial,
            cursor_store=ActivitySSECursorStore(path, ACCOUNT), opener=opener)
    assert len(opener.requests) == 1 and opener.response.closed
    assert not path.exists()


def test_reconnect_reason_disagreement_never_checkpoints(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    initial, _, raw = segments()
    opener = Opener(raw)
    with pytest.raises(subject.ActivitySSECheckpointError,
            match='RECONNECT_REVIEW_FAILED'):
        run(tmp_path, initial_events=initial,
            reconnect_reason='UNKNOWN',
            cursor_store=ActivitySSECursorStore(path, ACCOUNT), opener=opener)
    assert len(opener.requests) == 1 and not path.exists()


def test_cursor_store_binding_change_during_io_never_checkpoints(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    moved = tmp_path / 'moved.sqlite3'
    store = ActivitySSECursorStore(path, ACCOUNT)
    _, _, raw = segments()
    opener = Opener(raw, callback=lambda: setattr(store, 'path', moved))
    with pytest.raises(subject.ActivitySSECheckpointError,
            match='CURSOR_STORE_CHANGED'):
        run(tmp_path, cursor_store=store, opener=opener)
    assert len(opener.requests) == 1
    assert not path.exists() and not moved.exists()


def test_input_is_deep_copied_before_transport(tmp_path):
    initial, _, raw = segments()
    original = copy.deepcopy(initial)
    result, _, _ = run(tmp_path, initial_events=initial, opener=Opener(raw))
    assert initial == original and result['cursor_written']


def test_fixture_is_deterministic_disposable_and_non_authoritative():
    first = subject.fixture_report()
    assert first == subject.fixture_report() and first['passed']
    assert first['actual_storage_retained'] is False
    assert first['actual_provider_access'] is False
    assert all(first[flag] is False for flag in subject.FLAGS)
