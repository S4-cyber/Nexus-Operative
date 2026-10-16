import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.evidence_auditor import audit_evidence
from nexus.latency import LatencyObservatory

def evidence():
    return {"symbol":"BTC-USD","data_source":"coinbase_exchange_public",
        "data_verified":True,"history_observations":200,
        "out_of_sample_trades":50,"conservative_edge_bps":90,
        "equity":10000,"proposed_notional":10}

def council_evidence():
    return {**evidence(),"quote_age_seconds":1,"spread_bps":2,
      "estimated_roundtrip_fees_bps":2,"estimated_slippage_bps":5,
      "out_of_sample_net_pnl":20,"current_exposure":0,"daily_loss":0,
      "stress_loss_estimate":5,"emergency_halt":False,
      "account_reconciled":True,"market_eligible":True}

def test_self_certification_is_rejected():
    a=audit_evidence(evidence())
    assert a["decision"]=="UNVERIFIED"
    assert "SELF_ATTESTED_VERIFICATION_NOT_ACCEPTED" in a["reasons"]
    assert a["source_verified"] is False

def test_claimed_quote_cannot_bypass_audit():
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False}
    a=audit_evidence(evidence(),quote)
    assert a["decision"]=="UNVERIFIED"
    assert "QUOTE_SUPPLIED_BY_CALLER_NOT_ATTESTED" in a["reasons"]

def test_missing_fields_and_bad_numbers_fail_closed():
    assert audit_evidence({})["decision"]=="UNVERIFIED"
    with pytest.raises(ValueError):
        audit_evidence({**evidence(),"equity":float("nan")})

def test_council_api_cannot_promote_claimed_data():
    c=TestClient(app)
    r=c.post("/api/paper/council",json=council_evidence())
    assert r.status_code==200,r.text
    assert r.json()["consensus"]=="REJECT"
    assert r.json()["evidence_audit"]["decision"]=="UNVERIFIED"
    assert r.json()["live_orders_enabled"] is False

def test_local_latency_instrumentation():
    measurements=[0.0,0.02]
    obs=LatencyObservatory(clock=lambda: measurements.pop(0))
    assert obs.measure("council_decision",lambda:"ok")=="ok"
    s=obs.summary()
    assert s["observations"]==1
    assert s["p95_ms"]==20.0
    assert s["exchange_network_latency_measured"] is False
    with pytest.raises(ValueError):
        obs.measure("broker_order",lambda:None)

def test_latency_endpoint_not_orders():
    c=TestClient(app)
    r=c.get("/api/latency")
    assert r.status_code==200
    assert r.json()["orders_enabled"] is False
