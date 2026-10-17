from decimal import Decimal
from test_paper_forward import setup
from nexus.tier_permissions import configure


def test_exit_preserves_exact_preexisting_priced_quantity(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    quantity='0.1234567890123456789'
    g.submit('earlier_priced_buy','BTC-USD','BUY',20,tier='terrestrial')
    g.simulate_priced_fill('earlier_priced_buy','fill',1,quantity,100,0)
    g.cancel('earlier_priced_buy')
    configure('terrestrial',False)
    c.price=102
    assert r.single_cycle()['decision']=='SELL'
    report=g.recovery_report()
    assert report['portfolio_reconciled']
    assert not report['execution_account']['account']['positions']
    sold=g.session_orders(r.status()['session'])[0]['quote_events'][0]
    assert Decimal(sold['quantity'])==Decimal(quantity)
    assert Decimal(sold['quantity'])*Decimal(sold['price'])<=200
    r.stop()
    assert r.evidence_report(r.status()['session'])['local_consistency_ok']


def test_larger_exit_keeps_notional_cap_then_finishes_flat(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    g.submit('earlier_priced_buy','BTC-USD','BUY',100,tier='terrestrial')
    g.simulate_priced_fill('earlier_priced_buy','fill',1,1,100,0)
    g.cancel('earlier_priced_buy')
    configure('terrestrial',False)
    c.price=400
    assert r.single_cycle()['decision']=='SELL'
    assert g.recovery_report()['execution_account']['account']['positions']
    c.now+=5
    assert r.single_cycle()['decision']=='SELL'
    report=g.recovery_report()
    assert report['portfolio_reconciled'] and not report['execution_account']['account']['positions']
    for order in g.session_orders(r.status()['session']):
        assert Decimal(order['order']['simulated_filled_notional'])<=200
    r.stop()
    assert r.evidence_report(r.status()['session'])['local_consistency_ok']
