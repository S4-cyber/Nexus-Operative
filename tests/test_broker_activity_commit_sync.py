"""Explicit post-commit durability for retained paper activities."""
import os
from pathlib import Path
import stat

import pytest

from nexus import broker_activity_store as storage
from nexus.broker_activity_store import BrokerActivityStore
from test_alpaca_activity_normalizer import inputs, ACCOUNT
from test_alpaca_activity_collection import START, END

PREFIX = 'BROKER_ACTIVITY_STORE_'


def _store(tmp_path):
    return BrokerActivityStore(tmp_path / 'activities.sqlite3', ACCOUNT)


def _retain(store):
    rows, orders = inputs()
    return store.retain(rows, orders, after=START, until=END)


def test_windows_committed_fingerprint_schema_uses_portable_ctime():
    fingerprint = ('C:/activities.sqlite3', 1, 2, 4096, 3, None, 1,
        'a' * 64)
    assert storage._committed_fingerprint_boundary(
        fingerprint, host='nt') == fingerprint
    with pytest.raises(RuntimeError):
        storage._committed_fingerprint_boundary(
            (*fingerprint[:5], 4, *fingerprint[6:]), host='nt')


def test_windows_cold_fingerprint_ctime_normalizes_to_committed_schema(
        monkeypatch):
    expected = ('C:/activities.sqlite3', 1, 2, 4096, 3, None, 1,
        'a' * 64)
    observed = (*expected[:5], 4, *expected[6:])
    monkeypatch.setattr(storage, 'capture_file_fingerprints',
        lambda *_args, **_kwargs: (observed,))
    storage._require_committed_fingerprint(
        Path('activities.sqlite3'), expected, host='nt')
    with pytest.raises(RuntimeError):
        storage._require_committed_fingerprint(Path('activities.sqlite3'),
            (*expected[:4], 5, *expected[5:]), host='nt')


def test_commit_flushes_exact_main_and_only_new_posix_parent(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    original = storage.os.fsync
    calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)

    monkeypatch.setattr(storage.os, 'fsync', fsync)
    _retain(store)
    assert calls == (['file'] if os.name == 'nt' else
        ['file', 'directory'])
    calls.clear()
    assert _retain(store)['duplicate'] is True
    assert calls == ['file']


def test_main_flush_uncertainty_retains_batch_for_explicit_replay(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    original = storage.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError('injected private main-file flush failure')
        return original(descriptor)

    monkeypatch.setattr(storage.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        _retain(store)
    monkeypatch.setattr(storage.os, 'fsync', original)
    retained = store.inspect()
    replay = _retain(store)
    assert failed and retained['retained_batches'] == 1
    assert retained['automatic_continuation'] is False
    assert replay['duplicate'] is True
    assert replay['head_sha256'] == retained['head_sha256']


def test_commit_hashes_writable_descriptor_before_fsync(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    original_open = storage.os.open
    original_fsync = storage.os.fsync
    saved = None
    tampered = False
    sync_calls = 0

    def open_file(value, flags, *args, **kwargs):
        nonlocal saved, tampered
        descriptor = original_open(value, flags, *args, **kwargs)
        if (not tampered and flags & os.O_RDWR and
                Path(value).resolve() == store.path.resolve()):
            size = os.fstat(descriptor).st_size
            os.lseek(descriptor, 0, os.SEEK_SET)
            saved = os.read(descriptor, size)
            offset = min(100, size - 1)
            os.lseek(descriptor, offset, os.SEEK_SET)
            os.write(descriptor, bytes((saved[offset] ^ 1,)))
            os.lseek(descriptor, 0, os.SEEK_SET)
            tampered = True
        return descriptor

    def fsync(descriptor):
        nonlocal sync_calls
        sync_calls += 1
        return original_fsync(descriptor)

    monkeypatch.setattr(storage.os, 'open', open_file)
    monkeypatch.setattr(storage.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        _retain(store)
    assert tampered and saved and sync_calls == 0
    monkeypatch.setattr(storage.os, 'open', original_open)
    monkeypatch.setattr(storage.os, 'fsync', original_fsync)
    store.path.write_bytes(saved)
    retained = store.inspect()
    assert retained['retained_batches'] == 1
    assert _retain(store)['duplicate'] is True


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
def test_new_parent_flush_binds_exact_directory_descriptor(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    original_open = storage.os.open
    original_fsync = storage.os.fsync
    substituted = False
    directory_syncs = 0

    def open_file(value, flags, *args, **kwargs):
        nonlocal substituted
        if (not substituted and flags & getattr(os, 'O_DIRECTORY', 0) and
                Path(value).resolve() == store.path.resolve().parent):
            substituted = True
            return original_open(foreign, flags, *args, **kwargs)
        return original_open(value, flags, *args, **kwargs)

    def fsync(descriptor):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
        return original_fsync(descriptor)

    monkeypatch.setattr(storage.os, 'open', open_file)
    monkeypatch.setattr(storage.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        _retain(store)
    assert substituted and directory_syncs == 0
    monkeypatch.setattr(storage.os, 'open', original_open)
    monkeypatch.setattr(storage.os, 'fsync', original_fsync)
    retained = store.inspect()
    assert retained['retained_batches'] == 1
    assert retained['automatic_continuation'] is False


@pytest.mark.parametrize('signal', (KeyboardInterrupt, SystemExit))
def test_commit_sync_preserves_process_control_signals(
        tmp_path, monkeypatch, signal):
    store = _store(tmp_path)
    original = storage.os.fsync

    def interrupt(_descriptor):
        raise signal

    monkeypatch.setattr(storage.os, 'fsync', interrupt)
    with pytest.raises(signal):
        _retain(store)
    monkeypatch.setattr(storage.os, 'fsync', original)
    retained = store.inspect()
    assert retained['retained_batches'] == 1
    assert retained['automatic_continuation'] is False
    assert _retain(store)['duplicate'] is True
