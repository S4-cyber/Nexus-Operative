from fastapi.testclient import TestClient
from nexus.app import app
from nexus.safety_benchmark import benchmark

def test_fixed_challenges_detected_and_no_orders():
    d=benchmark()
    assert d["mode"]=="FIXED_SYNTHETIC_SAFETY_BENCHMARK"
    assert d["evaluated"]==10
    assert d["caught"]==10
    assert d["all_hazards_detected"] is True
    assert d["unverified_control_blocked"] is True
    assert d["control_has_no_specialist_vetoes"] is True
    assert d["advisory_fixture_abstains"] is True
    assert d["execution_authorized"] is False

def test_repaired_dashboard_has_one_document_and_script():
    page=TestClient(app).get("/portal").text
    assert page.count("<script>")==1
    assert page.count("</script>")==1
    assert page.count("</html>")==1
    assert page.rstrip().endswith("</html>")
    for control in ("activate","halt","safetybenchmark","modeldiagnostic",
                    "publicrefresh","paperreconcile","tournament","matrix-hangar"):
        assert 'id="'+control+'"' in page

def test_benchmark_api_discloses_synthetic_nature():
    c=TestClient(app)
    r=c.get("/api/paper/safety-benchmark")
    assert r.status_code==200,r.text
    d=r.json()
    assert d["live_trading_enabled"] is False
    assert "synthetic" in d["mode"].lower()
    assert c.get("/api/status").json()["live_orders_enabled"] is False
