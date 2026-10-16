from fastapi.testclient import TestClient
from nexus.app import app

def test_configurable_paper_roundtrip():
    c=TestClient(app)
    r=c.post("/api/paper/scenario",json={"capital":50000,"tier":"solar","symbol":"SPY","direction":"UP","notional":500,"entry_price":500,"exit_price":500})
    assert r.status_code==200,r.text
    d=r.json()
    assert d["synthetic"] is True and d["mode"]=="PAPER_SCENARIO_ONLY"
    assert d["net_pnl"] < 0
    assert d["ending_paper_cash"] < 50000

def test_oversized_trade_is_rejected():
    c=TestClient(app)
    r=c.post("/api/paper/scenario",json={"capital":100,"tier":"terrestrial","symbol":"SPY","direction":"UP","notional":25,"entry_price":500,"exit_price":550})
    assert r.status_code==422

def test_short_hypothesis_does_not_connect_broker():
    c=TestClient(app)
    r=c.post("/api/paper/scenario",json={"capital":10000,"tier":"solar","symbol":"SPY","direction":"DOWN","notional":100,"entry_price":500,"exit_price":490})
    assert r.status_code==200
    assert r.json()["close"]["direction"]=="DOWN"
    assert c.get("/api/status").json()["live_orders_enabled"] is False
