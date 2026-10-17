import copy
from concurrent.futures import ThreadPoolExecutor
import json
import math
import sqlite3
import zlib
import pytest
from nexus import market_hub as h
from nexus import hub_prediction as model

@pytest.fixture
def hub(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    return tmp_path


def bars(n=300,g=3600):
    return [dict(starts_at=(10000+i)*g,low=str(round(99+i*.01+math.sin(i/8),8)),high=str(round(101+i*.01+math.sin(i/8),8)),open=str(round(100+i*.01+math.sin(i/8),8)),close=str(round(100+i*.01+math.sin(i/8),8)),volume=str(10+i%7)) for i in range(n)]


def add(n=300,g=3600):
    rows=bars(n,g);return rows,h.ingest('BTC-USD',g,rows,now=rows[-1]['starts_at']+g)


def test_dedup_normalizes_equivalent_numbers_and_excludes_instance_metadata(hub):
    rows,result=add();assert result['inserted']==300
    changed=copy.deepcopy(rows)
    for row in changed:row['volume']+='.0'
    result=h.ingest('BTC-USD',3600,changed,now=rows[-1]['starts_at']+7200)
    assert result['duplicates']==300 and h.status()['bars']==300
    bad={**rows[0],'api_key':'secret'}
    with pytest.raises(ValueError,match='PUBLIC_FIELDS'):h.ingest('BTC-USD',3600,[bad])


def test_conflicts_are_deduplicated_quarantined_and_never_replace_accepted_history(hub):
    rows,_=add();original=h.snapshot('BTC-USD')
    changed={**rows[0],'volume':'99'}
    assert h.ingest('BTC-USD',3600,[changed])['quarantined']==1
    assert h.ingest('BTC-USD',3600,[changed])['duplicates']==1
    result=h.snapshot('BTC-USD')
    assert result['records']==original['records'] and result['issues']==['CONFLICTING_SOURCE_VERSIONS']
    with pytest.raises(ValueError,match='QUALITY_BLOCKED'):model.train(result)


def test_concurrent_instances_store_one_copy(hub):
    rows=bars(20)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda i:h.ingest('BTC-USD',3600,rows),range(4)))
    assert sum(r['inserted'] for r in results)==20
    assert sum(r['duplicates'] for r in results)==60
    assert h.status()['bars']==20


def test_batch_cap_failure_rolls_back_prior_insertions(hub,monkeypatch):
    monkeypatch.setattr(h,'MAX_BARS',1)
    with pytest.raises(ValueError,match='BAR_CAP'):h.ingest('BTC-USD',3600,bars(2))
    assert h.status()['bars']==0


def test_sealed_pack_is_content_addressed_compressed_read_only_and_reused(hub):
    add();data=h.snapshot('BTC-USD');a=h.seal(data);b=h.seal(data)
    assert a==b and a['packed_bytes']<a['raw_bytes'] and a['read_only']
    assert h.read(a['content_sha256'])==data and h.status()['artifact_count']==1
    assert not (h.root()/'objects'/(a['content_sha256']+'.nhub')).stat().st_mode&0o222


def test_tampered_pack_and_decompression_bomb_are_rejected(hub):
    add();a=h.seal(h.snapshot('BTC-USD'));path=h.root()/'objects'/(a['content_sha256']+'.nhub')
    path.chmod(0o600);path.write_bytes(zlib.compress(b'{}'))
    with pytest.raises(ValueError,match='INTEGRITY'):h.read(a['content_sha256'])
    with pytest.raises(ValueError,match='OVERSIZED'):h.unpack(zlib.compress(b'x'*(h.MAX_RAW_BYTES+1)))
    with pytest.raises(ValueError):h.unpack(zlib.compress(b'{}')+b'trailing')
    with pytest.raises(ValueError,match='ARTIFACT_ID'):h.read('../private')


def test_artifact_budget_has_no_silent_eviction(hub,monkeypatch):
    a=h.seal({'fixture':1});monkeypatch.setattr(h,'MAX_ARTIFACTS',1)
    with pytest.raises(ValueError,match='ARTIFACT_CAP'):h.seal({'fixture':2})
    assert h.read(a['content_sha256'])=={'fixture':1}


def test_status_reads_do_not_initialize_data(hub):
    assert h.status()['bars']==0 and not h.root().exists()


@pytest.mark.parametrize('field,value',[('close','nan'),('close',True),('volume','-1'),('low','999'),('starts_at',1),('close','1e-30')])
def test_invalid_public_records_fail_before_storage(hub,field,value):
    row=bars(1)[0];row[field]=value
    with pytest.raises(ValueError):h.ingest('BTC-USD',3600,[row])
    assert not h.root().exists()


def test_momentum_models_are_trained_with_purged_labels_and_training_only_scalers(hub):
    add();data=h.snapshot('BTC-USD');trained=model.train(data)
    assert len(trained['models'])==3 and not trained['live_orders_enabled'] and not trained['paper_orders_enabled']
    for fit in trained['models']:
        assert fit['last_training_target_at']<fit['first_holdout_feature_at']
        assert fit['training_samples']>100 and fit['holdout_samples']>50
        assert fit['holdout_mae_log_bps']>=0 and len(fit['coefficients'])==6
    # Change only held-out future prices: fitted weights/scalers cannot change.
    changed=copy.deepcopy(data)
    for row in changed['records'][210:]:
        for field in ('low','high','open','close'):row[field]=str(float(row[field])*1.1)
    retrained=model.train(changed)
    for a,b in zip(trained['models'],retrained['models']):
        assert a['coefficients']==b['coefficients'] and a['feature_means']==b['feature_means'] and a['feature_scales']==b['feature_scales']


def test_missing_data_blocks_learning_and_flat_prices_do_not_claim_directional_hits(hub):
    rows=bars();rows.pop(120);h.ingest('BTC-USD',3600,rows)
    with pytest.raises(ValueError,match='QUALITY_BLOCKED'):model.train(h.snapshot('BTC-USD'))
    snapshot=h.snapshot('BTC-USD');snapshot['issues']=[]
    with pytest.raises(ValueError,match='NONCONTIGUOUS'):model.train(snapshot)
    flat=bars()
    for row in flat:row.update(low='99',high='101',open='100',close='100')
    h.ingest('ETH-USD',3600,flat);trained=model.train(h.snapshot('ETH-USD'))
    assert all(f['directional_accuracy'] is None and f['forecast_return_bps']==0 for f in trained['models'])


def test_preparation_persists_and_reuses_models_and_council_never_executes(hub):
    add();a=model.prepare('BTC-USD');b=model.prepare('BTC-USD')
    assert a['model_artifact']==b['model_artifact'] and b['model_reused']
    assert a['dataset']==b['dataset'] and h.status()['artifact_count']==2
    assert len(a['council']['specialists'])==5 and a['council']['all_abstained']
    assert not a['council']['paper_orders_enabled'] and not a['cloud_connected']
    sha=h.latest_model('BTC-USD',3600);at=a['model']['forecast_as_of']
    fresh=model.signal(sha,now=at+1);stale=model.signal(sha,now=at+7201)
    assert fresh['fresh'] and len(fresh['forecasts'])==3
    assert not stale['forecasts'] and stale['decision']=='ABSTAIN_STALE_OR_FUTURE_DATA'
    assert not fresh['paper_orders_enabled']


def test_bounded_backfill_filters_requested_windows_and_replays_without_duplicates(hub):
    from urllib.parse import urlsplit,parse_qs
    from datetime import datetime
    calls=[]
    def transport(url):
        calls.append(url);q=parse_qs(urlsplit(url).query);end=int(datetime.fromisoformat(q['end'][0]).timestamp())
        return [[at,'99','101','100','100','10'] for at in range(end-150*3600,end,3600)]
    at=20000*3600
    result=h.backfill('BTC-USD',3600,now=at,transport=transport)
    assert len(calls)==4 and result['inserted']==600
    assert h.backfill('BTC-USD',3600,now=at,transport=transport)['duplicates']==600
    assert not h.snapshot('BTC-USD',limit=1000)['issues']


def test_new_data_invalidates_cached_signal_without_scanning_archive_and_requires_new_fit(hub):
    rows,_=add();prepared=model.prepare('BTC-USD');sha=prepared['model_artifact']['content_sha256'];at=prepared['model']['forecast_as_of']
    next_row={**rows[-1],'starts_at':rows[-1]['starts_at']+3600}
    h.ingest('BTC-USD',3600,[next_row])
    assert model.signal(sha,now=at+1)['decision']=='ABSTAIN_DATASET_CHANGED'
    with pytest.raises(ValueError,match='REQUIRES_REFRESH'):h.latest_model('BTC-USD',3600)
    replacement=model.prepare('BTC-USD')
    assert not replacement['model_reused'] and replacement['model_artifact']['content_sha256']!=sha


def test_conflicting_new_version_invalidates_saved_model_and_blocks_retraining(hub):
    rows,_=add();prepared=model.prepare('BTC-USD');sha=prepared['model_artifact']['content_sha256']
    h.ingest('BTC-USD',3600,[{**rows[0],'volume':'777'}])
    assert model.signal(sha,now=prepared['model']['forecast_as_of']+1)['decision']=='ABSTAIN_DATASET_CHANGED'
    with pytest.raises(ValueError,match='QUALITY_BLOCKED'):model.prepare('BTC-USD')


def test_model_publish_rechecks_dataset_generation(hub):
    rows,_=add();trained=model.train(h.snapshot('BTC-USD'));artifact=h.seal(trained)
    h.ingest('BTC-USD',3600,[{**rows[-1],'starts_at':rows[-1]['starts_at']+3600}])
    with pytest.raises(ValueError,match='DATA_CHANGED_DURING_TRAINING'):h.register_model('BTC-USD',3600,artifact['content_sha256'])


def test_local_import_checks_digest_and_revision_before_hub_write(hub):
    from nexus.historical_candles import _db
    rows=bars(3)
    with _db() as db:
        for row in rows:
            raw=json.dumps(row,sort_keys=True,separators=(',',':'))
            db.execute('INSERT INTO candles VALUES(?,?,?,?,?,?)',('BTC-USD',3600,row['starts_at'],raw,h.digest(raw.encode()),0))
    assert h.import_local('BTC-USD')['inserted']==3
    with _db() as db:db.execute('UPDATE candles SET revision_count=1')
    with pytest.raises(ValueError,match='REVISED_OR_CORRUPT'):h.import_local('BTC-USD')
    assert h.status()['bars']==3


def test_partial_backfill_retry_retains_and_deduplicates_successful_pages(hub):
    from urllib.parse import parse_qs,urlsplit
    from datetime import datetime
    calls=[]
    def transport(url):
        calls.append(url)
        if len(calls)==2:raise OSError('fixture outage')
        end=int(datetime.fromisoformat(parse_qs(urlsplit(url).query)['end'][0]).timestamp())
        return [[at,'99','101','100','100','10'] for at in range(end-150*3600,end,3600)]
    with pytest.raises(ValueError):h.backfill('BTC-USD',now=20000*3600,transport=transport)
    assert h.status()['bars']==150


def test_default_backfill_rejects_response_identity_before_reading_body(hub,monkeypatch):
    from nexus.public_data import _opener
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def geturl(self):return 'https://other.invalid/candles'
        def read(self,*args):pytest.fail('mismatched identity must precede body read')
    monkeypatch.setattr(_opener,'open',lambda *a,**k:Response())
    with pytest.raises(ValueError,match='source unavailable'):h.backfill('BTC-USD',now=20000*3600)
    assert not h.root().exists()


def test_hub_backup_restore_preserves_frozen_models_and_deduplication(hub,monkeypatch,tmp_path):
    from nexus.recovery_bundle import backup,restore
    add();prepared=model.prepare('BTC-USD');sha=prepared['model_artifact']['content_sha256']
    # Snapshot the whole data directory using the existing offline tool.
    destination=tmp_path.parent/(tmp_path.name+'-hub-backup');restored=tmp_path.parent/(tmp_path.name+'-hub-restored')
    assert backup(tmp_path,destination,application_closed=True)['verified']
    assert restore(destination,restored,application_closed=True)['verified']
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(restored))
    assert h.latest_model('BTC-USD',3600)==sha
    assert h.read(sha)==prepared['model']
    assert h.ingest('BTC-USD',3600,bars())['duplicates']==300
    import os,subprocess,sys
    code='import json;from nexus import market_hub as h;from nexus.hub_prediction import signal;sha=h.latest_model("BTC-USD",3600);print(json.dumps(signal(sha,now='+str(prepared['model']['forecast_as_of']+1)+')))'
    child=subprocess.run([sys.executable,'-c',code],env={**os.environ,'NEXUS_PORTABLE_DATA':str(restored)},capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)['model_content_sha256']==sha and json.loads(child.stdout)['fresh']


def test_api_hub_preparation_is_explicit_and_preserves_order_denial(hub,monkeypatch):
    from fastapi.testclient import TestClient
    import nexus.app as backend
    client=TestClient(backend.app)
    assert client.get('/api/matrix/hub').json()['bars']==0
    assert not h.root().exists()
    assert client.get('/api/matrix/hub/signal').status_code==422
    calls=[]
    def collect(product,g):
        calls.append(product);add();return dict(inserted=300,duplicates=0,quarantined=0)
    monkeypatch.setattr(h,'backfill',collect)
    result=client.post('/api/matrix/hub/build').json()
    assert calls==['BTC-USD'] and not result['paper_orders_enabled'] and not result['live_orders_enabled']
    assert client.get('/api/matrix/hub/signal').json()['decision']=='ABSTAIN_STALE_OR_FUTURE_DATA'
    assert client.post('/api/matrix/hub/build?product=PRIVATE').status_code==422


def test_failed_artifact_publication_leaves_no_partial_visible_pack(hub,monkeypatch):
    monkeypatch.setattr(h.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('fixture interrupted publish')))
    with pytest.raises(OSError,match='interrupted publish'):h.seal({'fixture':1})
    assert list((h.root()/'objects').iterdir())==[]


def test_hub_ui_requires_explicit_build_and_cached_reads_have_no_write(hub):
    import shutil,subprocess
    from pathlib import Path
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual hub-control check')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/hub_controls_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
