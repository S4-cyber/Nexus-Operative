"""Bundled, bounded numerical learners and independent research checks.

No LLM weights, plugins, generated code, provider calls or execution permissions.
The caller must supply a dataset already validated by hub_prediction.train.
"""
import math
import statistics

ENGINE_VERSION = 'nexus.onboard-council.v2'
# Fixed before evaluating the tail. Different feature views, same ridge family/data;
# these are correlated experiments, not independent sources or three LLMs.
VIEWS = (('trend', (0, 1, 2)), ('activity', (3, 4)), ('combined', (0, 1, 2, 3, 4)))


def review(learners, provenance_verified=False, walk_forward=None):
    """Recompute verdicts from measurements; never trust a learner's own vote."""
    checked = []
    for item in learners:
        reasons = []
        forecast = item.get('forecast_return_bps')
        if type(forecast) not in (int, float) or not math.isfinite(forecast) or abs(forecast) > 6488:
            reasons.append('FORECAST_OUT_OF_RANGE')
        if not item['last_training_target_at'] < item['first_holdout_feature_at']:
            reasons.append('TEMPORAL_LEAKAGE')
        blocks = item['validation_blocks']
        if len(blocks) != 3:
            reasons.append('VALIDATION_COVERAGE')
        for block in blocks:
            values = [block[k] for k in ('mae_log_bps', 'zero_mae_log_bps', 'momentum_mae_log_bps')]
            if (block['samples'] < 10 or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values)
                    or not values[0] < min(values[1:])):
                reasons.append('BASELINE_NOT_BEATEN_IN_EVERY_BLOCK')
                break
        if walk_forward is not None:
            parts=[];folds=walk_forward.get('folds',[]);coverage=len(folds)==3
            for fold in folds:
                matches=[x for x in fold.get('learners',[]) if x.get('role')==item['role']]
                scenarios=[s for x in matches for s in x.get('cost_scenarios',[]) if s.get('roundtrip_cost_bps')==25]
                if len(matches)!=1 or len(scenarios)!=1:coverage=False
                else:parts.append(scenarios[0])
            if not coverage or len(parts)!=3 or any(s.get('trades',0)<5 for s in parts):
                reasons.append('INSUFFICIENT_DELAYED_FILL_TRADES')
            if not coverage or len(parts)!=3 or any(type(s.get('net_return_pct')) not in (int,float) or not math.isfinite(s['net_return_pct']) or s['net_return_pct']<=0 for s in parts):
                reasons.append('COST_ADJUSTED_FOLDS_NOT_CONSISTENT')
        checked.append(dict(role=item['role'], kind='TRAINED_NUMERICAL_MODEL',
                            verdict='ABSTAIN' if reasons else 'FURTHER_RESEARCH', reasons=sorted(set(reasons)),
                            forecast_return_bps=forecast))
    eligible = [x for x in checked if x['verdict'] == 'FURTHER_RESEARCH']
    signs = {1 if x['forecast_return_bps'] > 0 else -1 if x['forecast_return_bps'] < 0 else 0 for x in eligible}
    disagreement = len(signs) > 1
    checked += [dict(role='validation_review', kind='DETERMINISTIC_REVIEWER',
                     verdict='CAUTION' if disagreement or len(eligible) < 3 else 'FURTHER_RESEARCH',
                     reasons=['PREDICTION_DISAGREEMENT'] if disagreement else ['NOT_ALL_LEARNERS_PASS'] if len(eligible) < 3 else ['RETROSPECTIVE_CHECK_ONLY']),
                dict(role='provenance_execution_review', kind='DETERMINISTIC_REVIEWER', verdict='ABSTAIN',
                     reasons=['NO_EXECUTABLE_FILL_OR_ACCOUNT_EVIDENCE'] + ([] if provenance_verified else ['SOURCE_NOT_INDEPENDENTLY_VERIFIED']))]
    return dict(specialists=checked, disagreement=disagreement, eligible_learners=len(eligible),
                all_abstained=not eligible, walk_forward_review_applied=walk_forward is not None, governor='NO_ORDER_AUTHORITY',
                paper_orders_enabled=False, live_orders_enabled=False)


def train_validated(snapshot, raw_features=None, walk_forward=None):
    from nexus.hub_prediction import features, fit, predict
    rows = snapshot['records']; n = len(rows); split = int(n * .7); horizon = 3
    closes = [float(row['close']) for row in rows]; volumes = [float(row['volume']) for row in rows]
    training = list(range(60, split - horizon)); testing = list(range(split, n - horizon))
    raw = raw_features if raw_features is not None else {t: features(closes, volumes, t) for t in range(60, n)}
    targets = {t: math.log(closes[t + horizon] / closes[t]) for t in training + testing}
    learners = []
    for role, columns in VIEWS:
        x = {t: [v if j in columns else 0.0 for j, v in enumerate(row)] for t, row in raw.items()}
        model = fit([x[t] for t in training], [targets[t] for t in training])
        blocks = []
        for i in range(3):
            indices = testing[len(testing)*i//3:len(testing)*(i+1)//3]
            blocks.append(dict(samples=len(indices),
                mae_log_bps=statistics.fmean(abs(predict(model, x[t])-targets[t]) for t in indices)*10000,
                zero_mae_log_bps=statistics.fmean(abs(targets[t]) for t in indices)*10000,
                momentum_mae_log_bps=statistics.fmean(abs(raw[t][0]/5*horizon-targets[t]) for t in indices)*10000))
        value = predict(model, x[n-1])
        learners.append(dict(role=role, feature_columns=list(columns), **model, horizon_bars=horizon,
            training_samples=len(training), last_training_target_at=rows[training[-1]+horizon]['starts_at']+snapshot['granularity'],
            first_holdout_feature_at=rows[testing[0]]['starts_at']+snapshot['granularity'],
            validation_blocks=blocks, forecast_return_bps=math.expm1(value)*10000 if abs(value)<=.5 else None))
    return dict(engine_version=ENGINE_VERSION, mode='BUILT_IN_TRAINED_RESEARCH_COUNCIL',
                provider_connected=False, local_llm_installed=False, learners=learners, **review(learners,walk_forward=walk_forward),
                refresh_policy='Manual preparation or explicitly armed bounded saved-data refresh; reuse unchanged data and engine. No price/provider requests or code/weight downloads.',
                limitations='Correlated ridge feature specialists, not GPT-class language models or independent data sources. Tail-block errors and separate expanding-window cost checks remain retrospective diagnostics, not calibrated confidence or evidence of profit.')
