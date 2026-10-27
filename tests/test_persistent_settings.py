"""Persistent settings and fail-closed paper allocation tests."""
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.state import DEFAULTS, load_settings, save_settings

def test_settings_persist_across_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA", str(tmp_path))
    values={"paper_budget":12500.0,"terrestrial_allocation":60,"solar_allocation":30,"universal_allocation":10}
    assert save_settings(values)==values
    assert load_settings()==values
    assert (tmp_path/"portal_settings.sqlite3").exists()

def test_invalid_allocation_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    with pytest.raises(ValueError):
        save_settings({"paper_budget":1000,"terrestrial_allocation":60,"solar_allocation":60,"universal_allocation":10})
    with pytest.raises(ValueError):
        save_settings({"paper_budget":-3,"terrestrial_allocation":60,"solar_allocation":30,"universal_allocation":10})

def test_api_settings_and_no_live_money(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    assert c.put("/api/paper/settings",json={"paper_budget":2500,"terrestrial_allocation":50,"solar_allocation":35,"universal_allocation":15}).status_code==200
    assert c.get("/api/paper/settings").json()["paper_budget"]==2500
    assert c.get("/api/status").json()["live_orders_enabled"] is False
    assert c.get("/api/status").json()["money_movement_enabled"] is False


@pytest.mark.parametrize(('key','raw'),[
    ('paper_budget','PRIVATE-CORRUPT-FIXTURE'),('paper_budget','true'),('paper_budget','null'),
    ('paper_budget','"1000"'),('paper_budget','0'),('paper_budget','-1'),('paper_budget','1000001'),
    ('paper_budget','NaN'),('paper_budget','1e309'),('paper_budget','{}'),('paper_budget','1'*129),
    ('paper_budget',b'1000'),('solar_allocation','35.0'),('solar_allocation','true'),
    ('solar_allocation','-1'),('solar_allocation','101'),('solar_allocation','36')],
    ids=['bad-json','bool','null','string','zero','negative','too-large','nan','infinity','object','oversized','blob','fractional-allocation','bool-allocation','negative-allocation','large-allocation','wrong-total'])
def test_malformed_saved_settings_never_fall_back_to_a_larger_default(tmp_path,monkeypatch,key,raw):
    import sqlite3
    from nexus.tier_permissions import require_entry,configure
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    save_settings({**DEFAULTS,'paper_budget':1000})
    path=tmp_path/'portal_settings.sqlite3'
    with sqlite3.connect(path) as db:db.execute('UPDATE configuration SET value=? WHERE key=?',(raw,key))
    before=path.read_bytes()
    for action in (load_settings,lambda:require_entry('terrestrial',1),lambda:configure('solar',True)):
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_REVIEW_REQUIRED$'):action()
    client=TestClient(app)
    for endpoint in ('/api/paper/settings','/api/tiers','/api/tiers/permissions'):
        reply=client.get(endpoint)
        assert reply.status_code==503 and reply.json()=={'detail':'PAPER_SETTINGS_REVIEW_REQUIRED'}
    assert path.read_bytes()==before
    assert not (tmp_path/'order_lifecycle.sqlite3').exists() and not (tmp_path/'paper_portfolio.sqlite3').exists()


def test_disengage_remains_available_and_repair_is_explicit(tmp_path,monkeypatch):
    import sqlite3
    from nexus.state import DEFAULTS
    from nexus.tier_permissions import current
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));save_settings(DEFAULTS)
    path=tmp_path/'portal_settings.sqlite3'
    with sqlite3.connect(path) as db:db.execute("UPDATE configuration SET value='PRIVATE-CORRUPT-FIXTURE' WHERE key='paper_budget'")
    client=TestClient(app)
    result=client.put('/api/tiers/terrestrial/permission',json={'enabled':False})
    assert result.status_code==200 and result.json()['enabled'] is False and result.json()['settings_review_required']
    assert 'tiers' not in result.json()  # no guessed caps for the other switches
    assert client.put('/api/tiers/solar/permission',json={'enabled':True}).status_code==422
    with sqlite3.connect(path) as db:
        assert dict(db.execute('SELECT tier,enabled FROM tier_permissions'))=={'terrestrial':0,'solar':0,'universal':0}
        assert db.execute("SELECT value FROM configuration WHERE key='paper_budget'").fetchone()[0]=='PRIVATE-CORRUPT-FIXTURE'
    assert client.put('/api/paper/settings',json={**DEFAULTS,'paper_budget':1000}).status_code==200
    assert load_settings()['paper_budget']==1000 and not any(row['enabled'] for row in current()['tiers'])


def test_missing_legacy_keys_use_defaults_but_duplicate_saved_keys_fail(tmp_path,monkeypatch):
    import sqlite3
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));path=tmp_path/'portal_settings.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE configuration(key TEXT,value TEXT)')
        db.execute("INSERT INTO configuration VALUES('paper_budget','2500')")
    with pytest.raises(ValueError,match='PAPER_SETTINGS_REVIEW_REQUIRED'):load_settings()
    with sqlite3.connect(path) as db:
        import json
        db.executemany('INSERT INTO configuration VALUES(?,?)',[(k,json.dumps(v)) for k,v in DEFAULTS.items() if k!='paper_budget'])
    assert load_settings()=={**DEFAULTS,'paper_budget':2500}
    with sqlite3.connect(path) as db:db.execute("INSERT INTO configuration VALUES('paper_budget','10000')")
    before=path.read_bytes()
    with pytest.raises(ValueError,match='PAPER_SETTINGS_REVIEW_REQUIRED'):load_settings()
    assert path.read_bytes()==before


def test_entry_is_vetoed_before_price_io_when_saved_budget_is_corrupt(tmp_path,monkeypatch):
    import sqlite3
    from nexus import app as backend
    from nexus.state import DEFAULTS
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));save_settings(DEFAULTS)
    monkeypatch.setattr(backend,'_paused',False);monkeypatch.setattr(backend,'_halted',False)
    monkeypatch.setattr(backend,'read_ticker',lambda *_:pytest.fail('Rejected entry fetched a price'))
    with sqlite3.connect(tmp_path/'portal_settings.sqlite3') as db:db.execute("UPDATE configuration SET value='broken' WHERE key='paper_budget'")
    response=TestClient(app).post('/api/paper/portfolio/buy',json={'product':'BTC-USD','notional':25,'tier':'terrestrial'})
    assert response.status_code==422 and response.json()['detail']=='PAPER_SETTINGS_REVIEW_REQUIRED'
    assert not (tmp_path/'paper_portfolio.sqlite3').exists()


def test_risk_reducing_forward_exit_survives_corrupt_budget(tmp_path,monkeypatch):
    import sqlite3
    from datetime import datetime,timezone
    from nexus.state import DEFAULTS
    from nexus.paper_forward import PaperForwardRunner
    from nexus.durable_orders import DurableOrderGateway
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));save_settings(DEFAULTS)
    clock=[1000.,100.]
    def quote(product):return dict(product=product,provider='coinbase_exchange_public',read_only=True,orders_allowed=False,
        bid=clock[1],ask=clock[1]+.01,last=clock[1],received_at=clock[0],exchange_time=datetime.fromtimestamp(clock[0],timezone.utc).isoformat())
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3');runner=PaperForwardRunner(gateway,quote,lambda:clock[0])
    try:
        runner.prepare('terrestrial')
        for i in range(7):clock[:]=[1000.+i*5,100.+i*.1];outcome=runner.single_cycle()
        assert outcome['decision']=='BUY'
        with sqlite3.connect(tmp_path/'portal_settings.sqlite3') as db:db.execute("UPDATE configuration SET value='broken' WHERE key='paper_budget'")
        clock[:]=[clock[0]+5,102.]
        assert runner.single_cycle()['decision']=='SELL'
        assert not gateway.recovery_report()['execution_account']['account']['positions']
    finally:runner.stop()


@pytest.mark.parametrize('missing',[*DEFAULTS,'all'])
def test_missing_previously_saved_values_require_review(tmp_path,monkeypatch,missing):
    import sqlite3
    from nexus.startup_tasks import checklist
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));save_settings({**DEFAULTS,'paper_budget':1000})
    path=tmp_path/'portal_settings.sqlite3'
    with sqlite3.connect(path) as db:
        if missing=='all':db.execute('DELETE FROM configuration')
        else:db.execute('DELETE FROM configuration WHERE key=?',(missing,))
    before=path.read_bytes()
    with pytest.raises(ValueError,match='PAPER_SETTINGS_REVIEW_REQUIRED'):load_settings()
    row=next(r for r in checklist()['items'] if r['id']=='budget')
    assert row['status']=='UNKNOWN' and not row['checked']
    assert path.read_bytes()==before


def test_actual_settings_controller_clears_unverified_values_without_starting_trade():
    import shutil,subprocess
    from pathlib import Path
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual settings controller check')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/paper_settings_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_settings_writer_contention_rolls_back_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));save_settings(DEFAULTS)
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    changed={**DEFAULTS,'paper_budget':2500}
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):save_settings(changed)
        assert path.read_bytes()==before
    assert save_settings(changed)==changed


def test_settings_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));save_settings(DEFAULTS)
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    changed={**DEFAULTS,'paper_budget':2500}
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN');blocker.execute('SELECT COUNT(*) FROM configuration').fetchone()
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):save_settings(changed)
        assert path.read_bytes()==before
    assert save_settings(changed)==changed


def test_settings_exclusive_contention_is_stable_at_api_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));save_settings(DEFAULTS)
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):load_settings()
        reply=TestClient(app).get('/api/paper/settings')
        assert reply.status_code==503 and reply.json()=={'detail':'PAPER_SETTINGS_DATABASE_BUSY'}
        assert path.read_bytes()==before
    assert load_settings()==DEFAULTS
