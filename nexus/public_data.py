"""Coinbase Exchange PUBLIC ticker reader — GET-only, no credentials or orders.

Access is strictly limited to three hard-coded product paths. A single public
HTTPS endpoint is used; redirects are rejected and the response URL must match
the requested product exactly. The adapter never accepts URLs, tokens, order details,
or account credentials. Readings are informational, not execution signals.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import threading
import time
from urllib.request import Request, HTTPRedirectHandler, build_opener

PRODUCTS={"BTC-USD","ETH-USD","SOL-USD"}
BASE="https://api.exchange.coinbase.com/products/"
MAX_AGE_SECONDS=30
CACHE_SECONDS=3
_lock=threading.Lock()
_cache={}
_flights={}
WAIT_SECONDS=5
CLOCK_TOLERANCE_SECONDS=1
_ERROR='Public market quote unavailable or invalid'

def _new_stats():
    return dict(requests=0,cache_hits=0,shared_waits=0,shared_returns=0,
                wait_timeouts=0,transport_attempts=0,transport_successes=0,
                transport_failures=0,transport_seconds=0.0,max_transport_seconds=0.0)

_stats=_new_stats()

class _Flight:
    def __init__(self):
        self.done=threading.Event()
        self.entry=None


def efficiency():
    """Process-local aggregate observations only; no quote fetch, cache mutation or disk IO."""
    with _lock:
        stats=dict(_stats)
        active=len(_flights)
        cached=sum(not entry[3] for entry in _cache.values())
    successes=stats['transport_successes']
    seconds=stats.pop('transport_seconds');maximum=stats.pop('max_transport_seconds')
    return dict(mode='READ_ONLY_PUBLIC_QUOTE_EFFICIENCY',read_only=True,
        scope='This process runtime default ticker reader only; excludes injected transports and explicit fixture clocks.',
        **stats,mean_successful_transport_ms=round(seconds/successes*1000,3) if successes else None,
        maximum_successful_transport_ms=round(maximum*1000,3) if successes else None,
        active_requests=active,active_request_limit=len(PRODUCTS),cached_products=cached,
        cache_seconds=CACHE_SECONDS,shared_wait_timeout_seconds=WAIT_SECONDS,
        network_requests_made=False,live_orders_enabled=False,
        note='Cache hits and shared returns avoid duplicate requests; timings exclude order execution and are not profitability or broker latency evidence. Counters reset with the process.')


def _finite_clock(value):
    try:return type(value) in (int,float) and math.isfinite(value)
    except OverflowError:return False


def _continuous(wall_start,wall_end,mono_start,mono_end):
    return (_finite_clock(wall_end) and _finite_clock(mono_end)
        and wall_end>=wall_start and mono_end>=mono_start
        and abs((wall_end-wall_start)-(mono_end-mono_start))<=CLOCK_TOLERANCE_SECONDS)


def _fresh(entry,wall,mono,explicit_now):
    if not entry or entry[3]!=explicit_now:return False
    received,quote,stored_mono,_=entry
    if not (0<=wall-received<CACHE_SECONDS and 0<=mono-stored_mono<CACHE_SECONDS):return False
    if not explicit_now and not _continuous(received,wall,stored_mono,mono):return False
    exchange=datetime.fromisoformat(quote['exchange_time'].replace('Z','+00:00')).timestamp()
    return -2<=wall-exchange<=MAX_AGE_SECONDS


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        fp.close()
        raise ValueError("Public market redirects disabled")


_opener=build_opener(_NoRedirect())

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

def _fetch_default(product):
    url=BASE+product+'/ticker'
    req=Request(url,headers={"Accept":"application/json","User-Agent":"NexusOperative-PublicMarketReader/1.0"})
    with _opener.open(req,timeout=4) as response:
        if response.geturl()!=url:raise ValueError("Unexpected market response URL")
        if response.status!=200:raise ValueError("Market provider not available")
        body=response.read(65537)
        if len(body)>65536:raise ValueError("Market response too large")
        return json.loads(body)


def read_ticker(product,fetch=None,now=None):
    if product not in PRODUCTS:raise ValueError("Unsupported market product")
    explicit_now=now is not None
    wall=time.time() if now is None else now
    mono=time.monotonic()
    if not _finite_clock(wall) or not _finite_clock(mono):raise ValueError(_ERROR)
    if fetch is not None:
        # Injected transports neither consume nor populate the production cache/flights.
        try:return parse_ticker(product,fetch(BASE+product+'/ticker'),received=wall)
        except (OSError,TimeoutError,ValueError,json.JSONDecodeError) as exc:raise ValueError(_ERROR) from exc
    runtime=not explicit_now
    flight=None;leader=True
    with _lock:
        if runtime:_stats['requests']+=1
        cached=_cache.get(product)
        if _fresh(cached,wall,mono,explicit_now):
            if runtime:_stats['cache_hits']+=1
            return dict(cached[1])
        if runtime:
            flight=_flights.get(product)
            if flight is None:
                flight=_Flight();_flights[product]=flight
                _stats['transport_attempts']+=1
            else:
                leader=False;_stats['shared_waits']+=1
    if not leader:
        if not flight.done.wait(WAIT_SECONDS):
            with _lock:_stats['wait_timeouts']+=1
            raise ValueError(_ERROR)
        # A shared result must still be fresh at the follower's own return time.
        checked_wall=time.time();checked_mono=time.monotonic()
        with _lock:
            if (_finite_clock(checked_wall) and _finite_clock(checked_mono)
                    and _continuous(wall,checked_wall,mono,checked_mono)
                    and _fresh(flight.entry,checked_wall,checked_mono,False)):
                _stats['shared_returns']+=1
                return dict(flight.entry[1])
        raise ValueError(_ERROR)
    success=False
    try:
        data=_fetch_default(product)
        received=wall if explicit_now else time.time()
        finished=time.monotonic()
        if not _finite_clock(received) or not _finite_clock(finished):raise ValueError(_ERROR)
        if runtime and not _continuous(wall,received,mono,finished):raise ValueError(_ERROR)
        result=parse_ticker(product,data,received=received)
        # Private cache metadata preserves the original receipt; it never refreshes quote age.
        entry=(received,dict(result),finished,explicit_now)
        with _lock:
            _cache[product]=entry
            if runtime:
                flight.entry=entry
                _stats['transport_successes']+=1
                elapsed=finished-mono
                _stats['transport_seconds']+=elapsed
                _stats['max_transport_seconds']=max(_stats['max_transport_seconds'],elapsed)
            success=True
        return dict(result)
    except (OSError,TimeoutError,ValueError,json.JSONDecodeError) as exc:
        raise ValueError(_ERROR) from exc
    finally:
        if runtime:
            # Every exit, including unexpected transport failures, releases all waiters.
            with _lock:
                if not success:_stats['transport_failures']+=1
                if _flights.get(product) is flight:del _flights[product]
                flight.done.set()
