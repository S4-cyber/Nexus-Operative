"""Leakage-controlled, volume-constrained spot research; no executable orders.

A signal formed from CLOSED bar t can enter no earlier than OPEN[t+1] and
exit at OPEN[t+2]. All costs are assumptions; open prints are not fills.
Only training data selects parameters. Evaluation uses two later, disjoint
chronological windows and enforces a conservative prior-volume capacity cap.
"""
from decimal import Decimal
from nexus.candle_research import review
from nexus.historical_candles import history
from nexus.paper_execution import positive

THRESHOLDS=(10,30,60)
SCENARIOS=((10,5),(25,15),(50,30))
PARTICIPATION=Decimal("0.001")
INITIAL=Decimal("10000")

def replay_window(bars,threshold,fee_bps,slippage_bps,start,end):
    if not (3<=start<end<=len(bars)-2):raise ValueError("Invalid chronological evaluation window")
    threshold=positive(threshold)/Decimal(10000)
    fee=positive(fee_bps)/Decimal(10000)
    slip=positive(slippage_bps)/Decimal(10000)
    cash=INITIAL
    peak=cash
    worst=Decimal(0)
    trades=0
    rejected_volume=0
    winners=0
    for t in range(start,end):
        # Signal uses only bars at or before t; fills use future bar OPENS.
        signal=positive(bars[t]["close"])/positive(bars[t-2]["close"])-1
        if signal<=threshold:continue
        last_close=positive(bars[t]["close"])
        prior_base_volume=Decimal(str(bars[t]["volume"]))
        if not prior_base_volume.is_finite() or prior_base_volume<0:
            raise ValueError("Invalid volume")
        capacity=prior_base_volume*last_close*PARTICIPATION
        amount=min(cash*Decimal(".02"),capacity)
        if amount<Decimal("1"):
            rejected_volume+=1
            continue
        entry=positive(bars[t+1]["open"])*(1+slip)
        exit_px=positive(bars[t+2]["open"])*(1-slip)
        pnl=amount*(exit_px/entry-1-fee)
        cash+=pnl
        trades+=1
        winners+=int(pnl>0)
        peak=max(peak,cash)
        worst=min(worst,cash/peak-1)
    return {"trades":trades,"volume_vetoes":rejected_volume,
        "wins":winners,"net_pnl":round(float(cash-INITIAL),6),
        "return_pct":round(float((cash/INITIAL-1)*100),6),
        "drawdown_pct":round(float(worst*100),6)}

def assess(product,granularity=3600,limit=300):
    gate=review(product,granularity,limit)
    common={"mode":"CHRONOLOGICAL_EXECUTION_STRESS","product":product,
        "live_orders_enabled":False,"paper_autotrading_enabled":False,
        "trade_approval":False,"execution_proxy":"NEXT_BAR_OPEN_TO_FOLLOWING_BAR_OPEN",
        "participation_limit_fraction":str(PARTICIPATION)}
    if not gate["research_eligible"]:
        return {**common,"decision":"DATA_REJECTED","reasons":gate["reasons"]}
    rows=history(product,granularity,limit)["candles"]
    if len(rows)<120:raise ValueError("Insufficient history")
    n=len(rows)
    a=int(n*.6);b=int(n*.8)
    # Train on first 60%. Later windows do not affect candidate selection.
    leaderboard=[{"threshold_bps":k,
                  "train":replay_window(rows,k,25,15,3,a-2)}
                 for k in THRESHOLDS]
    winner=max(leaderboard,key=lambda x:(x["train"]["net_pnl"],-x["threshold_bps"]))
    reports=[]
    for name,start,end in (("holdout_a",a,b-2),("holdout_b",b,n-2)):
        scenarios=[{"fee_bps_round_trip":fee,
                    "slippage_bps_per_side":slip,
                    "results":replay_window(rows,winner["threshold_bps"],fee,slip,start,end)}
                   for fee,slip in SCENARIOS]
        reports.append({"name":name,"bar_start_index":start,"bar_end_exclusive":end,
                        "cost_scenarios":scenarios})
    flags=["SINGLE_SOURCE_UNATTESTED_DATA","ASSUMED_OPEN_FILLS_AND_SLIPPAGE",
           "NO_REAL_DEPTH_LATENCY_OR_QUEUE_MODEL"]
    if any(case["results"]["trades"]<10 for h in reports for case in h["cost_scenarios"]):
        flags.append("INSUFFICIENT_HOLDOUT_TRADES")
    if any(case["results"]["net_pnl"]<=0 for h in reports for case in h["cost_scenarios"]):
        flags.append("COST_OR_REGIME_SENSITIVITY")
    return {**common,"decision":"RESEARCH_ONLY_NOT_APPROVED",
        "training_cutoff_index":a,"selected_by":"TRAINING_ONLY",
        "chosen_threshold_bps":winner["threshold_bps"],
        "training_leaderboard":leaderboard,"holdout_windows":reports,
        "warnings":flags,
        "note":"Entry/exit use following-bar open prices, not confirmed actual fills. Capacity uses PREVIOUS closed-bar reported volume; never trade approval."}
