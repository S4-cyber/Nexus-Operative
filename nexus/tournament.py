"""Out-of-sample strategy tournament with conservative admission, never order routing.

Candidates are fixed *before* the holdout is evaluated. No holdout optimization.
Two candidate families compare: directional momentum (UP) and directional
reversal (DOWN). Research rankings are descriptive, not predictions.
"""
from decimal import Decimal
from nexus.replay import replay
from nexus.paper_execution import positive

CANDIDATES=(
    {"id":"momentum_terrestrial","tier":"terrestrial","direction":"UP"},
    {"id":"momentum_solar","tier":"solar","direction":"UP"},
    {"id":"momentum_universal","tier":"universal","direction":"UP"},
    {"id":"reversal_terrestrial","tier":"terrestrial","direction":"DOWN"},
    {"id":"reversal_solar","tier":"solar","direction":"DOWN"},
    {"id":"reversal_universal","tier":"universal","direction":"DOWN"},
)

def tournament(closes,capital,split_fraction=0.7):
    if not isinstance(closes,(list,tuple)) or not 60<=len(closes)<=10000:
        raise ValueError("At least 60 and at most 10000 chronological closes are required")
    if type(split_fraction) not in (int,float) or not .6<=split_fraction<=.8:
        raise ValueError("Split must be 60%-80%")
    values=[str(positive(p)) for p in closes]
    capital=positive(capital)
    if capital>Decimal("1000000"):raise ValueError("Capital outside simulation limits")
    split=int(len(values)*split_fraction)
    training=values[:split]
    holdout=values[split-2:]
    # Training chooses one candidate. Holdout is evaluated exactly once per
    # fixed candidate for diagnostics, but NEVER used to reselect the winner.
    train=[]
    for c in CANDIDATES:
        report=replay(training,capital,c["tier"],c["direction"])
        train.append({**c,"trades":report["trades"],"net_pnl":report["net_pnl"],
                      "return_pct":report["return_pct"],"max_drawdown_pct":report["max_drawdown_pct"]})
    train.sort(key=lambda x:(x["return_pct"],x["max_drawdown_pct"],x["trades"]),reverse=True)
    selected=train[0]
    hold=replay(holdout,capital,selected["tier"],selected["direction"])
    # Trading cadence on synthetic close series is NOT a meaningful basis to
    # approve real money, even if the after-cost results look good.
    reasons=[]
    if selected["trades"]<20:reasons.append("INSUFFICIENT_TRAIN_TRADES")
    if hold["trades"]<20:reasons.append("INSUFFICIENT_HOLDOUT_TRADES")
    if selected["net_pnl"]<=0:reasons.append("TRAINING_NOT_PROFITABLE_AFTER_COSTS")
    if hold["net_pnl"]<=0:reasons.append("HOLDOUT_NOT_PROFITABLE_AFTER_COSTS")
    if selected["max_drawdown_pct"]< -2:reasons.append("TRAINING_DRAWDOWN")
    if hold["max_drawdown_pct"]< -2:reasons.append("HOLDOUT_DRAWDOWN")
    # No approval even if all gates pass: no timestamps, execution latency or
    # independently sourced candles exist in the generic replay interface.
    reasons.append("UNVERIFIED_EXECUTION_AND_MARKET_DATA")
    return {
        "mode":"OFFLINE_STRATEGY_TOURNAMENT",
        "simulation_only":True,
        "source":"USER_SUPPLIED_CLOSES",
        "input_observations":len(values),
        "train_observations":split,
        "holdout_observations":len(values)-split,
        "selected_by":"TRAINING_ONLY",
        "selected":selected,
        "holdout":{"trades":hold["trades"],"net_pnl":hold["net_pnl"],
                   "return_pct":hold["return_pct"],"max_drawdown_pct":hold["max_drawdown_pct"],
                   "win_rate_pct":hold["win_rate_pct"]},
        "training_leaderboard":train,
        "decision":"NOT_APPROVED",
        "reasons":reasons,
        "live_orders_enabled":False,
        "paper_autotrading_enabled":False,
        "warning":"In-sample selection bias and missing actual fills mean no estimated returns are guaranteed."
    }
