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


def rewrite_checkpoint(path, mutation):
    """Rehash one checkpoint after a test-only payload mutation."""
    with sqlite3.connect(path) as db:
        encoded, previous = db.execute(
            'SELECT body,previous FROM checkpoints WHERE seq=1').fetchone()
        body = json.loads(encoded)
        mutation(body)
        body['receipt_sha256'] = subject.sha(body['scope'])
        encoded = subject.canonical(body)
        digest = subject.sha(dict(seq=1, body=body, previous=previous))
        db.execute('UPDATE checkpoints SET body=?,digest=? WHERE seq=1',
            (encoded, digest))


def test_checkpoint_lock_contention_fails_closed_then_recovers(
        tmp_path, monkeypatch):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    # Establish a complete schema before holding a separate writer open.
    first = checkpoint(store, report, before, after)
    initial = pair(2, 'cccccccc-cccc-cccc-cccc-cccccccccccc',
        'fixture_client_2')
    later = pair(4, 'dddddddd-dddd-dddd-dddd-dddddddddddd',
        'fixture_client_3')
    second_report, second_before, second_after = checked(initial, later)
    monkeypatch.setattr(subject, 'DB_BUSY_TIMEOUT_MS', 50)
    blocker = sqlite3.connect(path)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(ValueError,
                match='ALPACA_ACTIVITY_SSE_CURSOR_STORE_DATABASE_BUSY'):
            store.checkpoint(second_report, previous_cursor=second_before,
                next_cursor=second_after,
                expected_head=first['head_sha256'])
    finally:
        blocker.rollback()
        blocker.close()
    recovered = store.checkpoint(second_report,
        previous_cursor=second_before, next_cursor=second_after,
        expected_head=first['head_sha256'])
    assert recovered['head_sha256'] != first['head_sha256']


def test_checkpoint_commit_contention_fails_closed_then_recovers(
        tmp_path, monkeypatch):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    first_report, first_before, first_after = checked()
    first = checkpoint(store, first_report, first_before, first_after)
    initial = pair(2, 'cccccccc-cccc-cccc-cccc-cccccccccccc',
        'fixture_client_2')
    later = pair(4, 'dddddddd-dddd-dddd-dddd-dddddddddddd',
        'fixture_client_3')
    second_report, second_before, second_after = checked(initial, later)
    monkeypatch.setattr(subject, 'DB_BUSY_TIMEOUT_MS', 50)
    blocker = sqlite3.connect(path)
    blocker.execute('BEGIN')
    blocker.execute('SELECT seq FROM checkpoints LIMIT 1').fetchone()
    try:
        with pytest.raises(ValueError,
                match='ALPACA_ACTIVITY_SSE_CURSOR_STORE_DATABASE_BUSY'):
            store.checkpoint(second_report, previous_cursor=second_before,
                next_cursor=second_after,
                expected_head=first['head_sha256'])
    finally:
        blocker.rollback()
        blocker.close()
    assert store.inspect()['head_sha256'] == first['head_sha256']
    recovered = store.checkpoint(second_report,
        previous_cursor=second_before, next_cursor=second_after,
        expected_head=first['head_sha256'])
    assert recovered['head_sha256'] != first['head_sha256']


@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
def test_public_read_lock_contention_fails_closed_then_recovers(
        tmp_path, monkeypatch, method):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    saved = checkpoint(store)
    expected = getattr(store, method)()
    original = path.read_bytes()
    monkeypatch.setattr(subject, 'DB_BUSY_TIMEOUT_MS', 50)
    blocker = sqlite3.connect(path)
    blocker.execute('BEGIN EXCLUSIVE')
    try:
        with pytest.raises(ValueError,
                match='ALPACA_ACTIVITY_SSE_CURSOR_STORE_DATABASE_BUSY'):
            getattr(store, method)()
    finally:
        blocker.rollback()
        blocker.close()
    assert path.read_bytes() == original
    recovered = getattr(store, method)()
    assert recovered == expected
    assert recovered['head_sha256'] == saved['head_sha256']


def test_checkpoint_reopens_exact_private_cursor_without_authority(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    saved = checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
        report, before, after)
    cold = subject.ActivitySSECursorStore(path, ACCOUNT).inspect()
    assert cold['initialized'] and cold['current_event_id'] == after
    assert cold['head_sha256'] == saved['head_sha256']
    assert cold['local_cursor_checkpoint_committed']
    assert cold['complete_review_counts_bound']
    assert saved['complete_review_counts_bound']
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


def test_exact_replay_same_inode_rewrite_withholds_duplicate_receipt(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    first = checkpoint(store, report, before, after)
    calls = 0

    def admission():
        nonlocal calls
        calls += 1
        if calls == 2:
            with path.open('r+b', buffering=0) as stream:
                stream.seek(60)
                stream.write((1).to_bytes(4, 'big'))
        return report['input_sha256']

    with pytest.raises(ValueError, match=
            'ALPACA_ACTIVITY_SSE_CURSOR_STORE_FILE_SET_CHANGED'):
        checkpoint(store, report, before, after, admission=admission)
    cold = subject.ActivitySSECursorStore(path, ACCOUNT)
    retained = cold.inspect()
    assert retained['head_sha256'] == first['head_sha256']
    replay = checkpoint(cold, report, before, after)
    assert replay['duplicate'] and replay['head_sha256'] == first['head_sha256']


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
    assert binding['complete_review_counts_bound']
    assert 'current_event_id' not in binding
    assert all(binding[flag] is False for flag in subject.FLAGS)


def test_checkpoint_can_bind_transport_digest_for_exact_recovery(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    digest = 'b' * 64
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    saved = checkpoint(store, transport_input_sha256=digest)
    binding = subject.ActivitySSECursorStore(
        path, ACCOUNT).latest_checkpoint_binding()
    assert saved['transport_input_bound']
    assert binding['transport_input_bound']
    assert binding['transport_input_sha256'] == digest
    assert not binding['reconnect_reservation_bound']
    assert binding['complete_review_counts_bound']


def test_invalid_transport_digest_never_creates_storage(tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    with pytest.raises(ValueError, match='TRANSPORT_DIGEST_INVALID'):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            transport_input_sha256='bad')
    assert not path.exists()


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


def test_postcommit_same_inode_rewrite_withholds_checkpoint_acknowledgement(
        tmp_path):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    calls = 0

    def admission():
        nonlocal calls
        calls += 1
        if calls == 4:
            with path.open('r+b', buffering=0) as stream:
                stream.seek(60)
                stream.write((1).to_bytes(4, 'big'))
        return report['input_sha256']

    with pytest.raises(ValueError, match=
            'ALPACA_ACTIVITY_SSE_CURSOR_STORE_FILE_SET_CHANGED'):
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
    (lambda value: value.__setitem__('private_extension', False),
        'REVIEW_INVALID'),
    (lambda value: value.__setitem__('sources', list(value['sources'])),
        'REVIEW_INVALID'),
    (lambda value: value.__setitem__('limitations', 'changed claim'),
        'REVIEW_INVALID'),
    (lambda value: value.__setitem__('reconnect_reason', 'UNKNOWN'),
        'REVIEW_INVALID'),
    (lambda value: value.__setitem__('unique_trades_reviewed',
        value['unique_trades_reviewed'] + 1), 'COUNT_INVALID'),
    (lambda value: value.__setitem__('redelivered_events_deduplicated',
        value['redelivered_events_deduplicated'] - 1), 'COUNT_INVALID'),
])
def test_invalid_review_never_creates_storage(tmp_path, mutation, code):
    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    mutation(report)
    with pytest.raises(ValueError, match=code):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            report, before, after)
    assert not path.exists()


def test_checkpoint_review_contract_is_total_and_exact(tmp_path):
    report, before, after = checked()
    assert set(report) == subject.REVIEW_RESULT_KEYS
    assert type(report['sources']) is tuple
    assert report['limitations'] == subject.REVIEW_LIMITATIONS
    saved = checkpoint(subject.ActivitySSECursorStore(
        tmp_path / 'cursor.sqlite3', ACCOUNT), report, before, after)
    assert saved['local_cursor_checkpoint_committed']


@pytest.mark.parametrize('field', (
    'mode', 'status', 'account_sha256', 'reconnect_reason', 'limitations'))
def test_checkpoint_review_rejects_foreign_text_equality(tmp_path, field):
    class EqualText(str):
        def __eq__(self, other): return True
        def __ne__(self, other): return False
        __hash__ = str.__hash__

    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    report[field] = EqualText('foreign')
    with pytest.raises(ValueError, match='REVIEW_INVALID'):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            report, before, after)
    assert not path.exists()


def test_checkpoint_review_rejects_foreign_tuple_equality(tmp_path):
    class EqualTuple(tuple):
        def __eq__(self, other): return True
        def __ne__(self, other): return False

    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    report['sources'] = EqualTuple(report['sources'])
    with pytest.raises(ValueError, match='REVIEW_INVALID'):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            report, before, after)
    assert not path.exists()


def test_checkpoint_review_rejects_foreign_schema_key_equality(tmp_path):
    class EqualText(str):
        __hash__ = str.__hash__

    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    report = {
        EqualText(key) if key == 'mode' else key: value
        for key, value in report.items()
    }
    with pytest.raises(ValueError, match='REVIEW_INVALID'):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            report, before, after)
    assert not path.exists()


def test_checkpoint_review_rejects_foreign_source_item_equality(tmp_path):
    class EqualText(str):
        def __eq__(self, other): return True
        def __ne__(self, other): return False
        __hash__ = str.__hash__

    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    report['sources'] = (EqualText('foreign'), *report['sources'][1:])
    with pytest.raises(ValueError, match='REVIEW_INVALID'):
        checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT),
            report, before, after)
    assert not path.exists()


@pytest.mark.parametrize('field', (
    'persisted_cursor_sha256', 'reconnect_since_id_sha256',
    'next_cursor_sha256'))
def test_checkpoint_review_rejects_foreign_cursor_digest_equality(
        tmp_path, field):
    class EqualText(str):
        def __eq__(self, other): return True
        def __ne__(self, other): return False
        __hash__ = str.__hash__

    path = tmp_path / 'cursor.sqlite3'
    report, before, after = checked()
    report[field] = EqualText('f' * 64)
    with pytest.raises(ValueError, match='REVIEW_CURSOR_DIGEST_INVALID'):
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


@pytest.mark.parametrize(('field','mutation','code'), (
    ('reconnect_reason', lambda value: 'UNKNOWN', 'SCOPE_INVALID'),
    ('replay_events_reviewed', lambda value: value + 1, 'COUNT_INVALID'),
    ('initial_events_reviewed', lambda value: value + 1, 'COUNT_INVALID'),
    ('unique_trades_reviewed', lambda value: value + 1, 'COUNT_INVALID'),
    ('unique_fees_reviewed', lambda value: value + 1, 'COUNT_INVALID'),
    ('linked_orders', lambda value: value + 1, 'COUNT_INVALID'),
))
@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
def test_cold_replay_revalidates_checkpoint_semantics_without_mutation(
        tmp_path, field, mutation, code, method):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    checkpoint(store)
    def mutate(body):
        body['scope'][field] = mutation(body['scope'][field])
    rewrite_checkpoint(path, mutate)
    tampered = path.read_bytes()
    with pytest.raises(ValueError, match=code):
        getattr(subject.ActivitySSECursorStore(path, ACCOUNT), method)()
    assert path.read_bytes() == tampered


@pytest.mark.parametrize('bound', (False, True))
def test_cold_replay_keeps_legacy_checkpoint_profiles_readable(
        tmp_path, bound):
    path = tmp_path / 'cursor.sqlite3'
    changes = dict(reconnect_reservation_token='a' * 64) if bound else {}
    checkpoint(subject.ActivitySSECursorStore(path, ACCOUNT), **changes)

    def downgrade(body):
        body['scope']['profile'] = (subject.LEGACY_BOUND_PROFILE if bound
            else subject.LEGACY_PROFILE)
        for field in ('unique_trades_reviewed', 'unique_fees_reviewed',
                'linked_orders'):
            body['scope'].pop(field)

    rewrite_checkpoint(path, downgrade)
    cold = subject.ActivitySSECursorStore(path, ACCOUNT)
    inspected = cold.inspect()
    assert inspected['retained_checkpoints'] == 1
    assert not inspected['complete_review_counts_bound']
    binding = cold.latest_checkpoint_binding()
    assert binding['reconnect_reservation_bound'] is bound
    assert not binding['complete_review_counts_bound']


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


@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
def test_public_read_check_cannot_retarget_store(tmp_path, monkeypatch,
        method):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    checkpoint(store)
    original_check = store._check
    retargeted = tmp_path / f'{method}-check-retargeted.sqlite3'

    def retarget_after_check():
        original_check()
        store.path = retargeted

    monkeypatch.setattr(store, '_check', retarget_after_check)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        getattr(store, method)()
    assert path.exists() and not retargeted.exists()


@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
def test_public_read_connection_cannot_retarget_store(tmp_path, monkeypatch,
        method):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    checkpoint(store)
    original_connect = store._connect
    retargeted = tmp_path / f'{method}-connect-retargeted.sqlite3'

    def retarget_after_connect(*args, **kwargs):
        retained = original_connect(*args, **kwargs)
        store.path = retargeted
        return retained

    monkeypatch.setattr(store, '_connect', retarget_after_connect)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        getattr(store, method)()
    assert path.exists() and not retargeted.exists()


@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
def test_public_read_rejects_same_path_byte_identical_replacement(
        tmp_path, monkeypatch, method):
    path = tmp_path / 'cursor.sqlite3'
    replacement = tmp_path / f'{method}-replacement.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    saved = checkpoint(store)
    replacement.write_bytes(path.read_bytes())
    original_connect = store._connect

    def replace_before_connect(*args, **kwargs):
        replacement.replace(path)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(store, '_connect', replace_before_connect)
    with pytest.raises(ValueError, match='FILE_IDENTITY_CHANGED'):
        getattr(store, method)()
    assert not replacement.exists()
    assert subject.ActivitySSECursorStore(
        path, ACCOUNT).inspect()['head_sha256'] == saved['head_sha256']


@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
def test_public_read_rejects_same_inode_rewrite(tmp_path, monkeypatch, method):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    checkpoint(store)
    original_load = store._load

    def rewrite(*args, **kwargs):
        result = original_load(*args, **kwargs)
        with path.open('r+b', buffering=0) as stream:
            stream.seek(60)
            stream.write((1).to_bytes(4, 'big'))
        return result

    monkeypatch.setattr(store, '_load', rewrite)
    with pytest.raises(ValueError, match=
            'ALPACA_ACTIVITY_SSE_CURSOR_STORE_FILE_SET_CHANGED'):
        getattr(store, method)()


@pytest.mark.parametrize('suffix', ('-wal', '-shm'))
def test_public_read_refuses_sqlite_sidecars(tmp_path, suffix):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    checkpoint(store)
    path.with_name(path.name + suffix).write_bytes(b'uncommitted')
    with pytest.raises(ValueError, match=
            'ALPACA_ACTIVITY_SSE_CURSOR_STORE_WAL_SIDECAR_REVIEW_REQUIRED'):
        store.inspect()


@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
@pytest.mark.parametrize('retarget_field', ('path', 'account_sha256'))
def test_public_read_replay_cannot_retarget_store(tmp_path, monkeypatch,
        method, retarget_field):
    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    saved = checkpoint(store)
    original_load = store._load
    retargeted = tmp_path / f'{method}-load-{retarget_field}.sqlite3'

    def retarget_after_load(*args, **kwargs):
        retained = original_load(*args, **kwargs)
        if retarget_field == 'path':
            store.path = retargeted
        else:
            store.account_sha256 = 'f' * 64
        return retained

    monkeypatch.setattr(store, '_load', retarget_after_load)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        getattr(store, method)()
    assert not retargeted.exists()
    retained = subject.ActivitySSECursorStore(path, ACCOUNT)
    assert retained.inspect()['head_sha256'] == saved['head_sha256']
    assert retained.latest_checkpoint_binding()['head_sha256'] == \
        saved['head_sha256']


@pytest.mark.parametrize('method', ('inspect', 'latest_checkpoint_binding'))
def test_public_read_binding_rejects_foreign_path_equality(tmp_path, method):
    class EqualPath:
        def __eq__(self, other): return True

    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    checkpoint(store)
    original_check = store._check

    def inject_equal_path():
        original_check()
        store.path = EqualPath()

    store._check = inject_equal_path
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        getattr(store, method)()
    assert path.exists()


@pytest.mark.parametrize('retarget_field', ('path', 'account_sha256'))
def test_checkpoint_review_scope_cannot_retarget_store(tmp_path, monkeypatch,
        retarget_field):
    path = tmp_path / 'cursor.sqlite3'
    retargeted = tmp_path / f'review-scope-{retarget_field}.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    original_scope = store._review_scope

    def retarget_after_scope(*args, **kwargs):
        retained = original_scope(*args, **kwargs)
        if retarget_field == 'path':
            store.path = retargeted
        else:
            store.account_sha256 = 'f' * 64
        return retained

    monkeypatch.setattr(store, '_review_scope', retarget_after_scope)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        checkpoint(store)
    assert not path.exists() and not retargeted.exists()


def test_checkpoint_inspection_cannot_retarget_store(tmp_path, monkeypatch):
    path = tmp_path / 'cursor.sqlite3'
    retargeted = tmp_path / 'checkpoint-inspect-retargeted.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    original_inspect = store.inspect

    def retarget_after_inspect():
        retained = original_inspect()
        store.path = retargeted
        return retained

    monkeypatch.setattr(store, 'inspect', retarget_after_inspect)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        checkpoint(store)
    assert not path.exists() and not retargeted.exists()


def test_checkpoint_connection_retarget_retains_only_readable_schema(
        tmp_path, monkeypatch):
    path = tmp_path / 'cursor.sqlite3'
    retargeted = tmp_path / 'checkpoint-connect-retargeted.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    original_connect = store._connect

    def retarget_after_connect(*args, **kwargs):
        retained = original_connect(*args, **kwargs)
        if kwargs.get('write'):
            store.path = retargeted
        return retained

    monkeypatch.setattr(store, '_connect', retarget_after_connect)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        checkpoint(store)
    assert path.exists() and not retargeted.exists()
    cold = subject.ActivitySSECursorStore(path, ACCOUNT).inspect()
    assert not cold['initialized'] and cold['current_event_id'] is None
    assert cold['head_sha256'] != subject.ZERO


@pytest.mark.parametrize('retarget_sample', (1, 2, 3, 4))
def test_checkpoint_admission_retarget_preserves_exact_commit_boundary(
        tmp_path, retarget_sample):
    path = tmp_path / 'cursor.sqlite3'
    retargeted = tmp_path / f'admission-retargeted-{retarget_sample}.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    report, before, after = checked()
    calls = []

    def admission():
        calls.append(True)
        if len(calls) == retarget_sample:
            store.path = retargeted
        return report['input_sha256']

    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        checkpoint(store, report, before, after, admission=admission)
    assert len(calls) == retarget_sample and not retargeted.exists()
    if retarget_sample == 1:
        assert not path.exists()
        return
    cold = subject.ActivitySSECursorStore(path, ACCOUNT)
    retained = cold.inspect()
    assert retained['retained_checkpoints'] == (1 if retarget_sample == 4 else 0)
    if retarget_sample == 4:
        replay = checkpoint(cold, report, before, after)
        assert replay['duplicate'] and replay['cursor_sha256'] == subject.sha(after)


@pytest.mark.parametrize('retarget_sample', (1, 2))
def test_checkpoint_transaction_replay_retarget_rolls_back_append(
        tmp_path, monkeypatch, retarget_sample):
    path = tmp_path / 'cursor.sqlite3'
    retargeted = tmp_path / f'transaction-load-{retarget_sample}.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    original_load = store._load
    calls = []

    def retarget_after_load(*args, **kwargs):
        retained = original_load(*args, **kwargs)
        calls.append(True)
        if len(calls) == retarget_sample:
            store.path = retargeted
        return retained

    monkeypatch.setattr(store, '_load', retarget_after_load)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        checkpoint(store)
    assert len(calls) == retarget_sample and not retargeted.exists()
    cold = subject.ActivitySSECursorStore(path, ACCOUNT).inspect()
    assert not cold['initialized'] and cold['current_event_id'] is None
    assert cold['head_sha256'] != subject.ZERO


def test_checkpoint_receipt_retarget_keeps_exact_replay_recoverable(
        tmp_path, monkeypatch):
    path = tmp_path / 'cursor.sqlite3'
    retargeted = tmp_path / 'receipt-retargeted.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    original_receipt = store._receipt

    def retarget_after_receipt(*args, **kwargs):
        retained = original_receipt(*args, **kwargs)
        store.path = retargeted
        return retained

    monkeypatch.setattr(store, '_receipt', retarget_after_receipt)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        checkpoint(store)
    assert not retargeted.exists()
    cold = subject.ActivitySSECursorStore(path, ACCOUNT)
    assert cold.inspect()['retained_checkpoints'] == 1
    replay = checkpoint(cold)
    assert replay['duplicate'] and cold.inspect()['retained_checkpoints'] == 1


def test_checkpoint_admission_rejects_foreign_string_equality(tmp_path):
    class EqualInput(str):
        def __eq__(self, other): return True
        def __ne__(self, other): return False

    path = tmp_path / 'cursor.sqlite3'
    store = subject.ActivitySSECursorStore(path, ACCOUNT)
    with pytest.raises(ValueError, match='INPUT_REVOKED'):
        checkpoint(store, admission=lambda: EqualInput('private'))
    assert not path.exists()


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
