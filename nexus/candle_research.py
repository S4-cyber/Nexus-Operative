"""Fail-closed historical candle admission and train-only strategy research.

The existing simulator is illustrative (no quote spread, true fills, or latency).
This interface never approves trading, even if the holdout appears profitable.
"""
from decimal import Decimal,InvalidOperation
from nexus.historical_candles import history, GRANULARITIES
from nexus.public_data import PRODUCTS
from nexus.tournament import tournament

MIN_BARS=120
MAX_BARS=1000

def review(product,granularity=3600,limit=300):
    if product not in PRODUCTS or granularity not in GRANULARITIES:
        raise ValueError("Unsupported research market or interval")
    if type(limit)!=int or not MIN_BARS<=limit<=MAX_BARS:
        raise ValueError("Research needs 120-1000 requested candles")
    source=history(product,granularity,limit)
    reasons=list(source["issues"])
    candles=source["candles"]
    if len(candles)<MIN_BARS:
        reasons.append("INSUFFICIENT_CHRONOLOGICAL_CANDLES")
    # Strictly reject tampered, revised, missing and malformed data rather
    # than quietly retaining only the convenient part of a time series.
    close_prices=[]
    prev=None
    for row in candles:
        if not row.get("valid") or row.get("revision_count",0):
            reasons.append("UNSTABLE_OR_INVALID_CANDLE")
            continue
        stamp=row["starts_at"]
        if prev is not None and stamp-prev!=granularity:
            reasons.append("NONCONTIGUOUS_CANDLE_SEQUENCE")
        prev=stamp
        try:
            close=Decimal(str(row["close"]))
            if not close.is_finite() or close<=0:raise ValueError
        except (ValueError,InvalidOperation,TypeError):
            reasons.append("INVALID_CLOSE_PRICE")
            continue
        close_prices.append(str(close))
    if len(close_prices)!=len(candles):
        reasons.append("INCOMPLETE_CANDLE_VALIDATION")
    return {"mode":"HISTORICAL_CANDLE_ADMISSION","product":product,
        "granularity":granularity,"candles_examined":len(candles),
        "research_eligible":not reasons,"reasons":sorted(set(reasons)),
        "closes":close_prices if not reasons else [],
        "provenance":"PUBLIC_COINBASE_SINGLE_SOURCE_UNATTESTED",
        "backtest_validated":False,"live_orders_enabled":False,
        "limitations":"Continuous unaltered observed OHLCV is a minimum prerequisite, not independent provenance or proof of executable fills."}

def evaluate(product,capital=10000,granularity=3600,limit=300):
    admission=review(product,granularity,limit)
    if not admission["research_eligible"]:
        return {"mode":"CANDLE_RESEARCH_TOURNAMENT","decision":"DATA_REJECTED",
            "reasons":admission["reasons"],"admission":{k:v for k,v in admission.items() if k!="closes"},
            "live_orders_enabled":False,"paper_autotrading_enabled":False}
    # This function deliberately uses the existing training-only selector.
    # An explicit research outcome never becomes an execution permission.
    result=tournament(admission["closes"],capital)
    return {"mode":"CANDLE_RESEARCH_TOURNAMENT",
        "decision":"RESEARCH_ONLY_NOT_APPROVED","admission":{k:v for k,v in admission.items() if k!="closes"},
        "training_selection":result["selected"],"holdout":result["holdout"],
        "training_leaderboard":result["training_leaderboard"],
        "risk_warnings":result["reasons"],
        "model_limitations":"Replays simplified next-bar closes rather than bid/ask books; selected strategies may overfit. This is not live execution.",
        "live_orders_enabled":False,"paper_autotrading_enabled":False}
