"""Actual SQLite/path fixtures: private journal review never converts WAL history."""
import os
import sqlite3

import pytest
from nexus.paper_read_pacing import PaperReadPacing
from nexus.broker_activity_store import BrokerActivityStore
from nexus.broker_request_journal import BrokerRequestJournal
from test_alpaca_paper_contract import plan

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'


def journal(kind, path):
    if kind == 'pacing':
        target = PaperReadPacing(path, ACCOUNT)
        def seed():
            token = target.begin(admission=lambda: 1000)
            target.finish(token, admission=lambda: 1000, status_code=200)
        return seed, target.inspect, lambda: target.begin(admission=lambda: 1060)
    if kind == 'activity':
        target = BrokerActivityStore(path, ACCOUNT)
        write = lambda: target.retain([], [], after='2026-10-17T00:00:00Z', until='2026-10-18T00:00:00Z')
        return write, target.inspect, write
    target = BrokerRequestJournal(path, ACCOUNT)
    write = lambda: target.prepare(plan())
    return write, target.recovery, write


def files(directory):
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


def deny_sqlite(monkeypatch):
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('preflight must reject before SQLite opens'))


@pytest.mark.parametrize('kind', ['pacing', 'activity', 'request'])
@pytest.mark.parametrize('operation', ['read', 'write'])
def test_wal_header_fails_before_sidecars_or_mode_conversion(tmp_path, monkeypatch, kind, operation):
    path = tmp_path/'private.sqlite3'; seed, read, write = journal(kind, path); seed()
    db = sqlite3.connect(path)
    try: assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    finally: db.close()
    before = files(tmp_path)
    assert not path.with_name(path.name+'-wal').exists()
    deny_sqlite(monkeypatch)
    with pytest.raises(ValueError, match='JOURNAL_MODE_REVIEW_REQUIRED'):
        (read if operation == 'read' else write)()
    assert files(tmp_path) == before


@pytest.mark.parametrize('kind', ['pacing', 'activity', 'request'])
@pytest.mark.parametrize('suffix', ['-wal', '-shm'])
@pytest.mark.parametrize('existing', [False, True])
def test_orphan_sidecars_are_preserved_and_block_initialization_or_replay(tmp_path, monkeypatch, kind, suffix, existing):
    path = tmp_path/'private.sqlite3'; seed, read, write = journal(kind, path)
    if existing: seed()
    path.with_name(path.name+suffix).write_bytes(b'private-uncertain-sidecar')
    before = files(tmp_path); deny_sqlite(monkeypatch)
    for action in (read, write):
        with pytest.raises(ValueError, match='WAL_SIDECAR_REVIEW_REQUIRED'): action()
    assert files(tmp_path) == before and path.exists() is existing


@pytest.mark.parametrize('kind', ['pacing', 'activity', 'request'])
@pytest.mark.parametrize('shape', ['directory', 'fifo', 'symlink', 'hardlink'])
def test_special_and_linked_paths_fail_without_sqlite_or_content_mutation(tmp_path, monkeypatch, kind, shape):
    path = tmp_path/'private.sqlite3'; original = tmp_path/'retained.sqlite3'
    if shape == 'directory': path.mkdir()
    elif shape == 'fifo':
        if os.name == 'nt': pytest.skip('Windows FIFO not assumed')
        os.mkfifo(path)
    else:
        original.write_bytes(b'private-retained-file')
        try:
            if shape == 'symlink': path.symlink_to(original)
            else: os.link(original, path)
        except OSError: pytest.skip('Link privilege/filesystem support is independently accepted')
    seed, read, write = journal(kind, path); deny_sqlite(monkeypatch)
    for action in (read, write):
        with pytest.raises(ValueError, match='REGULAR_FILE_REQUIRED|SYMLINK|HARDLINK'): action()
    if original.exists(): assert original.read_bytes() == b'private-retained-file'


@pytest.mark.parametrize('kind', ['pacing', 'activity', 'request'])
def test_missing_status_remains_empty_and_normal_delete_journal_still_replays(tmp_path, kind):
    path = tmp_path/'absent'/'private.sqlite3'; seed, read, write = journal(kind, path)
    read(); assert not path.parent.exists()
    seed(); before = files(path.parent); read(); assert files(path.parent) == before
    write()  # Existing ordinary writes and duplicate handling stay supported.
    read()
    db = sqlite3.connect(path)
    try: assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    finally: db.close()


def test_bounded_header_read_rejects_unsupported_version_without_echoing_path(tmp_path):
    from nexus.sqlite_journal_guard import check_existing
    path = tmp_path/'private-owner-name.sqlite3'
    path.write_bytes(b'SQLite format 3\x00'+b'\x10\x00\x02\x02'+b'private-contents')
    with pytest.raises(ValueError, match='FIXTURE_JOURNAL_MODE_REVIEW_REQUIRED') as caught:
        check_existing(path, 1024, 'FIXTURE_')
    assert str(path) not in str(caught.value) and 'private-contents' not in str(caught.value)


def test_file_replaced_between_lstat_and_open_is_rejected(tmp_path, monkeypatch):
    from nexus import sqlite_journal_guard as guard
    from types import SimpleNamespace
    path = tmp_path/'private.sqlite3'; path.write_bytes(b'original')
    replacement = tmp_path/'replacement'; replacement.write_bytes(b'changed')
    original_open = os.open
    def replaced(*args, **kwargs):
        replacement.replace(path)
        return original_open(*args, **kwargs)
    monkeypatch.setattr(guard, 'os', SimpleNamespace(**{**vars(os), 'open':replaced}))
    with pytest.raises(ValueError, match='FIXTURE_FILE_CHANGED'):
        guard.check_existing(path, 1024, 'FIXTURE_')


def test_growth_after_path_sample_is_checked_on_the_open_descriptor(tmp_path, monkeypatch):
    from nexus import sqlite_journal_guard as guard
    from types import SimpleNamespace
    path = tmp_path/'private.sqlite3'; path.write_bytes(b'x')
    original_open = os.open
    def grown(*args, **kwargs):
        path.write_bytes(b'x'*100)
        return original_open(*args, **kwargs)
    monkeypatch.setattr(guard, 'os', SimpleNamespace(**{**vars(os), 'open':grown}))
    with pytest.raises(ValueError, match='FIXTURE_DATABASE_CAP'):
        guard.check_existing(path, 10, 'FIXTURE_')
