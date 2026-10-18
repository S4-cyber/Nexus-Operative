"""First-run convenience never resets user choices or grants a running session."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient
from nexus import state
from nexus.app import app
from nexus.tier_permissions import current,configure


@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    from nexus import app as backend
    monkeypatch.setattr(backend,'_paused',True)
    monkeypatch.setattr(backend,'_halted',False)
    return tmp_path


def enabled():return {x['tier'] for x in current()['tiers'] if x['enabled']}


def test_new_settings_seed_one_paper_tier_without_account_or_network(local,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *_a,**_k:pytest.fail('Default setup contacted a service'))
    client=TestClient(app)
    assert client.get('/api/paper/settings').json()['paper_budget']==10000
    assert enabled()=={'terrestrial'}
    assert {p.name for p in local.iterdir()}=={'portal_settings.sqlite3'}
    with sqlite3.connect(local/'portal_settings.sqlite3') as db:
        assert dict((k,json.loads(v)) for k,v in db.execute('SELECT key,value FROM configuration'))==state.DEFAULTS
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='new_install_paper_defaults'").fetchone()[0]==1
    status=client.get('/api/status').json()
    assert not status['portal_enabled'] and not status['live_orders_enabled'] and not status['money_movement_enabled']


def test_explicit_off_survives_reloads_and_saved_budget_changes(local):
    state.load_settings();configure('terrestrial',False)
    state.save_settings({**state.DEFAULTS,'paper_budget':2500})
    for _ in range(3):assert enabled()==set() and state.load_settings()['paper_budget']==2500


def test_existing_empty_settings_are_not_reinterpreted_as_new_install(local):
    path=local/'portal_settings.sqlite3'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE configuration(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    assert state.load_settings()==state.DEFAULTS and enabled()==set()
    with sqlite3.connect(path) as db:assert db.execute('SELECT COUNT(*) FROM configuration').fetchone()[0]==0


@pytest.mark.parametrize('name',['paper_portfolio.sqlite3','order_lifecycle.sqlite3','paper_forward.sqlite3',
    'order_lifecycle.sqlite3-journal','paper_portfolio.sqlite3-wal','emergency_cut.intent','portal_settings.sqlite3-shm'])
def test_retained_or_interrupted_history_prevents_default_tier_selection(local,name):
    evidence=local/name;evidence.write_bytes(b'RETAINED-FIXTURE')
    assert state.load_settings()==state.DEFAULTS and enabled()==set()
    assert evidence.read_bytes()==b'RETAINED-FIXTURE'


def test_concurrent_first_reads_seed_only_one_atomic_preset(local):
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(v==state.DEFAULTS for v in pool.map(lambda _:state.load_settings(),range(12)))
    assert enabled()=={'terrestrial'}
    with sqlite3.connect(local/'portal_settings.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM configuration').fetchone()[0]==4
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='new_install_paper_defaults'").fetchone()[0]==1


def test_failed_preset_rolls_back_all_configuration_and_permissions(local,monkeypatch):
    connect=sqlite3.connect
    class Failing(sqlite3.Connection):
        def executemany(self,sql,parameters):
            if sql.startswith('INSERT INTO tier_permissions'):raise sqlite3.OperationalError('injected disk failure')
            return super().executemany(sql,parameters)
    monkeypatch.setattr(state.sqlite3,'connect',lambda *a,**k:connect(*a,**k,factory=Failing))
    with pytest.raises(sqlite3.OperationalError):state.load_settings()
    with connect(local/'portal_settings.sqlite3') as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()


def test_default_settings_can_prepare_fixed_paper_runner_without_manual_forms(local):
    from nexus.paper_forward import PaperForwardRunner
    from nexus.durable_orders import DurableOrderGateway
    state.load_settings()
    gateway=DurableOrderGateway(local/'order_lifecycle.sqlite3')
    runner=PaperForwardRunner(gateway,lambda _p:pytest.fail('Preparation fetched prices'),clock=lambda:1000.)
    assert not runner.status()['enabled']
    try:
        runner.prepare('terrestrial')
        report=gateway.recovery_report()
        assert report['portfolio_reconciled'] and report['execution_account']['account']['initial']=='10000.0'
        assert report['orders']==[]
    finally:runner.stop()
    assert not PaperForwardRunner(gateway).status()['enabled']


def test_first_run_explanations_do_not_make_optional_connections_required(local):
    from nexus.startup_tasks import checklist
    state.load_settings();items={x['id']:x for x in checklist()['items']}
    assert items['budget']['checked'] and items['tier']['checked']
    assert items['portfolio']['status']=='ON_DEMAND' and not items['portfolio']['checked']
    assert all(items[k]['setup_scope'] in ('Optional','Future feature') for k in ('external_ai','paper_read','payment','broker','cloud'))
    html=(Path(__file__).resolve().parents[1]/'dashboard/portal_preview.html').read_text()
    assert 'Welcome to Nexus' in html and 'Your research engine, ready to explore' in html
    assert 'No need to tick these boxes yourself' in html
