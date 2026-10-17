import pytest
from nexus import durable_orders,order_gateway
from nexus.durable_orders import DurableOrderGateway


def test_normal_intake_stops_while_controls_remain(tmp_path,monkeypatch):
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',1005)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(1000)
    g.submit('a','BTC-USD','BUY',25)
    g.submit('b','ETH-USD','BUY',25)
    g.set_execution_terms('b',1000,10)
    cap=g.recovery_report()['capacity']
    assert cap['ordinary_commands_remaining']==0
    assert cap['new_intents_allowed'] is False
    assert g.submit('a','BTC-USD','BUY',25)['idempotent_replay']
    with pytest.raises(ValueError,match='CAP_REACHED'):g.submit('c','SOL-USD','BUY',25)
    assert g.recovery_report()['commands']==4
    assert g.cancel('a')['status']=='SIMULATED_CANCELLED'
    assert g.expire_due(1010)['expired_count']==1
    assert g.emergency_halt()['halted']
    cap=g.recovery_report()['capacity']
    assert 'EMERGENCY_HALT_ACTIVE' in cap['reasons']
    assert cap['total_commands_remaining']>0
    assert cap['automatic_archival_available'] is False
    assert not cap['history_deleted']


def test_intent_ids_remain_reserved_after_cancel(tmp_path,monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.submit('a','BTC-USD','BUY',25);g.submit('b','ETH-USD','BUY',25)
    g.cancel('a')
    cap=g.recovery_report()['capacity']
    assert cap['intents_remaining']==0
    assert cap['new_intents_allowed'] is False
    assert cap['lifecycle_intake_allowed'] is True
    assert g.submit('a','BTC-USD','BUY',25)['idempotent_replay']
    with pytest.raises(ValueError,match='INTENT_CAP_REACHED'):g.submit('c','SOL-USD','BUY',25)


def test_simultaneous_admission_cannot_overrun_journal_limit(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',1006)
    path=tmp_path/'orders.sqlite3'
    g=DurableOrderGateway(path)
    g.initialize_account(1000)
    g.submit('seed','BTC-USD','BUY',25)
    def submit(index):
        try:
            DurableOrderGateway(path).submit('order_'+str(index),'ETH-USD','BUY',1)
            return True
        except ValueError as exc:
            assert str(exc)=='ORDER_JOURNAL_CAP_REACHED'
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted=list(pool.map(submit,range(16)))
    assert sum(admitted)==3
    report=g.recovery_report()
    assert report['commands']==5
    assert len(report['orders'])==4
    assert report['capacity']['ordinary_commands_remaining']==0
    assert g.emergency_halt()['halted']
