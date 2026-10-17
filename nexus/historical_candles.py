"""Coinbase PUBLIC OHLCV research bars, single-source and read-only.

No credentials, broker endpoints, order placement or synthetic gap filling.
Only FULLY CLOSED candles are admitted; timestamps are UTC epoch seconds.
"""
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from nexus.public_data import PRODUCTS

GRANULARITIES={60,300,900,3600,21600,86400}
MAX_RESPONSE=131072
MAX_BARS=300
_lock=threading.RLock()

def _db():
    folder=Path(os.environ.get("NEXUS_PORTABLE_DATA",str(Path.home()/".nexus-operative")))
    folder.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(folder/"research_candles.sqlite3",timeout=10)
    db.execute("""CREATE TABLE IF NOT EXISTS candles(
       product TEXT NOT NULL, granularity INTEGER NOT NULL, starts_at INTEGER NOT NULL,
       payload TEXT NOT NULL, digest TEXT NOT NULL, revision_count INTEGER NOT NULL DEFAULT 0,
       PRIMARY KEY(product,granularity,starts_at))""")
    return db

def _price(raw,zero=False):
    if isinstance(raw,bool):raise ValueError("Boolean market number")
    try: n=Decimal(str(raw))
    except (InvalidOperation,ValueError,TypeError) as exc:raise ValueError("Invalid candle number") from exc
    if not n.is_finite() or n<0 or (not zero and n==0):raise ValueError("Invalid candle number")
    return n

def parse_candles(product,granularity,rows,now):
    if product not in PRODUCTS or granularity not in GRANULARITIES:
        raise ValueError("Unapproved candle market or granularity")
    if not isinstance(rows,list) or len(rows)>MAX_BARS:raise ValueError("Invalid candle response length")
    if type(now) not in (int,float) or not math.isfinite(now) or now<=0:raise ValueError("Invalid observation time")
    result=[]
    seen=set()
    for row in rows:
        if not isinstance(row,list) or len(row)!=6:raise ValueError("Invalid candle row")
        start=row[0]
        if isinstance(start,bool) or not isinstance(start,int) or start<0 or start%granularity:
            raise ValueError("Bad candle timestamp alignment")
        if start in seen:raise ValueError("Duplicated candle timestamp")
        seen.add(start)
        if start+granularity>now:continue # incomplete candle is never retained
        low,high,open_,close,volume=(_price(v,zero=i==4) for i,v in enumerate(row[1:]))
        if low>high or not low<=open_<=high or not low<=close<=high:
            raise ValueError("Inconsistent OHLC")
        result.append({"starts_at":start,"low":str(low),"high":str(high),
            "open":str(open_),"close":str(close),"volume":str(volume)})
    return sorted(result,key=lambda r:r["starts_at"])

def fetch_candles(product,granularity=300,now=None,transport=None):
    if product not in PRODUCTS or granularity not in GRANULARITIES:
        raise ValueError("Unsupported candle market or granularity")
    if now is None:
        now=time.time()
    elif type(now) not in (int,float) or not math.isfinite(now) or now<=0:
        raise ValueError("Invalid candle observation time")
    # End on latest fully closed interval. A bounded 150-bar window prevents
    # Coinbase 300-bar pagination limits and oversized historical retrievals.
    end=int(now//granularity)*granularity
    start=end-150*granularity
    query=urlencode({"start":datetime.fromtimestamp(start,timezone.utc).isoformat(),
                     "end":datetime.fromtimestamp(end,timezone.utc).isoformat(),
                     "granularity":granularity})
    url=f"https://api.exchange.coinbase.com/products/{product}/candles?{query}"
    if transport is None:
        def transport(url):
            req=Request(url,headers={"Accept":"application/json","User-Agent":"NexusOperative-ResearchCandles/1.0"})
            with urlopen(req,timeout=5) as response:
                if response.status!=200:raise ValueError("Candle source unavailable")
                body=response.read(MAX_RESPONSE+1)
                if len(body)>MAX_RESPONSE:raise ValueError("Oversized candle response")
                return json.loads(body)
    try:rows=transport(url)
    except (OSError,TimeoutError,ValueError,json.JSONDecodeError) as exc:
        raise ValueError("Read-only candle source unavailable") from exc
    return parse_candles(product,granularity,rows,now)

def collect(product,granularity=300,now=None,transport=None):
    rows=fetch_candles(product,granularity,now,transport)
    inserted=revised=unchanged=0
    with _lock,_db() as db:
        for row in rows:
            raw=json.dumps(row,sort_keys=True,separators=(",",":"))
            digest=hashlib.sha256(raw.encode()).hexdigest()
            prev=db.execute("SELECT digest FROM candles WHERE product=? AND granularity=? AND starts_at=?",
                            (product,granularity,row["starts_at"])).fetchone()
            if prev is None:
                db.execute("INSERT INTO candles(product,granularity,starts_at,payload,digest) VALUES(?,?,?,?,?)",
                           (product,granularity,row["starts_at"],raw,digest))
                inserted+=1
            elif prev[0]!=digest:
                # Preserve a revision flag; never silently label revised bars stable.
                db.execute("UPDATE candles SET payload=?,digest=?,revision_count=revision_count+1 WHERE product=? AND granularity=? AND starts_at=?",
                           (raw,digest,product,granularity,row["starts_at"]))
                revised+=1
            else:unchanged+=1
    return {"mode":"READ_ONLY_HISTORICAL_CANDLES","product":product,"granularity":granularity,
        "received":len(rows),"inserted":inserted,"revised":revised,"unchanged":unchanged,
        "exchange_provenance":"coinbase_exchange_public_unattested",
        "live_orders_enabled":False,"trade_approval":False}

def history(product,granularity=300,limit=300):
    if product not in PRODUCTS or granularity not in GRANULARITIES or type(limit)!=int or not 2<=limit<=1000:
        raise ValueError("Invalid history request")
    with _lock,_db() as db:
        rows=db.execute("SELECT starts_at,payload,digest,revision_count FROM candles WHERE product=? AND granularity=? ORDER BY starts_at DESC LIMIT ?",
                        (product,granularity,limit)).fetchall()
    result=[];issues=[];prior=None
    for stamp,payload,digest,revisions in reversed(rows):
        if prior is not None and stamp-prior!=granularity:
            issues.append("MISSING_BAR_INTERVAL")
        prior=stamp
        try:
            data=json.loads(payload)
            valid=(hashlib.sha256(payload.encode()).hexdigest()==digest and
                   isinstance(data,dict) and data.get("starts_at")==stamp)
        except (ValueError,TypeError):data=None;valid=False
        if not valid:
            issues.append("CORRUPTED_BAR")
            result.append({"starts_at":stamp,"valid":False})
            continue
        if revisions:issues.append("REVISED_BAR")
        result.append({**data,"revision_count":revisions,"valid":True})
    if len(result)<2:issues.append("INSUFFICIENT_HISTORY")
    return {"mode":"SINGLE_SOURCE_CANDLE_RESEARCH","product":product,
        "granularity":granularity,"sample_count":len(result),
        "issues":sorted(set(issues)),"candles":result,
        "suitable_for_backtesting":False,"independent_source_verified":False,
        "live_orders_enabled":False,"note":"Historical public candles can omit periods without trades; missing bars are not filled. Revision flags and source integrity require validation before research approval."}
