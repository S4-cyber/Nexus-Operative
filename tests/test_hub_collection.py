from datetime import datetime
from urllib.parse import urlsplit,parse_qs
import math
import threading
import pytest
from nexus import hub_collection as collection,hub_refresh as refresh,market_hub as hub


@pytest.fixture
def session(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'data'))
    elapsed=[1000.];wall=[12000*300+10.];calls=[]
    def transport(url):
        calls.append(url);parts=urlsplit(url);q=parse_qs(parts.query);g=int(q['granularity'][0])
        assert parts.hostname=='api.exchange.coinbase.com' and parts.path=='/products/BTC-USD/candles'
        start=int(datetime.fromisoformat(q['start'][0]).timestamp());end=int(datetime.fromisoformat(q['end'][0]).timestamp())
        return [[t,str(99+math.sin(t/g/8)),str(101+math.sin(t/g/8)),str(100+math.sin(t/g/8)),str(100+math.sin(t/g/8)),'10'] for t in range(start,end,g)]
    worker=refresh.ResearchRefresh(lambda:elapsed[0],lambda:wall[0],transport)
    yield worker,elapsed,wall,calls,transport
    worker.stop()


def arm(session,public=True):
    worker,*_=session;worker._arm('BTC-USD',300,1000,collect_public_history=public);return worker


def advance(session,seconds=60):session[1][0]+=seconds;session[2][0]+=seconds


def test_default_saved_only_never_calls_transport(session):
    worker=arm(session,False);worker.step()
    assert not session[3] and worker.status()['source_requests']==0
    assert not worker.status()['external_requests_enabled']


def test_bootstrap_dedup_and_new_closed_interval_incremental_learning(session):
    worker=arm(session);first=worker.step()
    assert first['source_requests']==4 and hub.status()['bars']==600 and first['fits']==1
    assert first['public_history_enabled'] and not first['provider_requests_enabled']
    old=first['last_model_content_sha256'];advance(session);worker.step()
    assert len(session[3])==4 and worker.status()['unchanged_skips']==1
    advance(session,240);second=worker.step()
    assert len(session[3])==5 and hub.status()['bars']==601 and second['fits']==2
    assert second['last_model_content_sha256']!=old and hub.read(old)['schema']=='nexus.hub-momentum-model.v1'
    worker.stop();worker._arm('BTC-USD',300,1000,collect_public_history=True)
    third=worker.step();assert hub.status()['bars']==601 and third['cached_reuses']==1


def test_five_second_settling_delay_does_not_fetch_still_closing_interval(session):
    worker=arm(session);session[2][0]=12000*300+2;worker.step()
    end=parse_qs(urlsplit(session[3][0]).query)['end'][0]
    assert int(datetime.fromisoformat(end).timestamp())==11999*300


def test_stop_during_fetch_discards_response_and_retains_owner_until_done(session):
    worker=arm(session);entered=threading.Event();release=threading.Event();original=session[4]
    def blocked(url):entered.set();assert release.wait(5);return original(url)
    worker.transport=blocked;thread=threading.Thread(target=worker.step);thread.start()
    try:
        assert entered.wait(5);state=worker.stop()
        assert state['ownership_held'] and state['source_requests']==1
        with pytest.raises(ValueError,match='OWNED'):refresh.RefreshOwnership(hub.root()).acquire()
    finally:release.set();thread.join(10)
    assert len(session[3])==1 and not (hub.root()/'hub.sqlite3').exists()
    assert not worker.status()['ownership_held'] and not worker.status()['enabled']


def test_network_failures_are_charged_keep_earlier_pages_and_do_not_echo_secrets(session):
    worker=arm(session);original=session[4];count=[0]
    def fail(url):
        count[0]+=1
        if count[0]==3:raise OSError('PRIVATE KEY')
        return original(url)
    worker.transport=fail;state=worker.step()
    assert state['source_requests']==3 and state['reason']=='PUBLIC_SOURCE_FAILED'
    assert hub.status()['bars']==300 and 'PRIVATE' not in str(state) and not state['ownership_held']


def test_request_budget_stops_after_charged_success_and_before_next_request(session,monkeypatch):
    monkeypatch.setattr(collection,'MAX_SOURCE_REQUESTS',4)
    worker=arm(session);state=worker.step()
    assert state['source_requests']==4 and state['phase']=='COMPLETE' and not state['ownership_held']
    advance(session,300);worker.step();assert len(session[3])==4


def test_capacity_preflight_blocks_network_without_deleting_data(session,monkeypatch):
    worker=arm(session);monkeypatch.setattr(hub,'MAX_ARTIFACTS',1)
    state=worker.step();assert not session[3] and state['reason']=='COLLECTION_CAPACITY_REACHED'
    assert not (hub.root()/'hub.sqlite3').exists() and not state['ownership_held']


def test_clock_jump_during_network_discards_body_before_ingest(session):
    worker=arm(session);original=session[4]
    def jumped(url):
        body=original(url);session[2][0]+=10;return body
    worker.transport=jumped;state=worker.step()
    assert state['reason']=='CLOCK_FAULT' and not (hub.root()/'hub.sqlite3').exists()


def test_conflicting_collected_revisions_quarantine_and_pause_learning(session):
    worker=arm(session);worker.step();before=hub.snapshot('BTC-USD',300)['records'];worker.stop();original=session[4]
    def revised(url):
        rows=original(url);rows[0][4]=rows[0][2];return rows
    worker.transport=revised;worker._arm('BTC-USD',300,1000,collect_public_history=True)
    state=worker.step()
    assert hub.status()['conflicts']>0 and state['reason']=='DATA_OR_MODEL_REVIEW_REQUIRED'
    assert hub.snapshot('BTC-USD',300)['records']==before
    assert not state['enabled']


def test_default_reader_rejects_response_url_before_body(session,monkeypatch):
    class Reply:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def geturl(self):return 'https://attacker.invalid/'
        def read(self,*_):pytest.fail('Response body read before exact URL validation')
    class Opener:
        def open(self,*_a,**_k):return Reply()
    monkeypatch.setattr(collection,'_opener',Opener())
    with pytest.raises(ValueError,match='source unavailable'):collection.read_window('BTC-USD',300,12000*300)


def test_wall_jump_during_fit_cannot_publish_collected_model(session,monkeypatch):
    from nexus import hub_prediction
    worker=arm(session);original=hub_prediction.train
    def jumped(snapshot):
        result=original(snapshot);session[2][0]+=10;return result
    monkeypatch.setattr(hub_prediction,'train',jumped)
    state=worker.step()
    assert state['reason']=='CLOCK_FAULT' and hub.status()['bars']==600
    with pytest.raises(ValueError,match='NO_TRAINED_MODEL'):hub.latest_model('BTC-USD',300)


def test_api_forwards_only_explicit_public_opt_in_and_defaults_to_saved_data(session,monkeypatch):
    from fastapi.testclient import TestClient
    from nexus import app
    worker=session[0];seen=[]
    def capture(*_a,**kwargs):seen.append(kwargs);return dict(public_history_enabled=kwargs['collect_public_history'])
    monkeypatch.setattr(worker,'start',capture);monkeypatch.setattr(app,'refresh_worker',worker)
    client=TestClient(app.app);body=dict(product='BTC-USD',granularity=300,limit=1000)
    assert not client.post('/api/matrix/hub/refresh/start',json=body).json()['public_history_enabled']
    body['collect_public_history']=True
    assert client.post('/api/matrix/hub/refresh/start',json=body).json()['public_history_enabled']
    assert [s['collect_public_history'] for s in seen]==[False,True]
    assert not session[3]


def test_default_reader_bounds_body_before_json_decoding(session,monkeypatch):
    class Reply:
        status=200
        def __init__(self,url):self.url=url
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def geturl(self):return self.url
        def read(self,n):assert n==131073;return b'x'*n
    class Opener:
        def open(self,request,timeout):assert timeout==5;return Reply(request.full_url)
    monkeypatch.setattr(collection,'_opener',Opener())
    with pytest.raises(ValueError,match='source unavailable'):collection.read_window('BTC-USD',300,12000*300)
