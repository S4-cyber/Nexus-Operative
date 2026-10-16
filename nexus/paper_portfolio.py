"""Durable ledger of PAPER-only long spot positions, no autonomous orders.

Paper orders use externally supplied and validated READ-ONLY public quotes.
The API does not interact with brokers, funds or real order endpoints.
"""
import os
import sqlite3
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from nexus.public_data import PRODUCTS

LOCK=threading.RLock()
D=Decimal
FEE=D("0.001")

def num(x):
    if isinstance(x,bool):raise ValueError("Invalid number")
    try: n=D(str(x))
    except (ValueError,TypeError,InvalidOperation):raise ValueError("Invalid number")
    if not n.is_finite() or n<=0:raise ValueError("Expected finite positive number")
    return n

def _connect():
    path=Path(os.environ.get("NEXUS_PORTABLE_DATA",str(Path.home()/".nexus-operative")))
    path.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path/"paper_portfolio.sqlite3",timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS positions (product TEXT PRIMARY KEY, quantity TEXT NOT NULL, entry TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT,at INTEGER NOT NULL,action TEXT NOT NULL,product TEXT NOT NULL,cash_delta TEXT NOT NULL)")
    return db

def state():
    with LOCK,_connect() as db:
        meta=dict(db.execute("SELECT key,value FROM meta"))
        positions=[{"product":p,"quantity":float(D(q)),"entry":float(D(e))}
                   for p,q,e in db.execute("SELECT product,quantity,entry FROM positions ORDER BY product")]
        count=db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    return {"mode":"LOCAL_PAPER_LEDGER","simulation_only":True,"live_orders_enabled":False,
            "initialized":"initial" in meta,
            "initial_capital":float(D(meta["initial"])) if "initial" in meta else None,
            "cash":float(D(meta["cash"])) if "cash" in meta else None,
            "positions":positions,"event_count":count}

def initialize(capital):
    capital=num(capital)
    if capital>D("1000000"):raise ValueError("Capital outside paper limit")
    with LOCK,_connect() as db:
        if db.execute("SELECT value FROM meta WHERE key='initial'").fetchone():
            raise ValueError("Already initialized; no automatic reset")
        db.executemany("INSERT INTO meta(key,value) VALUES(?,?)",[("initial",str(capital)),("cash",str(capital))])
        db.execute("INSERT INTO ledger(at,action,product,cash_delta) VALUES(?,?,?,?)",(int(time.time()),"INIT","",str(capital)))
    return state()

def check_quote(product,quote):
    if product not in PRODUCTS or not isinstance(quote,dict) or quote.get("product")!=product:
        raise ValueError("Unknown product or mismatched quote")
    if quote.get("read_only") is not True or quote.get("orders_allowed") is not False:
        raise ValueError("Quote provenance invalid")
    if quote.get("provider")!="coinbase_exchange_public":
        raise ValueError("Unsupported quote source")
    try: age=time.time()-float(quote["received_at"])
    except (KeyError,TypeError,ValueError):raise ValueError("Missing quote timestamp")
    if not -1<=age<=10:raise ValueError("Quote too old")
    bid=num(quote.get("bid"));ask=num(quote.get("ask"))
    if bid>ask:raise ValueError("Crossed quote")
    return bid,ask

def buy(product,notional,quote):
    amount=num(notional)
    _,ask=check_quote(product,quote)
    with LOCK,_connect() as db:
        meta=dict(db.execute("SELECT key,value FROM meta"))
        if "cash" not in meta:raise ValueError("Initialize first")
        if db.execute("SELECT 1 FROM positions WHERE product=?",(product,)).fetchone():
            raise ValueError("Already holding this paper product")
        cash=D(meta["cash"]);capital=D(meta["initial"])
        if amount>capital*D(".02") or amount*(D(1)+FEE)>cash:
            raise ValueError("Position cap or cash exceeded")
        qty=amount/ask
        delta=-amount*(D(1)+FEE)
        db.execute("INSERT INTO positions(product,quantity,entry) VALUES(?,?,?)",(product,str(qty),str(ask)))
        db.execute("UPDATE meta SET value=? WHERE key='cash'",(str(cash+delta),))
        db.execute("INSERT INTO ledger(at,action,product,cash_delta) VALUES(?,?,?,?)",(int(time.time()),"PAPER_BUY",product,str(delta)))
    return state()

def sell(product,quote):
    bid,_=check_quote(product,quote)
    with LOCK,_connect() as db:
        cash=db.execute("SELECT value FROM meta WHERE key='cash'").fetchone()
        pos=db.execute("SELECT quantity,entry FROM positions WHERE product=?",(product,)).fetchone()
        if not cash or not pos:raise ValueError("Paper position unavailable")
        qty=D(pos[0]);delta=qty*bid*(D(1)-FEE)
        db.execute("DELETE FROM positions WHERE product=?",(product,))
        db.execute("UPDATE meta SET value=? WHERE key='cash'",(str(D(cash[0])+delta),))
        db.execute("INSERT INTO ledger(at,action,product,cash_delta) VALUES(?,?,?,?)",(int(time.time()),"PAPER_SELL",product,str(delta)))
    return state()

def reconcile(quotes):
    info=state()
    if not info["initialized"]:return {**info,"reconciled":False,"reason":"Paper portfolio uninitialized"}
    equity=D(str(info["cash"]))
    for pos in info["positions"]:
        try: bid,_=check_quote(pos["product"],quotes[pos["product"]])
        except (KeyError,ValueError):
            return {**info,"reconciled":False,"reason":"Missing, invalid or stale quote"}
        equity+=D(str(pos["quantity"]))*bid*(D(1)-FEE)
    return {**info,"reconciled":True,"liquidation_value":round(float(equity),6),
            "unrealized_inclusive_pnl":round(float(equity-D(str(info["initial_capital"]))),6),
            "note":"Paper liquidation estimate using available public bid, not guaranteed execution."}
