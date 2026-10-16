from fastapi.testclient import TestClient
from nexus.app import app
from nexus.onboarding import inventory

def test_zero_setup_inventory_does_not_create_files(tmp_path,monkeypatch):
    folder=tmp_path/"not_yet_created"
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.delenv("NEXUS_ENABLE_LLM_RESEARCH",raising=False)
    report=inventory(folder)
    assert report["onboarding_state"]=="READY_FOR_OFFLINE_RESEARCH"
    assert report["offline_research"]["setup_required"] is False
    assert report["optional_ai"]["configured"] is False
    assert report["defaults_safety"]["live_orders_enabled"] is False
    assert report["public_market_data"]["live_connection_tested"] is False
    assert report["paper_storage"]["configured_directory"] is False
    assert not folder.exists()

def test_readiness_endpoint_and_ui_do_not_require_paid_provider(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.delenv("NEXUS_ENABLE_LLM_RESEARCH",raising=False)
    client=TestClient(app)
    report=client.get("/api/onboarding/readiness")
    assert report.status_code==200
    assert report.json()["paper_storage"]["write_permission_indicated"] is True
    assert report.json()["defaults_safety"]["external_ai_enabled_automatically"] is False
    html=client.get("/portal").text
    assert 'id="readinesscheck"' in html
    assert "api('/api/onboarding/readiness')" in html
    assert "does not connect accounts" in html
