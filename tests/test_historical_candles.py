import sqlite3
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.historical_candles import collect,history,parse_candles

def row(t,close=100):
    return [t,90,110,99,close,5]

def test_candle_parser_rejects_bad_prices_and_open_bars():
    assert len(parse_candles("BTC-USD",60,[row(960),row(1020)],now=1050))==1
    try:parse_candles("BTC-USD",60,[[960,120,110,99,100,5]],now=1050)
    except ValueError:pass
    else:assert False,"crossed OHLC was accepted"
    try:parse_candles("BTC-USD",60,[row(960),row(960)],now=1100)
    except ValueError:pass
    else:assert False,"duplicate OHLC was accepted"

def test_collect_gap_revision_and_integrity(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    data=[row(900),row(960),row(1080)]
    result=collect("BTC-USD",60,now=1200,transport=lambda _:data)
    assert result["inserted"]==3
    assert "MISSING_BAR_INTERVAL" in history("BTC-USD",60)["issues"]
    assert collect("BTC-USD",60,now=1200,transport=lambda _:data)["unchanged"]==3
    changed=[row(900),row(960,101),row(1080)]
    assert collect("BTC-USD",60,now=1200,transport=lambda _:changed)["revised"]==1
    assert "REVISED_BAR" in history("BTC-USD",60)["issues"]
    with sqlite3.connect(tmp_path/"research_candles.sqlite3") as db:
        db.execute("UPDATE candles SET payload='{}' WHERE starts_at=960")
    d=history("BTC-USD",60)
    assert "CORRUPTED_BAR" in d["issues"]
    assert d["suitable_for_backtesting"] is False

def test_api_is_research_only(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    client=TestClient(app)
    assert client.get("/api/historical-candles/BTC-USD").status_code==200
    assert client.get("/api/historical-candles/BTC-USD").json()["live_orders_enabled"] is False
    assert client.get("/api/historical-candles/BTC-USD?granularity=17").status_code==422
    assert client.post("/api/historical-candles/INVALID/collect").status_code==404
    assert client.get("/api/status").json()["live_orders_enabled"] is False
