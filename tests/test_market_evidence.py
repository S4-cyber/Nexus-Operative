from fastapi.testclient import TestClient
from contextlib import closing
import sqlite3
import pytest
import nexus.market_evidence as market_evidence
from nexus.app import app
from nexus.market_evidence import capture,recent,MAX_ROWS

def cb(_):
    return {"product":"BTC-USD","provider":"coinbase_exchange_public",
      "bid":99.9,"ask":100.1,"received_at":1000,
      "read_only":True,"orders_allowed":False}
def kr(_):
    return {"product":"BTC-USD","provider":"kraken_spot_public",
      "bid":"99.9","ask":"100.1","received_at":1001,
      "read_only":True,"orders_allowed":False,"has_exchange_timestamp":False}

def test_capture_does_not_authorize_trading(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    report=capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1002)
    assert report["stored_locally"] is True
    assert report["trade_approval"] is False
    assert report["exchange_timestamps_verified"] is False
    audit=recent("BTC-USD")
    assert audit["observations"][0]["local_record_integrity"] is True
    assert audit["independent_price_verification"] is False

def test_tamper_detection_and_restricted_inputs(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1002)
    with sqlite3.connect(tmp_path/"market_evidence.sqlite3") as db:
        db.execute("UPDATE observations SET details='{}'")
    assert recent()["observations"][0]["decision"]=="VETO"
    client=TestClient(app)
    assert client.post("/api/market-evidence/NOT-REAL/capture").status_code==404
    assert client.get("/api/market-evidence?limit=0").status_code==422
    assert client.get("/api/status").json()["live_orders_enabled"] is False

def test_evidence_writer_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(market_evidence,'DB_BUSY_TIMEOUT_MS',50)
    capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1002)
    path=tmp_path/'market_evidence.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='^MARKET_EVIDENCE_DATABASE_BUSY$'):
            capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1003)
        assert path.read_bytes()==before
    assert capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1003)['stored_locally']

def test_evidence_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(market_evidence,'DB_BUSY_TIMEOUT_MS',50)
    capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1002)
    path=tmp_path/'market_evidence.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN')
        blocker.execute('SELECT count(*) FROM observations').fetchone()
        with pytest.raises(ValueError,match='^MARKET_EVIDENCE_DATABASE_BUSY$'):
            capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1003)
        assert path.read_bytes()==before
    assert capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1003)['stored_locally']

def test_evidence_exclusive_read_contention_is_bounded_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(market_evidence,'DB_BUSY_TIMEOUT_MS',50)
    capture("BTC-USD",coinbase_reader=cb,kraken_reader=kr,clock=lambda:1002)
    path=tmp_path/'market_evidence.sqlite3';expected=recent();before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^MARKET_EVIDENCE_DATABASE_BUSY$'):
            recent()
        assert path.read_bytes()==before
    assert recent()==expected
