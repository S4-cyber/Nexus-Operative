"""SQLite persistence for paper-only PORTAL configuration.

No banking information, payment credentials, brokerage tokens, or live orders.
Each connection is short-lived so a CLI, desktop app or tests can use the same database.
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from contextlib import contextmanager, closing
from contextvars import ContextVar

DEFAULTS = {
    "paper_budget": 10000.0,
    "terrestrial_allocation": 50,
    "solar_allocation": 35,
    "universal_allocation": 15,
}

_settings_root = ContextVar('nexus_isolated_settings_root', default=None)
_connect_lock = threading.RLock()

@contextmanager
def isolated_settings(directory):
    """Scope internal disposable diagnostics to their own settings, never process env."""
    token=_settings_root.set(Path(directory).resolve())
    try:yield
    finally:_settings_root.reset(token)

def db_path():
    directory = _settings_root.get() or Path(os.environ.get("NEXUS_PORTABLE_DATA", str(Path.home() / ".nexus-operative")))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "portal_settings.sqlite3"

def _connect():
    # Keep creation detection through the seed commit in one local critical
    # section. Otherwise a second thread can observe SQLite's newly created
    # empty file, install tables first and make every caller skip the preset.
    with _connect_lock:
        path=db_path()
        fresh=not os.path.lexists(path) and not any(os.path.lexists(path.parent/name)
            for name in ('emergency_cut.intent',*(base+suffix for base in
                ('portal_settings.sqlite3','paper_portfolio.sqlite3','order_lifecycle.sqlite3','paper_forward.sqlite3')
                for suffix in ('','-journal','-wal','-shm'))))
        conn = sqlite3.connect(path, timeout=5)
        try:
            if fresh:
                conn.execute('BEGIN IMMEDIATE')
                fresh=not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
            conn.execute("CREATE TABLE IF NOT EXISTS configuration (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER NOT NULL, event TEXT NOT NULL)")
            if fresh:
                conn.executemany('INSERT INTO configuration(key,value) VALUES(?,?)',
                                 [(key,json.dumps(value)) for key,value in DEFAULTS.items()])
                conn.execute("CREATE TABLE tier_permissions (tier TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))")
                conn.executemany('INSERT INTO tier_permissions VALUES(?,?)',
                                 [('terrestrial',1),('solar',0),('universal',0)])
                conn.execute('INSERT INTO audit_events(time,event) VALUES(?,?)',
                             (int(time.time()),'new_install_paper_defaults'))
            conn.commit()
        except BaseException:
            conn.close()
            raise
        return conn

def load_settings():
    with closing(_connect()) as conn,conn:
        values = dict(DEFAULTS)
        for key, raw in conn.execute("SELECT key, value FROM configuration"):
            if key in DEFAULTS:
                try:
                    values[key] = json.loads(raw)
                except (ValueError, TypeError):
                    pass
        return values

def save_settings(values):
    allowed = set(DEFAULTS)
    if set(values) != allowed:
        raise ValueError("Unexpected or missing paper setting")
    budget = values["paper_budget"]
    allocations = [values[x] for x in ("terrestrial_allocation", "solar_allocation", "universal_allocation")]
    if isinstance(budget, bool) or not isinstance(budget, (float, int)) or not (0 < budget <= 1000000):
        raise ValueError("Invalid paper budget")
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 or x > 100 for x in allocations):
        raise ValueError("Invalid allocations")
    if sum(allocations) != 100:
        raise ValueError("Tier allocations must total 100")
    with closing(_connect()) as conn,conn:
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO configuration(key,value) VALUES (?,?)", (key, json.dumps(value)))
        conn.execute("INSERT INTO audit_events(time,event) VALUES (?,?)", (int(time.time()), "paper_settings_updated"))
    return load_settings()

def audit(event):
    if event not in {"portal_resumed", "portal_paused", "emergency_halt", "emergency_halt_reset_paused","emergency_halt_reset_disengaged"}:
        raise ValueError("Unrecognized audit event")
    with closing(_connect()) as conn,conn:
        conn.execute("INSERT INTO audit_events(time,event) VALUES (?,?)", (int(time.time()), event))

