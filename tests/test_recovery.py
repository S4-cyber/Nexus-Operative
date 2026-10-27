import sqlite3
import os
import stat
import time
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.paper_portfolio import initialize,buy
from nexus.recovery import recovery_report,backup_snapshot
import nexus.paper_portfolio as paper_portfolio
import nexus.recovery as recovery

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
    observed=(tmp_path/"recovery_snapshots"/receipt["filename"]).stat()
    assert stat.S_ISREG(observed.st_mode) and observed.st_nlink==1
    if os.name!="nt":
        assert stat.S_IMODE(observed.st_mode)&0o077==0

def test_backup_rejects_source_and_destination_links(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    source=tmp_path/"paper_portfolio.sqlite3"
    original=source.read_bytes()
    source.rename(tmp_path/"actual.sqlite3")
    try:source.symlink_to(tmp_path/"actual.sqlite3")
    except OSError:pytest.skip("symlink creation unavailable")
    try:backup_snapshot()
    except ValueError as error:assert str(error)=="PAPER_SNAPSHOT_SOURCE_SYMLINK"
    else:raise AssertionError("source symlink accepted")
    source.unlink();(tmp_path/"actual.sqlite3").rename(source)
    external=tmp_path/"external";external.mkdir()
    (tmp_path/"recovery_snapshots").symlink_to(external,target_is_directory=True)
    client=TestClient(app)
    response=client.post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_DIRECTORY_SYMLINK"
    assert not list(external.iterdir()) and source.read_bytes()==original

def test_backup_rejects_hardlinked_source(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    source=tmp_path/"paper_portfolio.sqlite3"
    os.link(source,tmp_path/"paper_portfolio_copy.sqlite3")
    try:backup_snapshot()
    except ValueError as error:
        assert str(error)=="PAPER_SNAPSHOT_SOURCE_HARDLINK_REVIEW_REQUIRED"
    else:raise AssertionError("hardlinked source accepted")
    assert not (tmp_path/"recovery_snapshots").exists()

def test_backup_contention_is_bounded_redacted_and_cleans_partial(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    monkeypatch.setattr(paper_portfolio,"DB_BUSY_TIMEOUT_MS",25)
    monkeypatch.setattr(recovery,"SNAPSHOT_BUSY_TIMEOUT_MS",25)
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3",timeout=.025) as holder:
        holder.execute("BEGIN EXCLUSIVE")
        started=time.monotonic()
        response=TestClient(app).post("/api/paper/portfolio/snapshot")
        elapsed=time.monotonic()-started
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_DATABASE_BUSY"
    assert elapsed<1
    directory=tmp_path/"recovery_snapshots"
    assert not directory.exists() or not list(directory.iterdir())

def test_backup_failure_removes_exclusive_partial(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    def fail(_source,_target):
        raise ValueError("PAPER_SNAPSHOT_DATABASE_BUSY")
    monkeypatch.setattr(recovery,"_backup_with_deadline",fail)
    try:backup_snapshot()
    except ValueError as error:assert str(error)=="PAPER_SNAPSHOT_DATABASE_BUSY"
    else:raise AssertionError("injected snapshot failure accepted")
    assert not list((tmp_path/"recovery_snapshots").iterdir())

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
