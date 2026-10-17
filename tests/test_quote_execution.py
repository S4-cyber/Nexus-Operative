from decimal import Decimal
import pytest
from nexus.durable_orders import DurableOrderGateway
from nexus.quote_execution import plan
from nexus.tier_permissions import configure
from nexus.paper_portfolio import state
from nexus.portfolio_governor import audit_portfolio


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(10000);g.link_portfolio()
    g.submit('buy','BTC-USD','BUY',100,tier='terrestrial')
    g.set_execution_terms('buy',1000,10)
    return g


def fill(g,event='a',sequence=1,now=1001,**kwargs):
    return g.simulate_quote_fill('buy',event,sequence,1,25,25.1,2,now,now,**kwargs)


def test_adverse_spread_slippage_and_fees():
    b=plan('BUY',1,25,25.1,2,1000,1001)
    s=plan('SELL',1,25,25.1,2,1000,1001)
    assert Decimal(b['price'])>Decimal('25.1')
    assert Decimal(s['price'])<Decimal('25')
    assert Decimal(b['fee'])==Decimal(b['price'])*Decimal('.001')


def test_linked_quote_fill_restart_and_duplicate(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    first=fill(g)
    g=DurableOrderGateway(g.path)
    before=state()
    assert fill(g)['idempotent_replay']
    assert state()==before
    assert first['status']=='SIMULATED_PARTIALLY_FILLED'
    assert audit_portfolio()['integrity_ok']
    assert g.recovery_report()['portfolio_reconciled']
    with pytest.raises(ValueError,match='DUPLICATE_QUOTE_EVENT_CONFLICT'):
        fill(g,slippage_bps=6)


def test_partial_expiry_preserves_cash_and_blocks_late_fill(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    fill(g)
    before=state()
    expired=fill(g,'late',2,1010)
    assert expired['expired']
    assert expired['status']=='SIMULATED_EXPIRED'
    assert state()==before
    assert DurableOrderGateway(g.path).recovery_report()['orders'][0]['status']=='SIMULATED_EXPIRED'
    assert fill(g)['idempotent_replay']
    g.reset()
    assert g.expire_order('buy',1011)['idempotent_replay']


@pytest.mark.parametrize('field,value,reason',[
    ('bid',26,'CROSSED'),('ask',30,'EXCESSIVE'),('available_quantity','.1','LIQUIDITY'),
    ('quote_at',998,'STALE'),('quote_at',1002,'FUTURE'),('fee_bps',101,'COST'),
    ('slippage_bps',101,'COST'),('bid',True,'NUMBER')])
def test_invalid_synthetic_quote(field,value,reason):
    request=dict(side='BUY',quantity=1,bid=25,ask=25.1,available_quantity=2,quote_at=1000,evaluated_at=1001)
    request[field]=value
    with pytest.raises(ValueError,match=reason):plan(**request)


def test_terms_cannot_be_rewritten_or_bypassed(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    assert g.set_execution_terms('buy',1000,10)['idempotent_replay']
    with pytest.raises(ValueError,match='TERMS_CONFLICT'):g.set_execution_terms('buy',1000,11)
    with pytest.raises(ValueError,match='REQUIRES_QUOTE_MODEL'):g.simulate_priced_fill('buy','a',1,1,25)
    before=g.recovery_report()
    with pytest.raises(ValueError,match='NOT_EXPIRED'):g.expire_order('buy',1009)
    assert g.recovery_report()==before


def test_quote_sell_allowed_with_disengaged_tier(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    fill(g)
    g.submit('sell','BTC-USD','SELL',30,tier='terrestrial')
    g.set_execution_terms('sell',1002,10)
    configure('terrestrial',False)
    g.simulate_quote_fill('sell','exit',1,1,25,25.1,2,1003,1003)
    assert state()['positions']==[]
    assert audit_portfolio()['integrity_ok']


def test_scheduled_expiry_is_atomic_idempotent_and_durable(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    g.submit('later','ETH-USD','BUY',50,tier='terrestrial')
    g.set_execution_terms('later',1000,20)
    assert g.expire_due(1009)['expired_count']==0
    assert g.expire_due(1010)['expired_order_ids']==['buy']
    assert g.expire_due(1010)['expired_count']==0
    g=DurableOrderGateway(g.path)
    assert g.expire_due(1020)['expired_order_ids']==['later']
    assert g.recovery_report()['execution_account']['account']['positions']=={}


def test_raw_fill_bypass_and_time_regression_blocked(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='PRICED_FILLS|REQUIRES_QUOTE_MODEL'):
        g.simulate_event('buy','a',1,'FILL',25)
    fill(g,now=1003)
    before=g.recovery_report()
    with pytest.raises(ValueError,match='TIME_REGRESSION'):
        fill(g,event='b',sequence=2,now=1002)
    assert g.recovery_report()==before


def test_equivalent_numeric_quote_replay_is_idempotent(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    fill(g)
    result=g.simulate_quote_fill('buy','a',1,'1.00','25.000','25.100','2.0','1001.00','1001.0','10.00','5.0')
    assert result['idempotent_replay']
