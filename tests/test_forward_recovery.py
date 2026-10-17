import os
import sqlite3
import threading
import pytest
from fastapi.testclient import TestClient
from nexus.paper_forward import PaperForwardRunner
from nexus.durable_orders import DurableOrderGateway
from test_paper_forward import setup,warmup
from test_forward_cleanup import drive_entry


def test_empty_snapshot_does_not_initialize_account_or_start(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    g=DurableOrderGateway(tmp_path/'orders.sqlite3');r=PaperForwardRunner(g)
    before=dict(os.environ);result=r.recovery_status()
    assert dict(os.environ)==before
    assert not result['pending_orders'] and not result['positions'] and not result['session_reviews']
    assert not result['provisional'] and not result['automatic_start'] and not result['automatic_cancel']
    assert not result['automatic_repair'] and not result['external_owner_checked']
    report=g.recovery_report()
    assert report['commands']==0 and not report['execution_account']['initialized']
    assert not r.status()['enabled'] and not r.status()['ownership_held']


def test_stopped_holdings_visible_without_restarting_or_rewriting(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);assert warmup(r,c)['decision']=='BUY'
    r.stop();before=g.recovery_report();result=r.recovery_status()
    assert result['positions']==before['execution_account']['account']['positions']
    assert not result['pending_orders'] and not result['provisional']
    assert 'REVIEW_HOLDINGS_AND_EXPLICITLY_START_EXIT_MONITORING_IF_APPROPRIATE' in result['suggested_actions']
    result['positions'].clear()
    assert g.recovery_report()['commands']==before['commands']
    assert g.recovery_report()['execution_account']==before['execution_account']
    assert not r.status()['enabled']


def test_cleanup_marker_visible_and_survives_explicit_cancel_via_existing_api(tmp_path,monkeypatch):
    import nexus.app as backend
    r,g,c=setup(tmp_path,monkeypatch);cancel=g.cancel
    def fail(*args):raise ValueError('cleanup failed')
    monkeypatch.setattr(g,'cancel',fail)
    assert drive_entry(r,c)['decision']=='PAUSED'
    history=r.sessions()['sessions'][0]
    assert history['requires_review'] and 'ORDER_CLEANUP_REQUIRES_REVIEW' in history['review_reasons']
    monkeypatch.setattr(backend,'paper_forward_runner',r);monkeypatch.setattr(backend,'research_order_gateway',g)
    client=TestClient(backend.app);before=g.recovery_report()
    result=client.get('/api/paper/forward/recovery').json()
    assert not result['provisional'] and result['portfolio_reconciled']
    assert len(result['pending_orders'])==len(result['session_reviews'])==1
    oid=result['pending_orders'][0]['id']
    assert client.get('/api/execution/intent/'+oid).json()['order']['id']==oid
    assert g.recovery_report()['commands']==before['commands']
    monkeypatch.setattr(g,'cancel',cancel)
    assert client.post('/api/execution/intent/'+oid+'/cancel').status_code==200
    after=client.get('/api/paper/forward/recovery').json()
    assert not after['pending_orders'] and after['positions']==result['positions']
    assert after['session_reviews']==result['session_reviews']
    assert g.recovery_report()['execution_account']==before['execution_account']
    assert not r.status()['enabled']


def test_stop_during_fetch_keeps_snapshot_provisional_until_cycle_finishes(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);entered=threading.Event();release=threading.Event()
    def fetch(product):entered.set();assert release.wait(5);return c.quote(product)
    r.fetcher=fetch
    worker=threading.Thread(target=r.single_cycle);worker.start()
    try:
        assert entered.wait(5);r.stop()
        result=r.recovery_status()
        assert not result['runner_enabled'] and result['ownership_held'] and result['provisional']
        assert 'STOP_AND_WAIT_FOR_ACTIVE_CYCLE_BEFORE_RECOVERY' in result['suggested_actions']
    finally:release.set();worker.join(5)
    assert not worker.is_alive()
    assert not r.recovery_status()['provisional']


@pytest.mark.parametrize('limit',[0,101])
def test_api_invalid_history_limit_is_422(tmp_path,monkeypatch,limit):
    import nexus.app as backend
    r,g,c=setup(tmp_path,monkeypatch);r.stop();monkeypatch.setattr(backend,'paper_forward_runner',r)
    assert TestClient(backend.app).get('/api/paper/forward/recovery',params={'limit':limit}).status_code==422


def test_corrupt_session_is_unavailable_without_repair(tmp_path,monkeypatch):
    import nexus.app as backend
    r,g,c=setup(tmp_path,monkeypatch);r.stop()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:db.execute("UPDATE sessions SET counts_json='[]'")
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    response=TestClient(backend.app).get('/api/paper/forward/recovery')
    assert response.status_code==503 and response.json()['detail']=='FORWARD_RECOVERY_UNAVAILABLE'
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:assert db.execute('SELECT counts_json FROM sessions').fetchone()[0]=='[]'


def test_recovery_has_no_write_operation(tmp_path,monkeypatch):
    import nexus.app as backend
    r,g,c=setup(tmp_path,monkeypatch);r.stop();monkeypatch.setattr(backend,'paper_forward_runner',r)
    assert TestClient(backend.app).post('/api/paper/forward/recovery',json={}).status_code==405
