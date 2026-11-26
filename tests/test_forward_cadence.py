from datetime import datetime,timezone

import pytest

from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure


def _runner(tmp_path,monkeypatch,*,limit=3,cycle_advance=0,mode='nominal'):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    scheduler={'now':100.0,'waits':[]};starts=[];holder={}

    def scheduler_clock():
        if mode=='invalid':return float('nan')
        return scheduler['now']

    def scheduler_wait(seconds):
        scheduler['waits'].append(seconds)
        if mode=='wait_failure':raise RuntimeError('private detail must not be retained')
        if mode=='regression':scheduler['now']-=1
        elif mode!='stalled':scheduler['now']+=seconds
        if mode=='nominal' and len(scheduler['waits'])==limit:
            holder['runner']._pause_scheduler('DISPOSABLE_CADENCE_COMPLETE')

    def fetch(product):
        starts.append(scheduler['now'])
        if cycle_advance and len(starts)==1:scheduler['now']+=cycle_advance
        return dict(product=product,provider='coinbase_exchange_public',read_only=True,
            orders_allowed=False,bid=100.0,ask=100.01,last=100.0,received_at=1000.0,
            exchange_time=datetime.fromtimestamp(1000,timezone.utc).isoformat())

    gateway=DurableOrderGateway(tmp_path/'orders.sqlite3')
    runner=PaperForwardRunner(gateway,fetch,lambda:1000.0,
        scheduler_clock=scheduler_clock,scheduler_wait=scheduler_wait)
    holder['runner']=runner
    runner.start('terrestrial');runner._thread.join(10)
    assert not runner._thread.is_alive()
    return runner,gateway,scheduler,starts


def _reasons(runner):
    return [row['reason'] for row in reversed(runner.history())]


def test_worker_cycle_starts_hold_fixed_cadence_not_duration_plus_interval(
        tmp_path,monkeypatch):
    runner,gateway,scheduler,starts=_runner(tmp_path,monkeypatch)
    assert starts==[100.0,105.0,110.0]
    assert scheduler['waits']==[5.0,5.0,5.0]
    assert _reasons(runner)[-1]=='DISPOSABLE_CADENCE_COMPLETE'
    assert gateway.recovery_report()['orders']==[]
    assert not runner.status()['enabled'] and not runner.status()['ownership_held']


def test_worker_overrun_is_durable_and_reschedules_without_catchup(
        tmp_path,monkeypatch):
    runner,gateway,scheduler,starts=_runner(
        tmp_path,monkeypatch,limit=2,cycle_advance=7)
    assert starts==[100.0,112.0]
    assert scheduler['waits']==[5.0,5.0]
    overrun=[row for row in runner.history() if row['reason']=='SCHEDULER_OVERRUN']
    assert len(overrun)==1
    assert overrun[0]['scheduler_interval_seconds']==5
    assert overrun[0]['scheduler_cycle_duration_seconds']==7.0
    assert overrun[0]['scheduler_overrun_seconds']==2.0
    assert overrun[0]['scheduler_clock_valid'] is True
    assert overrun[0]['burst_catchup_allowed'] is False
    assert gateway.recovery_report()['orders']==[]


@pytest.mark.parametrize(('mode','reason'),[
    ('invalid','INVALID_SCHEDULER_CLOCK'),
    ('regression','SCHEDULER_CLOCK_REGRESSION'),
    ('stalled','SCHEDULER_CLOCK_STALLED'),
    ('wait_failure','SCHEDULER_WAIT_FAILURE'),
])
def test_scheduler_failure_pauses_durably_without_retry_or_order(
        tmp_path,monkeypatch,mode,reason):
    runner,gateway,_scheduler,starts=_runner(tmp_path,monkeypatch,mode=mode)
    assert starts==([] if mode=='invalid' else [100.0])
    assert _reasons(runner)[-1]==reason
    event=runner.history()[0]
    assert event['decision']=='PAUSED'
    assert event['scheduler_clock_valid'] is False
    assert event['burst_catchup_allowed'] is False
    assert 'private detail' not in str(event)
    assert gateway.recovery_report()['orders']==[]
    status=runner.status()
    assert not status['enabled'] and not status['worker_alive']
    assert not status['ownership_held'] and status['last_error']==reason


@pytest.mark.parametrize('value',[True,-1,float('inf'),float('-inf')])
def test_noncanonical_scheduler_sample_is_rejected_before_fetch(
        tmp_path,monkeypatch,value):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));configure('terrestrial',True)
    fetches=[]
    runner=PaperForwardRunner(DurableOrderGateway(tmp_path/'orders.sqlite3'),
        lambda product:fetches.append(product),lambda:1000.0,
        scheduler_clock=lambda:value,scheduler_wait=lambda _seconds:None)
    runner.start('terrestrial');runner._thread.join(10)
    assert fetches==[] and _reasons(runner)[-1]=='INVALID_SCHEDULER_CLOCK'
    assert not runner.status()['ownership_held']


def test_worker_cycle_concurrency_pauses_without_racing_cadence_evidence(
        tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));configure('terrestrial',True)
    fetches=[];gateway=DurableOrderGateway(tmp_path/'orders.sqlite3')
    runner=PaperForwardRunner(gateway,lambda product:fetches.append(product),
        lambda:1000.0,scheduler_clock=lambda:100.0,
        scheduler_wait=lambda _seconds:None)
    runner.prepare('terrestrial')
    assert runner._cycle_lock.acquire(blocking=False)
    try:
        worker=__import__('threading').Thread(target=runner._run)
        worker.start();worker.join(10)
        assert not worker.is_alive()
    finally:runner._cycle_lock.release()
    assert fetches==[] and _reasons(runner)[-1]=='WORKER_CYCLE_CONCURRENCY'
    assert not runner.status()['ownership_held']
    assert gateway.recovery_report()['orders']==[]
