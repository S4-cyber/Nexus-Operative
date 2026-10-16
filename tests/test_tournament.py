from fastapi.testclient import TestClient
from nexus.app import app
from nexus.tournament import tournament
import pytest

def prices():
    return [100+i*.12+(i%6)*.03 for i in range(110)]

def test_training_only_selection_and_holdout_isolation():
    data=tournament(prices(),capital=5000)
    assert data["selected_by"]=="TRAINING_ONLY"
    assert data["decision"]=="NOT_APPROVED"
    assert data["train_observations"]==77
    assert data["holdout_observations"]==33
    assert len(data["training_leaderboard"])==6
    assert data["live_orders_enabled"] is False
    assert "UNVERIFIED_EXECUTION_AND_MARKET_DATA" in data["reasons"]

def test_tournament_reproducible():
    one=tournament(prices(),capital=100)
    two=tournament(prices(),capital=100)
    assert one==two

def test_short_or_malformed_data_rejected():
    with pytest.raises(ValueError):tournament([100]*30,5000)
    with pytest.raises(ValueError):tournament([100]*65+[float("nan")],5000)

def test_api_uses_no_broker():
    c=TestClient(app)
    r=c.post("/api/paper/tournament",json={"capital":12000,"tier":"solar","direction":"UP","closes":prices()})
    assert r.status_code==200,r.text
    assert r.json()["paper_autotrading_enabled"] is False
    assert c.get("/api/status").json()["live_orders_enabled"] is False
