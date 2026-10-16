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
MAX_SPREAD_BPS=D("150")

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
    db.execute("""CREATE TABLE IF NOT EXISTS position_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ledger_id INTEGER NOT NULL UNIQUE,
      action TEXT NOT NULL, product TEXT NOT NULL,
      quantity TEXT NOT NULL, entry TEXT NOT NULL)""")
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
        db.executemany("INSERT INTO meta(key,value) VALUES(?,?)",[("initial",str(capital)),("cash",str(capital)),("position_journal_version","2")])
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
    if (ask-bid)/((ask+bid)/2)*D("10000")>MAX_SPREAD_BPS:
        raise ValueError("Excessive bid-ask spread")
    return bid,ask

def verify_ledger(db):
    row=db.execute("SELECT value FROM meta WHERE key='cash'").fetchone()
    if not row:raise ValueError("Uninitialized ledger")
    deltas=db.execute("SELECT cash_delta FROM ledger ORDER BY id").fetchall()
    if not deltas:raise ValueError("Missing cash journal")
    cash=D(row[0])
    if sum((D(x[0]) for x in deltas),D(0))!=cash:
        raise ValueError("Cash journal mismatch: new simulated buys blocked")
    return cash

def buy(product,notional,quote):
    amount=num(notional)
    _,ask=check_quote(product,quote)
    from nexus.portfolio_governor import require_safe_ledger
    require_safe_ledger()
    with LOCK,_connect() as db:
        meta=dict(db.execute("SELECT key,value FROM meta"))
        if "cash" not in meta:raise ValueError("Initialize first")
        if db.execute("SELECT 1 FROM positions WHERE product=?",(product,)).fetchone():
            raise ValueError("Already holding this paper product")
        cash=verify_ledger(db);capital=D(meta["initial"])
        exposure=sum((D(q)*D(entry) for q,entry in db.execute("SELECT quantity,entry FROM positions")),D(0))
        if exposure+amount>capital*D(".10"):raise ValueError("Aggregate exposure limit")
        if amount>capital*D(".02") or amount*(D(1)+FEE)>cash:
            raise ValueError("Position cap or cash exceeded")
        qty=amount/ask
        delta=-amount*(D(1)+FEE)
        db.execute("INSERT INTO positions(product,quantity,entry) VALUES(?,?,?)",(product,str(qty),str(ask)))
        db.execute("UPDATE meta SET value=? WHERE key='cash'",(str(cash+delta),))
        cur=db.execute("INSERT INTO ledger(at,action,product,cash_delta) VALUES(?,?,?,?)",(int(time.time()),"PAPER_BUY",product,str(delta)))
        db.execute("INSERT INTO position_events(ledger_id,action,product,quantity,entry) VALUES(?,?,?,?,?)",
                   (cur.lastrowid,"PAPER_BUY",product,str(qty),str(ask)))
    return state()

def sell(product,quote):
    bid,_=check_quote(product,quote)
    with LOCK,_connect() as db:
        cash=verify_ledger(db)
        pos=db.execute("SELECT quantity,entry FROM positions WHERE product=?",(product,)).fetchone()
        if not pos:raise ValueError("Paper position unavailable")
        qty=D(pos[0]);delta=qty*bid*(D(1)-FEE)
        db.execute("DELETE FROM positions WHERE product=?",(product,))
        db.execute("UPDATE meta SET value=? WHERE key='cash'",(str(cash+delta),))
        cur=db.execute("INSERT INTO ledger(at,action,product,cash_delta) VALUES(?,?,?,?)",(int(time.time()),"PAPER_SELL",product,str(delta)))
        db.execute("INSERT INTO position_events(ledger_id,action,product,quantity,entry) VALUES(?,?,?,?,?)",
                   (cur.lastrowid,"PAPER_SELL",product,str(qty),str(pos[1])))
    return state()

def reconcile(quotes):
    info=state()
    if not info["initialized"]:return {**info,"reconciled":False,"reason":"Paper portfolio uninitialized"}
    with LOCK,_connect() as db:
        try:verified_cash=verify_ledger(db)
        except (ValueError,InvalidOperation):
            return {**info,"reconciled":False,"reason":"Cash journal mismatch"}
    equity=verified_cash
    for pos in info["positions"]:
        try: bid,_=check_quote(pos["product"],quotes[pos["product"]])
        except (KeyError,ValueError):
            return {**info,"reconciled":False,"reason":"Missing, invalid or stale quote"}
        equity+=D(str(pos["quantity"]))*bid*(D(1)-FEE)
    return {**info,"reconciled":True,"liquidation_value":round(float(equity),6),
            "unrealized_inclusive_pnl":round(float(equity-D(str(info["initial_capital"]))),6),
            "note":"Paper liquidation estimate using available public bid, not guaranteed execution."}
