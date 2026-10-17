import copy
import math
import pytest
from nexus import market_hub as hub, hub_prediction as prediction, onboard_council as council


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA', str(tmp_path))
    rows=[]
    for i in range(300):
        close=str(round(100+i*.02+math.sin(i/8),8))
        rows.append(dict(starts_at=(10000+i)*3600,low=str(float(close)-1),high=str(float(close)+1),open=close,close=close,volume=str(10+i%7)))
    hub.ingest('BTC-USD',3600,rows)
    return hub.snapshot('BTC-USD')


def test_three_real_fits_have_distinct_views_and_five_review_roles(dataset):
    report=prediction.train(dataset)['onboard_council']
    assert len(report['learners'])==3 and len(report['specialists'])==5
    assert len({tuple(m['coefficients']) for m in report['learners']})==3
    for model in report['learners']:
        assert model['last_training_target_at']<model['first_holdout_feature_at']
        assert len(model['validation_blocks'])==3
        for j in set(range(5))-set(model['feature_columns']):
            assert model['coefficients'][j+1]==0 and model['feature_means'][j]==0
    assert report['governor']=='NO_ORDER_AUTHORITY'
    assert not report['provider_connected'] and not report['local_llm_installed']


def test_future_tail_cannot_change_any_specialist_fit(dataset):
    first=prediction.train(dataset)['onboard_council']
    changed=copy.deepcopy(dataset)
    for row in changed['records'][210:]:
        for k in ('open','close','low','high'):row[k]=str(float(row[k])*1.1)
    second=prediction.train(changed)['onboard_council']
    for a,b in zip(first['learners'],second['learners']):
        for key in ('coefficients','feature_means','feature_scales'):assert a[key]==b[key]
    assert first['learners'][0]['validation_blocks']!=second['learners'][0]['validation_blocks']


def passing_learners():
    return [dict(role=name,forecast_return_bps=1,last_training_target_at=1,first_holdout_feature_at=2,
        validation_blocks=[dict(samples=20,mae_log_bps=1,zero_mae_log_bps=2,momentum_mae_log_bps=3) for _ in range(3)]) for name,_ in council.VIEWS]


def test_unanimous_research_support_never_overrides_provenance_or_execution():
    report=council.review(passing_learners())
    assert report['eligible_learners']==3 and not report['disagreement']
    assert report['specialists'][-1]['verdict']=='ABSTAIN'
    assert not report['paper_orders_enabled'] and not report['live_orders_enabled']
    assert report['governor']=='NO_ORDER_AUTHORITY'


@pytest.mark.parametrize('fault',['weak_block','leakage','nan','few_samples','missing_block'])
def test_reviewer_rejects_forged_pass_vote_and_bad_measurements(fault):
    models=passing_learners();item=models[0];item['verdict']='FURTHER_RESEARCH'
    if fault=='weak_block':item['validation_blocks'][1]['mae_log_bps']=4
    if fault=='leakage':item['last_training_target_at']=2
    if fault=='nan':item['forecast_return_bps']=float('nan')
    if fault=='few_samples':item['validation_blocks'][0]['samples']=1
    if fault=='missing_block':item['validation_blocks'].pop()
    result=council.review(models)
    assert result['specialists'][0]['verdict']=='ABSTAIN' and result['eligible_learners']==2
    assert result['specialists'][3]['verdict']=='CAUTION'


def test_opposite_predictions_are_visible_and_cannot_vote_to_trade():
    models=passing_learners();models[0]['forecast_return_bps']=-1
    result=council.review(models)
    assert result['disagreement'] and result['specialists'][3]['reasons']==['PREDICTION_DISAGREEMENT']
    assert not result['paper_orders_enabled']


def test_refresh_retains_previous_artifact_and_failure_keeps_reference(dataset,monkeypatch):
    first=prediction.prepare('BTC-USD');old=first['model_artifact']['content_sha256']
    monkeypatch.setattr(prediction,'ENGINE_VERSION','next-engine-requires-retrain')
    def fail(*args):raise ValueError('candidate failed')
    monkeypatch.setattr(prediction,'train',fail)
    with pytest.raises(ValueError,match='candidate failed'):prediction.prepare('BTC-USD')
    assert hub.latest_model('BTC-USD',3600)==old
    assert hub.read(old)['onboard_council']==first['council']


def test_legacy_pack_retrained_once_and_unchanged_pack_reused_without_training(dataset,monkeypatch):
    legacy=prediction.train(dataset);legacy.pop('onboard_council')
    sealed=hub.seal(legacy);hub.register_model('BTC-USD',3600,sealed['content_sha256'])
    fresh=prediction.prepare('BTC-USD')
    assert not fresh['model_reused'] and fresh['council']['engine_version']==council.ENGINE_VERSION
    assert hub.read(sealed['content_sha256'])==legacy
    monkeypatch.setattr(prediction,'train',lambda *_:pytest.fail('Unchanged model retrained'))
    assert prediction.prepare('BTC-USD')['model_reused']


def test_new_history_refits_locally_and_retains_old_version(dataset,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *_a,**_k:pytest.fail('Local learning opened network'))
    first=prediction.prepare('BTC-USD');old=first['model_artifact']['content_sha256']
    row={k:dataset['records'][-1][k] for k in ('starts_at','open','close','low','high','volume')};row['starts_at']+=3600
    row.update(open='110',close='110',low='109',high='111')
    hub.ingest('BTC-USD',3600,[row,{**row,'starts_at':row['starts_at']+3600}])
    second=prediction.prepare('BTC-USD')
    assert not second['model_reused'] and second['model_artifact']['content_sha256']!=old
    assert second['council']['learners'][0]['coefficients']!=first['council']['learners'][0]['coefficients']
    assert hub.read(old)['onboard_council']==first['council']
    assert prediction.signal(old,now=first['model']['forecast_as_of'])['decision']=='ABSTAIN_DATASET_CHANGED'
