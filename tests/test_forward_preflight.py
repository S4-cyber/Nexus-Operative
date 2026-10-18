"""Local start diagnostics never create accounting or reserve permission."""
import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
from nexus import forward_preflight as pre, state
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure


@pytest.fixture
def local(tmp_path,monkeypatch):
    root=tmp_path/'data';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    runner=PaperForwardRunner(gateway,lambda *_:pytest.fail('Preflight fetched a quote'))
    return root,gateway,runner


def check(local,**options):
    root,gateway,runner=local
    return pre.build(gateway,runner,tier='terrestrial',portal_active=options.get('portal_active',True),halted=options.get('halted',False))


def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_pristine_preflight_never_creates_storage_or_selects_tier(local):
    root,_,_=local
    assert check(local,portal_active=False)['blockers']==['PORTAL_STOPPED'] and not root.exists()
    result=check(local);assert 'TIER_NOT_SELECTED' in result['blockers'] and not root.exists()
    assert all(result[k] is False for k in ('automatic_start','automatic_repair','running_permission_granted','external_owner_checked','fresh_prices_checked','provider_contacted','live_orders_enabled','money_movement_enabled'))


def test_ready_native_defaults_remain_only_configuration(local,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *_a,**_k:pytest.fail('Preflight contacted a service'))
    state.load_settings();root,_,runner=local;before=snapshot(root)
    result=check(local)
    assert result['local_checks_clear'] and result['will_prepare_unused_account'] and result['entry_mode']=='NEW_ENTRIES'
    assert snapshot(root)==before and set(before)=={'portal_settings.sqlite3'}
    assert not runner.status()['enabled'] and not runner.status()['ownership_held']


@pytest.mark.parametrize('kind',['off','small','bad-settings','cut','journal','wal','corrupt-order'])
def test_local_blockers_do_not_mutate_retained_files(local,kind):
    root,gateway,_=local;state.load_settings()
    expected={'off':'TIER_NOT_SELECTED','small':'TIER_LIMIT_TOO_SMALL','bad-settings':'SETTINGS_REVIEW_REQUIRED','cut':'TRADING_CUT_ACTIVE',
        'journal':'LOCAL_HISTORY_REVIEW_REQUIRED','wal':'LOCAL_HISTORY_REVIEW_REQUIRED','corrupt-order':'LOCAL_HISTORY_REVIEW_REQUIRED'}[kind]
    if kind=='off':configure('terrestrial',False)
    elif kind=='small':state.save_settings({**state.DEFAULTS,'terrestrial_allocation':0,'solar_allocation':85})
    elif kind=='bad-settings':
        with sqlite3.connect(root/'portal_settings.sqlite3') as db:db.execute("UPDATE configuration SET value='PRIVATE-FIXTURE' WHERE key='paper_budget'")
    elif kind=='cut':(root/'emergency_cut.intent').write_bytes(b'CUT')
    elif kind=='journal':(root/'order_lifecycle.sqlite3-journal').write_bytes(b'RETAINED')
    elif kind=='wal':(root/'portal_settings.sqlite3-wal').write_bytes(b'RETAINED')
    else:gateway._path().write_bytes(b'PRIVATE-CORRUPT-HISTORY')
    before=snapshot(root);result=check(local)
    assert not result['local_checks_clear'] and expected in result['blockers'] and snapshot(root)==before
    assert 'PRIVATE' not in str(result)


@pytest.mark.parametrize('capital',[1000,10000])
def test_existing_unused_manual_portfolio_checked_without_initializing_orders(local,capital):
    from nexus.paper_portfolio import initialize
    root,gateway,_=local;state.load_settings();initialize(capital);before=snapshot(root)
    result=check(local)
    assert result['local_checks_clear']==(capital==10000)
    if capital!=10000:assert 'ACCOUNT_LINK_REVIEW_REQUIRED' in result['blockers']
    assert snapshot(root)==before and not gateway._path().exists()


def test_linked_holdings_can_start_exit_monitoring_with_bad_budget_or_tier_off(tmp_path,monkeypatch):
    from test_paper_forward import setup,warmup
    runner,gateway,clock=setup(tmp_path,monkeypatch);assert warmup(runner,clock)['decision']=='BUY';runner.stop()
    configure('terrestrial',False)
    with sqlite3.connect(tmp_path/'portal_settings.sqlite3') as db:db.execute("INSERT OR REPLACE INTO configuration VALUES('paper_budget','PRIVATE-FIXTURE')")
    before=snapshot(tmp_path)
    result=pre.build(gateway,runner,tier='terrestrial',portal_active=True,halted=False)
    assert result['local_checks_clear'] and result['entry_mode']=='RETAINED_EXIT_MONITORING'
    assert not result['will_prepare_unused_account'] and snapshot(tmp_path)==before


def test_active_worker_and_api_diagnostics_never_authorize_or_write(local,monkeypatch):
    from nexus import app as backend
    root,gateway,runner=local;state.load_settings();before=snapshot(root)
    monkeypatch.setattr(backend,'research_order_gateway',gateway);monkeypatch.setattr(backend,'paper_forward_runner',runner)
    monkeypatch.setattr(backend,'_paused',False);monkeypatch.setattr(backend,'_halted',False)
    client=TestClient(backend.app)
    response=client.get('/api/paper/forward/preflight')
    assert response.status_code==200 and response.json()['local_checks_clear'] and response.headers['cache-control']=='no-store'
    assert client.get('/api/paper/forward/preflight?tier=unknown').status_code==422
    assert client.post('/api/paper/forward/preflight').status_code==405
    assert snapshot(root)==before
    original=runner.status
    monkeypatch.setattr(runner,'status',lambda:{**original(),'worker_alive':True})
    assert check(local)['blockers']==['RUNNER_ALREADY_ACTIVE'] and snapshot(root)==before
