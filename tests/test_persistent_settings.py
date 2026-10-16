"""Persistent settings and fail-closed paper allocation tests."""
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.state import load_settings, save_settings

def test_settings_persist_across_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA", str(tmp_path))
    values={"paper_budget":12500.0,"terrestrial_allocation":60,"solar_allocation":30,"universal_allocation":10}
    assert save_settings(values)==values
    assert load_settings()==values
    assert (tmp_path/"portal_settings.sqlite3").exists()

def test_invalid_allocation_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    with pytest.raises(ValueError):
        save_settings({"paper_budget":1000,"terrestrial_allocation":60,"solar_allocation":60,"universal_allocation":10})
    with pytest.raises(ValueError):
        save_settings({"paper_budget":-3,"terrestrial_allocation":60,"solar_allocation":30,"universal_allocation":10})

def test_api_settings_and_no_live_money(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    assert c.put("/api/paper/settings",json={"paper_budget":2500,"terrestrial_allocation":50,"solar_allocation":35,"universal_allocation":15}).status_code==200
    assert c.get("/api/paper/settings").json()["paper_budget"]==2500
    assert c.get("/api/status").json()["live_orders_enabled"] is False
    assert c.get("/api/status").json()["money_movement_enabled"] is False
