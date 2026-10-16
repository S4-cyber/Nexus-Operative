import time
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.risk_gate import evaluate

def viable():
    return dict(data_verified=True,instrument_authorized=True,market_open=True,
        account_reconciled=True,emergency_halt=False,cooldown_active=False,
        quote_timestamp=time.time(),tier="terrestrial",equity=10000,notional=50,
        current_exposure=0,realized_daily_loss=0,roundtrip_fees_bps=2,
        spread_bps=3,roundtrip_slippage_bps=5,
        conservative_expected_edge_bps=50,modeled_worst_loss_bps=100)

def test_default_denial():
    r=evaluate({})
    assert r["decision"]=="REJECT"
    assert r["live_execution_authorized"] is False

def test_good_candidate_is_still_not_live():
    r=evaluate(viable())
    assert r["decision"]=="PAPER_RESEARCH_CANDIDATE"
    assert r["live_execution_authorized"] is False

def test_weak_edge_and_late_quote_rejected():
    x=viable();x["conservative_expected_edge_bps"]=10
    assert "INSUFFICIENT_NET_EDGE_AFTER_COSTS" in evaluate(x)["reasons"]
    x=viable();x["quote_timestamp"]=time.time()-50
    assert "STALE_OR_MISSING_QUOTE" in evaluate(x)["reasons"]

def test_spending_and_halt_rejected():
    x=viable();x["realized_daily_loss"]=101
    assert "DAILY_LOSS_BUDGET_EXHAUSTED" in evaluate(x)["reasons"]
    x=viable();x["emergency_halt"]=True
    assert "EMERGENCY_HALT_OR_UNKNOWN" in evaluate(x)["reasons"]

def test_invalid_numeric_fails_closed():
    x=viable();x["equity"]="NaN"
    assert evaluate(x)["decision"]=="REJECT"

def test_endpoint_does_not_authorize_broker():
    c=TestClient(app)
    r=c.post("/api/paper/risk-screen",json=viable())
    assert r.status_code==200
    assert r.json()["live_execution_authorized"] is False
    assert c.get("/api/status").json()["money_movement_enabled"] is False
