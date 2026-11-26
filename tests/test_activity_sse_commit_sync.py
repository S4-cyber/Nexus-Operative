"""Explicit commit durability for Activity SSE cursor and reconnect state."""
import os
import stat

import pytest

from nexus import sqlite_journal_guard as durability
from nexus.alpaca_activity_sse_cursor_store import (
    ActivitySSECursorStore, ZERO)
from nexus.alpaca_activity_sse_reconnect_budget import (
    ActivitySSEReconnectBudget, ActivitySSEReconnectBudgetError)
from test_alpaca_activity_sse_cursor_store import ACCOUNT, checked, checkpoint


CURSOR_PREFIX = 'ALPACA_ACTIVITY_SSE_CURSOR_STORE_'
BUDGET_PREFIX = 'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_'
STOP_CLEAR = lambda: False


def _target_descriptor(descriptor, path):
    if not path.exists():
        return False
    try:
        opened = os.fstat(descriptor)
        observed = path.stat()
    except OSError:
        return False
    return ((opened.st_dev, opened.st_ino) ==
        (observed.st_dev, observed.st_ino))


def _target_directory(descriptor, path):
    try:
        opened = os.fstat(descriptor)
        observed = path.stat()
    except OSError:
        return False
    return (stat.S_ISDIR(opened.st_mode) and
        (opened.st_dev, opened.st_ino) ==
        (observed.st_dev, observed.st_ino))


def _begin(budget, expected=ZERO, at=1000):
    return budget.begin(evaluated_at=at, expected_cursor_head=ZERO,
        expected_head=expected, reconnect_reason='CONNECTION_DROPPED',
        confirm_reconnect_attempt=True, emergency_stop_check=STOP_CLEAR)


def test_cursor_commit_flushes_exact_main_and_only_new_posix_parent(
        tmp_path, monkeypatch):
    store = ActivitySSECursorStore(tmp_path / 'cursor.sqlite3', ACCOUNT)
    original = durability.os.fsync
    calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        if _target_descriptor(descriptor, store.path):
            calls.append('file')
        elif stat.S_ISDIR(mode) and _target_directory(
                descriptor, store.path.parent):
            calls.append('directory')
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', fsync)
    report, before, after = checked()
    saved = checkpoint(store, report, before, after)
    assert calls[-1:] == ['file'] if os.name == 'nt' else (
        calls[-2:] == ['file', 'directory'])
    calls.clear()
    replay = checkpoint(store, report, before, after)
    assert replay['duplicate'] is True
    assert replay['head_sha256'] == saved['head_sha256']
    assert calls[-1] == 'file' and calls.count('file') == 1


def test_cursor_main_flush_failure_retains_exact_duplicate_replay(
        tmp_path, monkeypatch):
    store = ActivitySSECursorStore(tmp_path / 'cursor.sqlite3', ACCOUNT)
    original = durability.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and _target_descriptor(descriptor, store.path):
            failed = True
            raise OSError('injected private cursor main-file flush failure')
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', fsync)
    report, before, after = checked()
    with pytest.raises(ValueError,
            match=f'^{CURSOR_PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        checkpoint(store, report, before, after)
    monkeypatch.setattr(durability.os, 'fsync', original)
    retained = store.inspect()
    replay = checkpoint(store, report, before, after)
    assert failed and retained['head_sha256'] != ZERO
    assert retained['current_event_id'] == after
    assert replay['duplicate'] is True
    assert replay['head_sha256'] == retained['head_sha256']


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
def test_cursor_new_parent_flush_binds_exact_directory_descriptor(
        tmp_path, monkeypatch):
    store = ActivitySSECursorStore(tmp_path / 'cursor.sqlite3', ACCOUNT)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    original_open = durability.os.open
    original_fsync = durability.os.fsync
    substituted = False
    main_flushed = False

    def fsync(descriptor):
        nonlocal main_flushed
        if _target_descriptor(descriptor, store.path):
            main_flushed = True
        return original_fsync(descriptor)

    def open_file(value, flags, *args, **kwargs):
        nonlocal substituted
        if (main_flushed and not substituted and
                flags & getattr(os, 'O_DIRECTORY', 0) and
                os.path.samefile(value, store.path.parent)):
            substituted = True
            return original_open(foreign, flags, *args, **kwargs)
        return original_open(value, flags, *args, **kwargs)

    monkeypatch.setattr(durability.os, 'open', open_file)
    monkeypatch.setattr(durability.os, 'fsync', fsync)
    report, before, after = checked()
    with pytest.raises(ValueError,
            match=f'^{CURSOR_PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        checkpoint(store, report, before, after)
    monkeypatch.setattr(durability.os, 'open', original_open)
    retained = store.inspect()
    assert substituted and retained['current_event_id'] == after
    assert checkpoint(store, report, before, after)['duplicate'] is True


@pytest.mark.parametrize('signal', (KeyboardInterrupt, SystemExit))
def test_cursor_commit_sync_preserves_process_control(
        tmp_path, monkeypatch, signal):
    store = ActivitySSECursorStore(tmp_path / 'cursor.sqlite3', ACCOUNT)
    original = durability.os.fsync

    def interrupt(descriptor):
        if _target_descriptor(descriptor, store.path):
            raise signal
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', interrupt)
    report, before, after = checked()
    with pytest.raises(signal):
        checkpoint(store, report, before, after)
    monkeypatch.setattr(durability.os, 'fsync', original)
    retained = store.inspect()
    assert retained['current_event_id'] == after
    assert checkpoint(store, report, before, after)['duplicate'] is True


def test_reconnect_begin_flush_failure_retains_exact_reservation(
        tmp_path, monkeypatch):
    budget = ActivitySSEReconnectBudget(
        tmp_path / 'reconnect.sqlite3', ACCOUNT)
    original = durability.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and _target_descriptor(descriptor, budget.path):
            failed = True
            raise OSError('injected reconnect reservation flush failure')
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', fsync)
    with pytest.raises(ActivitySSEReconnectBudgetError,
            match=f'^{BUDGET_PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        _begin(budget)
    monkeypatch.setattr(durability.os, 'fsync', original)
    retained = budget.inspect()
    replay = _begin(budget)
    assert failed and retained['pending_token'] == retained['head_sha256']
    assert retained['attempts'] == 1 and not retained['completed']
    assert replay['duplicate_reservation'] is True
    assert replay['provider_request_authorized'] is False
    assert replay['token'] == retained['pending_token']


def test_reconnect_finish_flush_failure_retains_exact_result(
        tmp_path, monkeypatch):
    budget = ActivitySSEReconnectBudget(
        tmp_path / 'reconnect.sqlite3', ACCOUNT)
    reservation = _begin(budget)
    original = durability.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and _target_descriptor(descriptor, budget.path):
            failed = True
            raise OSError('injected reconnect result flush failure')
        return original(descriptor)

    monkeypatch.setattr(durability.os, 'fsync', fsync)
    with pytest.raises(ActivitySSEReconnectBudgetError,
            match=f'^{BUDGET_PREFIX}COMMIT_DURABILITY_UNCERTAIN$'):
        budget.finish(reservation['token'], evaluated_at=1000,
            outcome='REVIEW_REQUIRED', detail_code='TEST_REVIEW')
    monkeypatch.setattr(durability.os, 'fsync', original)
    retained = budget.inspect()
    replay = budget.finish(reservation['token'], evaluated_at=1000,
        outcome='REVIEW_REQUIRED', detail_code='TEST_REVIEW')
    assert failed and retained['pending_token'] is None
    assert retained['review_required'] and not retained['completed']
    assert replay['duplicate_result'] is True
    assert replay['head_sha256'] == retained['head_sha256']
