"""Real SQLite contention and process-death boundaries, synthetic funds only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import pytest
from fastapi.testclient import TestClient
from nexus import app as api
from test_engine_throttle import rig,START
from test_paper_forward import Clock,warmup


@pytest.fixture
def control(rig):
    api._drain_emergency_lane()
    yield rig
    api._drain_emergency_lane()


async def until(predicate):
    loop=asyncio.get_running_loop();deadline=loop.time()+5
    while not predicate():
        assert loop.time()<deadline,'Expected storage/cut boundary not reached'
        await asyncio.sleep(.001)


@pytest.mark.parametrize('store',['orders','portfolio'])
def test_real_write_lock_keeps_cut_pending_but_signals_runner_without_waiting(control,monkeypatch,store):
    c,g,r,w,starts,obs=control
    c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    g.submit('partial','BTC-USD','BUY',100,tier='terrestrial');g.simulate_priced_fill('partial','f1',1,1,25)
    before=g.recovery_report();entered=threading.Event();original=g.emergency_halt
    def mark():entered.set();return original()
    monkeypatch.setattr(g,'emergency_halt',mark)
    path=g._path() if store=='orders' else g._path().parent/'paper_portfolio.sqlite3'
    writer=sqlite3.connect(path);writer.execute('BEGIN IMMEDIATE')
    async def exercise():
        cuts=[asyncio.create_task(api.halt()) for _ in range(2)]
        try:
            await until(lambda:entered.is_set() and api._cut_pending==1)
            assert api.status()['emergency_cut_state']=='PENDING' and not any(t.done() for t in cuts)
            assert r.status()['cut_requested'] and not r.status()['enabled'] and not r._stop.is_set()
            assert r.single_cycle()['reason']=='PAPER_FORWARD_STOPPED'  # No storage/feed needed.
            assert c.post('/api/simulate/halt/reset',json={'confirm_reset':True}).status_code==409
            assert w.status()['enabled'] and obs==['on']
        finally:writer.rollback();writer.close()
        results=await asyncio.wait_for(asyncio.gather(*cuts),5)
        assert all(v['emergency_cut_state']=='VERIFIED' for v in results)
    asyncio.run(exercise())
    after=g.recovery_report();assert after['halted'] and after['portfolio_reconciled']
    assert after['execution_account']==before['execution_account']
    assert next(o for o in after['orders'] if o['id']=='partial')['status']=='SIMULATED_CANCELLED'
    assert r.status()['cut_requested'] and not r.status()['enabled']
    assert c.post('/api/simulate/halt/reset',json={'confirm_reset':True}).json()['emergency_cut_state']=='CLEAR'
    assert not r.status()['cut_requested'] and not r.status()['enabled'] and w.status()['enabled']


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_cut_signal_vetoes_fill_after_actual_sqlite_write_wait(control,monkeypatch,side):
    c,g,r,w,starts,obs=control;clock=Clock()
    monkeypatch.setattr(r,'clock',lambda:clock.now);monkeypatch.setattr(r,'monotonic_clock',lambda:clock.now)
    monkeypatch.setattr(r,'fetcher',clock.quote)
    c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    if side=='SELL':warmup(r,clock);clock.now+=5;clock.price=103
    else:
        for i in range(6):clock.now=1000+i*5;clock.price=100+i*.1;r.single_cycle()
        clock.now=1030;clock.price=100.6
    before=g.recovery_report();at_fill=threading.Event();allow=threading.Event();waiting=threading.Event()
    original_fill=g.simulate_quote_fill;original_connect=g._connect
    def hold(*a,**k):at_fill.set();assert allow.wait(5);return original_fill(*a,**k)
    def connect():
        db=original_connect()
        def trace(sql):
            if sql=='BEGIN IMMEDIATE' and threading.current_thread().name.startswith('fixture-fill'):waiting.set()
        db.set_trace_callback(trace);return db
    monkeypatch.setattr(g,'simulate_quote_fill',hold);monkeypatch.setattr(g,'_connect',connect)
    with ThreadPoolExecutor(1,thread_name_prefix='fixture-fill') as pool:
        cycle=pool.submit(r.single_cycle);assert at_fill.wait(5)
        writer=sqlite3.connect(g._path());writer.execute('BEGIN IMMEDIATE');allow.set()
        async def exercise():
            await until(waiting.is_set)
            cut=asyncio.create_task(api.halt())
            try:
                await until(lambda:r.status()['cut_requested'] and api._cut_pending==1)
                assert not cycle.done() and not cut.done() and not r._stop.is_set()
                assert r.status()['ownership_held'] and api.status()['emergency_cut_state']=='PENDING'
            finally:writer.rollback();writer.close()
            return await asyncio.wait_for(cut,6)
        assert asyncio.run(exercise())['emergency_cut_state']=='VERIFIED'
        result=cycle.result(5);assert result['reason']=='RUNNER_STOPPED_BEFORE_FILL'
    after=g.recovery_report();assert after['halted'] and after['portfolio_reconciled']
    assert after['execution_account']==before['execution_account'] and after['quote_model_events']==before['quote_model_events']
    assert after['orders'][-1]['side']==side and after['orders'][-1]['status']=='SIMULATED_CANCELLED'
    assert after['orders'][-1]['simulated_filled_notional']=='0'
    assert not r.status()['ownership_held'] and w.status()['enabled'] and obs==['on']


def test_signal_blocks_prepare_and_cannot_be_cleared_by_stop_or_engine_retry(control):
    c,g,r,w,starts,obs=control
    r.request_cut()
    with pytest.raises(ValueError,match='EMERGENCY_CUT_REQUESTED'):r.prepare('terrestrial')
    assert not g.recovery_report()['execution_account']['initialized']
    r.stop();assert r.status()['cut_requested']
    c.post('/api/portal',json={'enabled':True})
    assert r.status()['cut_requested'] and not r.status()['enabled']
    c.post('/api/simulate/halt')
    assert c.post('/api/simulate/halt/reset',json={'confirm_reset':True}).status_code==200
    assert not r.status()['cut_requested'] and not r.status()['enabled']


SCRIPT=r'''
import asyncio,sys
from nexus import app
from nexus.tier_permissions import configure
configure('terrestrial',True)
g=app.research_order_gateway;r=app.paper_forward_runner
r.prepare('terrestrial');g.submit('pending','BTC-USD','BUY',100,tier='terrestrial')
g.simulate_priced_fill('pending','f1',1,1,25)
if sys.argv[1]=='before_commit':
    original=g.emergency_halt
    def held():
        print('boundary',flush=True);sys.stdin.readline();return original()
    g.emergency_halt=held
else:
    def held(_):
        print('boundary',flush=True);sys.stdin.readline()
    app.audit=held
asyncio.run(app.halt())
'''
COLD=r'''
import json
from nexus import app
initial=app.status()
r=app.paper_forward_runner;g=app.research_order_gateway
try:r.prepare('terrestrial');admission='UNEXPECTED_START'
except ValueError as exc:admission=str(exc)
print(json.dumps(dict(status=initial,runner=r.status(),report=g.recovery_report(),admission=admission)))
'''


@pytest.mark.parametrize('boundary',['before_commit','after_commit_before_ack'])
def test_process_death_without_ack_preserves_truth_and_never_auto_resumes(tmp_path,boundary):
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path)}
    proc=subprocess.Popen([sys.executable,'-c',SCRIPT,boundary],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    ready=queue.Queue()
    reader=threading.Thread(target=lambda:ready.put(proc.stdout.readline()),daemon=True);reader.start()
    try:
        line=ready.get(timeout=20)
        assert line.strip()=='boundary'
        proc.kill();proc.wait(10)
    finally:
        if proc.poll() is None:proc.kill();proc.wait(10)
        proc.stdin.close();reader.join(5);proc.stdout.close();proc.stderr.close()
    cold=subprocess.run([sys.executable,'-c',COLD],env=env,capture_output=True,text=True,timeout=30)
    assert cold.returncode==0,cold.stderr
    data=json.loads(cold.stdout);report=data['report'];committed=boundary=='after_commit_before_ack'
    assert report['halted']==committed and report['portfolio_reconciled']
    assert not data['status']['portal_enabled'] and not data['status']['live_orders_enabled']
    assert data['status']['emergency_cut_state']==('VERIFIED' if committed else 'CLEAR')
    assert not data['runner']['enabled'] and not data['runner']['worker_alive'] and not data['runner']['ownership_held']
    assert report['execution_account']['account']['positions']['BTC-USD']['quantity']=='1'
    order=report['orders'][0];assert order['simulated_filled_notional']=='25'
    assert order['status']==('SIMULATED_CANCELLED' if committed else 'SIMULATED_PARTIALLY_FILLED')
    assert data['admission']==('EMERGENCY_HALT_ACTIVE' if committed else 'REVIEW_OUTSTANDING_PAPER_ORDERS_FIRST')


def test_busy_timeout_is_unconfirmed_and_explicit_retry_cancels_preserving_fills(control,monkeypatch):
    c,g,r,w,starts,obs=control;c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    g.submit('partial','BTC-USD','BUY',100,tier='terrestrial');g.simulate_priced_fill('partial','f1',1,1,25)
    before=g.recovery_report();original=g._connect
    def short_wait():db=original();db.execute('PRAGMA busy_timeout=20');return db
    monkeypatch.setattr(g,'_connect',short_wait)
    writer=sqlite3.connect(g._path());writer.execute('BEGIN IMMEDIATE')
    try:
        reply=c.post('/api/simulate/halt')
        assert reply.status_code==503 and 'locked' not in reply.text
        assert api.status()['emergency_cut_state']=='UNCONFIRMED' and api._halted
        assert r.status()['cut_requested'] and not r.status()['enabled'] and w.status()['enabled']
    finally:writer.rollback();writer.close()
    uncommitted=g.recovery_report();assert not uncommitted['halted']
    assert uncommitted['execution_account']==before['execution_account']
    assert c.post('/api/simulate/halt').json()['emergency_cut_state']=='VERIFIED'
    after=g.recovery_report();assert after['halted'] and after['portfolio_reconciled']
    assert after['execution_account']==before['execution_account']
    assert after['orders'][0]['status']=='SIMULATED_CANCELLED'


def test_control_worker_failure_still_signals_runner_before_any_durable_job(control,monkeypatch):
    c,g,r,w,starts,obs=control;c.post('/api/portal',json={'enabled':True});c.post('/api/paper/forward/start',json=START)
    def unavailable(**_):raise RuntimeError('PRIVATE')
    monkeypatch.setattr(api,'ThreadPoolExecutor',unavailable)
    reply=c.post('/api/simulate/halt')
    assert reply.status_code==503 and 'PRIVATE' not in reply.text
    assert not g.recovery_report()['halted'] and api.status()['emergency_cut_state']=='UNCONFIRMED'
    assert r.status()['cut_requested'] and not r.status()['enabled'] and not r._stop.is_set()
    assert r.single_cycle()['reason']=='PAPER_FORWARD_STOPPED'
    assert r.status()['ownership_held'] and w.status()['enabled']


def test_cut_received_at_start_diagnostic_is_not_erased_by_preparation(control,monkeypatch):
    c,g,r,w,starts,obs=control;record=r._record
    def cut_on_start(decision,*a,**k):
        if decision=='START':r.request_cut()
        return record(decision,*a,**k)
    monkeypatch.setattr(r,'_record',cut_on_start)
    state=r.prepare('terrestrial')
    assert state['cut_requested'] and not state['enabled']
    assert r.single_cycle()['reason']=='PAPER_FORWARD_STOPPED' and not g.recovery_report()['orders']
    r.stop();assert r.status()['cut_requested']


def test_cut_during_recovery_keeps_previous_session_identity_and_history(control,monkeypatch):
    c,g,r,w,starts,obs=control;r.prepare('terrestrial');previous=r.status()['session'];r.stop()
    original=g.recovery_report
    def cut_after_read():report=original();r.request_cut();return report
    monkeypatch.setattr(g,'recovery_report',cut_after_read)
    with pytest.raises(ValueError,match='EMERGENCY_CUT_REQUESTED'):r.prepare('terrestrial')
    assert r.status()['session']==previous and not r.status()['enabled'] and not r.status()['ownership_held']
    assert [s['session'] for s in r.sessions()['sessions']]==[previous]
