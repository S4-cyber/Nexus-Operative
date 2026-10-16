from fastapi.testclient import TestClient
from nexus.app import app
from nexus.stress import stress_screen
import pytest

def test_stress_screens_severe_gap_and_liquidity():
    r=stress_screen(10000,100)
    assert r["simulation_only"] is True
    assert r["live_execution_authorized"] is False
    assert len(r["scenarios"])==4
    assert r["scenarios"][-1]["modeled_loss"]>r["scenarios"][0]["modeled_loss"]

def test_big_position_rejected_even_with_large_account():
    r=stress_screen(10000,500)
    assert r["decision"]=="REJECT"
    assert "STRESSED_LOSS_BUDGET_EXCEEDED" in r["reasons"]

def test_daily_loss_already_exhausted():
    r=stress_screen(10000,10,daily_loss=101)
    assert "DAILY_LOSS_LIMIT" in r["reasons"]

def test_invalid_scenario_inputs():
    with pytest.raises(ValueError):stress_screen(1000,1100)
    with pytest.raises(ValueError):stress_screen(1000,1,direction="SIDEWAYS")
    with pytest.raises(ValueError):stress_screen(float("nan"),1)

def test_api_is_paper_only():
    c=TestClient(app)
    r=c.post("/api/paper/stress",json={"equity":10000,"notional":100,"direction":"DOWN","daily_loss":0})
    assert r.status_code==200
    assert r.json()["mode"]=="STATIC_ADVERSE_SCENARIOS"
    assert c.get("/api/status").json()["live_orders_enabled"] is False
