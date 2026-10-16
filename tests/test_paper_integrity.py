import sqlite3
import time
import pytest
from nexus.paper_portfolio import initialize,buy,sell,reconcile,state

def quote(product="BTC-USD",bid=100,ask=101):
    return {"product":product,"provider":"coinbase_exchange_public","bid":bid,
            "ask":ask,"received_at":time.time(),"read_only":True,"orders_allowed":False}

def test_integrity_checked_before_new_paper_buy(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000)
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("UPDATE meta SET value='2000' WHERE key='cash'")
    with pytest.raises(ValueError,match="CASH_JOURNAL_DISAGREEMENT"):
        buy("BTC-USD",10,quote())
    result=reconcile({})
    assert result["reconciled"] is False
    assert "mismatch" in result["reason"].lower()

def test_abnormally_wide_spread_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000)
    with pytest.raises(ValueError,match="spread"):
        buy("BTC-USD",10,quote(bid=95,ask=105))
    assert state()["event_count"]==1

def test_reconciled_cash_after_roundtrip(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    buy("BTC-USD",100,quote())
    assert reconcile({"BTC-USD":quote()})["reconciled"] is True
    sell("BTC-USD",quote(bid=102,ask=103))
    result=reconcile({})
    assert result["reconciled"] is True
    assert result["event_count"]==3
