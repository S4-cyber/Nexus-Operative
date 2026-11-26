import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.paper_portfolio import initialize,buy,sell
from nexus.portfolio_governor import audit_portfolio
import nexus.portfolio_governor as governor

def q(product="BTC-USD",bid=100,ask=101):
    return {"product":product,"provider":"coinbase_exchange_public","bid":bid,"ask":ask,
        "received_at":time.time(),"read_only":True,"orders_allowed":False}

def test_initialization_passes_governor(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    assert audit_portfolio()["integrity_ok"] is False
    initialize(10000)
    assert audit_portfolio()["integrity_ok"] is True
    buy("BTC-USD",100,q())
    assert audit_portfolio()["integrity_ok"] is True
    sell("BTC-USD",q(bid=102,ask=103))
    assert audit_portfolio()["integrity_ok"] is True

def test_position_tampering_blocks_paper_buy(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    buy("BTC-USD",100,q())
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("UPDATE positions SET quantity='100000' WHERE product='BTC-USD'")
    report=audit_portfolio()
    assert not report["integrity_ok"]
    assert "POSITION_EXCEEDS_LIMIT" in report["reasons"]
    with pytest.raises(ValueError,match="governor veto"):
        buy("ETH-USD",50,q("ETH-USD"))
    assert report["orders_enabled"] is False

def test_modified_journal_and_account_rejected(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(2000)
    with sqlite3.connect(tmp_path/"paper_portfolio.sqlite3") as db:
        db.execute("UPDATE ledger SET cash_delta='3000' WHERE action='INIT'")
    report=audit_portfolio()
    assert "CASH_JOURNAL_DISAGREEMENT" in report["reasons"]
    assert "INVALID_INITIAL_ENTRY" in report["reasons"]
    assert not report["integrity_ok"]

def test_api_audit_never_enables_orders(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    c.post("/api/paper/portfolio/init",json={"paper_capital":1000})
    audit=c.get("/api/paper/portfolio/integrity")
    assert audit.status_code==200
    assert audit.json()["integrity_ok"] is True
    assert audit.json()["orders_enabled"] is False
    assert c.get("/api/status").json()["live_orders_enabled"] is False


def test_buy_governor_and_mutation_share_one_reserved_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000);path=tmp_path/"paper_portfolio.sqlite3"
    original=governor.require_safe_ledger;blocked=[]

    def probe(connection=None):
        assert connection is not None and connection.in_transaction
        report=original(connection)
        with sqlite3.connect(path,timeout=.05) as attacker:
            attacker.execute('PRAGMA busy_timeout=50')
            with pytest.raises(sqlite3.OperationalError):
                attacker.execute('DELETE FROM chain_seals')
            blocked.append(True)
        return report

    monkeypatch.setattr(governor,'require_safe_ledger',probe)
    result=buy('BTC-USD',10,q())
    assert blocked==[True]
    assert result['event_count']==2
    assert audit_portfolio()['integrity_ok'] is True


def test_preexisting_missing_seal_vetoes_buy_without_mutation(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    before=initialize(1000);path=tmp_path/"paper_portfolio.sqlite3"
    with sqlite3.connect(path) as db:db.execute('DELETE FROM chain_seals')
    with pytest.raises(ValueError,match='governor veto'):
        buy('BTC-USD',10,q())
    after=governor.audit_portfolio()
    assert before['event_count']==1
    assert after['integrity_ok'] is False
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]==0


def test_corrupt_portfolio_still_allows_reducing_sell_exit(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000);buy('BTC-USD',10,q())
    path=tmp_path/"paper_portfolio.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM chain_seals WHERE ledger_id=2')
    result=sell('BTC-USD',q(bid=102,ask=103))
    assert result['positions']==[] and result['event_count']==3
    report=audit_portfolio()
    assert report['integrity_ok'] is False
    assert 'HASH_CHAIN_LENGTH_MISMATCH' in report['reasons']


def test_standalone_audit_holds_one_read_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000);path=tmp_path/"paper_portfolio.sqlite3"
    original=governor.verify_seals;blocked=[]

    def probe(connection):
        with sqlite3.connect(path,timeout=.05) as attacker:
            attacker.execute('PRAGMA busy_timeout=50')
            attacker.execute('DELETE FROM chain_seals')
            with pytest.raises(sqlite3.OperationalError):attacker.commit()
            attacker.rollback();blocked.append(True)
        return original(connection)

    monkeypatch.setattr(governor,'verify_seals',probe)
    report=audit_portfolio()
    assert blocked==[True]
    assert report['integrity_ok'] is True
