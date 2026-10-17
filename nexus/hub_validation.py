"""Fixed expanding-window, delayed-fill diagnostics for validated hub data.

No parameter search, winner selection, provider calls or execution permission.
All prices/costs are retrospective modeling assumptions, never executable fills.
"""
import math
from nexus import market_hub as hub
from nexus.onboard_council import VIEWS

SCHEMA='nexus.hub-walk-forward.v1'
COSTS_BPS=(10,25,50)
HORIZON=3
ENTRY_DELAY_BARS=2  # feature at close t; wait a full bar before open t+2
ENTRY_THRESHOLD_BPS=30


def metrics(trades,cost_bps):
    """Compound sequential unit-capital trades, charging friction on both legs."""
    half=cost_bps/20000
    log_equity=peak=drawdown=positive=negative=0.0;wins=0
    for trade in trades:
        ratio=trade['exit_open']*(1-half)/(trade['entry_open']*(1+half))
        net=ratio-1;log_equity+=math.log(ratio);peak=max(peak,log_equity)
        drawdown=max(drawdown,-math.expm1(min(0,log_equity-peak)))
        wins+=net>0;positive+=max(net,0);negative+=max(-net,0)
    representable=abs(log_equity)<=50
    return dict(roundtrip_cost_bps=cost_bps,trades=len(trades),
        net_return_pct=math.expm1(log_equity)*100 if representable else None,
        max_drawdown_pct=drawdown*100,win_fraction=wins/len(trades) if trades else None,
        normalized_profit_factor=positive/negative if negative else None,
        status='NUMERIC_RANGE_ABSTAIN' if not representable else 'MODELED_ONLY' if trades else 'NO_TRADES')


def evaluate_validated(snapshot,raw_features):
    from nexus.hub_prediction import fit,predict
    rows=snapshot['records'];n=len(rows);g=snapshot['granularity'];first=n//2
    closes=[float(row['close']) for row in rows]
    cuts=[first+(n-first)*i//3 for i in range(4)]
    folds=[];all_trades={role:[] for role,_ in VIEWS}
    for start,end in zip(cuts,cuts[1:]):
        training=list(range(60,start-HORIZON))
        targets=[math.log(closes[t+HORIZON]/closes[t]) for t in training]
        decisions=list(range(start,end-HORIZON-ENTRY_DELAY_BARS,HORIZON+1))
        learners=[]
        for role,columns in VIEWS:
            def view(t):return [v if j in columns else 0.0 for j,v in enumerate(raw_features[t])]
            fitted=fit([view(t) for t in training],targets)
            trades=[];abstained=0
            for t in decisions:
                forecast=predict(fitted,view(t))
                if abs(forecast)>.5:abstained+=1;continue
                if math.expm1(forecast)*10000<=ENTRY_THRESHOLD_BPS:continue
                entry=t+ENTRY_DELAY_BARS;exit_=entry+HORIZON
                trades.append(dict(decision_at=rows[t]['starts_at']+g,
                    entry_at=rows[entry]['starts_at'],exit_at=rows[exit_]['starts_at'],
                    entry_open=float(rows[entry]['open']),exit_open=float(rows[exit_]['open']),
                    forecast_return_bps=math.expm1(forecast)*10000))
            all_trades[role].extend(trades)
            learners.append(dict(role=role,feature_columns=list(columns),fitted_model=fitted,
                decisions=len(decisions),extrapolation_abstentions=abstained,
                trade_evidence_sha256=hub.digest(hub.encoded(trades)),trade_examples=trades[:3],
                cost_scenarios=[metrics(trades,cost) for cost in COSTS_BPS]))
        folds.append(dict(training_bars=start,training_samples=len(training),
            last_training_target_at=rows[training[-1]+HORIZON]['starts_at']+g,
            first_decision_at=rows[start]['starts_at']+g,
            heldout_start_at=rows[start]['starts_at'],heldout_end_at=rows[end-1]['starts_at']+g,
            learners=learners))
    summary=[]
    for role,trades in all_trades.items():
        scenarios=[]
        for cost in COSTS_BPS:
            parts=[next(m for m in fold['learners'] if m['role']==role)['cost_scenarios'][COSTS_BPS.index(cost)] for fold in folds]
            returns=[m['net_return_pct'] for m in parts]
            scenarios.append(dict(**metrics(trades,cost),folds_with_trades=sum(m['trades']>0 for m in parts),
                positive_net_folds=sum(m['trades']>0 and m['net_return_pct'] is not None and m['net_return_pct']>0 for m in parts),
                worst_fold_net_return_pct=min(returns) if all(v is not None for v in returns) else None))
        summary.append(dict(role=role,cost_scenarios=scenarios,trade_evidence_sha256=hub.digest(hub.encoded(trades))))
    return dict(schema=SCHEMA,method='THREE_FIXED_EXPANDING_TRAINING_PREFIXES',
        first_training_fraction=.5,horizon_bars=HORIZON,entry_open_offset_bars=ENTRY_DELAY_BARS,
        decision_stride_bars=HORIZON+1,entry_threshold_bps=ENTRY_THRESHOLD_BPS,costs_bps=list(COSTS_BPS),
        folds=folds,summary=summary,
        decision='FORWARD_EVIDENCE_REQUIRED',paper_orders_enabled=False,live_orders_enabled=False,
        limitations='Fixed long-or-flat unit-capital research schedule, not the forward runner policy. All three feature views are reported without winner selection. Close-return models are refit before each fold; decisions use only closed bars. Entry is modeled at open t+2 and exit three bars later, with no overlapping holdings. Costs split equally across buy/sell legs; no depth, partial fills, impact, source attestation or guaranteed open execution. Full trade evidence is reproducible from the sealed dataset, fitted weights and fixed policy; hashes are not signatures. This retrospective walk-forward exercise cannot establish profitability, consistency or forward acceptance.')
