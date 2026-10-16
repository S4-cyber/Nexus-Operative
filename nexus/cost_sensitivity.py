"""Deterministic, cost-sensitive LONG-ONLY research on admitted candle closes.

No optimization on holdout. Signal uses observations through t-1; entry
occurs at close[t], exit at close[t+1]. Same-bar closing executions are
optimistic proxies, not achievable market fills. No broker connections.
"""
from decimal import Decimal,InvalidOperation
from nexus.candle_research import review
from nexus.paper_execution import positive

COST_SCENARIOS=((10,5),(25,15),(50,30)) # round-trip fee and per-side slippage bps
THRESHOLDS=(10,30,60) # training-only model selection in basis points

def simulate(prices, threshold_bps, fee_bps, slippage_bps):
    prices=[positive(p) for p in prices]
    if len(prices)<5:raise ValueError("Not enough replay observations")
    fee=Decimal(fee_bps)/Decimal(10000)
    slip=Decimal(slippage_bps)/Decimal(10000)
    threshold=Decimal(threshold_bps)/Decimal(10000)
    cash=Decimal("10000")
    trades=0
    wins=0
    peak=cash
    worst=Decimal(0)
    # One-bar holding period, no leverage or overlapping exposure. Signals
    # cannot incorporate the entry bar or any subsequent future bar.
    for t in range(3,len(prices)-1):
        momentum=prices[t-1]/prices[t-3]-1
        if momentum<=threshold:continue
        allocation=cash*Decimal(".02")
        entry=prices[t]*(1+slip)
        exit_price=prices[t+1]*(1-slip)
        gross=allocation*(exit_price/entry-1)
        costs=allocation*fee
        pnl=gross-costs
        cash+=pnl
        trades+=1
        wins+=int(pnl>0)
        peak=max(peak,cash)
        worst=min(worst,cash/peak-1)
    return {"trades":trades,"wins":wins,
        "net_pnl":round(float(cash-10000),6),
        "return_pct":round(float((cash/10000-1)*100),6),
        "max_drawdown_pct":round(float(worst*100),6)}

def sensitivity(product,granularity=3600,limit=300):
    admission=review(product,granularity,limit)
    base={"mode":"COST_SENSITIVITY_RESEARCH","product":product,
        "live_orders_enabled":False,"trade_approval":False,
        "paper_autotrading_enabled":False,
        "fill_model":"OPTIMISTIC_CLOSE_TO_NEXT_CLOSE_PROXY"}
    if not admission["research_eligible"]:
        return {**base,"decision":"DATA_REJECTED","reasons":admission["reasons"]}
    prices=admission["closes"]
    split=int(len(prices)*.7)
    train=prices[:split]
    holdout=prices[split-3:] # warmup only; no overlap in holdout execution
    scores=[(simulate(train,k,25,15),k) for k in THRESHOLDS]
    # Deterministic, train-only choice; never peek at holdout to select.
    selected=max(scores,key=lambda x:(x[0]["net_pnl"],-x[1]))[1]
    cases=[]
    for fee,slippage in COST_SCENARIOS:
        outcome=simulate(holdout,selected,fee,slippage)
        cases.append({"round_trip_fee_bps":fee,"slippage_each_side_bps":slippage,
                      "report":outcome})
    reasons=["UNVERIFIED_CLOSE_FILL_EXECUTION","SINGLE_SOURCE_HISTORICAL_DATA",
             "NO_SPREAD_DEPTH_OR_LATENCY_HISTORY"]
    if any(case["report"]["net_pnl"]<=0 for case in cases):
        reasons.append("NOT_ROBUST_TO_COST_SCENARIOS")
    if min(case["report"]["trades"] for case in cases)<20:
        reasons.append("INSUFFICIENT_HOLDOUT_TRADES")
    return {**base,"decision":"RESEARCH_ONLY_NOT_APPROVED",
        "train_length":split,"holdout_length":len(prices)-split,
        "selection_method":"TRAINING_ONLY",
        "selected_momentum_threshold_bps":selected,
        "train_candidates":[{"threshold_bps":k,"report":report} for report,k in scores],
        "cost_scenarios":cases,"warnings":reasons,
        "note":"Assumed costs only. Closing prices are not executable bids/asks; no live authorization."}
