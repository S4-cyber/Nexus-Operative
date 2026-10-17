"""Engine/throttle separation through real paper accounting; no public IO."""
import concurrent.futures
import sqlite3
import subprocess
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from nexus import app as api, hub_refresh
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure


@pytest.fixture
def rig(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr(api,'_paused',True);monkeypatch.setattr(api,'_halted',False)
    configure('terrestrial',True)
    gateway=DurableOrderGateway(tmp_path/'orders.sqlite3')
    runner=PaperForwardRunner(gateway,lambda *_:pytest.fail('Unexpected quote IO'))
    monkeypatch.setattr(runner,'start',runner.prepare)  # Real admission, no background quote thread.
    worker=hub_refresh.ResearchRefresh();starts=[];observer=[]
    def start(product,granularity,limit,**kwargs):
        starts.append((product,granularity,limit,kwargs['collect_public_history']))
        return worker._arm(product,granularity,limit,**kwargs)
    monkeypatch.setattr(worker,'start',start)
    monkeypatch.setattr(api,'research_order_gateway',gateway)
    monkeypatch.setattr(api,'paper_forward_runner',runner)
    monkeypatch.setattr(api,'refresh_worker',worker)
    monkeypatch.setattr(api,'observer',SimpleNamespace(start=lambda:observer.append('on'),stop=lambda:observer.append('off')))
    yield TestClient(api.app),gateway,runner,worker,starts,observer
    runner.stop();worker.shutdown()


START=dict(tier='terrestrial',confirm_experimental_paper=True)

def test_engine_defaults_public_learning_and_idempotent_start_without_trading(rig):
    client,g,r,w,starts,obs=rig
    for _ in range(2):assert client.post('/api/portal',json={'enabled':True}).status_code==200
    assert starts==[('BTC-USD',3600,1000,True)] and obs==['on']
    assert w.status()['enabled'] and w.status()['public_history_enabled']
    assert not r.status()['enabled'] and not g.recovery_report()['execution_account']['initialized']
    assert client.get('/api/status').json()['portal_enabled']


def test_explicit_saved_only_scope_and_validation(rig):
    c,g,r,w,starts,obs=rig
    assert c.post('/api/portal',json=dict(enabled=True,granularity=60)).status_code==422
    assert not starts and api._paused
    assert c.post('/api/portal',json=dict(enabled=True,product='ETH-USD',granularity=300,limit=600,collect_public_history=False)).status_code==200
    assert starts==[('ETH-USD',300,600,False)] and not w.status()['public_history_enabled']


def test_engage_cut_cancel_reset_and_disengage_leave_research_on(rig):
    c,g,r,w,starts,obs=rig
    assert c.post('/api/paper/forward/start',json=START).status_code==409
    c.post('/api/portal',json={'enabled':True})
    assert c.post('/api/paper/forward/start',json=START).status_code==200
    assert r.status()['enabled']
    g.submit('partial','BTC-USD','BUY',100,tier='terrestrial')
    g.simulate_priced_fill('partial','f1',1,1,25)
    cut=c.post('/api/simulate/halt')
    assert cut.status_code==200 and cut.json()['portal_enabled'] and cut.json()['halted']
    assert w.status()['enabled'] and w._admission() and obs==['on']
    report=g.recovery_report();assert report['halted'] and report['portfolio_reconciled']
    order=next(o for o in report['orders'] if o['id']=='partial')
    assert order['status']=='SIMULATED_CANCELLED' and report['execution_account']['account']['positions']
    assert not r.status()['enabled']
    assert c.post('/api/paper/forward/start',json=START).status_code==409
    reset=c.post('/api/simulate/halt/reset',json=dict(confirm_reset=True))
    assert reset.status_code==200 and reset.json()['portal_enabled'] and not reset.json()['halted']
    assert w.status()['enabled'] and not r.status()['enabled'] and len(starts)==1
    assert c.post('/api/paper/forward/start',json=START).status_code==200
    assert c.post('/api/paper/forward/stop').status_code==200
    assert not r.status()['enabled'] and w.status()['enabled'] and not g.recovery_report()['halted']
    assert c.post('/api/portal',json={'enabled':False}).status_code==200
    assert not w.status()['enabled'] and not w._admission() and obs==['on','off']


def test_start_failure_rolls_back_engine_without_trading(rig,monkeypatch):
    c,g,r,w,starts,obs=rig
    monkeypatch.setattr(w,'start',lambda *a,**k:(_ for _ in ()).throw(ValueError('PRIVATE')))
    reply=c.post('/api/portal',json={'enabled':True})
    assert reply.status_code==503 and 'PRIVATE' not in reply.text
    assert api._paused and not r.status()['enabled'] and not w.status()['enabled'] and obs==['on','off']


@pytest.mark.parametrize('action',['cut','engine_off'])
def test_slow_engage_cannot_survive_cut_or_engine_off(rig,monkeypatch,action):
    c,g,r,w,starts,obs=rig;c.post('/api/portal',json={'enabled':True})
    entered=threading.Event();release=threading.Event();original=r.prepare
    def slow(*a):entered.set();assert release.wait(5);return original(*a)
    monkeypatch.setattr(r,'start',slow)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        start=pool.submit(c.post,'/api/paper/forward/start',json=START)
        assert entered.wait(5)
        stop=pool.submit(c.post,'/api/simulate/halt' if action=='cut' else '/api/portal',**({} if action=='cut' else {'json':{'enabled':False}}))
        try:
            # Wait on the actual published latch, not timing luck.
            for _ in range(10000):
                if api._halted if action=='cut' else api._paused:break
                threading.Event().wait(.001)
            assert api._halted if action=='cut' else api._paused
        finally:release.set()
        assert start.result(10).status_code==409 and stop.result(10).status_code==200
    assert not r.status()['enabled']
    assert w.status()['enabled']==(action=='cut')
    assert g.recovery_report()['halted']==(action=='cut')


def test_new_cut_wins_over_inflight_reset(rig,monkeypatch):
    c,g,r,w,starts,obs=rig;c.post('/api/portal',json={'enabled':True});c.post('/api/simulate/halt')
    entered=threading.Event();release=threading.Event();original=g.reset;before=api._cut_epoch
    def slow_reset():entered.set();assert release.wait(5);return original()
    monkeypatch.setattr(g,'reset',slow_reset)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        reset=pool.submit(c.post,'/api/simulate/halt/reset',json={'confirm_reset':True});assert entered.wait(5)
        cut=pool.submit(c.post,'/api/simulate/halt')
        try:
            for _ in range(10000):
                if api._cut_epoch>before:break
                threading.Event().wait(.001)
            assert api._cut_epoch>before
        finally:release.set()
        assert reset.result(10).status_code==409 and cut.result(10).status_code==200
    assert api._halted and g.recovery_report()['halted'] and not r.status()['enabled']
    assert w.status()['enabled'] and obs==['on']


def test_storage_failure_keeps_local_cut_and_research_running(rig,monkeypatch):
    c,g,r,w,starts,obs=rig;c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    def broken():raise sqlite3.OperationalError('PRIVATE')
    monkeypatch.setattr(g,'emergency_halt',broken)
    response=c.post('/api/simulate/halt')
    assert response.status_code==503 and 'PRIVATE' not in response.text
    assert api._halted and not r.status()['enabled'] and w.status()['enabled'] and obs==['on']
    assert c.post('/api/paper/forward/start',json=START).status_code==409


def test_actual_engine_throttle_ui_controller():
    node=shutil.which('node')
    if not node:pytest.skip('Node required')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/engine_controls_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_emergency_cut_fences_inflight_quote_without_stopping_research(rig,monkeypatch):
    c,g,r,w,starts,obs=rig;c.post('/api/portal',json={'enabled':True})
    entered=threading.Event();release=threading.Event()
    def quote(*_):entered.set();assert release.wait(5);return {}
    monkeypatch.setattr(r,'fetcher',quote)
    monkeypatch.setattr(r,'start',PaperForwardRunner.start.__get__(r))
    assert c.post('/api/paper/forward/start',json=START).status_code==200
    assert entered.wait(5)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        cut=pool.submit(c.post,'/api/simulate/halt')
        try:assert r._stop.wait(5)
        finally:release.set()
        assert cut.result(10).status_code==200
    report=g.recovery_report()
    assert report['halted'] and not report['orders'] and report['portfolio_reconciled']
    assert not r.status()['enabled'] and not r.status()['worker_alive']
    assert w.status()['enabled'] and obs==['on']


def test_reset_does_not_turn_off_engine_on_and_does_not_start_engine_off(rig):
    c,g,r,w,starts,obs=rig
    c.post('/api/simulate/halt')
    reset=c.post('/api/simulate/halt/reset',json={'confirm_reset':True})
    assert reset.status_code==200 and not reset.json()['portal_enabled']
    assert not w.status()['enabled'] and not r.status()['enabled'] and not starts and not obs


def test_matrix_observation_can_continue_under_trading_cut(rig,monkeypatch):
    from nexus import matrix_account
    c,g,r,w,starts,obs=rig;c.post('/api/portal',json={'enabled':True});c.post('/api/simulate/halt')
    monkeypatch.setattr(matrix_account,'capture',lambda:dict(captured=True))
    assert c.post('/api/matrix/account/observe').json()==dict(captured=True)
    assert c.get('/api/matrix').json()['active']


@pytest.mark.parametrize('side',['buy','sell'])
def test_cut_fences_manual_quote_even_if_reset_before_response(rig,monkeypatch,side):
    c,g,r,w,starts,obs=rig;c.post('/api/portal',json={'enabled':True})
    entered=threading.Event();release=threading.Event();writes=[]
    def quote(*_):entered.set();assert release.wait(5);return {}
    monkeypatch.setattr(api,'read_ticker',quote)
    monkeypatch.setattr(api,'paper_'+side,lambda *args:writes.append(args))
    body=dict(product='BTC-USD',notional=25,tier='terrestrial') if side=='buy' else dict(product='BTC-USD')
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        trade=pool.submit(c.post,'/api/paper/portfolio/'+side,json=body);assert entered.wait(5)
        try:
            assert c.post('/api/simulate/halt').status_code==200
            assert c.post('/api/simulate/halt/reset',json={'confirm_reset':True}).status_code==200
        finally:release.set()
        assert trade.result(10).status_code==409
    assert not writes and w.status()['enabled']
