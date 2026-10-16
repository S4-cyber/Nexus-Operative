"""Opt-in local read-only market observer. It NEVER places paper or real orders.

Observations may provide market-regime *descriptions*, not calibrated returns
or tradable signals. Every observation is persisted with explicit veto reasons.
A single daemon polls public endpoints only while the PORTAL is active.
"""
import json
import os
import sqlite3
import threading
import time
from collections import deque
from decimal import Decimal
from pathlib import Path
from nexus.public_data import PRODUCTS, read_ticker

SYMBOLS=("BTC-USD","ETH-USD","SOL-USD")
MIN_INTERVAL=25.0
MAX_OBSERVATIONS=1000

def journal_path():
    root=Path(os.environ.get("NEXUS_PORTABLE_DATA",str(Path.home()/".nexus-operative")))
    root.mkdir(parents=True,exist_ok=True)
    return root/"market_observations.sqlite3"

def _db():
    con=sqlite3.connect(journal_path(),timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS observations(
      id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
      product TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT NOT NULL)""")
    return con

def persist(record):
    with _db() as db:
        db.execute("INSERT INTO observations(at,product,status,detail_json) VALUES(?,?,?,?)",
                   (record["observed_at"],record["product"],record["decision"],json.dumps(record,allow_nan=False)))
        db.execute("""DELETE FROM observations WHERE id NOT IN
                       (SELECT id FROM observations ORDER BY id DESC LIMIT ?)""",(MAX_OBSERVATIONS,))

def history(limit=30):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError("Bad limit")
    with _db() as db:
        rows=db.execute("SELECT detail_json FROM observations ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    return [json.loads(r[0]) for r in rows]

def assess(product,quote,prior):
    """No opportunity approved: sparse tick history cannot establish net profitability."""
    result={"product":product,"observed_at":time.time(),"mode":"READ_ONLY_MARKET_OBSERVER",
            "simulation_only":True,"live_order_authorized":False,"paper_order_authorized":False,
            "decision":"VETO","reasons":[],"regime":"UNKNOWN","price":None,"spread_bps":None}
    if product not in PRODUCTS or not isinstance(quote,dict) or quote.get("product")!=product:
        result["reasons"].append("INVALID_OR_MISSING_MARKET_QUOTE")
        return result
    if quote.get("read_only") is not True or quote.get("orders_allowed") is not False:
        result["reasons"].append("UNTRUSTED_QUOTE_SOURCE")
        return result
    try:
        bid,ask,last=(Decimal(str(quote[k])) for k in ("bid","ask","last"))
        ts=float(quote["received_at"])
        if not all(x.is_finite() and x>0 for x in (bid,ask,last)) or bid>ask:
            raise ValueError()
        if not -1<=result["observed_at"]-ts<=10:
            raise ValueError()
        spread=(ask-bid)/((ask+bid)/2)*10000
        result["price"]=float(last)
        result["spread_bps"]=round(float(spread),3)
    except (KeyError,ValueError,TypeError,OverflowError):
        result["reasons"].append("STALE_OR_INVALID_QUOTE")
        return result
    if spread>Decimal("30"):
        result["reasons"].append("EXCESSIVE_SPREAD")
    points=list(prior)
    if len(points)<5:
        result["reasons"].append("INSUFFICIENT_OBSERVATIONS")
    else:
        start=Decimal(str(points[-5]))
        move=(last/start-1)*10000
        result["five_observation_move_bps"]=round(float(move),2)
        result["regime"]="UPWARD_MOVE" if move>Decimal("25") else "DOWNWARD_MOVE" if move<Decimal("-25") else "SIDEWAYS"
    result["reasons"].append("NO_VERIFIED_NET_EDGE")
    result["reasons"].append("NO_BROKER_ACCOUNT_OR_TRADING_PERMISSION")
    return result

class ReadOnlyObserver:
    def __init__(self,fetcher=read_ticker,interval=MIN_INTERVAL):
        self.fetcher=fetcher
        self.interval=max(MIN_INTERVAL,float(interval))
        self._stop=threading.Event()
        self._thread=None
        self._lock=threading.Lock()
        self.prices={s:deque(maxlen=32) for s in SYMBOLS}
        self.last_error=None
        self.last_success=None
        self.cycles=0

    def single_cycle(self):
        result=[]
        for symbol in SYMBOLS:
            if self._stop.is_set():break
            try:
                q=self.fetcher(symbol)
                rec=assess(symbol,q,self.prices[symbol])
                if rec["price"] is not None:
                    self.prices[symbol].append(rec["price"])
                    self.last_success=time.time()
            except (ValueError,OSError,TimeoutError) as e:
                self.last_error=type(e).__name__
                rec=assess(symbol,None,[])
                rec["reasons"].append("MARKET_PROVIDER_UNAVAILABLE")
            persist(rec)
            result.append(rec)
        self.cycles+=1
        return result

    def _run(self):
        while not self._stop.is_set():
            try:self.single_cycle()
            except (OSError,sqlite3.Error,ValueError) as exc:
                self.last_error=type(exc).__name__
            self._stop.wait(self.interval)

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():return
            self._stop.clear()
            self._thread=threading.Thread(target=self._run,daemon=True,name="nexus-readonly-observer")
            self._thread.start()

    def stop(self):
        self._stop.set()
        worker=self._thread
        if worker and worker.is_alive() and threading.current_thread()!=worker:
            worker.join(timeout=5)

    def status(self):
        worker=self._thread
        return {"mode":"READ_ONLY_OBSERVER","active":bool(worker and worker.is_alive() and not self._stop.is_set()),
                "interval_seconds":self.interval,"cycles":self.cycles,
                "last_error":self.last_error,"last_success":self.last_success,
                "live_order_authorized":False,"paper_order_authorized":False}

observer=ReadOnlyObserver()
