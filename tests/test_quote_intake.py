"""Bounded shared default quote intake, injected HTTP only; no live source IO."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from nexus import public_data as public
from test_public_data import NOW,payload


@pytest.fixture
def intake(monkeypatch):
    wall=[NOW];elapsed=[1000.0];calls=[]
    monkeypatch.setattr(public,'time',SimpleNamespace(time=lambda:wall[0],monotonic=lambda:elapsed[0]))
    monkeypatch.setattr(public,'_cache',{});monkeypatch.setattr(public,'_flights',{})
    monkeypatch.setattr(public,'_stats',public._new_stats())
    def open_request(req,timeout):
        calls.append((req.full_url,req.get_method(),req.data,timeout))
        class Response:
            status=200
            def geturl(self):return req.full_url
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return json.dumps(payload()).encode()
        return Response()
    monkeypatch.setattr(public,'_opener',SimpleNamespace(open=open_request))
    return wall,elapsed,calls,open_request


def until(predicate):
    deadline=time.monotonic()+4
    while not predicate():
        assert time.monotonic()<deadline,'Concurrent reader did not reach expected state'
        threading.Event().wait(.001)


def test_twelve_concurrent_readers_share_one_transport_and_immutable_receipt(intake,monkeypatch):
    wall,elapsed,calls,original=intake;entered=threading.Event();release=threading.Event()
    def slow(*args,**kwargs):entered.set();assert release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(public._opener,'open',slow)
    with ThreadPoolExecutor(12) as pool:
        first=pool.submit(public.read_ticker,'BTC-USD');assert entered.wait(4)
        readers=[pool.submit(public.read_ticker,'BTC-USD') for _ in range(11)]
        try:until(lambda:public.efficiency()['shared_waits']==11)
        finally:release.set()
        quotes=[first.result(5)]+[f.result(5) for f in readers]
    assert len(calls)==1 and all(q==quotes[0] for q in quotes)
    assert all(q['received_at']==NOW for q in quotes)
    quotes[0]['bid']=999;assert quotes[1]['bid']==100
    assert public.read_ticker('BTC-USD')['bid']==100
    stats=public.efficiency()
    assert stats['requests']==13 and stats['transport_attempts']==1
    assert stats['shared_returns']==11 and stats['cache_hits']==1
    assert stats['active_requests']==0 and stats['cached_products']==1


def test_different_products_fetch_independently_under_fixed_three_product_bound(intake,monkeypatch):
    wall,elapsed,calls,original=intake;release=threading.Event();entered={p:threading.Event() for p in public.PRODUCTS}
    def slow(req,timeout):
        p=req.full_url.split('/')[-2];entered[p].set();assert release.wait(5);return original(req,timeout)
    monkeypatch.setattr(public._opener,'open',slow)
    with ThreadPoolExecutor(3) as pool:
        futures={p:pool.submit(public.read_ticker,p) for p in entered}
        try:
            assert all(event.wait(4) for event in entered.values())
            assert public.efficiency()['active_requests']==public.efficiency()['active_request_limit']==3
        finally:release.set()
        assert all(f.result(5)['product']==p for p,f in futures.items())
    assert len(calls)==3 and len(public._cache)==3 and not public._flights
    with pytest.raises(ValueError):public.read_ticker('ARBITRARY-USD')
    assert len(calls)==3 and len(public._cache)==3


@pytest.mark.parametrize('error',[TimeoutError('PRIVATE'),RuntimeError('PRIVATE')],ids=['timeout','unexpected'])
def test_failed_leader_releases_followers_and_allows_explicit_retry(intake,monkeypatch,error):
    wall,elapsed,calls,original=intake;entered=threading.Event();release=threading.Event();attempts=[]
    def broken(*a,**k):attempts.append(1);entered.set();assert release.wait(5);raise error
    monkeypatch.setattr(public._opener,'open',broken)
    with ThreadPoolExecutor(4) as pool:
        leader=pool.submit(public.read_ticker,'BTC-USD');assert entered.wait(4)
        followers=[pool.submit(public.read_ticker,'BTC-USD') for _ in range(3)]
        try:until(lambda:public.efficiency()['shared_waits']==3)
        finally:release.set()
        with pytest.raises(type(error) if isinstance(error,RuntimeError) else ValueError):leader.result(5)
        for f in followers:
            with pytest.raises(ValueError,match='Public market quote unavailable or invalid') as info:f.result(5)
            assert 'PRIVATE' not in str(info.value)
    assert attempts==[1] and not public._flights and not public._cache
    assert public.efficiency()['transport_failures']==1
    monkeypatch.setattr(public._opener,'open',original)
    assert public.read_ticker('BTC-USD')['received_at']==NOW
    assert public.efficiency()['transport_attempts']==2


def test_follower_timeout_does_not_cancel_owner_or_create_replacement_request(intake,monkeypatch):
    wall,elapsed,calls,original=intake;entered=threading.Event();release=threading.Event()
    def slow(*a,**k):entered.set();assert release.wait(5);return original(*a,**k)
    monkeypatch.setattr(public._opener,'open',slow);monkeypatch.setattr(public,'WAIT_SECONDS',.02)
    with ThreadPoolExecutor(1) as pool:
        leader=pool.submit(public.read_ticker,'BTC-USD');assert entered.wait(4)
        try:
            with pytest.raises(ValueError):public.read_ticker('BTC-USD')
            assert public.efficiency()['wait_timeouts']==1 and public.efficiency()['active_requests']==1
        finally:release.set()
        assert leader.result(5)['product']=='BTC-USD'
    assert len(calls)==1 and not public._flights


@pytest.mark.parametrize('wall_step,mono_step',[(3,3),(.5,3),(-.5,.5),(2,.1),(0,-.5)],ids=['ttl','monotonic-ttl','wall-backward','wall-forward-drift','monotonic-backward'])
def test_cache_invalidated_by_age_or_clock_change_and_receipt_never_rewritten(intake,wall_step,mono_step):
    wall,elapsed,calls,original=intake
    first=public.read_ticker('BTC-USD');wall[0]+=wall_step;elapsed[0]+=mono_step
    second=public.read_ticker('BTC-USD')
    assert len(calls)==2 and first['received_at']==NOW
    assert second['received_at']==wall[0] and public.efficiency()['cache_hits']==0


def test_cache_hit_preserves_source_receipt_and_transport_timing(intake):
    wall,elapsed,calls,original=intake
    first=public.read_ticker('BTC-USD');wall[0]+=1;elapsed[0]+=1
    second=public.read_ticker('BTC-USD')
    assert second==first and second['received_at']==NOW and len(calls)==1
    assert public.efficiency()['cache_hits']==1


@pytest.mark.parametrize('wall_step,mono_step',[(2,.1),(-1,.1),(1,-1)],ids=['wall-jump','wall-regression','elapsed-regression'])
def test_clock_change_during_transport_blocks_publication_and_unblocks_waiters(intake,monkeypatch,wall_step,mono_step):
    wall,elapsed,calls,original=intake
    def jump(*a,**k):
        result=original(*a,**k);wall[0]+=wall_step;elapsed[0]+=mono_step;return result
    monkeypatch.setattr(public._opener,'open',jump)
    with pytest.raises(ValueError):public.read_ticker('BTC-USD')
    assert not public._cache and not public._flights
    assert public.efficiency()['transport_failures']==1


def test_stale_shared_result_rejected_at_followers_own_return_time(intake,monkeypatch):
    wall,elapsed,calls,original=intake;entered=threading.Event();release=threading.Event()
    class DelayedCompletion:
        def __init__(self):self.event=threading.Event()
        def set(self):self.event.set()
        def wait(self,timeout):
            result=self.event.wait(timeout)
            wall[0]+=4;elapsed[0]+=4
            return result
    monkeypatch.setattr(public,'_Flight',lambda:SimpleNamespace(done=DelayedCompletion(),entry=None))
    def slow(*a,**k):entered.set();assert release.wait(5);return original(*a,**k)
    monkeypatch.setattr(public._opener,'open',slow)
    with ThreadPoolExecutor(2) as pool:
        leader=pool.submit(public.read_ticker,'BTC-USD');assert entered.wait(4)
        follower=pool.submit(public.read_ticker,'BTC-USD')
        try:until(lambda:public.efficiency()['shared_waits']==1)
        finally:release.set()
        assert leader.result(5)['received_at']==NOW
        with pytest.raises(ValueError):follower.result(5)
    assert len(calls)==1 and public.efficiency()['shared_returns']==0


def test_injected_and_explicit_clocks_do_not_use_or_publish_runtime_cache(intake):
    wall,elapsed,calls,original=intake
    runtime=public.read_ticker('BTC-USD')
    injected=public.read_ticker('BTC-USD',fetch=lambda _:payload(bid='50',ask='51'))
    assert injected['bid']==50 and public.read_ticker('BTC-USD')==runtime
    explicit=public.read_ticker('BTC-USD',now=NOW)
    assert len(calls)==2 and explicit==runtime
    assert public.read_ticker('BTC-USD')==runtime and len(calls)==3
    stats=public.efficiency();assert stats['requests']==3 and stats['transport_attempts']==2 and stats['cache_hits']==1


def test_failed_refresh_never_returns_previous_stale_cache(intake,monkeypatch):
    wall,elapsed,calls,original=intake;public.read_ticker('BTC-USD');wall[0]+=4;elapsed[0]+=4
    def broken(*a,**k):raise TimeoutError('PRIVATE')
    monkeypatch.setattr(public._opener,'open',broken)
    with pytest.raises(ValueError):public.read_ticker('BTC-USD')
    assert public.efficiency()['transport_failures']==1 and public.efficiency()['cache_hits']==0


def test_diagnostics_are_read_only_bounded_copies_no_source_or_storage_io(intake,tmp_path,monkeypatch):
    from nexus.app import app
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'absent'))
    monkeypatch.setattr(public,'_fetch_default',lambda *_:pytest.fail('Diagnostics fetched quotes'))
    report=public.efficiency();report['requests']=999
    assert public.efficiency()['requests']==0 and report['read_only']
    c=TestClient(app);response=c.get('/api/public-data/efficiency')
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.json()['mean_successful_transport_ms'] is None
    assert not (tmp_path/'absent').exists() and not public._cache and not public._flights
    for method in ('post','put','delete'):assert getattr(c,method)('/api/public-data/efficiency').status_code==405


def test_actual_efficiency_controller():
    node=shutil.which('node')
    if not node:pytest.skip('Node required')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/feed_efficiency_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_shared_read_continues_research_but_runner_stop_fences_execution(intake,tmp_path,monkeypatch):
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    wall,elapsed,calls,original=intake
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));configure('terrestrial',True)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    r=PaperForwardRunner(g,public.read_ticker,lambda:wall[0],lambda:elapsed[0]);r.prepare('terrestrial')
    policy=r.status()['policy'];entered=threading.Event();release=threading.Event()
    def slow(*a,**k):entered.set();assert release.wait(5);return original(*a,**k)
    monkeypatch.setattr(public._opener,'open',slow)
    with ThreadPoolExecutor(2) as pool:
        cycle=pool.submit(r.single_cycle);assert entered.wait(4)
        research=pool.submit(public.read_ticker,'BTC-USD')
        try:
            until(lambda:public.efficiency()['shared_waits']==1)
            g.emergency_halt();r.stop()
            assert r.status()['ownership_held'] and not r.status()['enabled']
        finally:release.set()
        assert cycle.result(5)['reason']=='STOPPED_DURING_FETCH'
        assert research.result(5)['received_at']==NOW
    report=g.recovery_report()
    assert report['halted'] and report['portfolio_reconciled'] and not report['orders']
    assert not r.status()['ownership_held'] and r.status()['policy']==policy and len(calls)==1


def test_cache_never_extends_exchange_timestamp_lifetime(intake,monkeypatch):
    wall,elapsed,calls,original=intake
    monkeypatch.setattr(public,'_fetch_default',lambda _:payload(seconds=-29))
    first=public.read_ticker('BTC-USD');assert first['age_seconds']==29
    wall[0]+=2;elapsed[0]+=2
    with pytest.raises(ValueError):public.read_ticker('BTC-USD')
    assert public.efficiency()['cache_hits']==0 and public.efficiency()['transport_attempts']==2


@pytest.mark.parametrize('invalid',[True,float('inf'),float('nan'),10**1000,'bad'],ids=['boolean','infinite','nan','huge-integer','text'])
def test_invalid_fixture_clock_rejected_before_transport(intake,invalid):
    with pytest.raises(ValueError):public.read_ticker('BTC-USD',now=invalid)
    assert not intake[2] and not public._cache and not public._flights
