import json
import sqlite3
import pytest
from test_atomic_quote_order import setup as atomic_setup,execute
from test_paper_forward import setup


def test_runtime_evaluation_is_sampled_after_restore_and_journaled(tmp_path,monkeypatch):
    g=atomic_setup(tmp_path,monkeypatch);now=[1001];original=g._restore
    def restore(db):
        state=original(db);now[0]=1001.5;return state
    monkeypatch.setattr(g,'_restore',restore)
    result=execute(g,execution_clock=lambda:now[0])
    assert not result['fill'].get('expired')
    with sqlite3.connect(g.path) as db:
        commands=[json.loads(row[0]) for row in db.execute('SELECT payload FROM commands')]
    fill=next(row for row in commands if row['method']=='simulate_quote_fill')
    assert fill['args'][8]==1001.5
    assert g.order_report('atomic')['quote_events'][0]['assumptions']['evaluated_at']=='1001.5'


def test_atomic_runtime_staleness_rolls_back_new_order(tmp_path,monkeypatch):
    g=atomic_setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='STALE_OR_FUTURE_SYNTHETIC_QUOTE'):
        execute(g,execution_clock=lambda:1005)
    report=g.recovery_report()
    assert report['commands']==2 and not report['orders']
    assert report['portfolio_reconciled'] and not report['execution_account']['account']['positions']


def test_forward_restore_delay_vetoes_stale_fill_and_cancels_pending_order(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);original=g._restore;delayed=[False]
    def restore(db):
        state=original(db)
        if state[0].order_terms and not state[0].quote_events and not delayed[0]:
            c.now+=3;delayed[0]=True
        return state
    monkeypatch.setattr(g,'_restore',restore)
    for i in range(7):
        c.now=1000+i*5;c.price=100+i*.1;result=r.single_cycle()
    assert result['reason']=='STALE_OR_FUTURE_SYNTHETIC_QUOTE'
    report=g.recovery_report()
    assert report['orders'][0]['status']=='SIMULATED_CANCELLED'
    assert report['orders'][0]['simulated_filled_notional']=='0'
    assert report['portfolio_reconciled'] and not r._prices
    r.stop()


def test_stop_seen_after_restore_prevents_fill(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);original=g._restore;stopped=[False]
    def restore(db):
        state=original(db)
        if state[0].order_terms and not state[0].quote_events and not stopped[0]:
            stopped[0]=True;r.stop()
        return state
    monkeypatch.setattr(g,'_restore',restore)
    for i in range(7):
        c.now=1000+i*5;c.price=100+i*.1;result=r.single_cycle()
    assert result['reason']=='RUNNER_STOPPED_BEFORE_FILL'
    report=g.recovery_report()
    assert not r.status()['enabled'] and not r.status()['ownership_held']
    assert not report['execution_account']['account']['positions']
    assert report['orders'][0]['status']=='SIMULATED_CANCELLED'


@pytest.mark.parametrize('value',[float('nan'),float('inf'),None,True,'1001'])
def test_invalid_runtime_sample_cannot_commit_atomic_order(tmp_path,monkeypatch,value):
    g=atomic_setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='INVALID_EXECUTION_CLOCK_SAMPLE'):execute(g,execution_clock=lambda:value)
    assert g.recovery_report()['commands']==2
    assert not g.recovery_report()['execution_account']['account']['positions']


def test_real_sqlite_writer_wait_refreshes_runtime_evaluation(tmp_path,monkeypatch):
    import threading
    g=atomic_setup(tmp_path,monkeypatch);now=[1001];connected=threading.Event();sampled=threading.Event()
    original=g._connect;errors=[]
    def connect():
        db=original();connected.set();return db
    def clock():sampled.set();return now[0]
    def worker():
        try:execute(g,execution_clock=clock)
        except Exception as exc:errors.append(exc)
    monkeypatch.setattr(g,'_connect',connect)
    blocker=sqlite3.connect(g.path);blocker.execute('BEGIN IMMEDIATE')
    thread=threading.Thread(target=worker);thread.start()
    try:
        assert connected.wait(timeout=5)
        assert not sampled.is_set()
        now[0]=1005
    finally:
        blocker.rollback();blocker.close();thread.join(timeout=10)
    assert not thread.is_alive() and sampled.is_set()
    assert len(errors)==1 and 'STALE_OR_FUTURE_SYNTHETIC_QUOTE' in str(errors[0])
    report=g.recovery_report()
    assert report['commands']==2 and not report['orders'] and report['portfolio_reconciled']
