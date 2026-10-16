"""Coinbase Exchange PUBLIC ticker reader — GET-only, no credentials or orders.

Access is strictly limited to three hard-coded product paths. A single public
HTTPS endpoint is used. The adapter never accepts URLs, tokens, order details,
or account credentials. Readings are informational, not execution signals.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import threading
import time
from urllib.request import Request, urlopen

PRODUCTS={"BTC-USD","ETH-USD","SOL-USD"}
BASE="https://api.exchange.coinbase.com/products/"
MAX_AGE_SECONDS=30
CACHE_SECONDS=3
_lock=threading.Lock()
_cache={}

def _value(raw):
    try:
        value=Decimal(str(raw))
    except (TypeError,ValueError,InvalidOperation) as exc:
        raise ValueError("Market response contains invalid price") from exc
    if not value.is_finite() or value<=0:
        raise ValueError("Market response contains nonpositive price")
    return value

def parse_ticker(product,payload,received=None):
    if product not in PRODUCTS:raise ValueError("Product not on public allowlist")
    if not isinstance(payload,dict):raise ValueError("Invalid market response")
    received=time.time() if received is None else received
    try:
        stamp=str(payload["time"])
        tick=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        if tick.tzinfo is None:raise ValueError("Timezone missing")
        age=received-tick.timestamp()
        bid=_value(payload["bid"])
        ask=_value(payload["ask"])
        last=_value(payload["price"])
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        raise ValueError("Incomplete or invalid public quote") from exc
    if not -2<=age<=MAX_AGE_SECONDS:raise ValueError("Stale or future-dated public quote")
    if bid>ask:raise ValueError("Crossed market quote")
    spread_bps=(ask-bid)/((ask+bid)/2)*10000
    return {"provider":"coinbase_exchange_public","product":product,"bid":float(bid),
        "ask":float(ask),"last":float(last),"spread_bps":round(float(spread_bps),3),
        "exchange_time":stamp,"received_at":received,"age_seconds":round(age,3),
        "read_only":True,"orders_allowed":False,
        "note":"Public exchange ticker; not a broker entitlement or executable price guarantee."}

def read_ticker(product,fetch=None,now=None):
    if product not in PRODUCTS:raise ValueError("Unsupported market product")
    # Never cache after the exchange tick itself is stale.
    now=time.time() if now is None else now
    with _lock:
        cached=_cache.get(product)
        if fetch is None and cached and 0<=now-cached[0]<CACHE_SECONDS:
            return dict(cached[1])
    if fetch is None:
        def fetch(url):
            req=Request(url,headers={"Accept":"application/json","User-Agent":"NexusOperative-PublicMarketReader/1.0"})
            with urlopen(req,timeout=4) as response:
                if response.status!=200:raise ValueError("Market provider not available")
                body=response.read(65537)
                if len(body)>65536:raise ValueError("Market response too large")
                return json.loads(body)
    try:
        data=fetch(BASE+product+"/ticker")
        result=parse_ticker(product,data,received=now)
    except (OSError,TimeoutError,ValueError,json.JSONDecodeError) as exc:
        raise ValueError("Public market quote unavailable or invalid") from exc
    if fetch is not None and fetch.__name__ != "fetch":
        # A test or caller-supplied transport must not populate production cache.
        return result
    with _lock:
        _cache[product]=(now,result)
    return result
