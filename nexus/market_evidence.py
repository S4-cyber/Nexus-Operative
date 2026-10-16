"""Bounded local journal for genuinely fetched read-only cross-venue observations.

Only the server-side read functions may populate this store. The stored hash
proves content continuity within this SQLite database, NOT authenticity of an
exchange quote, timestamp, or performance. No orders can be placed here.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from nexus.cross_venue import crosscheck, corroborate, fetch_kraken
from nexus.public_data import PRODUCTS, read_ticker

_lock=threading.RLock()
MAX_ROWS=1200

def _db():
    path=Path(os.environ.get("NEXUS_PORTABLE_DATA",str(Path.home()/".nexus-operative")))
    path.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path/"market_evidence.sqlite3",timeout=10)
    db.execute("""CREATE TABLE IF NOT EXISTS observations(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      observed_at REAL NOT NULL, product TEXT NOT NULL,
      decision TEXT NOT NULL, details TEXT NOT NULL,
      sha256 TEXT NOT NULL)""")
    return db

def _write(product,report,when):
    # Persist only whitelisted, bounded structured diagnostic fields.
    details={"product":product,"decision":report["decision"],
             "issues":report.get("issues",[]),
             "mid_divergence_bps":report.get("mid_divergence_bps"),
             "independent_venues":report.get("independent_venues",0),
             "exchange_timestamps_verified":False,
             "live_orders_enabled":False}
    payload=json.dumps(details,sort_keys=True,separators=(",",":"),allow_nan=False)
    if len(payload.encode())>4096:raise ValueError("Oversized diagnostic")
    digest=hashlib.sha256(payload.encode()).hexdigest()
    with _lock,_db() as db:
        db.execute("INSERT INTO observations(observed_at,product,decision,details,sha256) VALUES(?,?,?,?,?)",
                   (when,product,report["decision"],payload,digest))
        db.execute("DELETE FROM observations WHERE id NOT IN (SELECT id FROM observations ORDER BY id DESC LIMIT ?)",(MAX_ROWS,))
    return {"record_sha256":digest,"stored_locally":True}

def capture(product,coinbase_reader=None,kraken_reader=None,clock=None):
    if product not in PRODUCTS:raise ValueError("Unsupported product")
    now=time.time() if clock is None else float(clock())
    if not 0<now<float("inf"):raise ValueError("Invalid clock")
    # Directly acquire through server-controlled providers, never through
    # client-supplied quote data. Injected transports are test-only.
    cb_reader=coinbase_reader or read_ticker
    kr_reader=kraken_reader or fetch_kraken
    report=crosscheck(product,coinbase_reader=cb_reader,kraken_reader=kr_reader)
    receipt=_write(product,report,now)
    return {**report,**receipt,
            "provenance":"SERVER_SIDE_PUBLIC_READ_ONLY_ENDPOINTS",
            "independent_price_verification":False,
            "simulation_only":True,"trade_approval":False}

def recent(product=None,limit=25):
    if product is not None and product not in PRODUCTS:raise ValueError("Unsupported product")
    if type(limit) is not int or not 1<=limit<=100:raise ValueError("Invalid history limit")
    with _lock,_db() as db:
        if product is None:
            records=db.execute("SELECT id,observed_at,product,decision,details,sha256 FROM observations ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        else:
            records=db.execute("SELECT id,observed_at,product,decision,details,sha256 FROM observations WHERE product=? ORDER BY id DESC LIMIT ?",(product,limit)).fetchall()
    items=[]
    for identifier,when,p,decision,payload,digest in records:
        valid=hashlib.sha256(payload.encode()).hexdigest()==digest
        try: details=json.loads(payload)
        except (ValueError,TypeError):details=None
        valid=valid and isinstance(details,dict) and details.get("product")==p and details.get("decision")==decision and details.get("live_orders_enabled") is False
        items.append({"id":identifier,"observed_at":when,"product":p,
           "decision":decision if valid else "VETO",
           "details":details if valid else None,"local_record_integrity":valid,
           "sha256":digest})
    return {"mode":"LOCAL_READ_ONLY_MARKET_EVIDENCE",
            "observations":items,"live_orders_enabled":False,
            "independent_price_verification":False,
            "warning":"Local hashes do not authenticate exchange prices or prevent full database rewrites."}
