from fastapi.testclient import TestClient
from nexus.app import app

def test_matrix_is_explicitly_synthetic():
    c=TestClient(app)
    r=c.get("/api/matrix")
    assert r.status_code==200
    data=r.json()
    assert data["mode"]=="SYNTHETIC_TELEMETRY"
    assert data["live_orders"] is False
    assert len(data["events"])==3
    assert all(x["synthetic"] is True for x in data["events"])

def test_matrix_ui_is_bundled():
    html=TestClient(app).get("/portal").text
    assert 'id="matrix-hangar"' in html
    assert 'id="matrix-rain"' in html
    assert 'id="market-grid"' in html
    assert "loadMarkets()" in html
    assert "matrixUpdate()" in html
