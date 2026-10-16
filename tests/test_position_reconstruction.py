import sqlite3
import time
import pytest
from nexus.paper_portfolio import initialize,buy,sell
from nexus.portfolio_governor import audit_portfolio

def quote(product="BTC-USD",bid=100,ask=101):
    return {"product":product,"provider":"coinbase_exchange_public","bid":bid,"ask":ask,
        "received_at":time.time(),"read_only":True,"orders_allowed":False}

def test_tamper_small_quantity_detected(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    buy("BTC-USD",100,quote())
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("UPDATE positions SET quantity=CAST(quantity AS REAL)*0.8")
    r=audit_portfolio()
    assert "POSITION_RECONSTRUCTION_MISMATCH" in r["reasons"]
    with pytest.raises(ValueError,match="governor veto"):
        buy("ETH-USD",50,quote("ETH-USD"))

def test_deleted_event_detected(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000);buy("BTC-USD",100,quote())
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("DELETE FROM position_events")
    assert "MISSING_POSITION_EVENTS" in audit_portfolio()["reasons"]

def test_normal_buy_sell_reconstructs(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    buy("BTC-USD",100,quote())
    assert audit_portfolio()["integrity_ok"]
    sell("BTC-USD",quote(bid=102,ask=103))
    assert audit_portfolio()["integrity_ok"]
