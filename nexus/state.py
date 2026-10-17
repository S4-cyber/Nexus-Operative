"""SQLite persistence for paper-only PORTAL configuration.

No banking information, payment credentials, brokerage tokens, or live orders.
Each connection is short-lived so a CLI, desktop app or tests can use the same database.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULTS = {
    "paper_budget": 10000.0,
    "terrestrial_allocation": 50,
    "solar_allocation": 35,
    "universal_allocation": 15,
}

def db_path():
    directory = Path(os.environ.get("NEXUS_PORTABLE_DATA", str(Path.home() / ".nexus-operative")))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "portal_settings.sqlite3"

def _connect():
    conn = sqlite3.connect(db_path(), timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS configuration (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER NOT NULL, event TEXT NOT NULL)")
    return conn

def load_settings():
    with _connect() as conn:
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
    with _connect() as conn:
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO configuration(key,value) VALUES (?,?)", (key, json.dumps(value)))
        conn.execute("INSERT INTO audit_events(time,event) VALUES (?,?)", (int(time.time()), "paper_settings_updated"))
    return load_settings()

def audit(event):
    if event not in {"portal_resumed", "portal_paused", "emergency_halt", "emergency_halt_reset_paused"}:
        raise ValueError("Unrecognized audit event")
    with _connect() as conn:
        conn.execute("INSERT INTO audit_events(time,event) VALUES (?,?)", (int(time.time()), event))
