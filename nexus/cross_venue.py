"""Two-venue READ-ONLY quote corroboration, never executable trade approval.

Coinbase exposes an exchange timestamp; Kraken's REST ticker does not.
Consequently this check is provisional cross-venue agreement, NOT verified
quote freshness or a sufficient basis for autonomous orders.
"""
import json
import time
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
from nexus.public_data import PRODUCTS, read_ticker

KRAKEN_PAIRS={"BTC-USD":"XBTUSD","ETH-USD":"ETHUSD","SOL-USD":"SOLUSD"}
KRAKEN_URL="https://api.kraken.com/0/public/Ticker?pair="
MAX_PAYLOAD=65536
MAX_SKEW_SECONDS=5
MAX_SPREAD_BPS=150
MAX_MID_DIVERGENCE_BPS=100

def positive(raw):
    if isinstance(raw,bool):raise ValueError("Invalid quote number")
    try:value=Decimal(str(raw))
    except (ValueError,TypeError,InvalidOperation) as exc:raise ValueError("Invalid quote number") from exc
    if not value.is_finite() or value<=0:raise ValueError("Invalid quote number")
    return value

def parse_kraken(product,payload,received_at=None):
    if product not in PRODUCTS or not isinstance(payload,dict) or payload.get("error")!=[]:
        raise ValueError("Unexpected public Kraken reply")
    result=payload.get("result")
    if not isinstance(result,dict) or len(result)!=1:raise ValueError("Ambiguous Kraken ticker pair")
    ticker=next(iter(result.values()))
    if not isinstance(ticker,dict):raise ValueError("Invalid Kraken ticker data")
    try:
        bid=positive(ticker["b"][0])
        ask=positive(ticker["a"][0])
    except (KeyError,IndexError,TypeError) as exc:
        raise ValueError("Incomplete Kraken bid/ask") from exc
    if bid>ask:raise ValueError("Crossed Kraken market")
    return {"product":product,"provider":"kraken_spot_public",
            "bid":str(bid),"ask":str(ask),
            "received_at":time.time() if received_at is None else float(received_at),
            "has_exchange_timestamp":False,"read_only":True,"orders_allowed":False}

def fetch_kraken(product,transport=None,now=None):
    if product not in KRAKEN_PAIRS:raise ValueError("Product not allowed")
    if transport is None:
        def transport(url):
            req=Request(url,headers={"Accept":"application/json","User-Agent":"NexusOperative-PriceCrosscheck/1.0"})
            with urlopen(req,timeout=4) as response:
                if response.status!=200:raise ValueError("Provider unavailable")
                body=response.read(MAX_PAYLOAD+1)
                if len(body)>MAX_PAYLOAD:raise ValueError("Oversized Kraken response")
                return json.loads(body)
    try:return parse_kraken(product,transport(KRAKEN_URL+KRAKEN_PAIRS[product]),received_at=now)
    except (OSError,TimeoutError,ValueError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError("Public Kraken market data unavailable or invalid") from exc

def corroborate(product,coinbase,kraken,now=None):
    if product not in PRODUCTS:raise ValueError("Unknown instrument")
    now=time.time() if now is None else now
    issues=[]
    samples=[]
    for name,q in (("coinbase_exchange_public",coinbase),("kraken_spot_public",kraken)):
        try:
            if not isinstance(q,dict) or q.get("product")!=product or q.get("provider")!=name:
                raise ValueError("Unexpected venue or instrument")
            if q.get("read_only") is not True or q.get("orders_allowed") is not False:
                raise ValueError("Source permissions ambiguous")
            bid=positive(q["bid"]);ask=positive(q["ask"])
            if bid>ask:raise ValueError("Crossed market")
            if (ask-bid)/((ask+bid)/2)*10000>MAX_SPREAD_BPS:
                issues.append(name+":WIDE_SPREAD")
            received=float(q["received_at"])
            if not (-2<=now-received<=10):
                issues.append(name+":STALE_RECEIPT")
            samples.append(((ask+bid)/2,received))
        except (ValueError,KeyError,TypeError,OverflowError):
            issues.append(name+":INVALID_SNAPSHOT")
    divergence=None
    if len(samples)==2:
        if abs(samples[0][1]-samples[1][1])>MAX_SKEW_SECONDS:
            issues.append("CAPTURE_TIME_SKEW")
        midpoint=(samples[0][0]+samples[1][0])/2
        divergence=float(abs(samples[0][0]-samples[1][0])/midpoint*10000)
        if divergence>MAX_MID_DIVERGENCE_BPS:
            issues.append("CROSS_VENUE_PRICE_DIVERGENCE")
    if kraken.get("has_exchange_timestamp") is not False:
        issues.append("KRAKEN_FRESHNESS_UNCERTAIN")
    return {"component":"TWO_VENUE_READ_ONLY_CORROBORATION","product":product,
            "decision":"VETO" if issues else "PROVISIONAL_AGREEMENT",
            "issues":issues,"mid_divergence_bps":round(divergence,3) if divergence is not None else None,
            "venues":["coinbase_exchange_public","kraken_spot_public"],
            "independent_venues":2 if len(samples)==2 else len(samples),
            "exchange_timestamps_verified":False,"trade_approval":False,
            "live_orders_enabled":False,
            "limitations":"Kraken REST ticker has no exchange timestamp; receipt freshness is not exchange tick freshness. Cross-venue agreement is not proof of executable price or strategy edge."}

def crosscheck(product,coinbase_reader=read_ticker,kraken_reader=fetch_kraken):
    if product not in PRODUCTS:raise ValueError("Unknown instrument")
    # Fail closed if either provider unavailable, never hide the missing source.
    try:
        cb=coinbase_reader(product)
        kr=kraken_reader(product)
    except (ValueError,OSError,TimeoutError):
        return {"component":"TWO_VENUE_READ_ONLY_CORROBORATION","product":product,
                "decision":"VETO","issues":["ONE_OR_MORE_VENUES_UNAVAILABLE"],
                "exchange_timestamps_verified":False,"trade_approval":False,
                "live_orders_enabled":False}
    return corroborate(product,cb,kr)
