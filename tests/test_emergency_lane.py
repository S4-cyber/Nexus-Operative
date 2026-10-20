"""Actual ASGI overload, coalescing and cancelled-caller control-path fixtures."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from types import SimpleNamespace
import anyio.to_thread
import httpx
import pytest
from nexus import app as api
from test_engine_throttle import rig,START


@pytest.fixture
def lane(rig):
    api._drain_emergency_lane()
    yield rig
    api._drain_emergency_lane()


async def until(predicate,timeout=10):
    """Wait for fixture setup without weakening the timed control assertion.

    Aggregate regression load can suspend this event loop before a worker has
    entered its deliberately blocked state.  That scheduler delay is not the
    emergency-lane latency measured below, so keep setup and response budgets
    separate.
    """
    loop=asyncio.get_running_loop();deadline=loop.time()+timeout
    while not predicate():
        assert loop.time()<deadline,'Expected control state not reached'
        await asyncio.sleep(.001)


@pytest.mark.parametrize('saturate_default_executor',[False,True],ids=['feed-pool','both-general-pools'])
def test_emergency_cut_and_status_bypass_saturated_general_workers(lane,monkeypatch,saturate_default_executor):
    c,g,r,w,starts,obs=lane;c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    feed_entered=threading.Event();release=threading.Event();general_entered=threading.Event()
    def slow_quote(*_):feed_entered.set();assert release.wait(30);return {'product':'BTC-USD','read_only':True,'orders_allowed':False}
    monkeypatch.setattr(api,'read_ticker',slow_quote)
    async def exercise():
        limiter=anyio.to_thread.current_default_thread_limiter();old=limiter.total_tokens;limiter.total_tokens=1
        loop=asyncio.get_running_loop();general=None
        if saturate_default_executor:
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1,thread_name_prefix='blocked-general-fixture'))
            def block_general():general_entered.set();assert release.wait(30)
            general=loop.run_in_executor(None,block_general)
            await until(general_entered.is_set,30)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://127.0.0.1') as client:
            feed=asyncio.create_task(client.get('/api/public-quote/BTC-USD'))
            try:
                await until(feed_entered.is_set,30)
                # The old synchronous routes queue behind the blocked feed here.
                response=await asyncio.wait_for(client.post('/api/simulate/halt'),2)
                status=await asyncio.wait_for(client.get('/api/status'),2)
                assert not release.is_set() and not feed.done()
                assert response.status_code==200 and response.json()['halted']
                assert response.json()['portal_enabled'] and not response.json()['emergency_cut_pending']
                assert status.status_code==200 and status.headers['cache-control']=='no-store'
                assert status.json()['halted'] and not r.status()['enabled']
                assert g.recovery_report()['halted'] and w.status()['enabled'] and obs==['on']
            finally:
                release.set();await feed
                if general is not None:await general
                limiter.total_tokens=old
    asyncio.run(exercise())


def test_repeated_cut_requests_share_one_cleanup_job_and_no_reset_window(lane,monkeypatch):
    c,g,r,w,starts,obs=lane;c.post('/api/portal',json={'enabled':True})
    entered=threading.Event();release=threading.Event();calls=[];original=g.emergency_halt;before=api._cut_epoch
    def slow_cut():calls.append(threading.current_thread().name);entered.set();assert release.wait(5);return original()
    monkeypatch.setattr(g,'emergency_halt',slow_cut)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://127.0.0.1') as client:
            cuts=[asyncio.create_task(client.post('/api/simulate/halt')) for _ in range(20)]
            try:
                await until(lambda:entered.is_set() and api._cut_epoch==before+20)
                assert api._cut_pending==1 and len(calls)==1 and calls[0].startswith('nexus-emergency-cut')
                status=await client.get('/api/status');assert status.json()['emergency_cut_pending'] and status.json()['halted']
                reset=await client.post('/api/simulate/halt/reset',json={'confirm_reset':True})
                assert reset.status_code==409 and not any(task.done() for task in cuts)
            finally:release.set()
            results=await asyncio.gather(*cuts)
            assert all(x.status_code==200 and x.json()['halted'] for x in results)
            assert not (await client.get('/api/status')).json()['emergency_cut_pending']
    asyncio.run(exercise())
    assert len(calls)==1 and g.recovery_report()['halted'] and w.status()['enabled'] and obs==['on']


@pytest.mark.parametrize('fail_storage',[False,True],ids=['commit-after-disconnect','failure-after-disconnect'])
def test_cancelled_http_caller_cannot_cancel_cleanup_or_release_pending_early(lane,monkeypatch,fail_storage):
    c,g,r,w,starts,obs=lane;c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    entered=threading.Event();release=threading.Event();original=g.emergency_halt
    def slow_cut():
        entered.set();assert release.wait(5)
        if fail_storage:raise sqlite3.OperationalError('PRIVATE')
        return original()
    monkeypatch.setattr(g,'emergency_halt',slow_cut)
    async def exercise():
        errors=[];loop=asyncio.get_running_loop();loop.set_exception_handler(lambda _loop,ctx:errors.append(ctx))
        request=asyncio.create_task(api.halt())
        try:
            await until(entered.is_set)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):await request
            assert api._halted and api._cut_pending==1
            assert c.post('/api/simulate/halt/reset',json={'confirm_reset':True}).status_code==409
        finally:release.set()
        await until(lambda:api._cut_pending==0)
        await asyncio.sleep(0)
        assert not errors
    asyncio.run(exercise())
    assert api._halted and not r.status()['enabled'] and w.status()['enabled'] and obs==['on']
    assert g.recovery_report()['halted']==(not fail_storage)


def test_worker_creation_failure_latches_locally_and_explicit_retry_recovers(lane,monkeypatch):
    c,g,r,w,starts,obs=lane;c.post('/api/portal',json={'enabled':True})
    original=api.ThreadPoolExecutor
    def unavailable(**_):raise RuntimeError('PRIVATE')
    monkeypatch.setattr(api,'ThreadPoolExecutor',unavailable)
    response=c.post('/api/simulate/halt')
    assert response.status_code==503 and 'PRIVATE' not in response.text
    assert api._halted and api._cut_pending==0 and api._cut_future is None and api._cut_executor is None
    assert w.status()['enabled'] and obs==['on']
    monkeypatch.setattr(api,'ThreadPoolExecutor',original)
    assert c.post('/api/simulate/halt').status_code==200 and g.recovery_report()['halted']


def test_submit_failure_discards_abandoned_queue_and_keeps_cut_latched(lane,monkeypatch):
    c,g,r,w,starts,obs=lane;shutdown=[]
    class Broken:
        def submit(self,*_):raise RuntimeError('PRIVATE')
        def shutdown(self,**kwargs):shutdown.append(kwargs)
    monkeypatch.setattr(api,'_cut_executor',Broken())
    response=c.post('/api/simulate/halt')
    assert response.status_code==503 and 'PRIVATE' not in response.text
    assert shutdown==[dict(wait=False,cancel_futures=True)]
    assert api._halted and api._cut_pending==0 and api._cut_executor is None


def test_shutdown_drains_unacknowledged_cut_before_other_cleanup_and_can_reopen(lane,monkeypatch):
    c,g,r,w,starts,obs=lane;entered=threading.Event();release=threading.Event();events=[]
    original=g.emergency_halt
    def slow_cut():entered.set();assert release.wait(5);original();events.append('cut_committed')
    monkeypatch.setattr(g,'emergency_halt',slow_cut)
    monkeypatch.setattr(api,'paper_forward_runner',SimpleNamespace(stop=lambda reason:events.append(reason),request_cut=lambda:None))
    async def exercise():
        context=api.application_lifespan(None);await context.__aenter__()
        request=asyncio.create_task(api.halt());await until(entered.is_set)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):await request
        closing=asyncio.create_task(context.__aexit__(None,None,None))
        try:
            await until(lambda:api._cut_lane_closing)
            assert not closing.done() and 'APPLICATION_SHUTDOWN' not in events
            # Closing refuses new queue admission, but still publishes its local cut.
            with pytest.raises(api.HTTPException) as info:await api.halt()
            assert info.value.status_code==503
        finally:release.set()
        await asyncio.wait_for(closing,5)
    asyncio.run(exercise())
    assert events.index('cut_committed')<events.index('APPLICATION_SHUTDOWN')
    assert api._cut_executor is None and api._cut_future is None and api._cut_pending==0
    assert not api._cut_lane_closing and g.recovery_report()['halted']
    assert c.post('/api/simulate/halt').status_code==200


def test_failed_cleanup_has_no_success_ack_and_remains_retryable(lane,monkeypatch):
    c,g,r,w,starts,obs=lane;c.post('/api/portal',json={'enabled':True})
    original_stop=r.stop
    monkeypatch.setattr(r,'stop',lambda *_:(_ for _ in ()).throw(ValueError('PRIVATE')))
    response=c.post('/api/simulate/halt')
    assert response.status_code==503 and 'PRIVATE' not in response.text
    assert api._halted and api._cut_pending==0 and g.recovery_report()['halted'] and w.status()['enabled']
    # Restore for fixture cleanup.
    monkeypatch.setattr(r,'stop',original_stop)


def test_audit_failure_reports_unconfirmed_and_retry_can_verify(lane,monkeypatch):
    c,g,r,w,starts,obs=lane;c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    original=api.audit
    def broken(*_):raise sqlite3.OperationalError('PRIVATE')
    monkeypatch.setattr(api,'audit',broken)
    result=c.post('/api/simulate/halt')
    assert result.status_code==503 and 'PRIVATE' not in result.text
    state=c.get('/api/status').json()
    assert state['halted'] and not state['emergency_cut_pending'] and state['emergency_cut_state']=='UNCONFIRMED'
    assert g.recovery_report()['halted'] and not r.status()['enabled'] and w.status()['enabled']
    monkeypatch.setattr(api,'audit',original)
    assert c.post('/api/simulate/halt').json()['emergency_cut_state']=='VERIFIED'


def test_actual_thread_start_failure_discards_abandoned_work_before_retry(lane,monkeypatch):
    c,g,r,w,starts,obs=lane;created=[];calls=[];factory=api.ThreadPoolExecutor;start=threading.Thread.start;halt=g.emergency_halt
    def tracked(**kwargs):executor=factory(**kwargs);created.append(executor);return executor
    def unavailable(thread):
        if thread.name.startswith('nexus-emergency-cut'):raise RuntimeError('PRIVATE')
        return start(thread)
    def counted():calls.append(1);return halt()
    monkeypatch.setattr(api,'ThreadPoolExecutor',tracked);monkeypatch.setattr(threading.Thread,'start',unavailable)
    monkeypatch.setattr(g,'emergency_halt',counted)
    result=c.post('/api/simulate/halt')
    assert result.status_code==503 and 'PRIVATE' not in result.text
    assert api._cut_pending==0 and api._cut_executor is None and api._cut_future is None and not calls
    assert created[0]._shutdown and not created[0]._threads
    monkeypatch.setattr(threading.Thread,'start',start)
    assert c.post('/api/simulate/halt').json()['emergency_cut_state']=='VERIFIED'
    assert calls==[1] and len(created)==2


def test_status_reads_never_allocate_control_worker_or_touch_storage(lane,tmp_path,monkeypatch):
    c,g,r,w,starts,obs=lane;root=tmp_path/'untouched';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    monkeypatch.setattr(g,'recovery_report',lambda:pytest.fail('Status read journal'))
    monkeypatch.setattr(api,'ThreadPoolExecutor',lambda **_:pytest.fail('Status started worker'))
    state=c.get('/api/status')
    assert state.status_code==200 and state.headers['cache-control']=='no-store'
    assert state.json()['emergency_cut_state']=='CLEAR' and not state.json()['emergency_cut_pending']
    assert api._cut_executor is None and not root.exists()


def test_reset_queued_before_new_cut_cannot_clear_it_after_worker_queue_unblocks(lane):
    c,g,r,w,starts,obs=lane;c.post('/api/simulate/halt')
    entered=threading.Event();release=threading.Event()
    def block():entered.set();assert release.wait(5)
    async def exercise():
        loop=asyncio.get_running_loop();loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        busy=loop.run_in_executor(None,block);await until(entered.is_set)
        reset=asyncio.create_task(api.reset_emergency_halt(api.HaltResetConfirmation(confirm_reset=True)))
        try:
            await asyncio.sleep(0)  # Let reset capture its epoch and queue its blocking work.
            assert not reset.done()
            result=await asyncio.wait_for(api.halt(),2)
            assert result['emergency_cut_state']=='VERIFIED' and result['halted']
        finally:release.set()
        await busy
        with pytest.raises(api.HTTPException) as info:await reset
        assert info.value.status_code==409
    asyncio.run(exercise())
    assert api._halted and g.recovery_report()['halted']
