"""Explicit post-commit durability for shared account-read pacing."""
import os
from pathlib import Path
import stat

import pytest

from nexus import paper_read_pacing as pacing
from nexus.paper_read_pacing import PaperReadPacing

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
PREFIX = 'PAPER_READ_PACING_'


def test_windows_committed_fingerprint_schema_uses_portable_ctime():
    fingerprint = ('C:/pacing.sqlite3', 1, 2, 4096, 3, None, 1,
        'a' * 64)
    assert pacing._committed_fingerprint_boundary(
        fingerprint, host='nt') == fingerprint
    with pytest.raises(RuntimeError):
        pacing._committed_fingerprint_boundary(
            (*fingerprint[:5], 4, *fingerprint[6:]), host='nt')


def _begin(gate, owner, at=1000):
    return gate.begin(admission=lambda: at, ownership=owner)


def _finish(gate, owner, token, at=1001):
    return gate.finish(token, admission=lambda: at, ownership=owner,
        status_code=200)


def test_commit_flushes_exact_main_and_only_new_posix_parent(
        tmp_path, monkeypatch):
    gate = PaperReadPacing(tmp_path / 'pacing.sqlite3', ACCOUNT)
    original = pacing.os.fsync
    calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)

    owner = gate.ownership()
    owner.__enter__()
    try:
        monkeypatch.setattr(pacing.os, 'fsync', fsync)
        token = _begin(gate, owner)
        assert calls == (['file'] if os.name == 'nt' else
            ['file', 'directory'])
        calls.clear()
        _finish(gate, owner, token)
        assert calls == ['file']
        monkeypatch.setattr(pacing.os, 'fsync', original)
    finally:
        owner.__exit__(None, None, None)


def test_main_flush_uncertainty_retains_pending_and_blocks_retry(
        tmp_path, monkeypatch):
    gate = PaperReadPacing(tmp_path / 'pacing.sqlite3', ACCOUNT)
    original = pacing.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError('injected private main-file flush failure')
        return original(descriptor)

    with gate.ownership() as owner:
        monkeypatch.setattr(pacing.os, 'fsync', fsync)
        with pytest.raises(ValueError,
                match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
            _begin(gate, owner)
    monkeypatch.setattr(pacing.os, 'fsync', original)
    state = gate.inspect()
    assert failed and state['pending_token'] is not None
    assert state['ready_for_routing'] is False
    with pytest.raises(ValueError, match=f'^{PREFIX}PENDING_REVIEW$'):
        gate.begin(admission=lambda: 1001)


def test_commit_hashes_writable_descriptor_before_fsync(
        tmp_path, monkeypatch):
    gate = PaperReadPacing(tmp_path / 'pacing.sqlite3', ACCOUNT)
    original_open = pacing.os.open
    original_fsync = pacing.os.fsync
    saved = None
    tampered = False
    sync_calls = 0

    def open_file(value, flags, *args, **kwargs):
        nonlocal saved, tampered
        descriptor = original_open(value, flags, *args, **kwargs)
        if (not tampered and flags & os.O_RDWR and
                Path(value).resolve() == gate.path.resolve()):
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

    with gate.ownership() as owner:
        monkeypatch.setattr(pacing.os, 'open', open_file)
        monkeypatch.setattr(pacing.os, 'fsync', fsync)
        with pytest.raises(ValueError,
                match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
            _begin(gate, owner)
        assert tampered and saved and sync_calls == 0
        monkeypatch.setattr(pacing.os, 'open', original_open)
        monkeypatch.setattr(pacing.os, 'fsync', original_fsync)
    gate.path.write_bytes(saved)
    state = gate.inspect()
    assert state['pending_token'] is not None
    assert state['ready_for_routing'] is False


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
def test_new_parent_flush_binds_exact_directory_descriptor(
        tmp_path, monkeypatch):
    gate = PaperReadPacing(tmp_path / 'pacing.sqlite3', ACCOUNT)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    original_open = pacing.os.open
    original_fsync = pacing.os.fsync
    substituted = False
    directory_syncs = 0

    def open_file(value, flags, *args, **kwargs):
        nonlocal substituted
        if (not substituted and flags & getattr(os, 'O_DIRECTORY', 0) and
                Path(value).resolve() == gate.path.resolve().parent):
            substituted = True
            return original_open(foreign, flags, *args, **kwargs)
        return original_open(value, flags, *args, **kwargs)

    def fsync(descriptor):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
        return original_fsync(descriptor)

    with gate.ownership() as owner:
        monkeypatch.setattr(pacing.os, 'open', open_file)
        monkeypatch.setattr(pacing.os, 'fsync', fsync)
        with pytest.raises(ValueError,
                match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
            _begin(gate, owner)
        assert substituted and directory_syncs == 0
        monkeypatch.setattr(pacing.os, 'open', original_open)
        monkeypatch.setattr(pacing.os, 'fsync', original_fsync)
    state = gate.inspect()
    assert state['pending_token'] is not None
    assert state['ready_for_routing'] is False


@pytest.mark.parametrize('signal', (KeyboardInterrupt, SystemExit))
def test_commit_sync_preserves_process_control_signals(
        tmp_path, monkeypatch, signal):
    gate = PaperReadPacing(tmp_path / 'pacing.sqlite3', ACCOUNT)
    original = pacing.os.fsync

    def interrupt(_descriptor):
        raise signal

    with gate.ownership() as owner:
        monkeypatch.setattr(pacing.os, 'fsync', interrupt)
        with pytest.raises(signal):
            _begin(gate, owner)
        monkeypatch.setattr(pacing.os, 'fsync', original)
    state = gate.inspect()
    assert state['pending_token'] is not None
    assert state['ready_for_routing'] is False
