"""Desktop launcher and local-only server safety checks."""
from pathlib import Path
from portable.desktop import configure, select_port
from fastapi.testclient import TestClient
from nexus.app import app

def test_configure_uses_private_directory(tmp_path, monkeypatch):
    dest = tmp_path / "private-data"
    monkeypatch.setenv("NEXUS_PORTABLE_DATA", str(dest))
    root, storage = configure(tmp_path)
    assert root == tmp_path
    assert storage == dest
    assert storage.is_dir()

def test_select_port_is_local_and_valid():
    port = select_port(8000)
    assert 1024 <= port <= 65535

def test_no_banking_or_live_orders():
    client = TestClient(app)
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["money_movement_enabled"] is False
    assert res.json()["live_orders_enabled"] is False
