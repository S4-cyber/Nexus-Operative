from datetime import datetime,timezone
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import pytest
from nexus.paper_forward import PaperForwardRunner
from nexus.durable_orders import DurableOrderGateway
from nexus.tier_permissions import configure
from nexus import forward_evidence


class EqualText(str):
    __hash__=str.__hash__
    def __eq__(self,other):return True
    def __ne__(self,other):return False


class HostileQuote(dict):
    def get(self,*args,**kwargs):
        raise AssertionError('hostile mapping inspected')


@pytest.mark.parametrize('quote_factory',[
    lambda:HostileQuote(product='BTC-USD'),
    lambda:{EqualText('product'):'BTC-USD'},
])
def test_quote_evidence_sanitizer_rejects_polymorphic_mappings(quote_factory):
    assert forward_evidence.sanitize(quote_factory())=={
        'missing_or_invalid_quote':True}


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


def latest_quote(r):
    with r._read_db() as db:
        return forward_evidence.review(db,r._session)['points'][-1]['quote']


@pytest.mark.parametrize(('field','value'),[
    ('bid','100'),('ask',True),('last',float('nan')),
    ('bid',10**1000),('ask',10**1000),('last',10**1000),
    ('bid',1e-300),('ask',1e-300),('last',1e-300),
    ('received_at','1000'),('received_at',10**1000),
    ('provider',EqualText('coinbase_exchange_public')),
    ('product',EqualText('BTC-USD')),
    ('exchange_time',EqualText('1970-01-01T00:16:40+00:00')),
    ('read_only',1),('orders_allowed',0),
])
def test_noncanonical_or_out_of_range_quotes_are_storage_silent_and_orderless(
        tmp_path,monkeypatch,field,value):
    r,g,c=setup(tmp_path,monkeypatch);quote=c.quote('BTC-USD');quote[field]=value
    r.fetcher=lambda product:quote
    assert r.single_cycle()['reason']=='QUOTE_UNAVAILABLE_OR_INVALID'
    assert g.recovery_report()['orders']==[]
    assert latest_quote(r)=={'missing_or_invalid_quote':True}


def test_repeated_tiny_rising_quotes_never_reach_entry_arithmetic(
        tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);outcomes=[]
    for index in range(7):
        c.now=1000+index*5;price=1e-300*(index+1)
        r.fetcher=lambda product,p=price:dict(product=product,
            provider='coinbase_exchange_public',read_only=True,
            orders_allowed=False,bid=p,ask=p,last=p,received_at=c.now,
            exchange_time=datetime.fromtimestamp(
                c.now,timezone.utc).isoformat())
        outcomes.append(r.single_cycle())
    assert len(outcomes)==7 and not g.recovery_report()['orders']
    assert outcomes[0]['reason']=='QUOTE_UNAVAILABLE_OR_INVALID'
    assert latest_quote(r)=={'missing_or_invalid_quote':True}


@pytest.mark.parametrize('quote_factory',[
    lambda quote:HostileQuote(quote),
    lambda quote:{**quote,EqualText('private_key'):'PRIVATE_VALUE'},
])
def test_noncanonical_quote_mappings_fail_without_polymorphic_access(
        tmp_path,monkeypatch,quote_factory):
    r,g,c=setup(tmp_path,monkeypatch);quote=quote_factory(c.quote('BTC-USD'))
    r.fetcher=lambda product:quote
    assert r.single_cycle()['reason']=='QUOTE_UNAVAILABLE_OR_INVALID'
    assert g.recovery_report()['orders']==[]
    assert latest_quote(r)=={'missing_or_invalid_quote':True}


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


def test_duplicate_exchange_tick_is_ineligible_and_does_not_poison_guard(
        tmp_path, monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    configure('terrestrial',False)
    warmup(r,c)
    c.now=1035;c.stamp=1030;c.price=120
    duplicate=r.single_cycle()
    assert duplicate['decision']=='HOLD'
    assert duplicate['reason']=='DUPLICATE_EXCHANGE_TICK'
    c.now=1040;c.stamp=1040;c.price=100.7
    assert r.single_cycle()['reason']!='QUOTE_UNAVAILABLE_OR_INVALID'
    with r._read_db() as db:
        points=forward_evidence.review(db,r._session)['points']
    assert points[-2]['quote_eligible'] is False
    assert points[-2]['error_type']=='DUPLICATE_EXCHANGE_TICK'
    assert points[-1]['quote_eligible'] is True
    assert points[-1]['error_type'] is None
    assert r._stamp==1040 and g.recovery_report()['orders']==[]


def test_cold_restart_retains_exchange_and_price_baselines(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    configure('terrestrial',False)
    assert r.single_cycle()['reason']=='WARMUP_REQUIRES_SIX_DISTINCT_TICKS'
    r.stop()

    c.now=1005;c.stamp=1000;c.price=120
    configure('terrestrial',True)
    resumed=PaperForwardRunner(g,c.quote,lambda:c.now)
    resumed.prepare('terrestrial')
    configure('terrestrial',False)
    assert resumed._stamp==1000
    duplicate=resumed.single_cycle()
    assert duplicate['reason']=='DUPLICATE_EXCHANGE_TICK'

    c.now=1010;c.stamp=1010
    jumped=resumed.single_cycle()
    assert jumped['reason']=='QUOTE_UNAVAILABLE_OR_INVALID'
    with resumed._read_db() as db:
        points=forward_evidence.review(db,resumed._session)['points']
    assert [point['quote_eligible'] for point in points]==[False,False]
    assert points[0]['error_type']=='DUPLICATE_EXCHANGE_TICK'
    assert resumed._stamp==1010 and g.recovery_report()['orders']==[]
    resumed.stop()


def test_price_rejected_tick_consumes_marker_across_restart(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    configure('terrestrial',False)
    assert r.single_cycle()['reason']=='WARMUP_REQUIRES_SIX_DISTINCT_TICKS'
    c.now=1005;c.stamp=1005;c.price=120
    rejected=r.single_cycle()
    assert rejected['reason']=='QUOTE_UNAVAILABLE_OR_INVALID'
    assert r._stamp==1005
    r.stop()

    c.now=1010;c.price=100.1
    configure('terrestrial',True)
    resumed=PaperForwardRunner(g,c.quote,lambda:c.now)
    resumed.prepare('terrestrial')
    configure('terrestrial',False)
    assert resumed._stamp==1005
    duplicate=resumed.single_cycle()
    assert duplicate['reason']=='DUPLICATE_EXCHANGE_TICK'
    with resumed._read_db() as db:
        points=forward_evidence.review(db,resumed._session)['points']
    assert points[0]['quote_eligible'] is False
    assert points[0]['error_type']=='DUPLICATE_EXCHANGE_TICK'
    resumed.stop()


def test_process_death_after_durable_admission_consumes_exchange_tick(
        tmp_path,monkeypatch):
    """The real child exits in FeedGuard after marker fsync, before quote append."""
    script=r'''
import os
from datetime import datetime,timezone
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure
class Clock:
    now=1000
    def quote(self,product):
        return dict(product=product,provider='coinbase_exchange_public',
            read_only=True,orders_allowed=False,bid=100,ask=100.01,last=100,
            received_at=self.now,
            exchange_time=datetime.fromtimestamp(self.now,timezone.utc).isoformat())
clock=Clock();configure('terrestrial',True)
runner=PaperForwardRunner(DurableOrderGateway(os.path.join(
    os.environ['NEXUS_PORTABLE_DATA'],'orders.sqlite3')),clock.quote,
    lambda:clock.now)
runner.prepare('terrestrial')
runner._guard.evaluate=lambda quote,now:os._exit(79)
runner.single_cycle()
raise SystemExit(80)
'''
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path)}
    child=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).parents[1],
        env=env,capture_output=True,text=True,timeout=30)
    assert child.returncode==79,(child.stdout,child.stderr)
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM forward_exchange_admissions').fetchone()==(1,)
        assert db.execute('SELECT COUNT(*) FROM forward_quotes').fetchone()==(0,)

    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True);clock=Clock();clock.price=125
    gateway=DurableOrderGateway(tmp_path/'orders.sqlite3')
    resumed=PaperForwardRunner(gateway,clock.quote,lambda:clock.now)
    resumed.prepare('terrestrial');configure('terrestrial',False)
    assert resumed._stamp==1000
    outcome=resumed.single_cycle()
    assert outcome['reason']=='DUPLICATE_EXCHANGE_TICK'
    assert gateway.recovery_report()['orders']==[]
    with resumed._read_db() as db:
        assert db.execute('SELECT COUNT(*) FROM forward_exchange_admissions').fetchone()==(1,)
        points=forward_evidence.review(db,resumed._session)['points']
    assert points[0]['quote_eligible'] is False
    assert 'exchange_admission_sha256' not in points[0]
    resumed.stop()


def test_normal_and_price_rejected_quotes_bind_durable_admissions(
        tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    assert r.single_cycle()['reason']=='WARMUP_REQUIRES_SIX_DISTINCT_TICKS'
    c.now=1005;c.price=120
    assert r.single_cycle()['reason']=='QUOTE_UNAVAILABLE_OR_INVALID'
    c.now=1010;c.stamp=1005;c.price=100
    assert r.single_cycle()['reason']=='DUPLICATE_EXCHANGE_TICK'
    with r._read_db() as db:
        quotes=forward_evidence.review(db,r._session)
        admissions=forward_evidence.review_exchange_admissions(db,r._session)
        checked=forward_evidence.crosscheck_exchange_admissions(admissions,
            quotes,'RUNNING',r._policy['policy_sha256'])
    assert checked['integrity_ok'] and checked['matched_quotes']==2
    assert checked['admissions']==2 and checked['unmatched_admissions']==0
    assert 'exchange_admission_sha256' not in quotes['points'][-1]
    assert not g.recovery_report()['orders']


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


def test_shared_simulation_cannot_consume_linked_exit_intent_reserve(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from nexus import order_gateway
    import nexus.app as backend
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    r,g,c=setup(tmp_path,monkeypatch)
    assert warmup(r,c)['decision']=='BUY'
    capacity=g.recovery_report()['capacity']
    assert capacity['intents_remaining']==capacity['exit_intents_remaining']==1
    assert capacity['ordinary_intents_remaining']==0
    with pytest.raises(ValueError,match='EXIT_INTENT_RESERVE_PROTECTED'):
        g.submit('shared_simulation','ETH-USD','SELL',1,tier='terrestrial')
    monkeypatch.setattr(backend,'research_order_gateway',g)
    monkeypatch.setattr(backend,'_halted',False)
    response=TestClient(backend.app).post('/api/execution/intent/simulate',json={
        'client_order_id':'public_sell','product':'BTC-USD','side':'SELL',
        'notional':1.0,'provider':'nexus_local_paper','tier':'terrestrial'})
    assert response.status_code==422
    assert response.json()['detail']=='EXIT_INTENT_RESERVE_PROTECTED'
    configure('terrestrial',False);c.price=102;c.now+=5
    assert r.single_cycle()['decision']=='SELL'
    report=g.recovery_report()
    assert not report['execution_account']['account']['positions']
    assert len(report['orders'])==order_gateway.MAX_INTENTS
    r.stop()


def test_reserved_exit_mandate_resumes_bounded_slices_until_flat(tmp_path,monkeypatch):
    from nexus import order_gateway, order_checkpoints
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    clock=Clock();path=tmp_path/'orders.sqlite3';gateway=DurableOrderGateway(path)
    gateway.initialize_account(10000);gateway.link_portfolio()
    gateway.submit('entry','BTC-USD','BUY',100,tier='terrestrial')
    gateway.simulate_priced_fill('entry','fill',1,1,100)
    configure('terrestrial',False)
    clock.price=250;clock.now=1000
    runner=PaperForwardRunner(gateway,clock.quote,lambda:clock.now)
    runner.prepare('terrestrial')
    assert runner.single_cycle()['decision']=='SELL'
    report=gateway.recovery_report();position=report['execution_account']['account']['positions']['BTC-USD']
    assert Decimal(position['quantity'])>0
    assert report['capacity']['intents_remaining']==0
    mandate=next(order for order in report['orders'] if order.get('intent_scope')=='POSITION_REDUCING_EXIT')
    assert mandate['status']=='SIMULATED_REDUCTION_PENDING'
    assert mandate['reduction_slice_count']==1
    runner.stop();order_checkpoints.clear_cache()
    cold_gateway=DurableOrderGateway(path)
    resumed=PaperForwardRunner(cold_gateway,clock.quote,lambda:clock.now)
    resumed.prepare('terrestrial')
    clock.now+=5;clock.price=251
    assert resumed.single_cycle()['decision']=='SELL'
    final=cold_gateway.recovery_report()
    assert not final['execution_account']['account']['positions']
    mandate=next(order for order in final['orders'] if order.get('intent_scope')=='POSITION_REDUCING_EXIT')
    assert mandate['status']=='SIMULATED_FILLED'
    assert mandate['reduction_slice_count']==2
    assert len(final['orders'])==order_gateway.MAX_INTENTS
    first_review=runner.evidence_report(runner.status()['session'])
    second_review=resumed.evidence_report(resumed.status()['session'])
    assert first_review['local_consistency_ok'] and second_review['local_consistency_ok']
    assert first_review['counters']['SELL']==second_review['counters']['SELL']==1
    resumed.stop()


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
