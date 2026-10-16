from fastapi.testclient import TestClient
from nexus.app import app
from nexus.walkforward import walk_forward
import pytest

def test_walkforward_no_false_approval():
    data=[100+i*.1 for i in range(100)]
    result=walk_forward(data,capital=2000,tier="solar")
    assert result["mode"]=="WALK_FORWARD_PAPER_REPLAY"
    assert result["holdout_observations"]==30
    assert result["decision"] in ("INSUFFICIENT_EVIDENCE","REJECT","FURTHER_VALIDATION_REQUIRED")
    assert result["simulation_only"] is True

def test_walkforward_rejects_short_dataset():
    with pytest.raises(ValueError):walk_forward([100]*10,1000)

def test_walkforward_api_fails_closed():
    client=TestClient(app)
    prices=[100+i*.5 for i in range(35)]
    response=client.post("/api/paper/walk-forward",json={"capital":2500,"closes":prices,"tier":"solar","direction":"UP"})
    assert response.status_code==200,response.text
    assert response.json()["decision"]=="INSUFFICIENT_EVIDENCE"
    assert client.get("/api/status").json()["live_orders_enabled"] is False
