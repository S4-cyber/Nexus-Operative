import time
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.paper_portfolio import initialize,buy,sell,state,reconcile
from nexus.strategy_branches import available_branches

def quote(product="BTC-USD",bid=100,ask=101):
    return {"product":product,"provider":"coinbase_exchange_public","bid":bid,"ask":ask,
            "received_at":time.time(),"read_only":True,"orders_allowed":False}

def test_paper_ledger_survives_new_connection(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    r=buy("BTC-USD",100,quote())
    assert r["positions"][0]["product"]=="BTC-USD"
    assert r["live_orders_enabled"] is False
    assert state()["event_count"]==2
    marked=reconcile({"BTC-USD":quote()})
    assert marked["reconciled"] is True
    r=sell("BTC-USD",quote(bid=102,ask=103))
    assert len(r["positions"])==0
    assert r["event_count"]==3
    assert (tmp_path/"paper_portfolio.sqlite3").exists()

def test_fails_closed_missing_quotes_and_position_cap(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000)
    with pytest.raises(ValueError):buy("BTC-USD",30,quote())
    with pytest.raises(ValueError):buy("BTC-USD",10,quote(bid=105,ask=101))
    buy("BTC-USD",10,quote())
    assert reconcile({})["reconciled"] is False
    with pytest.raises(ValueError):initialize(1000)

def test_rejects_outdated_quote(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000)
    x=quote()
    x["received_at"]=time.time()-90
    with pytest.raises(ValueError):buy("BTC-USD",10,x)

def test_registry_is_never_authorized():
    b=available_branches()
    assert b["live_execution_enabled"] is False
    assert b["automated_bets_enabled"] is False
    assert {"options_defined_risk","event_contracts","spot_momentum"}.issubset({x["id"] for x in b["branches"]})

def test_portfolio_endpoints_remain_paper_only(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    assert c.post("/api/paper/portfolio/init",json={"paper_capital":1000}).status_code==200
    assert c.get("/api/paper/portfolio").json()["simulation_only"] is True
    assert c.get("/api/strategy-branches").json()["live_execution_enabled"] is False
    assert c.get("/api/status").json()["live_orders_enabled"] is False
