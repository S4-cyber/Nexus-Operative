"""Bounded, timestamped public Coinbase quote observations for research.

Stores server-acquired READ-ONLY ticks; no interpolation, candles or simulated
trades. Never passes as historical backtest provenance without further review.
"""
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from nexus.public_data import PRODUCTS, read_ticker

_lock=threading.RLock()
MAX_TICKS=20000

def _db():
    folder=Path(os.environ.get("NEXUS_PORTABLE_DATA",str(Path.home()/".nexus-operative")))
    folder.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(folder/"chronological_quotes.sqlite3",timeout=10)
    db.execute("""CREATE TABLE IF NOT EXISTS ticks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      product TEXT NOT NULL, exchange_at REAL NOT NULL, captured_at REAL NOT NULL,
      payload TEXT NOT NULL, digest TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS ticks_product_id ON ticks(product,id)")
    return db

def normalize(product,q,now):
    if product not in PRODUCTS or not isinstance(q,dict) or q.get("product")!=product:
        raise ValueError("Unsupported or inconsistent quote")
    if q.get("provider")!="coinbase_exchange_public" or q.get("read_only") is not True or q.get("orders_allowed") is not False:
        raise ValueError("Invalid source attribution")
    dt=datetime.fromisoformat(str(q["exchange_time"]).replace("Z","+00:00"))
    if dt.tzinfo is None:raise ValueError("Exchange timestamp without timezone")
    stamp=dt.timestamp()
    if not math.isfinite(now) or not -2<=now-stamp<=30:
        raise ValueError("Stale or future quote")
    values={}
    for field in ("bid","ask","last"):
        n=float(q[field])
        if not math.isfinite(n) or n<=0:
            raise ValueError("Nonfinite or negative price")
        values[field]=n
    if values["bid"]>values["ask"]:raise ValueError("Crossed public price")
    return {"product":product,"provider":"coinbase_exchange_public",
            "exchange_at":stamp,"captured_at":now,**values,
            "read_only":True,"trade_approval":False}

def capture_tick(product,reader=None,clock=None):
    if product not in PRODUCTS:raise ValueError("Unsupported product")
    reader=reader or read_ticker
    now=time.time() if clock is None else float(clock())
    normalized=normalize(product,reader(product),now)
    payload=json.dumps(normalized,sort_keys=True,separators=(",",":"),allow_nan=False)
    digest=hashlib.sha256(payload.encode()).hexdigest()
    with _lock,_db() as db:
        latest=db.execute("SELECT exchange_at FROM ticks WHERE product=? ORDER BY id DESC LIMIT 1",(product,)).fetchone()
        if latest and normalized["exchange_at"]<=latest[0]:
            return {"decision":"DUPLICATE_OR_OUT_OF_ORDER","stored":False,
                    "product":product,"live_orders_enabled":False}
        db.execute("INSERT INTO ticks(product,exchange_at,captured_at,payload,digest) VALUES(?,?,?,?,?)",
                   (product,normalized["exchange_at"],now,payload,digest))
        db.execute("DELETE FROM ticks WHERE id NOT IN (SELECT id FROM ticks ORDER BY id DESC LIMIT ?)",(MAX_TICKS,))
    return {"decision":"STORED_RESEARCH_TICK","stored":True,"product":product,
            "sha256":digest,"exchange_at":normalized["exchange_at"],
            "live_orders_enabled":False,"simulation_only":True}

def dataset(product,limit=250,gap_seconds=60):
    if product not in PRODUCTS:raise ValueError("Unsupported product")
    if type(limit) is not int or not 2<=limit<=1000:raise ValueError("Invalid limit")
    if type(gap_seconds) not in (int,float) or isinstance(gap_seconds,bool) or not math.isfinite(gap_seconds) or not 1<=gap_seconds<=3600:
        raise ValueError("Invalid gap threshold")
    with _lock,_db() as db:
        records=db.execute("SELECT id,product,exchange_at,captured_at,payload,digest FROM ticks WHERE product=? ORDER BY id DESC LIMIT ?",(product,limit)).fetchall()
    ticks=[]
    issues=[]
    previous=None
    for identifier,p,exchange_at,captured_at,payload,digest in reversed(records):
        valid=hashlib.sha256(payload.encode()).hexdigest()==digest
        try:
            data=json.loads(payload)
            valid=valid and isinstance(data,dict) and data.get("product")==p and data.get("exchange_at")==exchange_at and data.get("captured_at")==captured_at
        except (ValueError,TypeError):
            data=None;valid=False
        if not valid:
            issues.append("TICK_INTEGRITY_ERROR")
            ticks.append({"id":identifier,"valid":False})
            continue
        if previous is not None:
            delta=exchange_at-previous
            if delta<=0:issues.append("OUT_OF_ORDER_EXCHANGE_TIME")
            elif delta>gap_seconds:issues.append("MISSING_SAMPLE_INTERVAL")
        previous=exchange_at
        ticks.append({"id":identifier,**data,"valid":True})
    if len(ticks)<2:issues.append("INSUFFICIENT_DATA")
    return {"mode":"SPARSE_PUBLIC_TICK_RESEARCH","product":product,
            "sample_count":len(ticks),"issues":sorted(set(issues)),
            "ticks":ticks,"suitable_for_backtesting":False,
            "source_timestamp_verification":"SINGLE_EXCHANGE_TIMESTAMP_SANITY_ONLY",
            "live_orders_enabled":False,
            "note":"Coinbase event timestamp and local receipt time, not externally attested. This sparse sampled series lacks complete order-book and fill history."}
