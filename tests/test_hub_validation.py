import copy
import math
import pytest
from nexus import market_hub as hub,hub_prediction as prediction,hub_validation as validation


@pytest.fixture
def data(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    records=[]
    for i in range(300):
        c=100+i*.05+math.sin(i/8)
        records.append(dict(starts_at=(10000+i)*3600,open=str(c),close=str(c),low=str(c-1),high=str(c+1),volume=str(10+i%7)))
    hub.ingest('BTC-USD',3600,records)
    return hub.snapshot('BTC-USD')


def test_expanding_training_purges_targets_and_delays_nonoverlapping_fills(data):
    report=prediction.train(data)['walk_forward'];assert len(report['folds'])==3
    assert [f['training_bars'] for f in report['folds']]==[150,200,250]
    for fold in report['folds']:
        assert fold['last_training_target_at']<fold['first_decision_at']
        for learner in fold['learners']:
            events=learner['trade_examples']
            for event in events:
                assert event['entry_at']-event['decision_at']==3600
                assert event['exit_at']-event['entry_at']==3*3600
                assert event['exit_at']<fold['heldout_end_at']
            assert all(a['exit_at']<=b['entry_at'] for a,b in zip(events,events[1:]))
            assert learner['cost_scenarios'][0]['trades']<=learner['decisions']
    assert not report['paper_orders_enabled'] and report['decision']=='FORWARD_EVIDENCE_REQUIRED'


def test_future_tail_does_not_change_earlier_fold_or_its_weights(data):
    first=prediction.train(data)['walk_forward']
    changed=copy.deepcopy(data)
    for row in changed['records'][200:]:
        for k in ('open','close','low','high'):row[k]=str(float(row[k])*1.1)
    second=prediction.train(changed)['walk_forward']
    assert first['folds'][0]==second['folds'][0]
    assert first['folds'][1]['learners'][0]['fitted_model']==second['folds'][1]['learners'][0]['fitted_model']
    assert first['folds'][2]['learners'][0]['fitted_model']!=second['folds'][2]['learners'][0]['fitted_model']


def test_features_computed_once_and_shared_across_all_validation_fits(data,monkeypatch):
    original=prediction.features;calls=[]
    def counted(c,v,t):calls.append(t);return original(c,v,t)
    monkeypatch.setattr(prediction,'features',counted)
    prediction.train(data)
    assert calls==list(range(60,300))


def test_both_cost_legs_and_compounding_drawdown_are_charged():
    trades=[dict(entry_open=100,exit_open=100),dict(entry_open=100,exit_open=110)]
    m=validation.metrics(trades,50);ratio=100*.9975/(100*1.0025)
    expected=(ratio*(110*.9975/(100*1.0025))-1)*100
    assert m['net_return_pct']==pytest.approx(expected)
    assert m['max_drawdown_pct']==pytest.approx((1-ratio)*100)
    assert m['win_fraction']==.5
    assert validation.metrics(trades,10)['net_return_pct']>m['net_return_pct']


def test_no_trades_is_not_a_winning_strategy_or_infinite_profit_factor():
    result=validation.metrics([],25)
    assert result['status']=='NO_TRADES' and result['trades']==0 and result['net_return_pct']==0
    assert result['win_fraction'] is None and result['normalized_profit_factor'] is None


def test_extreme_compounding_abstains_without_overflow():
    result=validation.metrics([dict(entry_open=1e-18,exit_open=1e12)]*100,25)
    assert result['status']=='NUMERIC_RANGE_ABSTAIN' and result['net_return_pct'] is None
    assert math.isfinite(result['max_drawdown_pct'])


def test_open_fill_changes_affect_results_without_retraining_on_future_prices(data):
    trained=prediction.train(data)['walk_forward'];changed=copy.deepcopy(data)
    example=next(e for learner in trained['folds'][0]['learners'] for e in learner['trade_examples'])
    index=next(i for i,r in enumerate(changed['records']) if r['starts_at']==example['entry_at'])
    row=changed['records'][index];row['open']=str(float(row['open'])*1.1);row['high']=str(float(row['high'])*1.2)
    second=prediction.train(changed)['walk_forward']
    assert [x['fitted_model'] for x in trained['folds'][0]['learners']]==[x['fitted_model'] for x in second['folds'][0]['learners']]
    assert trained['summary']!=second['summary']


def test_flat_market_abstains_and_all_feature_views_and_costs_are_reported(data):
    for row in data['records']:row.update(open='100',close='100',low='99',high='101')
    report=prediction.train(data)['walk_forward']
    assert {x['role'] for x in report['summary']}=={'trend','activity','combined'}
    assert all([c['roundtrip_cost_bps'] for c in x['cost_scenarios']]==[10,25,50] for x in report['summary'])
    assert all(c['status']=='NO_TRADES' for x in report['summary'] for c in x['cost_scenarios'])


def test_prepare_and_build_share_bounded_job_admission(monkeypatch):
    from fastapi.testclient import TestClient
    from nexus import app
    app._hub_build_lock.acquire()
    try:
        client=TestClient(app.app)
        assert client.post('/api/matrix/hub/prepare').status_code==409
        assert client.post('/api/matrix/hub/build').status_code==409
    finally:app._hub_build_lock.release()
    monkeypatch.setattr(prediction,'prepare',lambda *_:(_ for _ in ()).throw(ValueError('fixture invalid dataset')))
    assert client.post('/api/matrix/hub/prepare').status_code==422
    assert app._hub_build_lock.acquire(False)
    app._hub_build_lock.release()


def test_council_cannot_ignore_cost_losses_despite_optimistic_accuracy_votes():
    from nexus.onboard_council import review,VIEWS
    learners=[dict(role=role,forecast_return_bps=50,last_training_target_at=1,first_holdout_feature_at=2,
        validation_blocks=[dict(samples=20,mae_log_bps=1,zero_mae_log_bps=2,momentum_mae_log_bps=3) for _ in range(3)]) for role,_ in VIEWS]
    folds=[dict(learners=[dict(role=role,cost_scenarios=[dict(roundtrip_cost_bps=25,trades=10,net_return_pct=1)]) for role,_ in VIEWS]) for _ in range(3)]
    accepted=review(learners,walk_forward=dict(folds=folds))
    assert accepted['eligible_learners']==3 and not accepted['paper_orders_enabled']
    folds[1]['learners'][0]['cost_scenarios'][0]['net_return_pct']=-1
    rejected=review(learners,walk_forward=dict(folds=folds))
    assert rejected['specialists'][0]['verdict']=='ABSTAIN'
    assert 'COST_ADJUSTED_FOLDS_NOT_CONSISTENT' in rejected['specialists'][0]['reasons']
    folds[1]['learners'][0]['cost_scenarios'][0].update(trades=1,net_return_pct=10)
    thin=review(learners,walk_forward=dict(folds=folds))
    assert thin['specialists'][0]['verdict']=='ABSTAIN' and 'INSUFFICIENT_DELAYED_FILL_TRADES' in thin['specialists'][0]['reasons']
    forged=copy.deepcopy(folds);forged[0]['learners'][0]['cost_scenarios']*=3;forged[1]['learners']=[];forged[2]['learners']=[]
    assert review(learners,walk_forward=dict(folds=forged))['eligible_learners']==0


def test_saved_model_pack_binds_council_and_reproducible_walk_forward(data):
    first=prediction.prepare('BTC-USD');second=prediction.prepare('BTC-USD')
    assert second['model_reused'] and first['model_artifact']==second['model_artifact']
    frozen=hub.read(first['model_artifact']['content_sha256'])
    assert frozen['onboard_council']['walk_forward_review_applied']
    assert frozen['walk_forward']==prediction.train(data)['walk_forward']
    for learner in frozen['walk_forward']['summary']:
        assert all(0<=s['positive_net_folds']<=s['folds_with_trades']<=3 for s in learner['cost_scenarios'])
