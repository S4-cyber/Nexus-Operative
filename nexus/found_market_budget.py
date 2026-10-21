"""Durable local admission budget for optional public market discovery.

The journal contains timestamps only. It coordinates cooperating Nexus processes
that share one data root; it is not a provider-wide, host-wide or IP-wide quota.
An admission is committed before transport, so a crash or lost response consumes
the slot conservatively. No market payloads, credentials or account data enter it.
"""
from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3
import time

from nexus.sqlite_journal_guard import check_existing

SCHEMA = 'nexus.found-market-request-budget.v1'
WINDOW_SECONDS = 3600
DEFAULT_LIMIT = 36
MAX_DATABASE_BYTES = 1024 * 1024
MAX_SEQUENCE = 2**63 - 2
LOCK_RETRY_SECONDS = 30.0
LOCK_RETRY_INTERVAL_SECONDS = 0.01
SQLITE_LOCK_CODES = (getattr(sqlite3, 'SQLITE_BUSY', 5), getattr(sqlite3, 'SQLITE_LOCKED', 6))
SQLITE_310_LOCK_MESSAGES = (('database is locked',), ('database table is locked',), ('database schema is locked',))


class _LockContention(Exception):
    """Internal bounded-retry signal; never exposes SQLite error text."""


def _lock_contention(error):
    code = getattr(error, 'sqlite_errorcode', None)
    if type(code) is int:
        return (code & 0xFF) in SQLITE_LOCK_CODES
    # Python 3.10 does not expose sqlite_errorcode. Match only SQLite's fixed
    # lock messages there; arbitrary error text is never returned to callers.
    return error.args in SQLITE_310_LOCK_MESSAGES


def _fail(code):
    raise ValueError('FOUND_MARKET_BUDGET_' + code) from None


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 20_000_000:
        _fail('CLOCK')
    return float(value)


class SharedRequestBudget:
    """One rolling-window budget bound to an explicit local SQLite path."""

    def __init__(self, path, *, limit=DEFAULT_LIMIT):
        if type(limit) is not int or not 1 <= limit <= DEFAULT_LIMIT:
            _fail('LIMIT')
        self.path = Path(path)
        self.limit = limit

    def _check(self):
        return check_existing(self.path, MAX_DATABASE_BYTES, 'FOUND_MARKET_BUDGET_')

    def _connect(self, write=False):
        # Keep the O_EXCL creator role across transient SQLite contention.  If
        # the creator were to return that role to admit(), the next call would
        # see an existing empty file and no contender would be authorized to
        # install the schema.  Non-creators wait for the creator's commit and
        # never initialize a pre-existing empty file themselves.
        initialization_owner = False
        initialization_deadline = time.monotonic() + LOCK_RETRY_SECONDS
        while True:
            exists = self._check()
            if write and not exists and not initialization_owner:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
                    initialization_owner = True
                except FileExistsError:
                    pass
                self._check()
            mode = '?mode=rw' if write else '?mode=ro'
            try:
                db = sqlite3.connect(self.path.resolve().as_uri() + mode, uri=True, timeout=5)
            except sqlite3.Error as error:
                if _lock_contention(error):
                    raise _LockContention from None
                _fail('DATABASE_UNAVAILABLE')
            try:
                if not write:
                    return db
                # Only the O_EXCL winner sets the persistent journal mode. Other
                # first-use contenders take/release the SQLite writer lock until
                # the winner has installed the binding.
                if initialization_owner:
                    db.execute('PRAGMA journal_mode=DELETE')
                db.execute('PRAGMA synchronous=FULL')
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
                db.execute('BEGIN IMMEDIATE')
                objects = db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                if initialization_owner:
                    if objects:
                        _fail('INITIALIZATION_RACE')
                    db.execute('CREATE TABLE binding (schema TEXT NOT NULL, last_seen REAL NOT NULL)')
                    db.execute('CREATE TABLE admissions (seq INTEGER PRIMARY KEY, admitted_at REAL NOT NULL)')
                    db.execute('INSERT INTO binding VALUES (?, ?)', (SCHEMA, 0.0))
                elif not objects:
                    db.rollback();db.close()
                    remaining = initialization_deadline - time.monotonic()
                    if remaining <= 0:
                        _fail('INITIALIZATION_INCOMPLETE')
                    time.sleep(min(LOCK_RETRY_INTERVAL_SECONDS, remaining))
                    continue
                return db
            except ValueError:
                db.close();raise
            except sqlite3.Error as error:
                db.close()
                if _lock_contention(error):
                    remaining = initialization_deadline - time.monotonic()
                    if remaining <= 0:
                        raise _LockContention from None
                    time.sleep(min(LOCK_RETRY_INTERVAL_SECONDS, remaining))
                    continue
                _fail('DATABASE_INVALID')

    def _load(self, db):
        try:
            if db.execute('PRAGMA quick_check(1)').fetchone() != ('ok',):
                _fail('DATABASE_INTEGRITY')
            objects = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
            if objects != [('table', 'admissions'), ('table', 'binding')]:
                _fail('SCHEMA')
            binding_shape = db.execute('PRAGMA table_info(binding)').fetchall()
            admission_shape = db.execute('PRAGMA table_info(admissions)').fetchall()
            if [(row[1],row[2],row[3],row[5]) for row in binding_shape] != [('schema','TEXT',1,0),('last_seen','REAL',1,0)]:
                _fail('SCHEMA')
            if [(row[1],row[2],row[3],row[5]) for row in admission_shape] != [('seq','INTEGER',0,1),('admitted_at','REAL',1,0)]:
                _fail('SCHEMA')
            binding = db.execute('SELECT schema,last_seen FROM binding LIMIT 2').fetchall()
            rows = db.execute('SELECT seq,admitted_at FROM admissions ORDER BY seq LIMIT ?', (self.limit + 2,)).fetchall()
        except sqlite3.Error as error:
            if _lock_contention(error):
                raise _LockContention from None
            _fail('DATABASE_INVALID')
        if len(binding) != 1 or binding[0][0] != SCHEMA:
            _fail('BINDING')
        last_seen = binding[0][1]
        if type(last_seen) not in (int, float) or not math.isfinite(last_seen) or last_seen < 0:
            _fail('CLOCK_RECORD')
        if len(rows) > self.limit:
            _fail('ROW_CAP')
        previous = 0
        times = []
        for seq, admitted_at in rows:
            if type(seq) is not int or not previous < seq <= MAX_SEQUENCE:
                _fail('SEQUENCE')
            if type(admitted_at) not in (int, float) or not math.isfinite(admitted_at) or admitted_at < 20_000_000:
                _fail('CLOCK_RECORD')
            previous = seq
            times.append(float(admitted_at))
        if times and (times != sorted(times) or times[-1] > float(last_seen)):
            _fail('CLOCK_RECORD')
        return float(last_seen), previous, times

    def inspect(self):
        """Read without creating a missing journal."""
        if not self._check():
            return dict(admissions=0, last_seen=0.0, limit=self.limit, window_seconds=WINDOW_SECONDS)
        try:
            with closing(self._connect()) as db:
                last_seen, _, times = self._load(db)
        except _LockContention:
            _fail('DATABASE_UNAVAILABLE')
        return dict(admissions=len(times), last_seen=last_seen, limit=self.limit, window_seconds=WINDOW_SECONDS)

    def admit(self, admission):
        """Commit one slot before external IO; sample time after the write lock."""
        if not callable(admission):
            _fail('CLOCK_SOURCE')
        deadline = time.monotonic() + LOCK_RETRY_SECONDS
        while True:
            denied = False
            try:
                with closing(self._connect(write=True)) as db:
                    try:
                        last_seen, sequence, _ = self._load(db)
                        now = _time(admission())
                        if now < last_seen:
                            _fail('CLOCK_REGRESSION')
                        db.execute('DELETE FROM admissions WHERE admitted_at <= ?', (now - WINDOW_SECONDS,))
                        remaining = db.execute('SELECT COUNT(*) FROM admissions').fetchone()[0]
                        if type(remaining) is not int or not 0 <= remaining <= self.limit:
                            _fail('ROW_CAP')
                        db.execute('UPDATE binding SET last_seen=?', (now,))
                        if remaining >= self.limit:
                            denied = True
                        else:
                            if sequence >= MAX_SEQUENCE:
                                _fail('SEQUENCE_CAP')
                            db.execute('INSERT INTO admissions VALUES (?, ?)', (sequence + 1, now))
                        self._load(db)
                        db.commit()
                    except sqlite3.Error as error:
                        db.rollback()
                        if _lock_contention(error):
                            raise _LockContention from None
                        _fail('DATABASE_INVALID')
                    except BaseException:
                        db.rollback()
                        raise
                break
            except _LockContention:
                remaining_wait = deadline - time.monotonic()
                if remaining_wait <= 0:
                    _fail('CONTENTION_TIMEOUT')
                time.sleep(min(LOCK_RETRY_INTERVAL_SECONDS, remaining_wait))
        if denied:
            _fail('EXHAUSTED')
        return dict(admitted=True, remaining=self.limit - remaining - 1,
                    window_seconds=WINDOW_SECONDS, shared_local_data_root=True)
