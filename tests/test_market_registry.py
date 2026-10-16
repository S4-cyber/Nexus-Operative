from fastapi.testclient import TestClient
from nexus.app import app
from nexus.markets import list_markets

def test_market_catalog_never_grants_trading():
    data=list_markets()
    assert data["mode"]=="CATALOG_ONLY"
    assert data["order_routing"] is False
    assert data["automatic_connection"] is False
    assert all(p["execution"]=="disabled" for p in data["providers"])
    assert {p["id"] for p in data["providers"]}=={"alpaca","ibkr","coinbase"}

def test_api_market_capabilities_are_disclosed():
    result=TestClient(app).get("/api/markets")
    assert result.status_code==200
    assert result.json()["real_time_quotes"] is False
    assert result.json()["providers"][0]["connectivity"]=="not_connected"
