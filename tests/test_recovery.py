import sqlite3
import os
import shutil
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
    assert receipt["integrity_check"]=="SQLITE_QUICK_CHECK_OK"
    assert receipt["portfolio_integrity_check"]=="LOCAL_HASH_CHAIN_AND_ACCOUNTING_OK"
    assert receipt["content_flushed"] is True
    assert receipt["directory_entry_synced"] is (os.name!="nt")
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

def test_backup_integrity_failure_is_redacted_and_removes_candidate(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    verify=recovery._verify_snapshot
    def corrupt_then_verify(path,identity):
        with open(path,'r+b',buffering=0) as stream:
            stream.seek(0);stream.write(b'not a sqlite image')
        verify(path,identity)
    monkeypatch.setattr(recovery,"_verify_snapshot",corrupt_then_verify)
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_INTEGRITY_REVIEW_REQUIRED"
    assert not list((tmp_path/"recovery_snapshots").iterdir())

def test_backup_refuses_logically_inconsistent_portfolio_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    source=tmp_path/"paper_portfolio.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("UPDATE ledger SET cash_delta='9999' WHERE action='INIT'")
    before=source.read_bytes()
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_PORTFOLIO_INTEGRITY_REVIEW_REQUIRED"
    assert source.read_bytes()==before
    assert not list((tmp_path/"recovery_snapshots").iterdir())

def test_snapshot_flush_syncs_file_and_supported_directory(tmp_path,monkeypatch):
    path=tmp_path/'snapshot.sqlite3';path.write_bytes(b'snapshot')
    identity=recovery._regular_identity(path,'DESTINATION')
    calls=[];monkeypatch.setattr(recovery.os,'fsync',lambda descriptor:calls.append(descriptor))
    assert recovery._flush_snapshot(path,identity) is (os.name!='nt')
    assert len(calls)==(1 if os.name=='nt' else 2)

def test_backup_capacity_refuses_without_pruning(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    receipt=backup_snapshot();snapshot=tmp_path/"recovery_snapshots"/receipt["filename"]
    before=snapshot.read_bytes()
    monkeypatch.setattr(recovery,"MAX_RETAINED_SNAPSHOTS",1)
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_CAPACITY_REVIEW_REQUIRED"
    assert list((tmp_path/"recovery_snapshots").iterdir())==[snapshot]
    assert snapshot.read_bytes()==before

def test_backup_source_and_free_space_caps_fail_before_candidate(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    monkeypatch.setattr(recovery,"MAX_SOURCE_BYTES",1)
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_SOURCE_CAPACITY_REVIEW_REQUIRED"
    monkeypatch.setattr(recovery,"MAX_SOURCE_BYTES",512*1024*1024)
    usage=shutil.disk_usage(tmp_path)
    monkeypatch.setattr(recovery.shutil,"disk_usage",lambda _path:usage._replace(free=0))
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_FREE_SPACE_REVIEW_REQUIRED"
    assert not list((tmp_path/"recovery_snapshots").iterdir())

def test_backup_rejects_retained_snapshot_links_and_sidecars(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    directory=tmp_path/"recovery_snapshots";directory.mkdir()
    external=tmp_path/"external.sqlite3";external.write_bytes(b"retained")
    link=directory/"paper_snapshot_1.sqlite3"
    try:link.symlink_to(external)
    except OSError:pytest.skip("symlink creation unavailable")
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_RETAINED_SYMLINK"
    assert external.read_bytes()==b"retained"
    link.unlink();sidecar=directory/"paper_snapshot_1.sqlite3-journal"
    sidecar.write_bytes(b"pending")
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_RETAINED_SIDECAR_REVIEW_REQUIRED"
    assert sidecar.read_bytes()==b"pending"

def test_backup_aggregate_and_directory_entry_caps_preserve_files(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    directory=tmp_path/"recovery_snapshots";directory.mkdir()
    retained=directory/"paper_snapshot_1.sqlite3";retained.write_bytes(b"retained")
    monkeypatch.setattr(recovery,"MAX_RETAINED_SNAPSHOT_BYTES",1)
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_CAPACITY_REVIEW_REQUIRED"
    assert retained.read_bytes()==b"retained"
    retained.unlink();first=directory/"operator-note";second=directory/"operator-review"
    first.write_bytes(b"first");second.write_bytes(b"second")
    monkeypatch.setattr(recovery,"MAX_RETAINED_SNAPSHOT_BYTES",4*1024*1024*1024)
    monkeypatch.setattr(recovery,"MAX_SNAPSHOT_DIRECTORY_ENTRIES",1)
    response=TestClient(app).post("/api/paper/portfolio/snapshot")
    assert response.status_code==503
    assert response.json()["detail"]=="PAPER_SNAPSHOT_DIRECTORY_CAPACITY_REVIEW_REQUIRED"
    assert first.read_bytes()==b"first" and second.read_bytes()==b"second"

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
