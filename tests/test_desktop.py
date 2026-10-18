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


def test_explicit_headless_mode_never_opens_browser_or_native_window(tmp_path,monkeypatch):
    from portable import desktop
    import types
    import uvicorn
    calls=[];server=types.SimpleNamespace(should_exit=False,run=lambda:None)
    monkeypatch.setattr(desktop,'configure',lambda:(tmp_path,tmp_path))
    monkeypatch.setattr(desktop,'select_port',lambda _:18000)
    monkeypatch.setattr(desktop,'wait_for_ready',lambda _:True)
    monkeypatch.setattr(uvicorn,'Server',lambda _:server)
    class Worker:
        def __init__(self,**kw):pass
        def start(self):calls.append('start')
        def join(self,timeout=None):calls.append(('join',timeout))
    monkeypatch.setattr(desktop.threading,'Thread',Worker)
    monkeypatch.setattr(desktop.webbrowser,'open',lambda _:(_ for _ in ()).throw(AssertionError('UNEXPECTED_UI')))
    monkeypatch.setenv('NEXUS_HEADLESS_MODE','1');monkeypatch.setenv('NEXUS_BROWSER_MODE','1')
    desktop.main([])
    assert server.should_exit and calls==['start',('join',None),('join',8)]
