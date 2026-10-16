from fastapi.testclient import TestClient
from nexus.app import app
from nexus.execution_realism import replay_window,assess
from nexus.historical_candles import collect

BASE=3600*1000

def bars(n=150, volume=1000):
    out=[]
    for i in range(n):
        px=100+i*.2
        out.append([BASE+i*3600,px-2,px+2,px,px+.1,volume])
    return out

def seed(rows):
    collect("BTC-USD",3600,now=BASE+151*3600,transport=lambda _:rows)

def test_entry_open_is_next_bar_and_future_close_cannot_change_past_pnl():
    rows=[{"open":str(100+i*.2),"close":str(100+i*.2),
           "volume":"1000"} for i in range(30)]
    original=replay_window(rows,10,25,15,3,15)
    mutated=[dict(r) for r in rows]
    for i in range(17,30):mutated[i]["close"]="999999"
    assert replay_window(mutated,10,25,15,3,15)==original

def test_participation_limit_is_conservative():
    rows=[{"open":str(100+i),"close":str(100+i),"volume":"0"} for i in range(30)]
    report=replay_window(rows,10,25,15,3,15)
    assert report["trades"]==0
    assert report["volume_vetoes"]>0

def test_two_holdouts_and_training_independence(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed(bars())
    first=assess("BTC-USD",3600,150)
    assert first["decision"]=="RESEARCH_ONLY_NOT_APPROVED"
    assert len(first["holdout_windows"])==2
    assert len(first["holdout_windows"][0]["cost_scenarios"])==3
    assert first["trade_approval"] is False
    second_dir=tmp_path/"other";second_dir.mkdir()
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(second_dir))
    altered=bars()
    for row in altered[90:]:
        row[4]=row[3]-0.3
    seed(altered)
    second=assess("BTC-USD",3600,150)
    assert second["chosen_threshold_bps"]==first["chosen_threshold_bps"]

def test_inadequate_data_veto_and_api_guard(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    assert assess("BTC-USD")["decision"]=="DATA_REJECTED"
    client=TestClient(app)
    assert client.post("/api/candle-research/BTC-USD/execution-stress").json()["live_orders_enabled"] is False
    assert client.post("/api/candle-research/INVALID/execution-stress").status_code==422
    assert client.get("/api/status").json()["version"]=="0.39.0"
