import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest
import nexus.tick_history as tick_history
from nexus.app import app
from nexus.tick_history import capture_tick,dataset

def quote(at=1000,last=100):
    stamp=datetime.fromtimestamp(at,timezone.utc).isoformat()
    return {"product":"BTC-USD","provider":"coinbase_exchange_public","exchange_time":stamp,
            "received_at":at+1,"bid":last-.1,"ask":last+.1,"last":last,
            "read_only":True,"orders_allowed":False}

def test_chronological_and_duplicate_timestamps(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    assert capture_tick("BTC-USD",reader=lambda _:quote(1000),clock=lambda:1001)["stored"]
    assert not capture_tick("BTC-USD",reader=lambda _:quote(1000),clock=lambda:1002)["stored"]
    assert capture_tick("BTC-USD",reader=lambda _:quote(1030),clock=lambda:1031)["stored"]
    result=dataset("BTC-USD",gap_seconds=60)
    assert result["sample_count"]==2
    assert result["issues"]==[]
    assert result["suitable_for_backtesting"] is False
    assert result["ticks"][0]["exchange_at"]<result["ticks"][1]["exchange_at"]

def test_gap_and_altered_payload(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    capture_tick("BTC-USD",reader=lambda _:quote(1000),clock=lambda:1001)
    capture_tick("BTC-USD",reader=lambda _:quote(1200),clock=lambda:1201)
    assert "MISSING_SAMPLE_INTERVAL" in dataset("BTC-USD",gap_seconds=60)["issues"]
    with sqlite3.connect(tmp_path/"chronological_quotes.sqlite3") as db:
        db.execute("UPDATE ticks SET payload='{}' WHERE id=2")
    result=dataset("BTC-USD")
    assert "TICK_INTEGRITY_ERROR" in result["issues"]
    assert result["ticks"][-1]["valid"] is False

def test_invalid_quote_and_api_remain_read_only(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    bad=quote(1000);bad["exchange_time"]="invalid"
    try:
        capture_tick("BTC-USD",reader=lambda _:bad,clock=lambda:1001)
        assert False,"invalid timestamp accepted"
    except ValueError:pass
    c=TestClient(app)
    assert c.post("/api/research-ticks/INVALID/capture").status_code==404
    assert c.get("/api/research-ticks/BTC-USD?limit=1").status_code==422
    assert c.get("/api/research-ticks/BTC-USD").json()["live_orders_enabled"] is False

def test_tick_writer_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(tick_history,'DB_BUSY_TIMEOUT_MS',50)
    capture_tick("BTC-USD",reader=lambda _:quote(1000),clock=lambda:1001)
    path=tmp_path/'chronological_quotes.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='^TICK_HISTORY_DATABASE_BUSY$'):
            capture_tick("BTC-USD",reader=lambda _:quote(1030),clock=lambda:1031)
        assert path.read_bytes()==before
    assert capture_tick("BTC-USD",reader=lambda _:quote(1030),clock=lambda:1031)['stored']

def test_tick_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(tick_history,'DB_BUSY_TIMEOUT_MS',50)
    capture_tick("BTC-USD",reader=lambda _:quote(1000),clock=lambda:1001)
    path=tmp_path/'chronological_quotes.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN')
        blocker.execute('SELECT count(*) FROM ticks').fetchone()
        with pytest.raises(ValueError,match='^TICK_HISTORY_DATABASE_BUSY$'):
            capture_tick("BTC-USD",reader=lambda _:quote(1030),clock=lambda:1031)
        assert path.read_bytes()==before
    assert capture_tick("BTC-USD",reader=lambda _:quote(1030),clock=lambda:1031)['stored']

def test_tick_exclusive_read_contention_is_bounded_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(tick_history,'DB_BUSY_TIMEOUT_MS',50)
    capture_tick("BTC-USD",reader=lambda _:quote(1000),clock=lambda:1001)
    path=tmp_path/'chronological_quotes.sqlite3';expected=dataset('BTC-USD');before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^TICK_HISTORY_DATABASE_BUSY$'):
            dataset('BTC-USD')
        assert path.read_bytes()==before
    assert dataset('BTC-USD')==expected
