import sqlite3
import time
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.paper_portfolio import initialize,buy
from nexus.recovery import recovery_report,backup_snapshot

def quote():
    return {"product":"BTC-USD","provider":"coinbase_exchange_public","bid":100,
            "ask":101,"received_at":time.time(),"read_only":True,"orders_allowed":False}

def test_recovery_detects_legacy_without_migrating(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("DELETE FROM meta WHERE key='position_journal_version'")
    report=recovery_report()
    assert report["status"]=="LEGACY_REQUIRES_MANUAL_REVIEW"
    assert report["migration_performed"] is False
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        assert db.execute("SELECT value FROM meta WHERE key='position_journal_version'").fetchone() is None

def test_backup_is_consistent_and_original_unchanged(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    buy("BTC-USD",100,quote())
    before=(tmp_path/"paper_portfolio.sqlite3").stat().st_size
    receipt=backup_snapshot()
    assert len(receipt["sha256"])==64
    assert receipt["source_modified"] is False
    with sqlite3.connect(tmp_path/"recovery_snapshots"/receipt["filename"]) as db:
        assert db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]==2
        assert db.execute("SELECT COUNT(*) FROM chain_seals").fetchone()[0]==2
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]==2

def test_recovery_api_never_authorizes_or_resets(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    client=TestClient(app)
    assert client.get("/api/paper/portfolio/recovery-report").json()["status"]=="UNINITIALIZED"
    assert client.post("/api/paper/portfolio/init",json={"paper_capital":1000}).status_code==200
    receipt=client.post("/api/paper/portfolio/snapshot")
    assert receipt.status_code==200
    assert receipt.json()["orders_enabled"] is False
    assert client.get("/api/paper/portfolio/recovery-report").json()["status"]=="VERIFIED_LOCAL_STRUCTURE"
    assert client.get("/api/status").json()["live_orders_enabled"] is False
