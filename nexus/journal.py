"""Bounded persistent audit journal for OFFLINE simulated research only.

No account identifiers, bank tokens or credentials are ever accepted.
Stores summarized results plus SHA-256 of the supplied historical prices,
not a full potentially large price dataset.
"""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import closing, contextmanager

from nexus.sqlite_journal_guard import check_existing

MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_REPLAY_RUNS = 10_000
DB_BUSY_TIMEOUT_MS = 10_000


def _fail(code):
    raise ValueError('PAPER_REPLAY_JOURNAL_' + code) from None


def _database_busy(error):
    code = getattr(error, 'sqlite_errorcode', None)
    return (code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or
        any(word in str(error).lower() for word in ('busy', 'locked')))


@contextmanager
def _transaction(db):
    """Normalize bounded statement/final-commit contention without retry."""
    try:
        with db:
            yield
    except sqlite3.OperationalError as error:
        if _database_busy(error):
            _fail('DATABASE_BUSY')
        raise


def journal_path(*, create_directory=False):
    root=Path(os.environ.get("NEXUS_PORTABLE_DATA",str(Path.home()/".nexus-operative"))).expanduser().resolve()
    if create_directory:
        root.mkdir(parents=True,exist_ok=True)
    return root/"paper_journal.sqlite3"


def _connect(*, write=False):
    path = journal_path(create_directory=write)
    if not write and not os.path.lexists(path):
        return None
    if write and not os.path.lexists(path):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
    check_existing(path, MAX_DATABASE_BYTES, 'PAPER_REPLAY_JOURNAL_')
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'),
            uri=True, timeout=DB_BUSY_TIMEOUT_MS / 1000)
        db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
        if write:
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
            db.execute('BEGIN IMMEDIATE')
            db.execute("""CREATE TABLE IF NOT EXISTS replay_runs(
                run_id TEXT PRIMARY KEY,created_at INTEGER NOT NULL,
                input_sha256 TEXT NOT NULL,tier TEXT NOT NULL,direction TEXT NOT NULL,
                trades INTEGER NOT NULL,net_pnl REAL NOT NULL,return_pct REAL NOT NULL,
                max_drawdown_pct REAL NOT NULL,capital REAL NOT NULL,
                result_json TEXT NOT NULL)""")
        else:
            db.execute('BEGIN')
        return db
    except sqlite3.OperationalError as error:
        if db is not None:
            db.close()
        if _database_busy(error):
            _fail('DATABASE_BUSY')
        raise
    except BaseException:
        if db is not None:
            db.close()
        raise

def record_replay(request, result):
    if result.get("simulation_only") is not True or result.get("mode")!="OFFLINE_HISTORICAL_REPLAY":
        raise ValueError("Only approved offline replay results can be recorded")
    payload=json.dumps(request,sort_keys=True,separators=(",",":"),allow_nan=False)
    sha=hashlib.sha256(payload.encode("utf-8")).hexdigest()
    run_id=str(uuid.uuid4())
    created=int(time.time())
    permitted={key:result[key] for key in (
        "mode","source","simulation_only","capital","ending_capital","net_pnl",
        "return_pct","max_drawdown_pct","trades","win_rate_pct","profit_factor","warning"
    )}
    with closing(_connect(write=True)) as db:
        with _transaction(db):
            if db.execute('SELECT COUNT(*) FROM replay_runs').fetchone()[0] >= MAX_REPLAY_RUNS:
                _fail('CAPACITY_REVIEW_REQUIRED')
            db.execute("""INSERT INTO replay_runs
            (run_id,created_at,input_sha256,tier,direction,trades,net_pnl,return_pct,
             max_drawdown_pct,capital,result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id,created,sha,request["tier"],request["direction"],result["trades"],
             result["net_pnl"],result["return_pct"],result["max_drawdown_pct"],
             result["capital"],json.dumps(permitted,allow_nan=False)))
    return {"run_id":run_id,"created_at":created,"input_sha256":sha,**permitted}

def recent_replays(limit=20):
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError("Invalid history limit")
    db = _connect()
    if db is None:
        return []
    with closing(db):
        with _transaction(db):
            rows=db.execute("""SELECT run_id,created_at,input_sha256,tier,direction,
                result_json FROM replay_runs ORDER BY created_at DESC, rowid DESC LIMIT ?""",(limit,)).fetchall()
    return [{"run_id":r[0],"created_at":r[1],"input_sha256":r[2],
             "tier":r[3],"direction":r[4],**json.loads(r[5])} for r in rows]
