"""Private Activity SSE cursor checkpoint durability tests."""
import copy
import json
from pathlib import Path
import sqlite3

import pytest

from nexus import alpaca_activity_sse_cursor_store as subject
from nexus.alpaca_activity_sse_reconnect_review import review
from nexus.alpaca_activity_sse_review import _event


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'


def pair(offset=0, order='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        client='fixture_client'):
    trade_event = f'01K0000000000000000000000{1 + offset}'
    fee_event = f'01K0000000000000000000000{2 + offset}'
    trade_ref = f'{1 + offset:08d}-1111-1111-1111-111111111111'
    fee_ref = f'{2 + offset:08d}-2222-2222-2222-222222222222'
    trade = _event(trade_event, trade_ref, 'TRD',
        details={**_event(trade_event, trade_ref, 'TRD')['details'],
            'order_id': order, 'client_order_id': client},
        at=f'2026-10-18T18:{offset:02d}:00.100000000Z',
        executed_at=f'2026-10-18T18:{offset:02d}:00.000000000Z')
    fee = _event(fee_event, fee_ref, 'FEE',
        details={'parent_id': trade_ref, 'system_date': '2026-10-18'},
        at=f'2026-10-18T18:{offset + 1:02d}:00.100000000Z',
        executed_at=f'2026-10-18T18:{offset + 1:02d}:00.000000000Z')
    return [trade, fee]


def checked(initial=None, later=None):
    initial = pair() if initial is None else initial
    later = pair(2, 'cccccccc-cccc-cccc-cccc-cccccccccccc',
        'fixture_client_2') if later is None else later
    result = review(initial, [*copy.deepcopy(initial), *later],
        expected_account_id=ACCOUNT,
        persisted_event_id=initial[-1]['event_id'],
        reconnect_since_id=initial[-1]['event_id'],
        reconnect_reason='CONNECTION_DROPPED')
    return result, initial[-1]['event_id'], later[-1]['event_id']


def checkpoint(store, report=None, before=None, after=None, **changes):
    if report is None:
        report, before, after = checked()
    args = dict(previous_cursor=before, next_cursor=after,
        expected_head=subject.ZERO)
    args.update(changes)
    return store.checkpoint(report, **args)


def test_checkpoint_reopens_exact_private_cursor_without_authority(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    saved = checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
        report, before, after)
    cold = subject.ActivitySSECursorStore(path, ACCOUNT).inspect()
    assert cold['initialized'] and cold['current_event_id'] == after
    assert cold['head_sha256'] == saved['head_sha256']
    assert cold['local_cursor_checkpoint_committed']
    assert all(cold[flag] is False for flag in subject.FLAGS)
    content = path.read_bytes()
    assert ACCOUNT.encode() not in content
    assert b'fixture_client' not in content
    assert b'11111111-1111' not in content


def test_exact_stale_head_replay_is_idempotent_and_byte_stable(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    first = checkpoint(store, report, before, after)
    saved = path.read_bytes()
    replay = checkpoint(store, report, before, after)
    assert replay['duplicate'] and replay['head_sha256'] == first['head_sha256']
    assert path.read_bytes() == saved


def test_checkpoint_can_bind_reconnect_reservation_for_exact_recovery(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    token = 'a' * 64
    saved = checkpoint(store, reconnect_reservation_token=token)
    binding = subject.ActivitySSECursorStore(
        path, ACCOUNT).latest_checkpoint_binding()
    assert saved['reconnect_reservation_bound']
    assert binding['head_sha256'] == saved['head_sha256']
    assert binding['expected_head_sha256'] == subject.ZERO
    assert binding['checkpoint_receipt_sha256'] == saved['receipt_sha256']
    assert binding['reconnect_reservation_sha256'] == subject.sha(token)
    assert binding['reconnect_reservation_bound']
    assert 'current_event_id' not in binding
    assert all(binding[flag] is False for flag in subject.FLAGS)


def test_invalid_reconnect_reservation_never_creates_storage(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    with pytest.raises(ValueError, match='RECONNECT_RESERVATION_INVALID'):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            reconnect_reservation_token='bad')
    assert not path.exists()


def test_second_checkpoint_requires_exact_cursor_and_head(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    first_report, first_before, first_after = checked()
    first = checkpoint(store, first_report, first_before, first_after)
    initial = pair(2, 'cccccccc-cccc-cccc-cccc-cccccccccccc',
        'fixture_client_2')
    later = pair(4, 'dddddddd-dddd-dddd-dddd-dddddddddddd',
        'fixture_client_3')
    second_report, second_before, second_after = checked(initial, later)
    second = checkpoint(store, second_report, second_before, second_after,
        expected_head=first['head_sha256'])
    assert second['head_sha256'] != first['head_sha256']
    assert store.inspect()['current_event_id'] == second_after
    with pytest.raises(ValueError, match='CURSOR_CHANGED'):
        checkpoint(store, first_report, first_before, first_after,
            expected_head=second['head_sha256'])


def test_changed_stale_head_transition_is_refused_unchanged(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    checkpoint(store, report, before, after)
    saved = path.read_bytes()
    changed = copy.deepcopy(report)
    changed['input_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='HEAD_CHANGED'):
        checkpoint(store, changed, before, after)
    assert path.read_bytes() == saved


def test_postcommit_lost_ack_reopens_and_exact_replay_is_safe(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    calls = 0

    def admission():
        nonlocal calls
        calls += 1
        return report['input_sha256'] if calls < 4 else 'f' * 64

    with pytest.raises(ValueError, match='INPUT_REVOKED_AFTER_COMMIT'):
        checkpoint(store, report, before, after, admission=admission)
    cold = subject.ActivitySSECursorStore(path, ACCOUNT)
    assert cold.inspect()['current_event_id'] == after
    replay = checkpoint(cold, report, before, after)
    assert replay['duplicate'] and cold.inspect()['retained_checkpoints'] == 1


@pytest.mark.parametrize(('mutation','code'), [
    (lambda value: value.__setitem__('account_sha256', 'f' * 64),
        'REVIEW_INVALID'),
    (lambda value: value.__setitem__('cursor_persisted_durably', True),
        'REVIEW_INVALID'),
    (lambda value: value.__setitem__('next_cursor_sha256', 'f' * 64),
        'REVIEW_CURSOR_DISAGREEMENT'),
    (lambda value: value.__setitem__('new_events_processed', 0),
        'COUNT_INVALID'),
])
def test_invalid_review_never_creates_storage(tmp_path, mutation, code):
    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    mutation(report)
    with pytest.raises(ValueError, match=code):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            report, before, after)
    assert not path.exists()


def test_account_mismatch_and_cursor_regression_fail_before_storage(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    with pytest.raises(ValueError, match='REVIEW_INVALID'):
        checkpoint(subject.ActivitySSECursorStore(path,
            'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'), report, before, after)
    assert not path.exists()
    with pytest.raises(ValueError, match='CURSOR_REGRESSION'):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            report, before, before)
    assert not path.exists()


def test_tamper_and_foreign_binding_fail_closed(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    checkpoint(store)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE checkpoints SET previous=? WHERE seq=1", ('f' * 64,))
    with pytest.raises(ValueError, match='CHAIN_DISAGREEMENT'):
        store.inspect()
    with pytest.raises(ValueError, match='ACCOUNT_OR_SCHEMA'):
        subject.ActivitySSECursorStore(path,
            'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa').inspect()


def test_symlink_and_rollback_sidecar_are_refused(tmp_path):
    target = tmp_path / 'target.sqlite3'
    target.write_bytes(b'')
    alias = tmp_path / 'alias.sqlite3'
    alias.symlink_to(target)
    with pytest.raises(ValueError, match='SYMLINK'):
        subject.ActivitySSECursorStore(alias, ACCOUNT).inspect()
    path = tmp_path / 'cursor.sqlite3'
    Path(str(path) + '-journal').write_bytes(b'pending')
    with pytest.raises(ValueError, match='ROLLBACK_REVIEW_REQUIRED'):
        subject.ActivitySSECursorStore(path, ACCOUNT).inspect()


def test_capacity_is_bounded_without_partial_append(tmp_path, monkeypatch):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    first = checkpoint(store, report, before, after)
    monkeypatch.setattr(subject, 'MAX_CHECKPOINTS', 1)
    initial = pair(2, 'cccccccc-cccc-cccc-cccc-cccccccccccc',
        'fixture_client_2')
    later = pair(4, 'dddddddd-dddd-dddd-dddd-dddddddddddd',
        'fixture_client_3')
    second_report, second_before, second_after = checked(initial, later)
    saved = path.read_bytes()
    with pytest.raises(ValueError, match='CHECKPOINT_CAP'):
        checkpoint(store, second_report, second_before, second_after,
            expected_head=first['head_sha256'])
    assert path.read_bytes() == saved


def test_fixture_is_aggregate_only_disposable_and_deterministic():
    first = subject.fixture_report()
    assert first == subject.fixture_report() and first['passed']
    encoded = json.dumps(first)
    assert all(token not in encoded for token in (
        '01234567', '01K0000', '11111111', 'fixture_client',
        'PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_CHECKPOINT'))
    assert first['actual_storage_retained'] is False
    assert all(first[flag] is False for flag in subject.FLAGS)
