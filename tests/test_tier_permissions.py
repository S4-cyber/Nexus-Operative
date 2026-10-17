import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus import app as backend
from nexus.tier_permissions import current,configure,require_entry
from nexus.tier_performance import tier_performance

def test_all_tiers_off_by_default_and_caps_are_independent(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    policies=current()["tiers"]
    assert len(policies)==3
    assert all(x["enabled"] is False for x in policies)
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
