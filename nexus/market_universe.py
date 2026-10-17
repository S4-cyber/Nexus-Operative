"""Bulk public-market discovery, not bulk trading permission.

GET-only Coinbase Exchange catalog. A catalog row establishes neither live
quotes nor data quality nor account entitlement. Constrain untrusted provider
rows and retain only conservative USD spot candidates for later validation.
"""
from decimal import Decimal, InvalidOperation
from urllib.request import Request,urlopen
import json,re,threading,time

URL="https://api.exchange.coinbase.com/products"
MAX_RESPONSE_BYTES=3_000_000
MAX_PRODUCTS=10000
TTL_SECONDS=300
PATTERN=re.compile(r"^[A-Z0-9]{2,18}-USD$")
_lock=threading.Lock()
_cache=None

def parse(data):
    if not isinstance(data,list) or len(data)>MAX_PRODUCTS:
        raise ValueError("CATALOG_MALFORMED_OR_OVERSIZED")
    accepted=[]
    rejected=0
    observed=set()
    for row in data:
        if not isinstance(row,dict):
            rejected+=1;continue
        symbol=row.get("id")
        if not isinstance(symbol,str) or not PATTERN.fullmatch(symbol) or symbol in observed:
            rejected+=1;continue
        if row.get("quote_currency")!="USD" or row.get("base_currency")!=symbol[:-4]:
            rejected+=1;continue
        observed.add(symbol)
        # Catalog eligibility is strictly research-only and NOT a quote/risk approval.
        status=row.get("status")
        disabled=any(row.get(flag) is True for flag in ("trading_disabled","cancel_only","limit_only","post_only"))
        if status!="online" or disabled:
            rejected+=1;continue
        inc=row.get("quote_increment")
        try:
            price_increment=Decimal(str(inc))
            if not price_increment.is_finite() or price_increment<=0:raise ValueError
        except (InvalidOperation,ValueError,TypeError):
            rejected+=1;continue
        accepted.append({"product":symbol,"base":symbol[:-4],
                         "quote":"USD","status":"online",
                         "quote_increment":str(price_increment),
                         "research_status":"DISCOVERED_UNVERIFIED",
                         "execution_allowed":False})
    accepted.sort(key=lambda row:row["product"])
    return {"mode":"PUBLIC_SPOT_DISCOVERY_ONLY","source":"coinbase_exchange_public",
            "total_provider_rows":len(data),"discovered_count":len(accepted),
            "rejected_rows":rejected,"products":accepted,
            "independently_verified":False,"historical_data_validated":False,
            "ready_for_simulated_orders":False,"live_orders_enabled":False,
            "note":"Discovery alone cannot prove quote freshness, depth, historical quality, legal eligibility, or trade execution permissions."}

def discover(fetch=None,now=None):
    global _cache
    now=time.time() if now is None else now
    if fetch is None:
        with _lock:
            if _cache and 0<=now-_cache[0]<TTL_SECONDS:
                return dict(_cache[1])
        def transport(url):
            req=Request(url,headers={"Accept":"application/json",
              "User-Agent":"NexusOperative-PublicResearchCatalog/1.0"})
            with urlopen(req,timeout=8) as response:
                if response.status!=200:raise ValueError("CATALOG_PROVIDER_UNAVAILABLE")
                raw=response.read(MAX_RESPONSE_BYTES+1)
                if len(raw)>MAX_RESPONSE_BYTES:raise ValueError("CATALOG_TOO_LARGE")
                return json.loads(raw)
        fetch=transport
        use_cache=True
    else:use_cache=False
    try:result=parse(fetch(URL))
    except (OSError,TimeoutError,ValueError,json.JSONDecodeError) as exc:
        raise ValueError("PUBLIC_CATALOG_UNAVAILABLE_OR_INVALID") from exc
    if use_cache:
        with _lock:_cache=(now,result)
    return result

def page(limit=100,offset=0,search="",fetch=None):
    if type(limit)!=int or not 1<=limit<=250 or type(offset)!=int or not 0<=offset<=MAX_PRODUCTS:
        raise ValueError("INVALID_CATALOG_PAGINATION")
    if type(search)!=str or len(search)>24 or not re.fullmatch(r"[A-Za-z0-9-]*",search):
        raise ValueError("INVALID_CATALOG_FILTER")
    data=discover(fetch=fetch)
    filtered=[r for r in data["products"] if search.upper() in r["product"]]
    return {**{k:v for k,v in data.items() if k!="products"},
            "filtered_count":len(filtered),"offset":offset,"limit":limit,
            "products":filtered[offset:offset+limit],"cached_public_catalog":fetch is None}
