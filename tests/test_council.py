import time
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.council import council
import pytest

def baseline():
    return dict(symbol="BTC-USD",data_source="coinbase_exchange_public",
        data_verified=True,quote_age_seconds=1,spread_bps=2,
        estimated_roundtrip_fees_bps=3,estimated_slippage_bps=3,
        conservative_edge_bps=70,history_observations=500,
        out_of_sample_trades=80,out_of_sample_net_pnl=15,
        equity=10000,proposed_notional=30,current_exposure=0,
        daily_loss=0,stress_loss_estimate=15,
        emergency_halt=False,account_reconciled=True,market_eligible=True)

def test_council_has_five_distinct_specialists_and_no_orders():
    result=council(baseline())
    assert len(result["specialists"])==5
    assert len(set(x["specialist"] for x in result["specialists"]))==5
    assert result["consensus"]=="FURTHER_PAPER_VALIDATION"
    assert result["governor_decision"]=="NO_ORDER_AUTHORITY"
    assert result["human_review_required"] is True
    assert all(x["implementation"]=="DETERMINISTIC_RULES_NOT_AI_MODEL" for x in result["specialists"])

def test_any_veto_blocks_candidate():
    for field,value,role in [
      ("quote_age_seconds",300,"market_regime"),
      ("out_of_sample_trades",1,"strategy_research"),
      ("spread_bps",100,"execution_quality"),
      ("daily_loss",105,"portfolio_risk"),
      ("conservative_edge_bps",1000,"adversarial_review")]:
        x=baseline();x[field]=value
        r=council(x)
        assert r["consensus"]=="REJECT"
        assert role in r["vetoes"]
        assert r["live_orders_enabled"] is False

def test_missing_and_nonnumeric_fail_closed():
    with pytest.raises(ValueError):council({})
    x=baseline();x["equity"]="NaN"
    with pytest.raises(ValueError):council(x)

def test_council_api_no_broker_authority():
    c=TestClient(app)
    r=c.post("/api/paper/council",json=baseline())
    assert r.status_code==200,r.text
    assert r.json()["governor_decision"]=="NO_ORDER_AUTHORITY"
    assert c.get("/api/status").json()["money_movement_enabled"] is False
