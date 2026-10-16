"""Privacy-scoped, deterministic public candle research preview.

No provider calls. Only pre-approved public-market numeric summaries leave
the machine if a user explicitly authorizes the separate paid adviser action.
"""
import hashlib
import json
from decimal import Decimal
from nexus.candle_research import review
from nexus.public_data import PRODUCTS

def build(product="BTC-USD",granularity=3600,limit=150):
    if product not in PRODUCTS or type(granularity)!=int or granularity!=3600 or type(limit)!=int or limit!=150:
        raise ValueError("Unsupported fixed public research selection")
    report=review(product,granularity,limit)
    if not report["research_eligible"]:
        return {"ready":False,"reasons":report["reasons"],"product":product,
                "provider_request_sent":False,"live_orders_enabled":False}
    closes=[Decimal(x) for x in report["closes"]]
    start,end=closes[0],closes[-1]
    change=(end/start-1)*100
    snapshot={"purpose":"public_market_research_only","product":product,
              "provider":"coinbase_exchange_public_unattested",
              "bars":len(closes),"granularity_seconds":granularity,
              "first_close_usd":round(float(start),6),
              "last_close_usd":round(float(end),6),
              "observed_change_pct":round(float(change),6),
              "independent_source_verified":False,
              "suitable_for_live_orders":False,
              "no_account_or_personal_data_included":True}
    serialized=json.dumps(snapshot,sort_keys=True,separators=(",",":"),allow_nan=False)
    digest=hashlib.sha256(serialized.encode()).hexdigest()
    return {"ready":True,"snapshot":snapshot,"sha256":digest,
            "what_is_sent":"Only the displayed aggregate public market fields; no keys, account balances, trades, raw database or personal data.",
            "requires_separate_paid_request_consent":True,
            "provider_request_sent":False,"live_orders_enabled":False}

def authorize_preview(product,digest,role="market_regime",sender=None):
    from nexus.provider_research import request
    if role!="market_regime" or type(digest)!=str or len(digest)!=64:
        raise ValueError("Invalid consent preview")
    preview=build(product)
    if not preview["ready"] or preview["sha256"]!=digest:
        raise ValueError("RESEARCH_PREVIEW_CHANGED_OR_UNAVAILABLE")
    # No silent request; only explicit POST initiated by confirmed UI.
    advice=(sender or request)(role,preview["snapshot"])
    return {"mode":"PUBLIC_SNAPSHOT_AI_ADVISORY","preview_sha256":digest,
            "advisory":advice,"live_orders_enabled":False,
            "trade_approval":False,
            "note":"Output is untrusted research commentary; no trading permissions."}
