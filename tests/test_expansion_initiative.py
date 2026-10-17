from fastapi.testclient import TestClient
from nexus.app import app
from nexus.expansion_initiative import roadmap, assess_evidence, REQUIRED

def strong_claim():
    return {"independent_sources":3,"fresh_quotes":1,"historical_samples":500,
            "spread_bps":3,"daily_usd_volume":10000000,"holdout_trades":80,
            "net_holdout_after_costs":True,"jurisdiction_eligible":True,
            "venue_eligible":True,"no_market_halt":True,
            "counterparty_reviewed":True,"risk_reviewed":True}

def test_expansion_covers_twelve_distinct_avenues_without_orders():
    result=roadmap()
    assert len(result["avenues"])>=12
    assert len({x["id"] for x in result["avenues"]})==len(result["avenues"])
    assert all(x["live_orders_enabled"] is False and x["paper_execution_approved"] is False for x in result["avenues"])
    assert result["autonomous_trading_enabled"] is False

def test_claims_never_approve_trading_even_when_strong():
    for avenue in ("spot_crypto","new_listings","options","futures","fx_spot"):
        result=assess_evidence(avenue,strong_claim())
        assert result["live_orders_enabled"] is False
        assert result["paper_execution_approved"] is False
        assert result["research_claim_validated"] is False
    assert "HIGH_RISK_QUARANTINE" in assess_evidence("new_listings",strong_claim())["blockers"]

def test_fail_closed_missing_bad_and_model_supplied_evidence():
    assert assess_evidence("us_equities",{})["screen_status"]=="INCOMPLETE"
    suspicious={**strong_claim(),"risk_reviewed":"true","spread_bps":float("nan")}
    r=assess_evidence("us_equities",suspicious)
    assert "UNVERIFIED_RISK_REVIEWED" in r["blockers"]
    assert "UNVERIFIED_SPREAD_BPS" in r["blockers"]
    try:assess_evidence("imaginary_market",strong_claim())
    except ValueError:pass
    else:assert False

def test_expansion_endpoints_and_portal():
    client=TestClient(app)
    r=client.get("/api/expansion/roadmap")
    assert r.status_code==200 and len(r.json()["avenues"])>=12
    assert client.post("/api/expansion/new_listings/screen",json=strong_claim()).json()["live_orders_enabled"] is False
    assert client.post("/api/expansion/not-real/screen",json={}).status_code==422
    html=client.get("/portal").text
    assert 'id="expansion-load"' in html
    assert "Explore New Trade Avenues" in html
    assert "byId('expansion-load').onclick" in html
