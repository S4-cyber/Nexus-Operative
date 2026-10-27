import time
import sqlite3
from contextlib import closing
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
import nexus.paper_portfolio as portfolio
from nexus.paper_portfolio import initialize,buy,sell,state,reconcile
from nexus.strategy_branches import available_branches

def quote(product="BTC-USD",bid=100,ask=101):
    return {"product":product,"provider":"coinbase_exchange_public","bid":bid,"ask":ask,
            "received_at":time.time(),"read_only":True,"orders_allowed":False}

def test_paper_ledger_survives_new_connection(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(10000)
    r=buy("BTC-USD",100,quote())
    assert r["positions"][0]["product"]=="BTC-USD"
    assert r["live_orders_enabled"] is False
    assert state()["event_count"]==2
    marked=reconcile({"BTC-USD":quote()})
    assert marked["reconciled"] is True
    r=sell("BTC-USD",quote(bid=102,ask=103))
    assert len(r["positions"])==0
    assert r["event_count"]==3
    assert (tmp_path/"paper_portfolio.sqlite3").exists()

def test_fails_closed_missing_quotes_and_position_cap(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000)
    with pytest.raises(ValueError):buy("BTC-USD",30,quote())
    with pytest.raises(ValueError):buy("BTC-USD",10,quote(bid=105,ask=101))
    buy("BTC-USD",10,quote())
    assert reconcile({})["reconciled"] is False
    with pytest.raises(ValueError):initialize(1000)

def test_rejects_outdated_quote(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    initialize(1000)
    x=quote()
    x["received_at"]=time.time()-90
    with pytest.raises(ValueError):buy("BTC-USD",10,x)

def test_registry_is_never_authorized():
    b=available_branches()
    assert b["live_execution_enabled"] is False
    assert b["automated_bets_enabled"] is False
    assert {"options_defined_risk","event_contracts","spot_momentum"}.issubset({x["id"] for x in b["branches"]})

def test_portfolio_endpoints_remain_paper_only(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    assert c.post("/api/paper/portfolio/init",json={"paper_capital":1000}).status_code==200
    assert c.get("/api/paper/portfolio").json()["simulation_only"] is True
    assert c.get("/api/strategy-branches").json()["live_execution_enabled"] is False
    assert c.get("/api/status").json()["live_orders_enabled"] is False



def test_passive_state_and_http_do_not_initialize_missing_accounting(tmp_path,monkeypatch):
    root=tmp_path/'absent';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    missing=state()
    assert missing['initialized'] is False and missing['cash'] is None and missing['positions']==[]
    assert not root.exists()
    assert TestClient(app).get('/api/paper/portfolio').json()==missing
    assert not root.exists()
    initialize(1000)
    path=root/'paper_portfolio.sqlite3';before=path.read_bytes()
    assert state()['cash']==1000 and state()['initialized']
    assert path.read_bytes()==before


def test_passive_state_refuses_corrupt_or_incomplete_history_without_repair(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));path=tmp_path/'paper_portfolio.sqlite3'
    path.write_bytes(b'corrupt fixture');before=path.read_bytes()
    with pytest.raises(sqlite3.Error):state()
    response=TestClient(app).get('/api/paper/portfolio')
    assert response.status_code==503 and response.json()=={'detail':'PAPER_PORTFOLIO_UNAVAILABLE'}
    assert path.read_bytes()==before
    path.unlink()
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE unrelated (value TEXT)')
    before=path.read_bytes()
    with pytest.raises(sqlite3.Error):state()
    assert path.read_bytes()==before


def test_writer_contention_is_bounded_redacted_and_leaves_portfolio_unchanged(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr(portfolio,'DB_BUSY_TIMEOUT_MS',50)
    initialize(1000);path=tmp_path/'paper_portfolio.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='^PAPER_PORTFOLIO_DATABASE_BUSY$'):
            buy('BTC-USD',10,quote())
        assert path.read_bytes()==before
    assert buy('BTC-USD',10,quote())['positions'][0]['product']=='BTC-USD'


def test_reader_blocked_final_commit_is_bounded_and_rolls_back_portfolio(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr(portfolio,'DB_BUSY_TIMEOUT_MS',50)
    initialize(1000);path=tmp_path/'paper_portfolio.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN')
        blocker.execute('SELECT count(*) FROM ledger').fetchone()
        with pytest.raises(ValueError,match='^PAPER_PORTFOLIO_DATABASE_BUSY$'):
            buy('BTC-USD',10,quote())
        assert path.read_bytes()==before
    assert buy('BTC-USD',10,quote())['event_count']==2


def test_exclusive_contention_is_bounded_and_read_snapshot_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr(portfolio,'DB_BUSY_TIMEOUT_MS',50)
    expected=initialize(1000);path=tmp_path/'paper_portfolio.sqlite3';before=path.read_bytes()
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^PAPER_PORTFOLIO_DATABASE_BUSY$'):
            state()
        assert path.read_bytes()==before
    assert state()==expected
