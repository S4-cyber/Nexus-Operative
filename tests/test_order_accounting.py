from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from nexus.durable_orders import DurableOrderGateway


def setup(tmp_path):
    g = DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account('1000')
    g.submit('buy', 'BTC-USD', 'BUY', 100)
    return g


def account(g):
    return g.recovery_report()['execution_account']


def test_partial_buy_sell_and_restart(tmp_path):
    g = setup(tmp_path)
    g.simulate_priced_fill('buy','a',1,'2','25','.10')
    g = DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.simulate_priced_fill('buy','b',2,'1','50','.10')
    assert Decimal(account(g)['account']['cash']) == Decimal('899.80')
    assert Decimal(account(g)['account']['positions']['BTC-USD']['quantity']) == 3
    g.submit('sell','BTC-USD','SELL', 60)
    g.simulate_priced_fill('sell','c',1,'2','30','.06')
    g.cancel('sell')
    a = account(DurableOrderGateway(tmp_path/'orders.sqlite3'))
    assert Decimal(a['account']['cash']) == Decimal('959.74')
    assert Decimal(a['account']['positions']['BTC-USD']['quantity']) == 1
    assert a['reconciled'] is True


def test_concurrent_duplicate_and_conflicting_price(tmp_path):
    g = setup(tmp_path)
    def fill(_):
        return DurableOrderGateway(tmp_path/'orders.sqlite3').simulate_priced_fill('buy','a',1,2,25,'.10')
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(fill, range(15)))
    assert sum(not r['idempotent_replay'] for r in results) == 1
    with pytest.raises(ValueError, match='DUPLICATE_EXECUTION_CONFLICT'):
        g.simulate_priced_fill('buy','a',1,1,50,'.10')
    assert account(g)['priced_fills'] == 1


def test_oversell_and_overfill_roll_back(tmp_path):
    g = setup(tmp_path)
    before = g.recovery_report()
    with pytest.raises(ValueError, match='SIMULATED_OVERFILL'):
        g.simulate_priced_fill('buy','a',1,5,25)
    assert g.recovery_report() == before
    g.submit('sell','BTC-USD','SELL',50)
    before = g.recovery_report()
    with pytest.raises(ValueError, match='INSUFFICIENT_SIMULATED_POSITION'):
        g.simulate_priced_fill('sell','b',1,1,50)
    assert g.recovery_report() == before


def test_unpriced_legacy_fill_not_invented(tmp_path):
    g = setup(tmp_path)
    g.simulate_event('buy','old',1,'FILL',10)
    assert account(g)['reconciled'] is False
    assert account(g)['account']['positions'] == {}
    with pytest.raises(ValueError, match='ORDER_HAS_UNPRICED_FILLS'):
        g.simulate_priced_fill('buy','new',2,1,10)


def test_failed_commit_rolls_back_account_and_fill(tmp_path):
    g = setup(tmp_path)
    before = g.recovery_report()
    with sqlite3.connect(tmp_path/'orders.sqlite3') as db:
        db.execute("CREATE TRIGGER fail BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.Error):
        g.simulate_priced_fill('buy','a',1,2,25)
    assert g.recovery_report() == before


def test_insufficient_cash_and_invalid_numbers(tmp_path):
    g = DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(10)
    g.submit('buy','BTC-USD','BUY',20)
    for value in (True, 'NaN', 'Infinity', '-1', '1e1000000', 0):
        with pytest.raises(ValueError):
            g.simulate_priced_fill('buy','a',1,value,1)
    with pytest.raises(ValueError, match='INSUFFICIENT_SIMULATED_CASH'):
        g.simulate_priced_fill('buy','a',1,20,1)
    assert account(g)['priced_fills'] == 0
