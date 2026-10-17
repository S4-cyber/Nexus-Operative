import math
import os
import subprocess
import sys
import threading
import pytest
from nexus import hub_refresh as refresh,market_hub as hub,hub_prediction as prediction


@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'data'))
    clock=[1000.0];worker=refresh.ResearchRefresh(lambda:clock[0])
    yield worker,clock
    worker.stop()


def seed(n=300):
    rows=[]
    for i in range(n):
        c=100+i*.01+math.sin(i/8);rows.append(dict(starts_at=(10000+i)*3600,open=str(c),close=str(c),low=str(c-1),high=str(c+1),volume='10'))
    hub.ingest('BTC-USD',3600,rows);return rows


def arm(local):
    worker,clock=local;worker._arm('BTC-USD',3600,1000);return worker,clock


def test_status_starts_stopped_and_does_not_initialize_storage(local):
    worker,_=local;state=worker.status()
    assert state['phase']=='STOPPED' and not state['enabled'] and not state['ownership_held']
    assert not hub.root().exists() and not state['external_requests_enabled']
    assert not state['paper_orders_enabled'] and not state['starts_automatically']


def test_local_refit_cadence_and_generation_skip_without_second_archive_scan(local,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *_a,**_k:pytest.fail('Refresh contacted network'))
    rows=seed();worker,clock=arm(local);first=worker.step()
    assert first['fits']==1 and first['last_model_content_sha256']==hub.latest_model('BTC-USD',3600)
    # No early poll and no preparation at all for unchanged data on next poll.
    monkeypatch.setattr(prediction,'prepare',lambda *_a,**_k:pytest.fail('Unchanged data retrained'))
    assert worker.step()['polls']==1
    clock[0]+=60;second=worker.step()
    assert second['unchanged_skips']==1 and second['fits']==1
    assert hub.read(first['last_model_content_sha256'])['walk_forward']['decision']=='FORWARD_EVIDENCE_REQUIRED'


def test_new_stored_data_refits_and_keeps_old_pack(local):
    rows=seed();worker,clock=arm(local);old=worker.step()['last_model_content_sha256']
    row={**rows[-1],'starts_at':rows[-1]['starts_at']+3600}
    hub.ingest('BTC-USD',3600,[row]);clock[0]+=60;state=worker.step()
    assert state['fits']==2 and state['last_model_content_sha256']!=old
    assert hub.read(old)['schema']==prediction.SCHEMA


def test_existing_current_pack_reuses_without_charging_fit_budget(local):
    seed();pack=prediction.prepare('BTC-USD');worker,_=arm(local)
    state=worker.step()
    assert state['fits']==0 and state['cached_reuses']==1 and state['last_model_content_sha256']==pack['model_artifact']['content_sha256']


def test_busy_job_defers_and_poll_limit_releases_ownership(local,monkeypatch):
    worker,clock=arm(local);monkeypatch.setattr(refresh,'MAX_POLLS',2)
    refresh.BUILD_LOCK.acquire()
    try:assert worker.step()['busy_deferrals']==1
    finally:refresh.BUILD_LOCK.release()
    clock[0]+=60;state=worker.step()
    assert state['phase']=='COMPLETE' and state['reason']=='SESSION_BUDGET_REACHED'
    assert not state['enabled'] and not state['ownership_held']


def test_fit_attempt_budget_includes_generation_race_failures(local,monkeypatch):
    seed();worker,clock=arm(local);monkeypatch.setattr(refresh,'MAX_FITS',2)
    def raced(*_a,**kwargs):
        kwargs['before_train']();raise ValueError('HUB_DATA_CHANGED_DURING_TRAINING')
    monkeypatch.setattr(prediction,'prepare',raced)
    assert worker.step()['reason']=='DATA_CHANGED_RETRY_LATER'
    clock[0]+=60;state=worker.step()
    assert state['fits']==2 and state['phase']=='COMPLETE' and not state['ownership_held']


def test_missing_data_waits_without_creating_account_or_hub_database(local):
    worker,_=arm(local);state=worker.step()
    assert state['enabled'] and state['reason']=='WAITING_FOR_STORED_DATA'
    assert not (hub.root()/'hub.sqlite3').exists()
    assert not (hub.root().parent/'paper_portfolio.sqlite3').exists()


def test_corrupt_history_pauses_without_erasing_prior_model_or_echoing_exception(local,monkeypatch):
    seed();worker,_=arm(local)
    monkeypatch.setattr(hub,'import_local',lambda *_a:(_ for _ in ()).throw(OSError('PRIVATE SECRET')))
    state=worker.step()
    assert state['phase']=='PAUSED' and state['reason']=='STORAGE_OR_MODEL_FAILURE'
    assert not state['ownership_held'] and 'PRIVATE' not in str(state)


def test_stop_during_training_fences_publication_and_holds_owner_until_done(local,monkeypatch):
    rows=seed();old=prediction.prepare('BTC-USD')['model_artifact']['content_sha256']
    hub.ingest('BTC-USD',3600,[{**rows[-1],'starts_at':rows[-1]['starts_at']+3600}])
    worker,_=arm(local);entered=threading.Event();release=threading.Event();original=prediction.train
    def slow(snapshot):entered.set();assert release.wait(5);return original(snapshot)
    monkeypatch.setattr(prediction,'train',slow)
    thread=threading.Thread(target=worker.step);thread.start()
    try:
        assert entered.wait(5);state=worker.stop()
        assert state['phase']=='STOPPING' and state['ownership_held']
        with pytest.raises(ValueError,match='OWNED'):refresh.RefreshOwnership(hub.root()).acquire()
    finally:release.set();thread.join(10)
    assert not thread.is_alive() and not worker.status()['ownership_held']
    # Old reference remains, but it correctly abstains because its generation is old.
    assert hub.read(old)['schema']==prediction.SCHEMA
    with pytest.raises(ValueError,match='REQUIRES_REFRESH'):hub.latest_model('BTC-USD',3600)
    assert worker.status()['fits']==1 and worker.status()['phase']=='STOPPED'


def test_second_worker_rejected_and_restart_never_resumes(local):
    worker,_=arm(local);other=refresh.ResearchRefresh()
    with pytest.raises(ValueError,match='OWNED'):other._arm('BTC-USD',3600,1000)
    assert not other.status()['enabled']
    worker.stop();other._arm('BTC-USD',3600,1000);other.stop()
    assert not refresh.ResearchRefresh().status()['enabled']


def test_bad_clock_and_changed_root_pause_before_import(local,monkeypatch,tmp_path):
    worker,clock=arm(local);worker.step();clock[0]-=1
    assert worker.step()['reason']=='CLOCK_FAULT' and not worker.status()['ownership_held']
    clock[0]+=100;worker._arm('BTC-USD',3600,1000)
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'elsewhere'))
    state=worker.step();assert state['reason']=='DATA_DIRECTORY_CHANGED' and not hub.root().exists()


def test_admission_loss_stops_before_a_model_can_publish(local):
    allowed=[False];worker,_=local
    with pytest.raises(ValueError,match='PORTAL_MUST_BE_ACTIVE'):worker._arm('BTC-USD',3600,1000,lambda:allowed[0])
    allowed[0]=True;worker._arm('BTC-USD',3600,1000,lambda:allowed[0]);allowed[0]=False
    state=worker.step();assert state['reason']=='PORTAL_INACTIVE' and not state['ownership_held']


def test_thread_start_failure_releases_owner(local,monkeypatch):
    worker,_=local
    monkeypatch.setattr(threading.Thread,'start',lambda *_:(_ for _ in ()).throw(RuntimeError('failed')))
    with pytest.raises(RuntimeError):worker.start('BTC-USD')
    assert worker.status()['reason']=='WORKER_START_FAILED' and not worker.status()['ownership_held']


def test_os_owner_released_after_process_death(tmp_path):
    script='from nexus.hub_refresh import RefreshOwnership;import sys; x=RefreshOwnership(sys.argv[1]);x.acquire();print("ready",flush=True);sys.stdin.read()'
    proc=subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='ready'
        with pytest.raises(ValueError,match='OWNED'):refresh.RefreshOwnership(tmp_path).acquire()
        proc.kill();proc.wait(10);candidate=refresh.RefreshOwnership(tmp_path);candidate.acquire();candidate.release()
    finally:
        if proc.poll() is None:proc.kill();proc.wait(10)
        proc.stdin.close();proc.stdout.close()


def test_api_reads_do_not_start_worker_and_explicit_start_requires_active_portal(local,monkeypatch):
    from fastapi.testclient import TestClient
    from nexus import app
    worker,_=local;monkeypatch.setattr(app,'refresh_worker',worker)
    monkeypatch.setattr(app,'_paused',True);monkeypatch.setattr(app,'_halted',False)
    client=TestClient(app.app)
    assert not client.get('/api/matrix/hub/refresh').json()['enabled']
    assert not hub.root().exists()
    body=dict(product='BTC-USD',granularity=3600,limit=1000)
    assert client.post('/api/matrix/hub/refresh/start',json=body).status_code==409
    assert not hub.root().exists()
    monkeypatch.setattr(app,'_paused',False)
    try:
        assert client.post('/api/matrix/hub/refresh/start',json=body).status_code==200
        assert worker.status()['ownership_held']
        assert client.post('/api/matrix/hub/refresh/stop').status_code==200
    finally:worker.shutdown()
    assert not worker.status()['enabled'] and not worker.status()['ownership_held']


def test_pause_stops_refresh_but_cut_keeps_it_and_shutdown_survives_other_cleanup_failure(local,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from nexus import app
    worker,_=local;monkeypatch.setattr(app,'refresh_worker',worker)
    monkeypatch.setattr(app,'audit',lambda *_:None)
    monkeypatch.setattr(app,'paper_forward_runner',SimpleNamespace(stop=lambda *_:None))
    monkeypatch.setattr(app,'observer',SimpleNamespace(stop=lambda:None))
    monkeypatch.setattr(app,'research_order_gateway',SimpleNamespace(emergency_halt=lambda:None))
    monkeypatch.setattr(app,'_paused',False);monkeypatch.setattr(app,'_halted',False)
    client=TestClient(app.app);worker._arm('BTC-USD',3600,1000)
    assert client.post('/api/portal',json=dict(enabled=False)).status_code==200
    assert not worker.status()['enabled']
    worker._arm('BTC-USD',3600,1000)
    assert client.post('/api/simulate/halt').status_code==200
    assert worker.status()['enabled'] and worker.status()['ownership_held']
    worker.stop()
    monkeypatch.setattr(app,'paper_forward_runner',SimpleNamespace(stop=lambda *_:(_ for _ in ()).throw(ValueError('cleanup failed'))))
    worker._arm('BTC-USD',3600,1000)
    async def close():
        async with app.application_lifespan(None):pass
    with pytest.raises(ValueError,match='cleanup failed'):asyncio.run(close())
    assert not worker.status()['enabled'] and not worker.status()['ownership_held']


def test_status_configuration_is_a_copy_and_actual_ui_controller_is_safe(local):
    import shutil
    from pathlib import Path
    worker,_=arm(local);state=worker.status();state['configuration']['limit']=10000
    assert worker.status()['configuration']['limit']==1000
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual refresh controller smoke')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/hub_refresh_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('product,g,limit',[('DOGE-USD',3600,1000),('BTC-USD',60,1000),('BTC-USD',3600,1001),('BTC-USD',3600,True)],ids=['market','interval','large-window','bool-window'])
def test_invalid_scope_cannot_create_owner_or_arm(local,product,g,limit):
    worker,_=local
    with pytest.raises(ValueError,match='REFRESH_SCOPE'):worker._arm(product,g,limit)
    assert not worker.status()['enabled'] and not hub.root().exists()


def test_stop_ack_waits_for_a_publication_that_already_acquired_guard(local,monkeypatch):
    worker,_=arm(local);entered=threading.Event();release=threading.Event();ack=threading.Event();events=[]
    def publish(*_):entered.set();assert release.wait(5);events.append('published')
    monkeypatch.setattr(hub,'register_model',publish)
    publishing=threading.Thread(target=worker._publish,args=('BTC-USD',3600,'a'*64));publishing.start()
    stopping=None
    try:
        assert entered.wait(5)
        def stop():worker.stop();events.append('stopped');ack.set()
        stopping=threading.Thread(target=stop);stopping.start()
        assert not ack.wait(.05)
    finally:
        release.set();publishing.join(10)
        if stopping:stopping.join(10)
    assert events==['published','stopped'] and not worker.status()['ownership_held']


def test_clock_regression_during_fit_fences_publication(local,monkeypatch):
    rows=seed();prediction.prepare('BTC-USD')
    hub.ingest('BTC-USD',3600,[{**rows[-1],'starts_at':rows[-1]['starts_at']+3600}])
    worker,clock=arm(local);original=prediction.train
    def regressed(snapshot):
        model=original(snapshot);clock[0]-=1;return model
    monkeypatch.setattr(prediction,'train',regressed)
    state=worker.step()
    assert state['reason']=='CLOCK_FAULT' and not state['ownership_held']
    with pytest.raises(ValueError,match='REQUIRES_REFRESH'):hub.latest_model('BTC-USD',3600)
