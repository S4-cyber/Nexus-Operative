import sqlite3
import pytest
from test_paper_forward import setup
from nexus.paper_forward import PaperForwardRunner
from nexus.forward_review import build


def drive_entry(r,c):
    for i in range(7):
        c.now=1000+i*5;c.price=100+i*.1;result=r.single_cycle()
    return result


@pytest.mark.parametrize('error',[ValueError,sqlite3.OperationalError,OSError])
@pytest.mark.parametrize('filled',[False,True])
def test_cleanup_failure_pauses_and_preserves_committed_state(tmp_path,monkeypatch,error,filled):
    r,g,c=setup(tmp_path,monkeypatch);cancel=g.cancel
    def fail(*args,**kwargs):raise error('injected cleanup failure')
    monkeypatch.setattr(g,'cancel',fail)
    if not filled:
        monkeypatch.setattr(g,'simulate_quote_fill',lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('STALE_OR_FUTURE_SYNTHETIC_QUOTE')))
    result=drive_entry(r,c)
    assert result['decision']=='PAUSED' and result['reason']=='ORDER_CLEANUP_FAILED_REVIEW_REQUIRED'
    assert result['error_type']==error.__name__ and not result['cleanup_acknowledged']
    assert not r.status()['enabled'] and not r.status()['ownership_held'] and not r._prices
    before=g.recovery_report();order=before['orders'][0]
    assert before['portfolio_reconciled']
    assert bool(before['execution_account']['account']['positions'])==filled
    assert order['status']==('SIMULATED_PARTIALLY_FILLED' if filled else 'SIMULATED_ACK_ONLY')
    assert result['order_id']==order['id']
    assert r.single_cycle()['reason']=='PAPER_FORWARD_STOPPED'
    assert g.recovery_report()['commands']==before['commands']
    sid=r.status()['session'];report=r.evidence_report(sid)
    assert report['stored_state']=='PAUSED'
    assert report['execution_crosscheck']['modeled_buy_orders']==int(filled)
    assert 'ORDER_CLEANUP_REQUIRES_REVIEW' in build(report)['payload']['review_blockers']
    restarted=PaperForwardRunner(g,c.quote,lambda:c.now)
    with pytest.raises(ValueError,match='REVIEW_OUTSTANDING_PAPER_ORDERS_FIRST'):restarted.prepare('terrestrial')
    assert not restarted.status()['ownership_held']
    # Explicit recovery never erases the original unresolved-cleanup evidence.
    monkeypatch.setattr(g,'cancel',cancel);cancel(order['id'])
    assert 'ORDER_CLEANUP_REQUIRES_REVIEW' in r.evidence_report(sid)['reasons']
    assert g.recovery_report()['execution_account']==before['execution_account']
    if filled:
        from nexus.tier_permissions import configure
        configure('terrestrial',False)
        restarted.prepare('terrestrial');c.now+=5;c.price=102
        assert restarted.single_cycle()['decision']=='SELL'
        restarted.stop()
        final=g.recovery_report()
        assert final['portfolio_reconciled'] and not final['execution_account']['account']['positions']
        assert 'ORDER_CLEANUP_REQUIRES_REVIEW' in r.evidence_report(sid)['reasons']


def test_real_cancel_transaction_failure_rolls_back_and_pauses(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    with sqlite3.connect(g.path) as db:
        db.execute("CREATE TRIGGER fail_cancel BEFORE INSERT ON commands WHEN json_extract(NEW.payload,'$.method')='cancel' BEGIN SELECT RAISE(ABORT,'cancel blocked'); END")
    result=drive_entry(r,c)
    assert result['reason']=='ORDER_CLEANUP_FAILED_REVIEW_REQUIRED'
    assert result['error_type']=='IntegrityError'
    report=g.recovery_report()
    assert report['portfolio_reconciled'] and report['execution_account']['account']['positions']
    assert report['orders'][0]['status']=='SIMULATED_PARTIALLY_FILLED'
    with sqlite3.connect(g.path) as db:db.execute('DROP TRIGGER fail_cancel')
    g.cancel(report['orders'][0]['id'])
    assert g.recovery_report()['orders'][0]['status']=='SIMULATED_CANCELLED'
    assert g.recovery_report()['execution_account']==report['execution_account']


def test_lost_cleanup_ack_keeps_actual_terminal_order_and_idempotent_retry(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);cancel=g.cancel
    def lost_ack(oid):cancel(oid);raise OSError('ack lost after committed cancellation')
    monkeypatch.setattr(g,'cancel',lost_ack)
    assert drive_entry(r,c)['reason']=='ORDER_CLEANUP_FAILED_REVIEW_REQUIRED'
    before=g.recovery_report();order=before['orders'][0]
    assert order['status']=='SIMULATED_CANCELLED'
    cancel(order['id'])
    assert g.recovery_report()['commands']==before['commands']
    assert g.recovery_report()['execution_account']==before['execution_account']
    assert not r.status()['enabled']


def test_failed_pause_record_still_stops_and_requires_pending_review(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);record=r._record
    def fail(*args,**kwargs):raise ValueError('cleanup failed')
    def diagnostic(decision,*args,**kwargs):
        if decision=='PAUSED':raise sqlite3.OperationalError('diagnostics unavailable')
        return record(decision,*args,**kwargs)
    monkeypatch.setattr(g,'cancel',fail);monkeypatch.setattr(r,'_record',diagnostic)
    result=drive_entry(r,c)
    assert result['reason']=='FORWARD_STORAGE_OR_INTEGRITY_FAILURE'
    assert not r.status()['enabled'] and not r.status()['ownership_held']
    restarted=PaperForwardRunner(g,c.quote,lambda:c.now)
    assert restarted.sessions()['sessions'][0]['requires_review']
    with pytest.raises(ValueError,match='REVIEW_OUTSTANDING_PAPER_ORDERS_FIRST'):restarted.prepare('terrestrial')
    assert g.recovery_report()['portfolio_reconciled']
