from fastapi.testclient import TestClient
from nexus.app import app
from nexus.historical_candles import collect
from nexus.regime_stress import classify,analyze

BASE=3600*1000

def candles(n=150):
    out=[]
    for i in range(n):
        price=100+i*.3
        out.append([BASE+i*3600,price-2,price+2,price,price+.1,150])
    return out

def seed(data):
    collect("BTC-USD",3600,now=BASE+151*3600,transport=lambda _:data)

def test_classification_only_uses_closed_historical_bars():
    bars=[{"close":str(100+i*.3)} for i in range(40)]
    baseline=classify(bars,20)
    for i in range(21,40):bars[i]["close"]="9999999"
    assert classify(bars,20)==baseline

def test_holdouts_use_frozen_training_selection(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed(candles())
    a=analyze("BTC-USD",3600,150)
    assert a["decision"]=="RESEARCH_ONLY_NOT_APPROVED"
    assert len(a["holdout_regimes"])==2
    assert a["trade_approval"] is False
    assert a["threshold_selected_from"]=="TRAINING_ONLY"
    changed=candles()
    for item in changed[90:]:item[4]=item[3]-.2
    new=tmp_path/"different";new.mkdir()
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(new))
    seed(changed)
    b=analyze("BTC-USD",3600,150)
    assert a["selected_threshold_bps"]==b["selected_threshold_bps"]

def test_empty_data_and_unsupported_product_fail_closed(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    d=analyze("BTC-USD")
    assert d["decision"]=="DATA_REJECTED"
    c=TestClient(app)
    assert c.post("/api/candle-research/BTC-USD/regime-stress").json()["live_orders_enabled"] is False
    assert c.post("/api/candle-research/FAKE/regime-stress").status_code==422
