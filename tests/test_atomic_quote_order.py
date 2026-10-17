import json
import os
import sqlite3
import subprocess
import sys
import pytest
from nexus import durable_orders,order_checkpoints
from nexus.durable_orders import DurableOrderGateway
from nexus.tier_permissions import configure


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));configure('terrestrial',True)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3');g.initialize_account(10000);g.link_portfolio()
    return g


def execute(g,**changes):
    args=dict(tier='terrestrial',created_at=1000,ttl_seconds=10,quantity=1,bid=25,ask=25.1,
              available_quantity=2,quote_at=1001,evaluated_at=1001)
    args.update(changes)
    return g.execute_quote_order('atomic','BTC-USD','BUY',50,**args)


def test_atomic_order_retains_all_commands_and_replays_without_duplicate_fill(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch);monkeypatch.setattr(order_checkpoints,'INTERVAL',2)
    result=execute(g)
    assert result['atomic_transaction'] and result['order']['status']=='SIMULATED_CANCELLED'
    before=g.recovery_report();assert before['commands']==6 and before['portfolio_reconciled']
    assert execute(g)['fill']['idempotent_replay']
    assert g.recovery_report()['commands']==6
    order_checkpoints.clear_cache();restored=DurableOrderGateway(g.path).recovery_report()
    assert restored['execution_account']==before['execution_account']
    assert restored['portfolio_reconciled']
    with sqlite3.connect(g.path) as db:
        methods=[json.loads(row[0])['method'] for row in db.execute('SELECT payload FROM commands ORDER BY sequence')]
    assert methods[-4:]==['submit','set_execution_terms','simulate_quote_fill','cancel']
    with pytest.raises(ValueError,match='DUPLICATE_QUOTE_EVENT_CONFLICT'):execute(g,ask=25.2)
    assert g.recovery_report()['commands']==6


@pytest.mark.parametrize('failure',['tier','quote','capacity','cancel'])
def test_failure_rolls_back_intent_terms_fills_ledger_and_checkpoints(tmp_path,monkeypatch,failure):
    g=setup(tmp_path,monkeypatch);monkeypatch.setattr(order_checkpoints,'INTERVAL',1)
    baseline=g.recovery_report()
    if failure=='tier':configure('terrestrial',False)
    if failure=='capacity':monkeypatch.setattr(durable_orders,'MAX_COMMANDS',durable_orders.CONTROL_RESERVE+4)
    if failure=='cancel':
        with sqlite3.connect(g.path) as db:
            db.execute("CREATE TRIGGER fail_cancel BEFORE INSERT ON commands WHEN NEW.sequence=6 BEGIN SELECT RAISE(ABORT,'cancel failure'); END")
    with pytest.raises((ValueError,sqlite3.Error)):
        execute(g,evaluated_at=1005 if failure=='quote' else 1001)
    order_checkpoints.clear_cache();after=DurableOrderGateway(g.path).recovery_report()
    assert after['commands']==baseline['commands']==2
    assert after['orders']==[] and after['execution_account']==baseline['execution_account']
    assert after['portfolio_reconciled']


def test_expiry_is_durable_but_never_acknowledges_a_fill(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    result=execute(g,ttl_seconds=1)
    assert result['fill']['expired'] and result['order']['status']=='SIMULATED_EXPIRED'
    report=g.recovery_report()
    assert report['commands']==5 and not report['execution_account']['account']['positions']
    assert report['portfolio_reconciled']


@pytest.mark.parametrize('phase,code',[('before_commit',77),('after_commit',78)])
def test_actual_process_death_has_all_or_nothing_order_and_linked_account(tmp_path,monkeypatch,phase,code):
    g=setup(tmp_path,monkeypatch)
    if phase=='before_commit':
        with sqlite3.connect(g.path) as db:
            db.execute('CREATE TRIGGER die BEFORE INSERT ON commands WHEN NEW.sequence=5 BEGIN SELECT kill_process(); END')
    script=r'''
import os,sys
from pathlib import Path
from nexus.durable_orders import DurableOrderGateway
g=DurableOrderGateway(Path(os.environ['NEXUS_PORTABLE_DATA'])/'orders.sqlite3')
original=g._connect
def connect():
    db=original();db.create_function('kill_process',0,lambda:os._exit(77));return db
g._connect=connect
g.execute_quote_order('atomic','BTC-USD','BUY',50,tier='terrestrial',created_at=1000,ttl_seconds=10,
    quantity=1,bid=25,ask=25.1,available_quantity=2,quote_at=1001,evaluated_at=1001)
os._exit(78)
'''
    result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path)},capture_output=True,text=True,timeout=30)
    assert result.returncode==code,result.stderr
    order_checkpoints.clear_cache();restored=DurableOrderGateway(g.path).recovery_report()
    assert restored['portfolio_reconciled']
    assert restored['commands']==(2 if phase=='before_commit' else 6)
    assert bool(restored['execution_account']['account']['positions'])==(phase=='after_commit')
    if phase=='before_commit':
        with sqlite3.connect(g.path) as db:db.execute('DROP TRIGGER die')
    execute(g)
    final=g.recovery_report()
    assert final['commands']==6 and final['portfolio_reconciled']
    assert final['execution_account']['account']['positions']['BTC-USD']['quantity']=='1'
