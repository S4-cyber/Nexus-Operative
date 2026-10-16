import time
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.replay import replay
from nexus.paper_execution import quote_age_ok

def test_quote_age_boundary_and_future_grace():
    now=time.time()
    assert quote_age_ok(now,now=now)
    assert quote_age_ok(now+.5,now=now)
    assert not quote_age_ok(now+5,now=now)
    assert not quote_age_ok(now-20,now=now)

def test_replay_is_deterministic():
    prices=[100,101,102,103,104,105,106,107,108,109,110]
    a=replay(prices,capital=5000,tier="solar")
    b=replay(prices,capital=5000,tier="solar")
    assert a==b
    assert a["simulation_only"] is True
    assert a["trades"]>0
    assert a["max_drawdown_pct"]<=0

def test_variable_capital_replay():
    closes=[100,102,105,101,99,100,103,107,105,104,108]
    a=replay(closes,capital=100,tier="solar")
    b=replay(closes,capital=100000,tier="solar")
    assert a["capital"]==100
    assert b["capital"]==100000

def test_invalid_prices_and_insufficient_history():
    for prices in ([100,100,100],[100,100,0,100,100],[100,100,float("nan"),100,100]):
        with pytest.raises(ValueError):
            replay(prices,capital=5000)

def test_replay_api_blocks_real_routing():
    c=TestClient(app)
    req={"capital":3000,"closes":[100,101,102,103,104,105],"tier":"solar","direction":"UP"}
    res=c.post("/api/paper/replay",json=req)
    assert res.status_code==200, res.text
    assert res.json()["source"]=="USER_SUPPLIED_CLOSES"
    assert c.get("/api/status").json()["live_orders_enabled"] is False
