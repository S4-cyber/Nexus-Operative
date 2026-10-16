import time
import pytest
from decimal import Decimal
from nexus.paper_execution import PaperEngine

def now():return time.time()

def test_variable_capital_scales_risk():
    for capital in ("100","10000","250000"):
        e=PaperEngine(capital=capital)
        amount=Decimal(capital)*Decimal(".01")
        trade=e.open("terrestrial","SPY","UP",amount,"500",now(),{})
        assert trade["simulation_only"]
        assert Decimal(str(trade["paper_notional"]))==amount

def test_costs_make_flat_roundtrip_a_loss():
    e=PaperEngine(capital="10000")
    e.open("solar","SPY","UP","100","500",now(),{})
    result=e.close("SPY","500",now(),{})
    assert result["realized_pnl_net_fees"]<0
    assert e.cash<e.initial

def test_down_direction_is_synthetic_not_short_sale():
    e=PaperEngine(capital="10000")
    e.open("solar","SPY","DOWN","100","500",now(),{})
    result=e.close("SPY","490",now(),{})
    assert result["simulation_only"] is True
    assert result["realized_pnl_net_fees"]>0

def test_stale_or_bad_quote_rejected():
    e=PaperEngine()
    with pytest.raises(ValueError):e.open("terrestrial","SPY","UP","1","500",now()-100,{})
    with pytest.raises(ValueError):e.open("terrestrial","SPY","UP","1","NaN",now(),{})

def test_risk_cap_and_halt():
    e=PaperEngine()
    with pytest.raises(ValueError):e.open("terrestrial","SPY","UP","3","500",now(),{})
    e.open("terrestrial","SPY","UP","1","500",now(),{})
    e.cash = Decimal("90")
    with pytest.raises(ValueError):e._risk({"SPY":"1"})
    assert e.halted

def test_symbol_allowlist():
    e=PaperEngine()
    with pytest.raises(ValueError):e.open("solar","UNKNOWN","UP","1","500",now(),{})

def test_no_automatic_live_placement():
    from nexus import paper_execution
    assert not hasattr(paper_execution.PaperEngine,"submit_live_order")
