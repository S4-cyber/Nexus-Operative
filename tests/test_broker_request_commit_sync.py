"""Explicit post-commit durability for the broker request journal."""
import os
from pathlib import Path
import stat

import pytest

from nexus import broker_request_journal as journal
from nexus.broker_request_journal import BrokerRequestJournal
from test_alpaca_paper_contract import plan

ACCOUNT = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
PREFIX = 'BROKER_JOURNAL_'


def test_windows_committed_fingerprint_schema_uses_portable_ctime():
    fingerprint = ('C:/requests.sqlite3', 1, 2, 4096, 3, None, 1,
        'a' * 64)
    assert journal._committed_fingerprint_boundary(
        fingerprint, host='nt') == fingerprint
    with pytest.raises(RuntimeError):
        journal._committed_fingerprint_boundary(
            (*fingerprint[:5], 4, *fingerprint[6:]), host='nt')


def test_windows_cold_fingerprint_ctime_normalizes_to_committed_schema(
        monkeypatch):
    expected = ('C:/requests.sqlite3', 1, 2, 4096, 3, None, 1,
        'a' * 64)
    observed = (*expected[:5], 4, *expected[6:])
    monkeypatch.setattr(journal, 'capture_file_fingerprints',
        lambda *_args, **_kwargs: (observed,))
    journal._require_committed_fingerprint(
        Path('requests.sqlite3'), expected, host='nt')
    with pytest.raises(RuntimeError):
        journal._require_committed_fingerprint(Path('requests.sqlite3'),
            (*expected[:4], 5, *expected[5:]), host='nt')


def _store(tmp_path):
    return BrokerRequestJournal(tmp_path / 'requests.sqlite3', ACCOUNT)


def _arm(store):
    store.prepare(plan())
    store.mark_submission_uncertain('nx_fixture')


def test_commit_flushes_exact_main_and_only_new_posix_parent(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    original = journal.os.fsync
    calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)

    monkeypatch.setattr(journal.os, 'fsync', fsync)
    store.prepare(plan())
    assert calls == (['file'] if os.name == 'nt' else
        ['file', 'directory'])
    calls.clear()
    store.mark_submission_uncertain('nx_fixture')
    assert calls == ['file']


def test_main_flush_uncertainty_retains_pending_lookup_and_blocks_retry(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    _arm(store)
    original = journal.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError('injected private main-file flush failure')
        return original(descriptor)

    monkeypatch.setattr(journal.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        store.begin_lookup('nx_fixture', evaluated_at=1000)
    monkeypatch.setattr(journal.os, 'fsync', original)
    pending = store.pending_lookup()
    assert failed and pending['pending'] is True
    assert pending['automatic_submission'] is False
    with pytest.raises(ValueError, match='PENDING_REVIEW_REQUIRED'):
        store.begin_lookup('nx_fixture', evaluated_at=1001)


def test_commit_hashes_writable_descriptor_before_fsync(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    _arm(store)
    original_open = journal.os.open
    original_fsync = journal.os.fsync
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

    monkeypatch.setattr(journal.os, 'open', open_file)
    monkeypatch.setattr(journal.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        store.begin_lookup('nx_fixture', evaluated_at=1000)
    assert tampered and saved and sync_calls == 0
    monkeypatch.setattr(journal.os, 'open', original_open)
    monkeypatch.setattr(journal.os, 'fsync', original_fsync)
    store.path.write_bytes(saved)
    pending = store.pending_lookup()
    assert pending['pending'] is True
    assert pending['automatic_resume'] is False


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
def test_new_parent_flush_binds_exact_directory_descriptor(
        tmp_path, monkeypatch):
    store = _store(tmp_path)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    original_open = journal.os.open
    original_fsync = journal.os.fsync
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

    monkeypatch.setattr(journal.os, 'open', open_file)
    monkeypatch.setattr(journal.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        store.prepare(plan())
    assert substituted and directory_syncs == 0
    monkeypatch.setattr(journal.os, 'open', original_open)
    monkeypatch.setattr(journal.os, 'fsync', original_fsync)
    recovered = store.recovery()
    assert recovered['requests'][0]['recovery_state'] == (
        'PREPARED_UNSENT_DIAGNOSTIC')
    assert recovered['automatic_submission'] is False


@pytest.mark.parametrize('signal', (KeyboardInterrupt, SystemExit))
def test_commit_sync_preserves_process_control_signals(
        tmp_path, monkeypatch, signal):
    store = _store(tmp_path)
    _arm(store)
    original = journal.os.fsync

    def interrupt(_descriptor):
        raise signal

    monkeypatch.setattr(journal.os, 'fsync', interrupt)
    with pytest.raises(signal):
        store.begin_lookup('nx_fixture', evaluated_at=1000)
    monkeypatch.setattr(journal.os, 'fsync', original)
    pending = store.pending_lookup()
    assert pending['pending'] is True
    assert pending['automatic_submission'] is False
