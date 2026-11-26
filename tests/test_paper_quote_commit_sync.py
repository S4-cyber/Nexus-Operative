"""Explicit commit durability for retained request-bound paper quotes."""
import os
import stat

import pytest

from nexus import paper_quote_store as subject
from nexus import sqlite_journal_guard as durability
from test_paper_quote_store import setup


PREFIX = 'PAPER_QUOTE_STORE_'


def _target(descriptor, path):
    if not path.exists():
        return False
    try:
        opened = os.fstat(descriptor)
        observed = path.stat()
    except OSError:
        return False
    return ((opened.st_dev, opened.st_ino) ==
        (observed.st_dev, observed.st_ino))


def test_commit_flushes_exact_main_and_only_new_posix_parent(
        tmp_path, monkeypatch):
    store, value, args = setup(tmp_path)
    original = durability.os.fsync
    calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        if _target(descriptor, store.path):
            calls.append('file')
        elif stat.S_ISDIR(mode):
            calls.append('directory')
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', fsync)
    receipt = store.retain(value, **args)
    assert calls[-1:] == ['file'] if os.name == 'nt' else (
        calls[-2:] == ['file', 'directory'])
    calls.clear()
    args['expected_quote_head'] = receipt['head_sha256']
    replay = store.retain(value, **args)
    assert replay['duplicate'] is True
    assert replay['head_sha256'] == receipt['head_sha256']
    assert calls == ['file']


def test_main_flush_failure_retains_exact_duplicate_replay(
        tmp_path, monkeypatch):
    store, value, args = setup(tmp_path)
    original = durability.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and _target(descriptor, store.path):
            failed = True
            raise OSError('injected private quote main-file flush failure')
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        store.retain(value, **args)
    monkeypatch.setattr(durability.os, 'fsync', original)
    retained = store.inspect(
        expected_request_head=args['expected_request_head'])
    args['expected_quote_head'] = retained['head_sha256']
    replay = store.retain(value, **args)
    assert failed and retained['retained_records'] == 1
    assert replay['duplicate'] is True
    assert replay['head_sha256'] == retained['head_sha256']


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
def test_new_parent_flush_binds_exact_directory_descriptor(
        tmp_path, monkeypatch):
    store, value, args = setup(tmp_path)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    original_open = durability.os.open
    original_fsync = durability.os.fsync
    main_flushed = False
    substituted = False

    def fsync(descriptor):
        nonlocal main_flushed
        if _target(descriptor, store.path):
            main_flushed = True
        return original_fsync(descriptor)

    def open_file(value, flags, *open_args, **open_kwargs):
        nonlocal substituted
        if (main_flushed and not substituted and
                flags & getattr(os, 'O_DIRECTORY', 0) and
                os.path.samefile(value, store.path.parent)):
            substituted = True
            return original_open(foreign, flags, *open_args, **open_kwargs)
        return original_open(value, flags, *open_args, **open_kwargs)

    monkeypatch.setattr(durability.os, 'open', open_file)
    monkeypatch.setattr(durability.os, 'fsync', fsync)
    with pytest.raises(ValueError,
            match=f'^{PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        store.retain(value, **args)
    monkeypatch.setattr(durability.os, 'open', original_open)
    retained = store.inspect(
        expected_request_head=args['expected_request_head'])
    args['expected_quote_head'] = retained['head_sha256']
    assert substituted and store.retain(value, **args)['duplicate'] is True


@pytest.mark.parametrize('signal', (KeyboardInterrupt, SystemExit))
def test_commit_sync_preserves_process_control(tmp_path, monkeypatch, signal):
    store, value, args = setup(tmp_path)
    original = durability.os.fsync

    def interrupt(descriptor):
        if _target(descriptor, store.path):
            raise signal
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', interrupt)
    with pytest.raises(signal):
        store.retain(value, **args)
    monkeypatch.setattr(durability.os, 'fsync', original)
    retained = store.inspect(
        expected_request_head=args['expected_request_head'])
    args['expected_quote_head'] = retained['head_sha256']
    assert retained['retained_records'] == 1
    assert store.retain(value, **args)['duplicate'] is True
