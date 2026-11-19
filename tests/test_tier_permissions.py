import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from nexus.app import app
from nexus import app as backend
from nexus.tier_permissions import current,configure,require_entry
from nexus.tier_performance import tier_performance


class EqualText(str):
    def __eq__(self,other):return True
    def __ne__(self,other):return False


@pytest.mark.parametrize('notional',(
    0,-1,True,None,'10',float('nan'),float('inf'),
    Decimal('NaN'),Decimal('Infinity')))
def test_invalid_entry_value_fails_before_settings_or_permission_read(
        tmp_path,monkeypatch,notional):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr('nexus.tier_permissions.current',
        lambda:pytest.fail('tier permission state read'))
    with pytest.raises(ValueError,match='^RISK_TIER_INTENT_INVALID$'):
        require_entry('terrestrial',notional)
    assert not tuple(tmp_path.iterdir())


def test_type_confused_tier_fails_before_notional_or_settings_read(
        tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr('nexus.tier_permissions.current',
        lambda:pytest.fail('tier permission state read'))
    with pytest.raises(ValueError,match='^UNKNOWN_RISK_TIER$'):
        require_entry(EqualText('terrestrial'),10)
    assert not tuple(tmp_path.iterdir())

def test_new_install_terrestrial_preset_and_caps_are_independent(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    policies=current()["tiers"]
    assert len(policies)==3
    assert {x["tier"] for x in policies if x["enabled"]}=={"terrestrial"}
    require_entry("terrestrial",10)
    configure("terrestrial",False)
    with pytest.raises(ValueError,match="DISENGAGED"):
        require_entry("terrestrial",10)
    configure("solar",True)
    assert {x["tier"] for x in current()["tiers"] if x["enabled"]}=={"solar"}
    require_entry("solar",100)
    with pytest.raises(ValueError,match="CAP"):
        require_entry("solar",201)
    with pytest.raises(ValueError,match="DISENGAGED"):
        require_entry("universal",10)

def test_tier_buy_is_blocked_unless_engaged_and_portal_active(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend.observer,"start",lambda:None)
    monkeypatch.setattr(backend.refresh_worker,"start",lambda *a,**k:None)
    monkeypatch.setattr(backend.observer,"stop",lambda:None)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_paused",True)
    c=TestClient(app)
    body={"client_order_id":"policy_1","product":"BTC-USD","side":"BUY","notional":10,
          "provider":"nexus_local_paper","tier":"terrestrial"}
    assert c.post("/api/execution/intent/simulate",json=body).status_code==409
    assert c.put("/api/tiers/terrestrial/permission",json={"enabled":True}).status_code==200
    assert c.post("/api/execution/intent/simulate",json=body).status_code==409
    assert c.post("/api/portal",json={"enabled":True}).status_code==200
    result=c.post("/api/execution/intent/simulate",json=body)
    assert result.status_code==200
    assert result.json()["tier"]=="terrestrial"
    assert result.json()["sent_to_broker"] is False
    assert c.put("/api/tiers/terrestrial/permission",json={"enabled":False}).status_code==200
    assert c.post("/api/execution/intent/simulate",json={**body,"client_order_id":"policy_2"}).status_code==422
    assert c.post("/api/execution/intent/simulate",json={**body,"client_order_id":"policy_exit","side":"SELL"}).status_code==200
    c.post("/api/simulate/halt")
    assert c.put("/api/tiers/solar/permission",json={"enabled":True}).status_code==409
    c.post("/api/simulate/halt/reset",json={"confirm_reset":True})
    c.post("/api/portal",json={"enabled":False})

def test_performance_readout_fails_closed_without_valid_history(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    r=tier_performance("BTC-USD")
    assert r["predicted_success_rate_pct"] is None
    assert r["projected_gain_per_minute_pct"] is None
    assert r["projected_gain_per_day_pct"] is None
    assert r["available"] is False
    assert r["live_orders_enabled"] is False
    c=TestClient(app)
    assert c.get("/api/tiers/performance?product=BTC-USD").status_code==200
    assert c.get("/api/tiers/performance?product=NOT-REAL").status_code==422

def test_controls_and_historical_labels_in_portal():
    html=TestClient(app).get("/portal").text
    assert html.count("<script>")==html.count("</script>")==1
    assert html.count("</html>")==1
    assert 'id="tier-engagement"' in html
    assert "Engage Research Risk Tiers" not in html or "tier-engagement" in html
    assert "HISTORICAL WINS" in html
    assert "NO RELIABLE ESTIMATE" in html
    assert "Not a prediction" in html

def test_paper_ledger_buy_requires_selected_engaged_tier(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",True)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend.observer,"start",lambda:None)
    monkeypatch.setattr(backend.refresh_worker,"start",lambda *a,**k:None)
    monkeypatch.setattr(backend.observer,"stop",lambda:None)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    c=TestClient(app)
    assert c.post("/api/paper/portfolio/init",json={"paper_capital":10000}).status_code==200
    req={"product":"BTC-USD","notional":25,"tier":"terrestrial"}
    assert c.post("/api/paper/portfolio/buy",json=req).status_code==409
    c.post("/api/portal",json={"enabled":True})
    assert c.post("/api/paper/portfolio/buy",json=req).status_code==422
    assert c.post("/api/paper/portfolio/buy",json={"product":"BTC-USD","notional":25}).status_code==422
    assert c.put("/api/tiers/solar/permission",json={"enabled":True}).status_code==200
    assert c.post("/api/paper/portfolio/buy",json=req).status_code==422
    assert c.put("/api/tiers/terrestrial/permission",json={"enabled":True}).status_code==200
    approved=c.post("/api/paper/portfolio/buy",json=req)
    assert approved.status_code==200
    assert len(approved.json()["positions"])==1
    assert approved.json()["live_orders_enabled"] is False
    c.put("/api/tiers/terrestrial/permission",json={"enabled":False})
    assert c.post("/api/paper/portfolio/sell",json={"product":"BTC-USD"}).status_code==200
    c.post("/api/portal",json={"enabled":False})

def test_emergency_cut_keeps_reducing_paper_exit_available(tmp_path,monkeypatch):
    from nexus import emergency_intent
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",False)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_cut_confirmed",False)
    monkeypatch.setattr(backend.observer,"start",lambda:None)
    monkeypatch.setattr(backend.refresh_worker,"start",lambda *a,**k:None)
    monkeypatch.setattr(backend.observer,"stop",lambda:None)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    client=TestClient(app)
    assert client.post("/api/paper/portfolio/init",json={"paper_capital":10000}).status_code==200
    configure("terrestrial",True)
    buy={"product":"BTC-USD","notional":10,"tier":"terrestrial"}
    assert client.post("/api/paper/portfolio/buy",json=buy).status_code==200
    emergency_intent.path(backend.research_order_gateway._path()).write_bytes(emergency_intent.RECORD)
    assert client.post("/api/paper/portfolio/buy",json=buy).status_code==409
    closed=client.post("/api/paper/portfolio/sell",json={"product":"BTC-USD"})
    assert closed.status_code==200 and closed.json()["positions"]==[]
    assert backend._execution_halted() is True

def test_emergency_cut_exit_still_fails_closed_on_invalid_quote(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_paused",False)
    good={"product":"BTC-USD","read_only":True,"orders_allowed":False,
          "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
          "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(good))
    client=TestClient(app)
    client.post("/api/paper/portfolio/init",json={"paper_capital":10000})
    configure("terrestrial",True)
    assert client.post("/api/paper/portfolio/buy",json={"product":"BTC-USD","notional":10,"tier":"terrestrial"}).status_code==200
    monkeypatch.setattr(backend,"_halted",True)
    monkeypatch.setattr(backend,"read_ticker",lambda product:(_ for _ in ()).throw(ValueError("QUOTE_STALE")))
    assert client.post("/api/paper/portfolio/sell",json={"product":"BTC-USD"}).status_code==422
    assert len(client.get("/api/paper/portfolio").json()["positions"])==1

def test_ledger_ui_has_explicit_tier_selector():
    html=TestClient(app).get("/portal").text
    assert 'id="papertier"' in html
    assert "tier:byId('papertier').value" in html


def test_tier_writer_contention_never_enables_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state,tier_permissions
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));current()
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    monkeypatch.setattr(tier_permissions,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):configure('solar',True)
        assert path.read_bytes()==before
    assert not next(row for row in current()['tiers'] if row['tier']=='solar')['enabled']
    assert next(row for row in configure('solar',True)['tiers'] if row['tier']=='solar')['enabled']


def test_tier_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state,tier_permissions
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));current()
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    monkeypatch.setattr(tier_permissions,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN');blocker.execute('SELECT COUNT(*) FROM tier_permissions').fetchone()
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):configure('solar',True)
        assert path.read_bytes()==before
    assert next(row for row in configure('solar',True)['tiers'] if row['tier']=='solar')['enabled']


def test_tier_exclusive_contention_is_stable_at_api_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state,tier_permissions
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));expected=current()
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    monkeypatch.setattr(tier_permissions,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):current()
        reply=TestClient(app).get('/api/tiers/permissions')
        assert reply.status_code==503 and reply.json()=={'detail':'PAPER_SETTINGS_DATABASE_BUSY'}
        assert path.read_bytes()==before
    assert current()==expected

