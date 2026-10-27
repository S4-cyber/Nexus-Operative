import time
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
import pytest
import nexus.observer as observer_module
from nexus.observer import ReadOnlyObserver,assess,history,persist
from fastapi.testclient import TestClient
from nexus.app import app

def quote(symbol):
    now=time.time()
    return {"product":symbol,"read_only":True,"orders_allowed":False,
        "received_at":now,"bid":100,"ask":100.05,"last":100.025}

def test_assess_veto_even_when_data_looks_valid():
    record=assess("BTC-USD",quote("BTC-USD"),[99]*6)
    assert record["decision"]=="VETO"
    assert record["live_order_authorized"] is False
    assert record["paper_order_authorized"] is False
    assert "NO_VERIFIED_NET_EDGE" in record["reasons"]
    assert record["regime"]=="UPWARD_MOVE"

def test_bad_data_fails_closed():
    bad=quote("BTC-USD");bad["received_at"]=time.time()-100
    assert assess("BTC-USD",bad,[])["decision"]=="VETO"
    assert "STALE_OR_INVALID_QUOTE" in assess("BTC-USD",bad,[])["reasons"]
    assert "INVALID_OR_MISSING_MARKET_QUOTE" in assess("BTC-USD",None,[])["reasons"]

def test_record_history_is_persistent_and_bounded(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    observed=ReadOnlyObserver(fetcher=quote)
    result=observed.single_cycle()
    assert len(result)==3
    saved=history()
    assert len(saved)==3
    assert all(x["decision"]=="VETO" for x in saved)
    assert observed.status()["active"] is False
    assert (tmp_path/"market_observations.sqlite3").exists()

def test_observer_public_endpoints_are_orderless(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    assert c.get("/api/observer/status").json()["live_order_authorized"] is False
    assert c.get("/api/observer/history").json()["orders_enabled"] is False
    assert c.get("/api/status").json()["live_orders_enabled"] is False

def _record():
    return assess('BTC-USD',quote('BTC-USD'),[99]*6)

def test_observer_writer_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(observer_module,'DB_BUSY_TIMEOUT_MS',50)
    persist(_record())
    path=tmp_path/'market_observations.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='^MARKET_OBSERVER_DATABASE_BUSY$'):
            persist(_record())
        assert path.read_bytes()==before
    persist(_record());assert len(history())==2

def test_observer_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(observer_module,'DB_BUSY_TIMEOUT_MS',50)
    persist(_record())
    path=tmp_path/'market_observations.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN')
        blocker.execute('SELECT count(*) FROM observations').fetchone()
        with pytest.raises(ValueError,match='^MARKET_OBSERVER_DATABASE_BUSY$'):
            persist(_record())
        assert path.read_bytes()==before
    persist(_record());assert len(history())==2

def test_observer_exclusive_read_contention_is_bounded_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(observer_module,'DB_BUSY_TIMEOUT_MS',50)
    persist(_record())
    path=tmp_path/'market_observations.sqlite3';expected=history();before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^MARKET_OBSERVER_DATABASE_BUSY$'):
            history()
        assert path.read_bytes()==before
    assert history()==expected
