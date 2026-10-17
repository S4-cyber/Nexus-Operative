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

def test_matrix_live_chart_is_read_only_not_synthetic():
    html=TestClient(app).get("/portal").text
    assert 'id="matrix-price-chart"' in html
    assert 'id="matrix-price-caption"' in html
    assert "sampleMatrixPrice()" in html
    assert "/api/public-quote/" in html
    assert "Unverified or stale ticker" in html
    assert "No simulated replacement data" in html
    assert "observed exchange ticks" in html

def test_halt_reset_controls_are_explicit():
    html=TestClient(app).get("/portal").text
    assert 'id="reset-halt"' in html
    assert "window.confirm('Clear emergency halt?" in html
    assert "confirm_reset:true" in html
    assert "Trading stays DISENGAGED" in html

