import sqlite3
import time
import pytest
from nexus.paper_portfolio import initialize,buy,sell
from nexus.portfolio_governor import audit_portfolio

def quote(product="BTC-USD",bid=100,ask=101):
    return {"product":product,"provider":"coinbase_exchange_public",
      "bid":bid,"ask":ask,"received_at":time.time(),"read_only":True,"orders_allowed":False}

def test_new_paper_ledger_is_sealed(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    buy("BTC-USD",100,quote())
    sell("BTC-USD",quote(bid=102,ask=103))
    assert audit_portfolio()["integrity_ok"] is True
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM chain_seals").fetchone()[0]==3

def test_cash_delta_edit_detected_even_if_cash_also_updated(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("UPDATE ledger SET cash_delta='11000' WHERE action='INIT'")
        db.execute("UPDATE meta SET value='11000' WHERE key='cash'")
        db.execute("UPDATE meta SET value='11000' WHERE key='initial'")
    d=audit_portfolio()
    assert not d["integrity_ok"]
    assert "HASH_CHAIN_DIGEST_MISMATCH" in d["reasons"]

def test_removed_seal_blocks_subsequent_paper_buy(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("DELETE FROM chain_seals")
    d=audit_portfolio()
    assert "HASH_CHAIN_LENGTH_MISMATCH" in d["reasons"]
    with pytest.raises(ValueError,match="governor veto"):
        buy("BTC-USD",50,quote())

def test_legacy_unsealed_portfolio_never_auto_migrates(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("DELETE FROM meta WHERE key='hash_chain_version'")
    assert "LEGACY_UNSEALED_JOURNAL" in audit_portfolio()["reasons"]
