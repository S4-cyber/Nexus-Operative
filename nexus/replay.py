"""Deterministic historical-paper replay with configurable capital and measured net outcomes.

Data source is caller-provided candle closes. This module never downloads prices,
creates orders, accesses banks, or represents replay as observed live execution.
"""
from decimal import Decimal
from statistics import mean
from nexus.paper_execution import PaperEngine, TIERS, positive

def replay(closes, capital, tier="terrestrial", direction="UP", threshold_bps=20):
    if tier not in TIERS or direction not in ("UP","DOWN"):
        raise ValueError("Unsupported paper scenario")
    if not isinstance(closes,(list,tuple)) or not 5<=len(closes)<=10000:
        raise ValueError("Provide 5–10000 chronological close prices")
    prices=[positive(p) for p in closes]
    threshold=positive(threshold_bps)/Decimal(10000)
    engine=PaperEngine(capital=capital)
    equity_curve=[engine.initial]
    decisions=[]
    # Next-bar execution: a signal based on past closes cannot trade the same bar.
    # One round-trip at a time, never leverage or naked shorts.
    for i in range(3,len(prices)-1):
        history=prices[:i]
        signal=(history[-1]/history[-3]-1)
        side="UP" if signal>threshold else "DOWN" if signal< -threshold else None
        if side!=direction:
            equity_curve.append(engine.cash)
            continue
        notional=min(engine.cash/Decimal(2),engine.cash*TIERS[tier])
        if notional<=engine.fee*2:
            equity_curve.append(engine.cash)
            continue
        # Replay explicitly supplies deterministic timestamps to avoid checking
        # historical candles against the current system wall clock.
        import time
        ts=time.time()
        try:
            entry=engine.open(tier,"SPY",direction,notional,prices[i],ts,{})
            exit_event=engine.close("SPY",prices[i+1],ts,{})
        except ValueError:
            break
        decisions.append({"entry_index":i,"exit_index":i+1,"direction":direction,
                          "notional":float(notional),"net_pnl":exit_event["realized_pnl_net_fees"]})
        equity_curve.append(engine.cash)
    initial=engine.initial
    high=initial
    worst=Decimal(0)
    for eq in equity_curve:
        high=max(high,eq)
        worst=min(worst,eq/high-1)
    wins=sum(1 for t in decisions if t["net_pnl"]>0)
    return {"mode":"OFFLINE_HISTORICAL_REPLAY","source":"USER_SUPPLIED_CLOSES","simulation_only":True,
            "capital":float(initial),"ending_capital":float(engine.cash),
            "net_pnl":float(engine.cash-initial),"return_pct":float((engine.cash/initial-1)*100),
            "max_drawdown_pct":float(worst*100),"trades":len(decisions),
            "win_rate_pct":round(100*wins/len(decisions),2) if decisions else None,
            "profit_factor": None if not decisions else round(sum(max(0,t["net_pnl"]) for t in decisions)/abs(sum(min(0,t["net_pnl"]) for t in decisions)),3) if any(t["net_pnl"]<0 for t in decisions) else None,
            "trades_sample":decisions[:50],
            "warning":"No spread history, market impact, latency, venue entitlements or out-of-sample validation. This is not evidence of future profitability."}
