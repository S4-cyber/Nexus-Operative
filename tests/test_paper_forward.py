from datetime import datetime,timezone
import threading
import pytest
from nexus.paper_forward import PaperForwardRunner
from nexus.durable_orders import DurableOrderGateway
from nexus.tier_permissions import configure


class Clock:
    now=1000
    price=100
    stamp=None
    def quote(self,product):
        return dict(product=product,provider='coinbase_exchange_public',read_only=True,orders_allowed=False,
                    bid=self.price,ask=self.price+.01,last=self.price,received_at=self.now,
                    exchange_time=datetime.fromtimestamp(self.stamp or self.now,timezone.utc).isoformat())


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    clock=Clock();g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    r=PaperForwardRunner(g,clock.quote,lambda:clock.now)
    r.prepare('terrestrial')
    return r,g,clock


def warmup(r,c):
    for i in range(7):
        c.now=1000+i*5;c.price=100+i*.1
        outcome=r.single_cycle()
    return outcome


def test_quote_driven_buy_hold_exit_and_recovery(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    assert warmup(r,c)['decision']=='BUY'
    assert g.recovery_report()['portfolio_reconciled']
    c.now+=5
    assert r.single_cycle()['decision']=='HOLD'
    configure('terrestrial',False)
    c.price=102;c.now+=5
    assert r.single_cycle()['decision']=='SELL'
    report=g.recovery_report()
    assert not report['execution_account']['account']['positions']
    assert all(o['status'] in ('SIMULATED_CANCELLED','SIMULATED_FILLED') for o in report['orders'])
    assert not PaperForwardRunner(g,c.quote,lambda:c.now).status()['enabled']
    assert len(r.history())>=9


def test_disengaged_entry_and_duplicate_tick(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    configure('terrestrial',False)
    assert warmup(r,c)['reason']=='RISK_TIER_DISENGAGED'
    assert g.recovery_report()['orders']==[]
    assert r.single_cycle()['reason']=='DUPLICATE_EXCHANGE_TICK'


def test_three_feed_failures_pause_without_orders(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    c.stamp=900
    for _ in range(3):out=r.single_cycle()
    assert out['decision']=='PAUSED'
    assert not r.status()['enabled']
    assert not g.recovery_report()['orders']


def test_halt_and_storage_divergence_pause(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    g.emergency_halt()
    assert r.single_cycle()['reason']=='HALT_OR_RECONCILIATION_VETO'
    assert not r.status()['enabled']


def test_stop_during_blocked_fetch_prevents_submit(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    warmup(r,c)
    # Existing position is retained, never liquidated by stop.
    before=g.recovery_report()['commands']
    entered=threading.Event();release=threading.Event()
    def fetch(product):
        entered.set();assert release.wait(5);return c.quote(product)
    r.fetcher=fetch;c.now+=5;c.price=102
    worker=threading.Thread(target=r.single_cycle);worker.start()
    assert entered.wait(5)
    r.stop();release.set();worker.join(5)
    assert not worker.is_alive()
    assert g.recovery_report()['commands']==before


def test_new_session_requires_engaged_tier(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    r=PaperForwardRunner(DurableOrderGateway(tmp_path/'orders.sqlite3'))
    with pytest.raises(ValueError,match='DISENGAGED'):r.prepare('solar')
    assert not r.status()['enabled']


def test_forward_api_start_is_explicit_and_pause_stops_worker(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import nexus.app as backend
    r,g,c=setup(tmp_path,monkeypatch);r.stop()
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    monkeypatch.setattr(backend,'_paused',True)
    monkeypatch.setattr(backend,'_halted',False)
    monkeypatch.setattr(backend.observer,'stop',lambda:None)
    client=TestClient(backend.app)
    data=dict(tier='terrestrial',confirm_experimental_paper=True)
    assert client.post('/api/paper/forward/start',json=data).status_code==409
    monkeypatch.setattr(backend,'_paused',False)
    assert client.post('/api/paper/forward/start',json={**data,'confirm_experimental_paper':False}).status_code==422
    # Test the API control path with deterministic preparation, no external thread/network.
    monkeypatch.setattr(r,'start',r.prepare)
    assert client.post('/api/paper/forward/start',json=data).status_code==200
    assert client.get('/api/paper/forward').json()['enabled']
    assert client.post('/api/portal',json={'enabled':False}).status_code==200
    assert not r.status()['enabled']


@pytest.mark.parametrize('bad', ['wrong_product','no_timezone'])
def test_quote_identity_and_timezone_are_required(tmp_path,monkeypatch,bad):
    r,g,c=setup(tmp_path,monkeypatch)
    def fetch(product):
        q=c.quote(product)
        if bad=='wrong_product':q['product']='ETH-USD'
        else:q['exchange_time']='1970-01-01T00:16:40'
        return q
    r.fetcher=fetch
    assert r.single_cycle()['decision']=='VETO'
    assert not g.recovery_report()['orders']


def test_entry_preserves_an_exit_order_id(tmp_path,monkeypatch):
    from nexus import order_gateway
    r,g,c=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',1)
    assert r.single_cycle()['reason']=='JOURNAL_OR_ID_CAPACITY_VETO'
    assert not g.recovery_report()['orders']
    assert not r.status()['enabled']


def test_failed_start_diagnostics_leave_runner_stopped(tmp_path,monkeypatch):
    import sqlite3
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    r=PaperForwardRunner(DurableOrderGateway(tmp_path/'orders.sqlite3'))
    def failure(*args,**kwargs):raise sqlite3.OperationalError('unavailable')
    monkeypatch.setattr(r,'_record',failure)
    with pytest.raises(sqlite3.Error):r.prepare('terrestrial')
    assert not r.status()['enabled']


def test_emergency_halt_stops_forward_control(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import nexus.app as backend
    r,g,c=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    monkeypatch.setattr(backend,'research_order_gateway',g)
    monkeypatch.setattr(backend,'_paused',False)
    monkeypatch.setattr(backend,'_halted',False)
    monkeypatch.setattr(backend.observer,'stop',lambda:None)
    response=TestClient(backend.app).post('/api/simulate/halt')
    assert response.status_code==200
    assert not r.status()['enabled']
    assert g.recovery_report()['halted']


def test_application_shutdown_stops_forward_runner(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import nexus.app as backend
    r,g,c=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    monkeypatch.setattr(backend.observer,'stop',lambda:None)
    with TestClient(backend.app) as client:
        assert r.status()['enabled']
    assert not r.status()['enabled']


def test_held_position_can_resume_exit_monitoring_with_tier_off(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    assert warmup(r,c)['decision']=='BUY'
    r.stop();configure('terrestrial',False)
    resumed=PaperForwardRunner(g,c.quote,lambda:c.now)
    resumed.prepare('terrestrial')
    c.now+=5;c.price=102
    assert resumed.single_cycle()['decision']=='SELL'
    assert not g.recovery_report()['execution_account']['account']['positions']
    resumed.stop()
