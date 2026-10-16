import time
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.model_adapters import coordinate, validate_response, ROLES

def test_all_fixtures_abstain_and_no_execution():
    r=coordinate({"symbol":"BTC-USD","mock":True})
    assert len(r["specialists"])==5
    assert r["all_abstained"] is True
    assert r["provider_connected"] is False
    assert r["governor"]=="NO_ORDER_AUTHORITY"
    assert r["live_orders_enabled"] is False

def test_disagreement_never_grants_orders():
    def cautious(role,snapshot):
        return {"role":role,"verdict":"CAUTION" if role=="portfolio_risk" else "ABSTAIN",
                "reason":"Independent diagnostic","estimated_cost_usd":0}
    r=coordinate({"test":1},adapters={x:cautious for x in ROLES})
    assert r["disagreement"] is True
    assert r["paper_orders_enabled"] is False

def test_timeout_fails_closed():
    def slow(role,snapshot):
        time.sleep(.1)
        return {"role":role,"verdict":"FURTHER_RESEARCH","reason":"Late result","estimated_cost_usd":0}
    r=coordinate({"test":1},adapters={"market_regime":slow},timeout_seconds=.01)
    assert r["specialists"][0]["verdict"]=="ABSTAIN"
    assert r["specialists"][0]["reason"]=="MODEL_TIMEOUT"

def test_untrusted_payload_cannot_submit_order():
    with pytest.raises(ValueError):
        validate_response({"role":"market_regime","verdict":"FURTHER_RESEARCH",
                           "reason":"trade now","order":{"side":"BUY"}}, "market_regime")
    with pytest.raises(ValueError):
        validate_response({"role":"market_regime","verdict":"FURTHER_RESEARCH",
                           "reason":"0"*10000},"market_regime")

def test_invalid_snapshot_and_preview_endpoint():
    with pytest.raises(ValueError):coordinate({"number":float("nan")})
    c=TestClient(app)
    r=c.post("/api/paper/model-council",json={"product":"BTC-USD"})
    assert r.status_code==200
    assert r.json()["provider_connected"] is False
    assert c.get("/api/status").json()["live_orders_enabled"] is False
