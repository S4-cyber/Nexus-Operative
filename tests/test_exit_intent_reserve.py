from concurrent.futures import ThreadPoolExecutor

import pytest

from nexus import order_gateway, order_checkpoints
from nexus.durable_orders import DurableOrderGateway


def positioned(path):
    gateway=DurableOrderGateway(path)
    gateway.initialize_account(10000)
    gateway.link_portfolio()
    gateway.submit('entry','BTC-USD','BUY',25,tier='terrestrial')
    gateway.simulate_priced_fill('entry','entry_fill',1,1,25)
    return gateway


def test_reducing_exit_uses_only_reserved_slot_and_cold_replays(tmp_path,monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    path=tmp_path/'orders.sqlite3';gateway=positioned(path)
    with pytest.raises(ValueError,match='EXIT_INTENT_RESERVE_PROTECTED'):
        gateway.submit('ordinary_sell','BTC-USD','SELL',25,tier='terrestrial')
    exit_order=gateway.submit_reducing('exit','BTC-USD',25,1,1,tier='terrestrial')
    assert exit_order['intent_scope']=='POSITION_REDUCING_EXIT'
    assert exit_order['reduction_quantity']==exit_order['expected_position_quantity']=='1'
    gateway.simulate_priced_fill('exit','exit_fill',1,1,25)
    order_checkpoints.clear_cache()
    cold=DurableOrderGateway(path)
    report=cold.recovery_report()
    assert len(report['orders'])==report['capacity']['intent_limit']==2
    assert not report['execution_account']['account']['positions']
    assert report['capacity']['exit_intents_remaining']==0
    assert cold.submit_reducing('exit','BTC-USD',25,1,1,tier='terrestrial')['idempotent_replay']
    with pytest.raises(ValueError,match='DUPLICATE_ID_CONFLICT'):
        cold.submit('exit','BTC-USD','SELL',25,tier='terrestrial')


@pytest.mark.parametrize(('product','quantity','expected'),[
    ('ETH-USD',1,1),('BTC-USD',2,1),('BTC-USD',1,2),
])
def test_reducing_reserve_rejects_wrong_position_bindings_storage_silently(
        tmp_path,monkeypatch,product,quantity,expected):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    path=tmp_path/'orders.sqlite3';gateway=positioned(path)
    before=path.read_bytes()
    with pytest.raises(ValueError,match='EXIT_INTENT_RESERVE_DENIED'):
        gateway.submit_reducing('exit',product,25,quantity,expected,tier='terrestrial')
    assert path.read_bytes()==before
    assert len(gateway.recovery_report()['orders'])==1


def test_reducing_reserve_rejects_flat_unlinked_and_halted_accounts(tmp_path,monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    unlinked=DurableOrderGateway(tmp_path/'unlinked'/'orders.sqlite3')
    with pytest.raises(ValueError,match='EXIT_INTENT_RESERVE_DENIED'):
        unlinked.submit_reducing('exit','BTC-USD',25,1,1,tier='terrestrial')
    assert unlinked.recovery_report()['commands']==0
    flat=DurableOrderGateway(tmp_path/'flat'/'orders.sqlite3')
    flat.initialize_account(1000);flat.link_portfolio();flat_before=flat._path().read_bytes()
    with pytest.raises(ValueError,match='EXIT_INTENT_RESERVE_DENIED'):
        flat.submit_reducing('exit','BTC-USD',25,1,1,tier='terrestrial')
    assert flat._path().read_bytes()==flat_before
    held=positioned(tmp_path/'halted'/'orders.sqlite3');held.emergency_halt();halted_before=held._path().read_bytes()
    with pytest.raises(ValueError,match='EMERGENCY_HALT_ACTIVE'):
        held.submit_reducing('exit','BTC-USD',25,1,1,tier='terrestrial')
    assert held._path().read_bytes()==halted_before


def test_ordinary_race_cannot_steal_reducing_exit_slot(tmp_path,monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    path=tmp_path/'orders.sqlite3';positioned(path)
    def ordinary():
        with pytest.raises(ValueError,match='EXIT_INTENT_RESERVE_PROTECTED'):
            DurableOrderGateway(path).submit('ordinary','ETH-USD','SELL',1,tier='terrestrial')
        return 'ordinary_blocked'
    def reducing():
        DurableOrderGateway(path).submit_reducing('exit','BTC-USD',25,1,1,tier='terrestrial')
        return 'exit_admitted'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(lambda fn:fn(),(ordinary,reducing)))=={
            'ordinary_blocked','exit_admitted'}
    assert len(DurableOrderGateway(path).recovery_report()['orders'])==2


def test_reducing_fill_rechecks_bound_position_and_declared_quantity(tmp_path,monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    changed=DurableOrderGateway(tmp_path/'changed'/'orders.sqlite3')
    changed.initialize_account(10000);changed.link_portfolio()
    changed.submit('entry','BTC-USD','BUY',50,tier='terrestrial')
    changed.simulate_priced_fill('entry','first',1,1,25)
    changed.submit_reducing('exit','BTC-USD',25,1,1,tier='terrestrial')
    changed.simulate_priced_fill('entry','second',2,1,25)
    before=changed.recovery_report()['commands']
    with pytest.raises(ValueError,match='REDUCING_EXIT_POSITION_CHANGED'):
        changed.simulate_priced_fill('exit','fill',1,1,25)
    assert changed.recovery_report()['commands']==before

    bounded=positioned(tmp_path/'bounded'/'orders.sqlite3')
    bounded.submit_reducing('exit','BTC-USD',25,'0.5',1,tier='terrestrial')
    before=bounded.recovery_report()['commands']
    with pytest.raises(ValueError,match='REDUCING_EXIT_QUANTITY_EXCEEDED'):
        bounded.simulate_priced_fill('exit','fill',1,1,25)
    assert bounded.recovery_report()['commands']==before
