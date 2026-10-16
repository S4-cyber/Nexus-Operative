"""Walk-forward validation: compare development slice with untouched holdout.

No parameter optimization on holdout data. Cost assumptions identical between
periods; a signal is not approved just because a short sample makes money.
"""
from decimal import Decimal
from nexus.replay import replay
from nexus.paper_execution import positive

def walk_forward(closes,capital,tier="terrestrial",direction="UP",train_fraction=0.7):
    if not isinstance(closes,(list,tuple)) or len(closes)<30 or len(closes)>10000:
        raise ValueError("Need 30-10000 chronological closes")
    if not isinstance(train_fraction,(float,int)) or isinstance(train_fraction,bool) or not .5<=train_fraction<=.8:
        raise ValueError("Train fraction must be between 0.5 and 0.8")
    prices=[str(positive(x)) for x in closes]
    split=int(len(prices)*train_fraction)
    train=replay(prices[:split],capital,tier,direction)
    # Include only the two preceding prices for indicator warmup.
    # Report indices and holdout length to expose the warmup overlap.
    holdout=replay(prices[split-2:],capital,tier,direction)
    holdout_trades=holdout["trades"]
    train_trades=train["trades"]
    return {
        "mode":"WALK_FORWARD_PAPER_REPLAY",
        "simulation_only":True,
        "split_index":split,
        "train_observations":split,
        "holdout_observations":len(prices)-split,
        "train":{k:train[k] for k in ("trades","net_pnl","return_pct","max_drawdown_pct","win_rate_pct")},
        "holdout":{k:holdout[k] for k in ("trades","net_pnl","return_pct","max_drawdown_pct","win_rate_pct")},
        "decision":"INSUFFICIENT_EVIDENCE" if holdout_trades<20 or train_trades<20 else (
            "REJECT" if holdout["net_pnl"]<=0 or holdout["max_drawdown_pct"]< -5 else "FURTHER_VALIDATION_REQUIRED"
        ),
        "notice":"No training occurs here. This does not prove future profitability; holdout sample needs external market data and realistic execution."
    }
