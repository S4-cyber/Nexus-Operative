import sqlite3
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.historical_candles import collect
from nexus.candle_research import review,evaluate

BASE=3600*1000
def bars(count=125, gap=None):
    return [[BASE+i*3600,90,110,100,101,10] for i in range(count) if i!=gap]

def seed(rows):
    collect("BTC-USD",3600,now=BASE+126*3600,transport=lambda _:rows)

def test_clean_history_still_cannot_authorize_trading(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed(bars())
    result=review("BTC-USD",3600,125)
    assert result["research_eligible"] is True
    assert len(result["closes"])==125
    evaluated=evaluate("BTC-USD",10000,3600,125)
    assert evaluated["decision"]=="RESEARCH_ONLY_NOT_APPROVED"
    assert evaluated["live_orders_enabled"] is False
    assert evaluated["paper_autotrading_enabled"] is False
    assert evaluated["training_selection"]["id"]

def test_missing_intervals_fail_closed(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed(bars(gap=10))
    r=evaluate("BTC-USD",10000,3600,125)
    assert r["decision"]=="DATA_REJECTED"
    assert "MISSING_BAR_INTERVAL" in r["reasons"]
    assert "holdout" not in r

def test_revised_or_tampered_data_are_not_admitted(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed(bars())
    revised=bars()
    revised[10][4]=102
    seed(revised)
    assert "REVISED_BAR" in review("BTC-USD",3600,125)["reasons"]
    with sqlite3.connect(tmp_path/"research_candles.sqlite3") as db:
        db.execute("UPDATE candles SET payload='{}' WHERE starts_at=?",(BASE+20*3600,))
    assert "CORRUPTED_BAR" in review("BTC-USD",3600,125)["reasons"]

def test_api_and_empty_history_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    d=c.post("/api/candle-research/BTC-USD/evaluate").json()
    assert d["decision"]=="DATA_REJECTED"
    assert d["live_orders_enabled"] is False
    assert c.get("/api/candle-research/BTC-USD/admission").status_code==200
    assert c.post("/api/candle-research/INVALID/evaluate").status_code==422
    assert c.get("/api/status").json()["live_orders_enabled"] is False
