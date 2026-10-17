"""Small trained multi-horizon momentum baseline over sealed hub datasets.

Chronological purged holdout, training-only scaling. Research, never routing.
"""
import math
import statistics
import time
from nexus import market_hub as hub
from nexus.onboard_council import ENGINE_VERSION, train_validated

SCHEMA='nexus.hub-momentum-model.v1'
FEATURES=('momentum_5','momentum_20','momentum_60','volatility_20','volume_surprise_20')
HORIZONS=(1,3,12)


def features(closes,volumes,t):
    returns=[math.log(closes[i]/closes[i-1]) for i in range(t-19,t+1)]
    recent=volumes[t-20:t]
    mean=statistics.fmean(recent);sd=statistics.pstdev(recent)
    return [math.log(closes[t]/closes[t-k]) for k in (5,20,60)]+[statistics.pstdev(returns),(volumes[t]-mean)/(sd or 1)]


def solve(matrix,vector):
    a=[row[:]+[v] for row,v in zip(matrix,vector)];n=len(vector)
    for col in range(n):
        pivot=max(range(col,n),key=lambda r:abs(a[r][col]));a[col],a[pivot]=a[pivot],a[col]
        if abs(a[col][col])<1e-12:raise ValueError('HUB_MODEL_SINGULAR_FIT')
        divisor=a[col][col];a[col]=[v/divisor for v in a[col]]
        for r in range(n):
            if r==col:continue
            factor=a[r][col];a[r]=[x-factor*y for x,y in zip(a[r],a[col])]
    values=[row[-1] for row in a]
    if not all(math.isfinite(v) for v in values):raise ValueError('HUB_MODEL_NONFINITE_FIT')
    return values


def fit(x,y):
    means=[statistics.fmean(row[j] for row in x) for j in range(len(FEATURES))]
    scales=[statistics.pstdev(row[j] for row in x) or 1 for j in range(len(FEATURES))]
    z=[[1]+[(v-m)/s for v,m,s in zip(row,means,scales)] for row in x];width=len(FEATURES)+1
    matrix=[[sum(row[j]*row[k] for row in z)+(0.5 if j==k and j>0 else 0) for k in range(width)] for j in range(width)]
    vector=[sum(row[j]*target for row,target in zip(z,y)) for j in range(width)]
    coefficients=solve(matrix,vector)
    return dict(coefficients=coefficients,feature_means=means,feature_scales=scales,ridge_alpha=0.5)


def predict(model,row):
    z=[1]+[(v-m)/s for v,m,s in zip(row,model['feature_means'],model['feature_scales'])]
    value=sum(w*v for w,v in zip(model['coefficients'],z))
    if not math.isfinite(value):raise ValueError('HUB_MODEL_NONFINITE_PREDICTION')
    return value


def train(snapshot):
    if snapshot.get('schema')!=hub.SCHEMA or snapshot.get('issues'):raise ValueError('HUB_DATASET_QUALITY_BLOCKED')
    rows=snapshot['records'];n=len(rows)
    if not 240<=n<=10000:raise ValueError('HUB_MODEL_REQUIRES_240_TO_10000_BARS')
    g=snapshot['granularity'];product=snapshot['product']
    for row in rows:
        seen=row.get('first_seen_at')
        if type(seen) not in (int,float) or not math.isfinite(seen):raise ValueError('HUB_MODEL_INVALID_AVAILABILITY')
        normalized=hub.normalize(product,g,{k:row[k] for k in ('starts_at','low','high','open','close','volume')},seen)
        if row!={**normalized,'first_seen_at':seen}:raise ValueError('HUB_MODEL_RECORD_IDENTITY')
    if any(b['starts_at']-a['starts_at']!=g for a,b in zip(rows,rows[1:])):raise ValueError('HUB_MODEL_NONCONTIGUOUS_DATA')
    closes=[float(hub.number(row['close'])) for row in rows];volumes=[float(hub.number(row['volume'],zero=True)) for row in rows]
    x={t:features(closes,volumes,t) for t in range(60,n)};split=int(n*.7);models=[]
    for horizon in HORIZONS:
        # Every training target ends strictly before the first held-out feature.
        training=list(range(60,split-horizon));testing=list(range(split,n-horizon))
        y={t:math.log(closes[t+horizon]/closes[t]) for t in training+testing}
        model=fit([x[t] for t in training],[y[t] for t in training])
        errors=[abs(predict(model,x[t])-y[t]) for t in testing]
        residuals=sorted(abs(predict(model,x[t])-y[t]) for t in training)
        forecast=predict(model,x[n-1]);in_range=abs(forecast)<=.5
        momentum=[x[t][0]/5*horizon for t in testing]
        directional=[t for t in testing if abs(y[t])>1e-12]
        models.append(dict(horizon_bars=horizon,horizon_seconds=horizon*g,implementation='TRAINED_LINEAR_RIDGE_NOT_LLM',
            **model,training_samples=len(training),holdout_samples=len(testing),
            last_training_feature_at=rows[training[-1]]['starts_at']+g,
            last_training_target_at=rows[training[-1]+horizon]['starts_at']+g,
            first_holdout_feature_at=rows[testing[0]]['starts_at']+g,
            holdout_mae_log_bps=statistics.fmean(errors)*10000,
            zero_change_baseline_mae_log_bps=statistics.fmean(abs(y[t]) for t in testing)*10000,
            momentum_baseline_mae_log_bps=statistics.fmean(abs(p-y[t]) for p,t in zip(momentum,testing))*10000,
            directional_accuracy=sum((predict(model,x[t])>0)==(y[t]>0) for t in directional)/len(directional) if directional else None,directional_samples=len(directional),
            training_residual_p90_log_bps=residuals[min(len(residuals)-1,int(.9*len(residuals)))]*10000,
            forecast_return_bps=math.expm1(forecast)*10000 if in_range else None,
            forecast_status='RESEARCH_ESTIMATE' if in_range else 'EXTRAPOLATION_ABSTAIN',calibrated_probability=False))
    return dict(schema=SCHEMA,implementation='PURGED_CHRONOLOGICAL_MULTI_HORIZON_RIDGE_BASELINE',
        dataset_content_sha256=hub.digest(hub.encoded(snapshot)),dataset_generation=snapshot['generation'],product=product,granularity=g,bars=n,
        feature_names=list(FEATURES),feature_lookback_bars=60,training_fraction=.7,
        onboard_council=train_validated(snapshot),latest_features=dict(zip(FEATURES,x[n-1])),forecast_as_of=rows[-1]['starts_at']+g,models=models,
        source_verified=False,historical_point_in_time_verified=False,live_orders_enabled=False,paper_orders_enabled=False,
        limitations='Retrospective single-source research, not a proven powerful predictor or profit guarantee. Training-only scalers and purged labels; fixed held-out chronological tail, no random split. Baselines are comparisons, not model selection. Residual bands are training diagnostics, not calibrated intervals. No spread/depth/executable-fill or independent strategy acceptance is established.')


def prepare(product,granularity=3600,limit=1000):
    if type(limit) is not int or not 240<=limit<=10000:raise ValueError('HUB_MODEL_WINDOW_LIMIT')
    try:imported=hub.import_local(product,granularity,min(limit,1000))
    except ValueError as exc:
        if str(exc)!='COLLECT_LOCAL_CANDLES_FIRST':raise
        imported=dict(inserted=0,duplicates=0,quarantined=0,note='No local candle cache; use already stored hub history')
    dataset=hub.snapshot(product,granularity,limit);frozen=hub.seal(dataset)
    reused=False
    try:cached=hub.read(hub.latest_model(product,granularity))
    except ValueError as exc:
        if str(exc) not in ('HUB_NO_TRAINED_MODEL','HUB_MODEL_REQUIRES_REFRESH'):raise
        cached=None
    if cached and cached.get('schema')==SCHEMA and cached.get('dataset_content_sha256')==frozen['content_sha256'] and cached.get('onboard_council',{}).get('engine_version')==ENGINE_VERSION:model=cached;reused=True
    else:model=train(dataset)
    artifact=hub.seal(model)
    council=model['onboard_council']
    hub.register_model(product,granularity,artifact['content_sha256'])
    return dict(mode='SEALED_HUB_RESEARCH_ONLY',imported=imported,dataset=frozen,model_artifact=artifact,
        model=model,model_reused=reused,council=council,cloud_connected=False,live_orders_enabled=False,paper_orders_enabled=False,
        note='One local preparation action; no new public downloads or provider requests. Bundled numerical specialists learn locally; no external AI service or LLM weights are connected. No model output is promoted into the fixed forward policy.')


def signal(model_sha,now=None):
    model=hub.read(model_sha)
    if model.get('schema')!=SCHEMA:raise ValueError('HUB_NOT_A_MODEL_ARTIFACT')
    now=time.time() if now is None else now
    if type(now) not in (int,float) or not math.isfinite(now):raise ValueError('HUB_INVALID_SIGNAL_TIME')
    age=now-model['forecast_as_of'];unchanged=hub.generation(model['product'],model['granularity'])==model['dataset_generation'];fresh=unchanged and 0<=age<=2*model['granularity']
    return dict(mode='FROZEN_MODEL_RESEARCH_SIGNAL',model_content_sha256=model_sha,dataset_content_sha256=model['dataset_content_sha256'],
        product=model['product'],forecast_as_of=model['forecast_as_of'],age_seconds=age,fresh=fresh,
        forecasts=[dict(horizon_bars=m['horizon_bars'],return_bps=m['forecast_return_bps']) for m in model['models']] if fresh else [],
        decision='RESEARCH_ONLY' if fresh else 'ABSTAIN_DATASET_CHANGED' if not unchanged else 'ABSTAIN_STALE_OR_FUTURE_DATA',
        live_orders_enabled=False,paper_orders_enabled=False,cloud_connected=False,
        note='Reads only the small hash-verified frozen model artifact; does not rescan history, retrain or place an order. Not connected to the forward runner.')
