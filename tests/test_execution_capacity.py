import json
import sqlite3
import pytest
from nexus import durable_orders,order_gateway
from nexus.durable_orders import DurableOrderGateway
from nexus.execution_capacity import report


def seed_halted_control_history(path,count):
    assert count%2==1
    gateway=DurableOrderGateway(path)
    with gateway._connect() as db:
        rows=[]
        for sequence in range(1,count+1):
            method='emergency_halt' if sequence%2 else 'reset'
            payload=json.dumps(dict(method=method,args=(),kwargs={}),allow_nan=False)
            rows.append((sequence,payload))
        db.executemany('INSERT INTO commands VALUES (?,?)',rows)
    report=DurableOrderGateway(path).recovery_report()
    assert report['commands']==count and report['halted'] is True
    return report


@pytest.mark.parametrize('args',[
    (True,0,False,10000),(0,True,False,10000),(0,0,0,10000),
    (0,0,False,1000),(10001,0,False,10000),(0,1001,False,10000),
])
def test_capacity_report_rejects_noncanonical_or_impossible_bounds(args):
    with pytest.raises(ValueError,match='EXECUTION_CAPACITY_INPUT_INVALID'):
        report(*args)


def test_capacity_report_rejects_retained_history_inconsistency():
    with pytest.raises(ValueError,match='EXECUTION_CAPACITY_INPUT_INVALID'):
        report(4,1,False,10000,retained_commands=3)
    with pytest.raises(ValueError,match='EXECUTION_CAPACITY_INPUT_INVALID'):
        report(4,1,False,10000,retained_commands=100001,retained_limit=100000)


def test_retained_limit_is_independent_from_active_epoch_limit():
    capacity=report(1,0,False,10000,retained_commands=13,retained_limit=1014)
    assert capacity['ordinary_commands_remaining']==0
    assert capacity['total_commands_remaining']==1001
    assert capacity['control_commands_reserved']==1001
    assert 'RETAINED_HISTORY_INTAKE_EXHAUSTED' in capacity['reasons']


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


def test_linked_account_reports_one_existing_slot_as_exit_only(monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    capacity=report(4,1,False,10000,exit_intent_reserve_active=True)
    assert capacity['intent_limit']==2 and capacity['intents_remaining']==1
    assert capacity['ordinary_intent_limit']==1
    assert capacity['ordinary_intents_remaining']==0
    assert capacity['exit_intent_reserve_active'] is True
    assert capacity['exit_intents_remaining']==1
    assert capacity['new_intents_allowed'] is False
    assert capacity['lifecycle_intake_allowed'] is True
    assert 'EXIT_INTENT_RESERVE_PROTECTED' in capacity['reasons']


def test_capacity_report_rejects_non_boolean_exit_reserve():
    with pytest.raises(ValueError,match='EXECUTION_CAPACITY_INPUT_INVALID'):
        report(0,0,False,10000,exit_intent_reserve_active=1)


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


@pytest.mark.parametrize('limiting_cap',['active','retained'])
def test_final_durable_slot_is_reserved_for_halt(tmp_path,monkeypatch,limiting_cap):
    cap=durable_orders.CONTROL_RESERVE+3
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',
        cap if limiting_cap=='active' else cap+100)
    monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',
        cap if limiting_cap=='retained' else cap+100)
    path=tmp_path/(limiting_cap+'.sqlite3')
    before=seed_halted_control_history(path,cap-1)
    before_bytes=path.read_bytes()

    with pytest.raises(ValueError,match='^ORDER_JOURNAL_LAST_HALT_RESERVE$'):
        DurableOrderGateway(path).reset()

    assert path.read_bytes()==before_bytes
    assert DurableOrderGateway(path).recovery_report()==before
    assert not (tmp_path/'paper_portfolio.sqlite3').exists()


@pytest.mark.parametrize('limiting_cap',['active','retained'])
def test_penultimate_slot_can_reset_and_final_slot_can_rehalt(
        tmp_path,monkeypatch,limiting_cap):
    cap=durable_orders.CONTROL_RESERVE+4
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',
        cap if limiting_cap=='active' else cap+100)
    monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',
        cap if limiting_cap=='retained' else cap+100)
    path=tmp_path/(limiting_cap+'.sqlite3')
    seed_halted_control_history(path,cap-2)
    gateway=DurableOrderGateway(path)

    assert gateway.reset()['halted'] is False
    running=DurableOrderGateway(path).recovery_report()
    assert running['commands']==cap-1 and running['halted'] is False
    assert 'FINAL_HALT_SLOT_RESERVED' in running['capacity']['reasons']
    assert running['capacity']['recommended_action']=='EMERGENCY_HALT_REQUIRED'
    assert gateway.emergency_halt()['halted'] is True
    halted=DurableOrderGateway(path).recovery_report()
    assert halted['commands']==cap and halted['halted'] is True
    assert gateway.emergency_halt()['halted'] is True
    assert DurableOrderGateway(path).recovery_report()==halted


def test_denied_final_slot_reset_cannot_repair_linked_portfolio(tmp_path,monkeypatch):
    cap=durable_orders.CONTROL_RESERVE+5
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',cap)
    monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',cap+100)
    path=tmp_path/'orders.sqlite3'
    gateway=DurableOrderGateway(path)
    gateway.initialize_account(1000)
    gateway.link_portfolio()
    gateway.emergency_halt()
    with gateway._connect() as db:
        rows=[]
        for sequence in range(4,cap):
            method='reset' if sequence%2==0 else 'emergency_halt'
            payload=json.dumps(dict(method=method,args=(),kwargs={}),allow_nan=False)
            rows.append((sequence,payload))
        db.executemany('INSERT INTO commands VALUES (?,?)',rows)
    portfolio=tmp_path/'paper_portfolio.sqlite3'
    with sqlite3.connect(portfolio) as db:
        db.execute('DROP TABLE execution_fills')
    main_before=path.read_bytes();portfolio_before=portfolio.read_bytes()

    with pytest.raises(ValueError,match='^ORDER_JOURNAL_LAST_HALT_RESERVE$'):
        gateway.reset()

    assert path.read_bytes()==main_before
    assert portfolio.read_bytes()==portfolio_before
    with sqlite3.connect(portfolio) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='execution_fills'").fetchone()==(0,)


def test_final_halt_reserve_applies_to_non_reset_state_change(tmp_path,monkeypatch):
    cap=durable_orders.CONTROL_RESERVE+4
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',cap)
    monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',cap+100)
    path=tmp_path/'orders.sqlite3';gateway=DurableOrderGateway(path)
    with gateway._connect() as db:
        rows=[]
        for sequence in range(1,cap):
            method='emergency_halt' if sequence%2 else 'reset'
            payload=json.dumps(dict(method=method,args=(),kwargs={}),allow_nan=False)
            rows.append((sequence,payload))
        db.executemany('INSERT INTO commands VALUES (?,?)',rows)
    before=path.read_bytes()

    with pytest.raises(ValueError,match='^ORDER_JOURNAL_LAST_HALT_RESERVE$'):
        gateway.initialize_account(1000)

    assert path.read_bytes()==before
    assert DurableOrderGateway(path).recovery_report()['execution_account']['initialized'] is False


def test_legacy_unhalted_full_history_remains_readable(tmp_path,monkeypatch):
    cap=durable_orders.CONTROL_RESERVE+3
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',cap)
    monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',cap+100)
    path=tmp_path/'orders.sqlite3';gateway=DurableOrderGateway(path)
    with gateway._connect() as db:
        rows=[]
        for sequence in range(1,cap+1):
            method='emergency_halt' if sequence%2 else 'reset'
            payload=json.dumps(dict(method=method,args=(),kwargs={}),allow_nan=False)
            rows.append((sequence,payload))
        db.executemany('INSERT INTO commands VALUES (?,?)',rows)

    report=gateway.recovery_report()

    assert report['commands']==cap and report['halted'] is False
    assert report['capacity']['total_commands_remaining']==0


def test_halted_archive_can_use_final_retained_slot(tmp_path,monkeypatch):
    retained_cap=durable_orders.CONTROL_RESERVE+9
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',retained_cap+100)
    monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',retained_cap)
    path=tmp_path/'orders.sqlite3';gateway=DurableOrderGateway(path)
    gateway.initialize_account(1000);gateway.link_portfolio()
    gateway.submit('closed','BTC-USD','BUY',25);gateway.cancel('closed')
    gateway.emergency_halt()
    with gateway._connect() as db:
        rows=[]
        for sequence in range(6,retained_cap):
            method='reset' if sequence%2==0 else 'emergency_halt'
            payload=json.dumps(dict(method=method,args=(),kwargs={}),allow_nan=False)
            rows.append((sequence,payload))
        db.executemany('INSERT INTO commands VALUES (?,?)',rows)

    manifest=gateway.archive_terminal('final_slot')
    report=DurableOrderGateway(path).recovery_report()

    assert manifest['sequence_boundary']==retained_cap
    assert report['commands']==retained_cap and report['halted'] is True
    assert report['capacity']['total_commands_remaining']==0
