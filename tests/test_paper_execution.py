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

def test_cap_rejected_open_is_state_invariant():
    e=PaperEngine(capital="10000")
    e.open("terrestrial","SPY","UP","100","100",now(),{})
    before=(e.cash,dict(e.positions),list(e.history),e.peak,e.halted)
    with pytest.raises(ValueError,match="Per-tier or aggregate risk cap exceeded"):
        e.open("terrestrial","QQQ","UP","200","100",now(),{"SPY":"1000"})
    assert (e.cash,e.positions,e.history,e.peak,e.halted)==before

def test_cap_rejection_cannot_poison_peak_or_trigger_false_halt():
    e=PaperEngine(capital="10000")
    e.open("terrestrial","SPY","UP","100","100",now(),{})
    with pytest.raises(ValueError,match="Per-tier or aggregate risk cap exceeded"):
        e.open("terrestrial","QQQ","UP","200","100",now(),{"SPY":"1000"})
    assert e.peak==Decimal("10000")
    event=e.open("terrestrial","IWM","UP","10","100",now(),{"SPY":"100"})
    assert event["action"]=="OPEN" and e.halted is False

def test_drawdown_halt_blocks_open_but_allows_existing_position_close():
    e=PaperEngine(capital="10000")
    e.open("terrestrial","SPY","UP","100","100",now(),{})
    e.cash=Decimal("9000")
    with pytest.raises(ValueError,match="Max drawdown: engine halted"):
        e._risk({"SPY":"1"})
    with pytest.raises(ValueError,match="Engine halted"):
        e.open("terrestrial","IWM","UP","10","100",now(),{"SPY":"100"})
    event=e.close("SPY","100",now(),{})
    assert event["action"]=="CLOSE"
    assert "SPY" not in e.positions and e.halted is True
    with pytest.raises(ValueError,match="Engine halted"):
        e.open("terrestrial","IWM","UP","10","100",now(),{})

def test_symbol_allowlist():
    e=PaperEngine()
    with pytest.raises(ValueError):e.open("solar","UNKNOWN","UP","1","500",now(),{})

def test_no_automatic_live_placement():
    from nexus import paper_execution
    assert not hasattr(paper_execution.PaperEngine,"submit_live_order")

@pytest.mark.parametrize('price',(Decimal('1e10000'),Decimal('1e-10000')))
def test_extreme_open_price_is_rejected_before_state_mutation(price):
    e=PaperEngine(capital='10000')
    before=(e.cash,dict(e.positions),list(e.history),e.peak,e.halted)
    with pytest.raises(ValueError):
        e.open('solar','SPY','UP','100',price,now(),{})
    assert (e.cash,e.positions,e.history,e.peak,e.halted)==before

@pytest.mark.parametrize('price',(Decimal('1e10000'),Decimal('1e-10000')))
def test_extreme_close_price_is_rejected_before_state_mutation(price):
    e=PaperEngine(capital='10000')
    e.open('solar','SPY','UP','100','500',now(),{})
    before=(e.cash,dict(e.positions),list(e.history),e.peak,e.halted)
    with pytest.raises(ValueError):e.close('SPY',price,now(),{})
    assert (e.cash,e.positions,e.history,e.peak,e.halted)==before

@pytest.mark.parametrize(('field','value'),(
    ('capital',Decimal('1e10000')),('capital',Decimal('1e-10000')),
    ('fee',Decimal('1e10000')),('fee',Decimal('1e-10000')),
    ('cost_bps',Decimal('1e10000')),('cost_bps',Decimal('1e-10000')),
))
def test_extreme_engine_parameters_are_rejected(field,value):
    with pytest.raises(ValueError):PaperEngine(**{field:value})
