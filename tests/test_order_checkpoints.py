import json
import sqlite3
import pytest
from nexus import order_checkpoints as cp
from nexus.durable_orders import DurableOrderGateway


def populate(tmp_path,monkeypatch):
    monkeypatch.setattr(cp,'INTERVAL',4)
    cp.clear_cache()
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(1000)
    g.submit('a','BTC-USD','BUY',25)
    g.simulate_priced_fill('a','fill',1,1,25)
    g.cancel('a')  # Already filled; no command consumed.
    g.submit('b','ETH-USD','BUY',25)
    return g


def test_checkpoint_prefix_verified_fresh_then_cached(tmp_path,monkeypatch):
    g=populate(tmp_path,monkeypatch)
    cp.clear_cache()
    report=g.recovery_report()
    assert report['recovery_diagnostics']['prefix_replayed']==4
    report=g.recovery_report()
    assert report['recovery_diagnostics']['cache_hit']
    assert report['recovery_diagnostics']['prefix_replayed']==0
    assert report['execution_account']['priced_fills']==1
    full=DurableOrderGateway(g.path,checkpoints_enabled=False).recovery_report()
    for key in ('orders','execution_account','halted','commands'):
        assert report[key]==full[key]


def test_state_mutation_rejected_even_with_cache(tmp_path,monkeypatch):
    g=populate(tmp_path,monkeypatch)
    g.recovery_report()
    with sqlite3.connect(g.path) as db:
        raw=db.execute('SELECT state_json FROM order_checkpoints').fetchone()[0]
        state=json.loads(raw);state['account']['cash']='999999'
        db.execute('UPDATE order_checkpoints SET state_json=?',(json.dumps(state),))
    with pytest.raises(ValueError,match='CHECKPOINT_STATE_MISMATCH'):g.reset()


def test_retained_history_edit_detected_on_cache_hit(tmp_path,monkeypatch):
    g=populate(tmp_path,monkeypatch)
    g.recovery_report()
    with sqlite3.connect(g.path) as db:
        db.execute("UPDATE commands SET payload='{}' WHERE sequence=1")
    with pytest.raises(ValueError,match='PREFIX_MISMATCH'):g.recovery_report()


def test_tail_replay_and_checkpoint_retention(tmp_path,monkeypatch):
    g=populate(tmp_path,monkeypatch)
    for i in range(8):g.submit('extra_'+str(i),'SOL-USD','BUY',1)
    g.cancel('b')
    report=g.recovery_report()
    assert report['recovery_diagnostics']['tail_replayed']==1
    with sqlite3.connect(g.path) as db:
        assert db.execute('SELECT COUNT(*) FROM order_checkpoints').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]==13
    assert not report['retention']['command_history_deleted']


def test_failed_checkpoint_write_rolls_back_command(tmp_path,monkeypatch):
    monkeypatch.setattr(cp,'INTERVAL',2)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(1000)
    with sqlite3.connect(g.path) as db:
        db.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON order_checkpoints BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.Error):g.submit('a','BTC-USD','BUY',25)
    assert g.recovery_report()['commands']==1
    assert g.recovery_report()['orders']==[]


def test_linked_checkpoint_failure_rolls_back_fill_and_ledger(tmp_path,monkeypatch):
    from nexus.paper_portfolio import state
    from nexus.tier_permissions import configure
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr(cp,'INTERVAL',4)
    configure('terrestrial',True)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(10000);g.link_portfolio()
    g.submit('buy','BTC-USD','BUY',100,tier='terrestrial')
    g.set_execution_terms('buy',1000,10)
    g.simulate_quote_fill('buy','fill',1,1,25,25.1,2,1001,1001)
    g.submit('sell','BTC-USD','SELL',30,tier='terrestrial')
    g.set_execution_terms('sell',1002,10)
    before=state()
    with sqlite3.connect(g.path) as db:
        db.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON order_checkpoints BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.Error):
        g.simulate_quote_fill('sell','exit',1,1,25,25.1,2,1003,1003)
    assert state()==before
    assert g.recovery_report()['commands']==7
    assert g.recovery_report()['portfolio_reconciled']


def test_process_death_during_checkpoint_save_rolls_back(tmp_path,monkeypatch):
    import subprocess,sys
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(1000)
    with sqlite3.connect(g.path) as db:
        db.execute('CREATE TRIGGER die BEFORE INSERT ON order_checkpoints BEGIN SELECT kill_process(); END')
    code='''
import os,sys
from nexus import order_checkpoints as cp
from nexus.durable_orders import DurableOrderGateway
cp.INTERVAL=2
original=DurableOrderGateway._connect
def connect(self):
    db=original(self)
    db.create_function('kill_process',0,lambda:os._exit(72))
    return db
DurableOrderGateway._connect=connect
DurableOrderGateway(sys.argv[1]).submit('a','BTC-USD','BUY',25)
'''
    result=subprocess.run([sys.executable,'-c',code,str(g.path)],capture_output=True,timeout=20)
    assert result.returncode==72,result.stderr.decode()
    cp.clear_cache()
    report=g.recovery_report()
    assert report['commands']==1
    assert report['orders']==[]
    assert report['recovery_diagnostics']['checkpoint_sequence']==0
