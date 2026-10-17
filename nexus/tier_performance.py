"""Measured per-tier historical holdout diagnostics (not forecasts).

Reads only locally admitted Coinbase candle history; no requests, model
inference or broker orders. Unknown sample quality reports NO ESTIMATE.
"""
from nexus.candle_research import review
from nexus.walkforward import walk_forward

MIN_HOLDOUT_TRADES=30

def tier_performance(product="BTC-USD"):
    admission=review(product,3600,300)
    base={"mode":"OBSERVED_HISTORICAL_HOLDOUT","product":product,
          "timeframe":"1-hour candles","predicted_success_rate_pct":None,
          "projected_gain_per_minute_pct":None,"projected_gain_per_day_pct":None,
          "live_orders_enabled":False,"trading_approval":False,
          "source":"LOCAL_UNATTESTED_COINBASE_HISTORY",
          "disclaimer":"Historical simulations are not forecasts. No real fills, independent corroboration or performance guarantee."}
    if not admission["research_eligible"]:
        return {**base,"available":False,"reason":"INSUFFICIENT_VALIDATED_HISTORY",
                "data_issues":admission["reasons"],"tiers":[]}
    rows=[]
    for tier in ("terrestrial","solar","universal"):
        study=walk_forward(admission["closes"],10000,tier,"UP")
        hold=study["holdout"]
        sufficient=hold["trades"]>=MIN_HOLDOUT_TRADES and study["train"]["trades"]>=20
        rows.append({"tier":tier,"holdout_trade_count":hold["trades"],
            "holdout_win_rate_pct":hold["win_rate_pct"] if sufficient else None,
            "holdout_net_return_pct":hold["return_pct"] if sufficient else None,
            "holdout_drawdown_pct":hold["max_drawdown_pct"] if sufficient else None,
            "observed_holdout_days":round(study["holdout_observations"]/24,3),
            "observed_return_per_day_pct":round(hold["return_pct"]/(study["holdout_observations"]/24),5) if sufficient else None,
            "usable_as_forecast":False,
            "evidence_status":"HISTORICAL_ONLY_NOT_PREDICTIVE" if sufficient else "INSUFFICIENT_HOLDOUT_TRADES"})
    return {**base,"available":True,"reason":"HISTORICAL_REPLAY_ONLY",
            "tiers":rows,"data_issues":[],
            "note":"The observed per-day rate divides total test return by test duration; it is NOT an expected future daily gain."}
