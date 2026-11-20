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


def test_resumable_exit_slice_expiry_is_vetoed_then_fills_on_fresh_terms(
        tmp_path,monkeypatch):
    from decimal import Decimal
    from nexus import order_checkpoints
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    r,g,c=setup(tmp_path,monkeypatch)
    g.submit('entry','BTC-USD','BUY',100,tier='terrestrial')
    g.simulate_priced_fill('entry','fill',1,1,100)
    configure('terrestrial',False);c.price=250;c.now=1000
    assert r.single_cycle()['decision']=='SELL'
    first=g.recovery_report()
    mandate=next(order for order in first['orders']
                 if order.get('intent_scope')=='POSITION_REDUCING_EXIT')
    assert mandate['status']=='SIMULATED_REDUCTION_PENDING'
    assert mandate['reduction_slice_count']==1
    position=Decimal(first['execution_account']['account']['positions']['BTC-USD']['quantity'])
    r.stop();order_checkpoints.clear_cache()

    cold=DurableOrderGateway(g.path);resumed=PaperForwardRunner(cold,c.quote,lambda:c.now)
    resumed.prepare('terrestrial');c.now+=5;c.price=251
    original=cold._restore;delayed=[False]
    def restore(db):
        state=original(db);gateway=state[0]
        pending=next((order for order in gateway.intents.values()
            if order.get('intent_scope')=='POSITION_REDUCING_EXIT' and
            order['status']=='SIMULATED_REDUCTION_PENDING'),None)
        if (pending is not None and
                Decimal(gateway.order_terms[pending['id']]['created_at'])==Decimal(str(c.now)) and
                pending.get('reduction_slice_count')==1 and not delayed[0]):
            c.now+=2;delayed[0]=True
        return state
    monkeypatch.setattr(cold,'_restore',restore)
    expired=resumed.single_cycle()
    assert expired['decision']=='VETO'
    assert expired['reason']=='ORDER_EXPIRED_WITHOUT_FILL'
    after_expiry=cold.recovery_report()
    mandate=next(order for order in after_expiry['orders']
                 if order.get('intent_scope')=='POSITION_REDUCING_EXIT')
    assert mandate['status']=='SIMULATED_REDUCTION_PENDING'
    assert mandate['reduction_slice_count']==1
    assert Decimal(after_expiry['execution_account']['account']['positions']['BTC-USD']['quantity'])==position
    assert len(cold.order_report(mandate['id'])['quote_events'])==1
    no_fill=cold.order_report(mandate['id'])['no_fill_events']
    assert len(no_fill)==1
    assert no_fill[0]['order_id']==mandate['id']
    assert no_fill[0]['side']=='SELL'
    assert no_fill[0]['reason']=='ORDER_EXPIRED_WITHOUT_FILL'
    assert no_fill[0]['evidence_session']==resumed.status()['session']
    assert no_fill[0]['evidence_cycle']==1
    assert len(no_fill[0]['evidence_quote_sha256'])==64

    c.now+=5;c.price=252
    filled=resumed.single_cycle()
    assert filled['decision']=='SELL'
    final=cold.recovery_report()
    mandate=next(order for order in final['orders']
                 if order.get('intent_scope')=='POSITION_REDUCING_EXIT')
    assert mandate['status']=='SIMULATED_FILLED'
    assert mandate['reduction_slice_count']==2
    assert not final['execution_account']['account']['positions']
    review=resumed.evidence_report(resumed.status()['session'])
    assert review['local_consistency_ok']
    assert review['counters']['VETO']==review['counters']['SELL']==1
    events=review['durable_event_evidence']
    assert events['durable_no_fill_outcomes_reconciled']
    assert events['durable_no_fill_attempts_bound']
    assert events['modeled_no_fill_outcomes']==1

    # Recomputing the complete forward chain cannot disguise a changed
    # complete-attempt binding because the order journal is independent.
    changed=False
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        rows=db.execute('SELECT session,sequence,payload FROM forward_events ORDER BY session,sequence').fetchall()
        previous_by_session={};heads={}
        from nexus import forward_event_evidence
        for session,sequence,payload in rows:
            data=json.loads(payload)
            record=data['record']
            if (not changed and record.get('decision')=='VETO' and
                    record.get('reason')=='ORDER_EXPIRED_WITHOUT_FILL'):
                record['no_fill_attempt_sha256']='0'*64;changed=True
            rewritten=forward_event_evidence.canonical(data)
            previous=previous_by_session.get(session,forward_event_evidence.GENESIS)
            digest=forward_event_evidence.digest(previous,rewritten)
            db.execute('UPDATE forward_events SET payload=?,previous=?,digest=? WHERE session=? AND sequence=?',
                (rewritten,previous,digest,session,sequence))
            previous_by_session[session]=digest;heads[session]=(sequence,digest)
        for session,(count,head) in heads.items():
            db.execute('UPDATE forward_event_heads SET events=?,head=? WHERE session=?',
                (count,head,session))
    assert changed
    rewritten=resumed.evidence_report(resumed.status()['session'])
    assert rewritten['durable_event_evidence']['event_trace_complete']
    assert rewritten['durable_event_evidence']['durable_no_fill_outcomes_reconciled']
    assert not rewritten['durable_event_evidence']['durable_no_fill_attempts_bound']
    assert 'DURABLE_NO_FILL_ATTEMPT_DIGEST_DISAGREEMENT' in rewritten['reasons']
    assert not rewritten['local_consistency_ok']
    resumed.stop()


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
    def connect(path=None):
        db=original(path);connected.set();return db
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
