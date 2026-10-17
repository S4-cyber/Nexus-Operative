import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.advisory_benchmark import benchmark
from nexus.model_adapters import validate_response

def test_all_contract_cases_and_disagreement_governor():
    report=benchmark()
    assert report["evaluated"] == 24
    assert report["passed"] == 24
    assert len({item["case"] for item in report["cases"]}) == 24
    assert report["all_contract_cases_passed"] is True
    assert report["disagreement_has_no_trade_authority"] is True
    assert report["external_models_called"] is False
    assert report["trade_approval"] is False
    assert report["live_orders_enabled"] is False

def test_unknown_and_null_capability_fields_are_rejected():
    base={"role":"market_regime","verdict":"ABSTAIN","reason":"Not enough evidence",
          "estimated_cost_usd":0}
    assert validate_response(base,"market_regime")["verdict"]=="ABSTAIN"
    for extra in ({"order":None},{"credentials":None},{"tool_calls":[]},
                  {"execute":False},{"account_id":None}):
        with pytest.raises(ValueError):
            validate_response({**base,**extra},"market_regime")

def test_read_only_offline_benchmark_endpoint_and_ui():
    client=TestClient(app)
    report=client.get("/api/paper/ai-provider/advisory-benchmark")
    assert report.status_code==200
    assert report.json()["all_contract_cases_passed"] is True
    assert report.json()["external_models_called"] is False
    html=client.get("/portal").text
    assert 'id="advisorybenchmark"' in html
    assert "Check AI Safety Rules (Offline)" in html
    assert client.get("/api/status").json()["live_orders_enabled"] is False


def test_legacy_unreviewed_ai_research_is_forbidden(monkeypatch):
    from nexus import app as app_module
    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy endpoint must never call billable provider")
    monkeypatch.setattr("nexus.provider_research.request", forbidden)
    client = TestClient(app)
    response = client.post("/api/paper/ai-provider/market_regime/research", json={"private": "unreviewed"})
    assert response.status_code == 403
    assert "reviewed" in response.json()["detail"].lower()


def test_portal_describes_reviewed_paid_consent():
    html = TestClient(app).get("/portal").text
    assert "SHA-256 hash" in html
    assert "May Cost Money" in html
    assert "If the public dataset is not eligible or changes" in html
