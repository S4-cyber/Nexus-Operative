"""Out-of-sample regime diagnostics for validated historical candles.

Regimes are descriptive, tagged using the preceding 12 *closed* candles.
They cannot select a trading strategy or authorize orders. Testing uses the
threshold selected solely from the training period.
"""
from decimal import Decimal
from nexus.candle_research import review
from nexus.historical_candles import history
from nexus.execution_realism import replay_window, THRESHOLDS

LOOKBACK=12
VOLATILITY_CUTOFF_BPS=Decimal("75")

def classify(bars,t):
    if t<LOOKBACK:raise ValueError("Insufficient trailing context")
    # All contextual candles end at or before t; the future open is never used.
    prices=[Decimal(str(bars[i]["close"])) for i in range(t-LOOKBACK,t+1)]
    returns=[abs(prices[i]/prices[i-1]-1) for i in range(1,len(prices))]
    movement=(prices[-1]/prices[0]-1)*10000
    avg_abs=sum(returns,Decimal(0))/len(returns)*10000
    if avg_abs>=VOLATILITY_CUTOFF_BPS:
        return "HIGH_VOLATILITY"
    if movement>Decimal("50"):return "RISING"
    if movement<Decimal("-50"):return "FALLING"
    return "SIDEWAYS"

def analyze(product,granularity=3600,limit=300):
    gate=review(product,granularity,limit)
    base={"mode":"REGIME_STRATIFIED_RESEARCH","product":product,
          "decision":"DATA_REJECTED","trade_approval":False,
          "live_orders_enabled":False,"paper_autotrading_enabled":False}
    if not gate["research_eligible"]:
        return {**base,"reasons":gate["reasons"]}
    bars=history(product,granularity,limit)["candles"]
    n=len(bars);cut=int(n*.6)
    # No holdout tuning: choose candidate only on the training interval.
    selected=max(THRESHOLDS,key=lambda k:(replay_window(bars,k,25,15,LOOKBACK,cut-2)["net_pnl"],-k))
    labels=("RISING","FALLING","SIDEWAYS","HIGH_VOLATILITY")
    results=[]
    for name,start,end in (("HOLDOUT_A",cut,int(n*.8)-2),("HOLDOUT_B",int(n*.8),n-2)):
        # Segment contiguous sequences with the same regime. We allow short
        # segments for audit visibility but never treat small samples as proof.
        segments=[]
        start_segment=start
        prev_label=None
        for t in range(start,end):
            label=classify(bars,t)
            if prev_label is not None and label!=prev_label:
                segments.append((prev_label,start_segment,t))
                start_segment=t
            prev_label=label
        if prev_label is not None:segments.append((prev_label,start_segment,end))
        by_regime=[]
        for label in labels:
            pieces=[]
            for kind,begin,finish in segments:
                if kind==label:
                    # Single-index windows are valid: replay_window requires
                    # start < end, and never leaves the sample boundary.
                    pieces.append(replay_window(bars,selected,25,15,begin,finish))
            by_regime.append({"regime":label,"segments":len(pieces),
                "bars":sum(finish-begin for kind,begin,finish in segments if kind==label),
                "trades":sum(p["trades"] for p in pieces),
                "net_pnl_sum_independent_segments":round(sum(p["net_pnl"] for p in pieces),6),
                "low_sample":sum(p["trades"] for p in pieces)<10})
        results.append({"window":name,"start_index":start,"end_exclusive":end,
                        "regimes":by_regime})
    warnings=["INFERRED_REGIMES_NOT_FORECASTS","SINGLE_SOURCE_RESEARCH_ONLY",
              "SEGMENT_PNL_IS_NOT_COMPOUNDED_ACCOUNT_EQUITY",
              "NO_LIVE_SPREAD_OR_ORDER_BOOK_FILLS"]
    if any(x["low_sample"] for w in results for x in w["regimes"]):
        warnings.append("INSUFFICIENT_REGIME_TRADE_SAMPLES")
    return {**base,"decision":"RESEARCH_ONLY_NOT_APPROVED",
        "training_cutoff_index":cut,"threshold_selected_from":"TRAINING_ONLY",
        "selected_threshold_bps":selected,"lookback_closed_candles":LOOKBACK,
        "holdout_regimes":results,"warnings":warnings}
