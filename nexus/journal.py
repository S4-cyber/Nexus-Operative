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

def journal_path():
    root=Path(os.environ.get("NEXUS_PORTABLE_DATA",str(Path.home()/".nexus-operative"))).expanduser().resolve()
    root.mkdir(parents=True,exist_ok=True)
    return root/"paper_journal.sqlite3"

def connect():
    db=sqlite3.connect(journal_path(),timeout=10)
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("""CREATE TABLE IF NOT EXISTS replay_runs(
        run_id TEXT PRIMARY KEY,created_at INTEGER NOT NULL,
        input_sha256 TEXT NOT NULL,tier TEXT NOT NULL,direction TEXT NOT NULL,
        trades INTEGER NOT NULL,net_pnl REAL NOT NULL,return_pct REAL NOT NULL,
        max_drawdown_pct REAL NOT NULL,capital REAL NOT NULL,
        result_json TEXT NOT NULL)""")
    return db

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
    with connect() as db:
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
    with connect() as db:
        rows=db.execute("""SELECT run_id,created_at,input_sha256,tier,direction,
            result_json FROM replay_runs ORDER BY created_at DESC, rowid DESC LIMIT ?""",(limit,)).fetchall()
    return [{"run_id":r[0],"created_at":r[1],"input_sha256":r[2],
             "tier":r[3],"direction":r[4],**json.loads(r[5])} for r in rows]
