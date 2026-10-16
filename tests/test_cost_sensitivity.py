from fastapi.testclient import TestClient
from nexus.app import app
from nexus.cost_sensitivity import simulate,sensitivity
from nexus.historical_candles import collect

BASE=3600*1000
def candles():
    # Synthetic trend is explicitly test-only, not historical evidence.
    return [[BASE+i*3600,90+i*.1,110+i*.1,100+i*.1,100+i*.1,10] for i in range(150)]

def test_cost_increases_reduce_research_net_pnl():
    prices=[str(100+i*.5) for i in range(150)]
    low=simulate(prices,10,10,5)
    high=simulate(prices,10,50,30)
    assert low["trades"]==high["trades"]>0
    assert high["net_pnl"]<low["net_pnl"]

def test_train_only_selection_is_holdout_independent(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    rows=candles()
    collect("BTC-USD",3600,now=BASE+151*3600,transport=lambda _:rows)
    first=sensitivity("BTC-USD",3600,150)
    assert first["decision"]=="RESEARCH_ONLY_NOT_APPROVED"
    assert len(first["cost_scenarios"])==3
    assert first["trade_approval"] is False
    assert first["selection_method"]=="TRAINING_ONLY"
    assert first["train_length"]==105
    # A different holdout cannot affect threshold selection.
    changed=[list(x) for x in rows]
    for x in changed[105:]:
        x[4]=x[4]+1 if x[4]+1<=x[2] else x[4]-1
    # Fresh database permits different holdout without revision veto.
    other=tmp_path/"alternative";other.mkdir()
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(other))
    collect("BTC-USD",3600,now=BASE+151*3600,transport=lambda _:changed)
    second=sensitivity("BTC-USD",3600,150)
    assert second["selected_momentum_threshold_bps"]==first["selected_momentum_threshold_bps"]

def test_incomplete_data_fail_closed_and_no_trading(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    result=sensitivity("BTC-USD")
    assert result["decision"]=="DATA_REJECTED"
    client=TestClient(app)
    response=client.post("/api/candle-research/BTC-USD/cost-sensitivity")
    assert response.status_code==200
    assert response.json()["live_orders_enabled"] is False
    assert client.post("/api/candle-research/INVALID/cost-sensitivity").status_code==422
