from fastapi.testclient import TestClient
from nexus.app import app

def test_optional_connector_controls_are_visible_with_clear_consent():
    page=TestClient(app).get("/portal")
    assert page.status_code==200
    html=page.text
    for identifier in ("aistatus","aiguide","aitest","aiconnectorstatus","aisetup"):
        assert f'id="{identifier}"' in html
    assert "May Cost Money" in html
    assert "No connection or paid requests are made automatically" in html
    assert "Never paste your API key" in html
    assert "window.confirm(" in html
    assert "refreshAISetup();" in html

def test_setup_status_does_not_trigger_paid_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.delenv("NEXUS_ENABLE_LLM_RESEARCH",raising=False)
    client=TestClient(app)
    report=client.get("/api/paper/ai-provider/status").json()
    assert report["configured"] is False
    assert report["provider_connected"] is False
    assert report["live_orders_enabled"] is False
