from decimal import Decimal
import sqlite3
import pytest
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_portfolio import state, buy
from nexus.portfolio_governor import audit_portfolio
from nexus.tier_permissions import configure


def linked(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    g=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    g.initialize_account(10000)
    g.link_portfolio()
    g.submit('buy','BTC-USD','BUY',100,tier='terrestrial')
    return g


def test_link_partial_fills_and_sell_reconcile(tmp_path, monkeypatch):
    g=linked(tmp_path,monkeypatch)
    g.simulate_priced_fill('buy','a',1,2,25,'.05')
    g.simulate_priced_fill('buy','b',2,1,50,'.05')
    assert audit_portfolio()['integrity_ok']
    assert state()['positions'][0]['quantity']==3
    assert g.recovery_report()['portfolio_reconciled']
    g.submit('sell','BTC-USD','SELL',60,tier='terrestrial')
    configure('terrestrial',False)
    g.simulate_priced_fill('sell','c',1,2,30,'.06')
    g.cancel('sell')
    assert state()['positions'][0]['quantity']==1
    assert audit_portfolio()['integrity_ok']
    assert DurableOrderGateway(g.path).recovery_report()['portfolio_reconciled']


def test_risk_veto_and_duplicate_after_disengage(tmp_path,monkeypatch):
    g=linked(tmp_path,monkeypatch)
    g.simulate_priced_fill('buy','a',1,1,25)
    configure('terrestrial',False)
    assert g.simulate_priced_fill('buy','a',1,1,25)['idempotent_replay']
    before=g.recovery_report()
    with pytest.raises(ValueError,match='RISK_TIER_DISENGAGED'):
        g.simulate_priced_fill('buy','b',2,1,25)
    assert g.recovery_report()==before


def test_failure_rolls_back_both_files(tmp_path,monkeypatch):
    g=linked(tmp_path,monkeypatch)
    before=state()
    commands=g.recovery_report()['commands']
    with sqlite3.connect(g.path) as db:
        db.execute("CREATE TRIGGER fail BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.Error):
        g.simulate_priced_fill('buy','a',1,1,25)
    assert state()==before
    assert g.recovery_report()['commands']==commands
    assert audit_portfolio()['integrity_ok']


def test_divergence_and_unpriced_fills_blocked(tmp_path,monkeypatch):
    g=linked(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='LINKED_ACCOUNT_REQUIRES_PRICED_FILLS'):
        g.simulate_event('buy','a',1,'FILL',25)
    with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
        db.execute("UPDATE meta SET value='1' WHERE key='cash'")
    with pytest.raises(ValueError,match='governor veto'):
        g.simulate_priced_fill('buy','a',1,1,25)


def test_existing_trade_history_not_auto_merged(tmp_path,monkeypatch):
    import time
    from nexus.paper_portfolio import initialize
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    initialize(10000)
    quote=dict(product='BTC-USD',read_only=True,orders_allowed=False,provider='coinbase_exchange_public',received_at=time.time(),bid=25,ask=25)
    buy('BTC-USD',25,quote)
    g=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    g.initialize_account(10000)
    with pytest.raises(ValueError,match='LINK_REQUIRES_UNUSED'):
        g.link_portfolio()
    assert not g.recovery_report()['portfolio_reconciled']


def test_process_death_before_commit_recovers_both_databases(tmp_path,monkeypatch):
    import subprocess, sys
    g=linked(tmp_path,monkeypatch)
    before=state()
    commands=g.recovery_report()['commands']
    with sqlite3.connect(g.path) as db:
        db.execute('CREATE TRIGGER die BEFORE INSERT ON commands BEGIN SELECT kill_process(); END')
    code='''
import os,sys
from nexus.durable_orders import DurableOrderGateway
original=DurableOrderGateway._connect
def connect(self):
    db=original(self)
    db.create_function('kill_process',0,lambda:os._exit(71))
    return db
DurableOrderGateway._connect=connect
DurableOrderGateway(sys.argv[1]).simulate_priced_fill('buy','crash',1,1,25,'.01')
'''
    result=subprocess.run([sys.executable,'-c',code,str(g.path)],capture_output=True,timeout=20)
    assert result.returncode==71,result.stderr.decode()
    assert state()==before
    assert g.recovery_report()['commands']==commands
    assert audit_portfolio()['integrity_ok']
    with sqlite3.connect(g.path) as db:
        db.execute('DROP TRIGGER die')
    g.simulate_priced_fill('buy','crash',1,1,25,'.01')
    assert g.recovery_report()['portfolio_reconciled']
    assert state()['positions'][0]['quantity']==1


def test_independent_audit_detects_resealed_execution_disagreement(tmp_path,monkeypatch):
    from nexus.transaction_seals import checksum,row_payload
    g=linked(tmp_path,monkeypatch)
    g.simulate_priced_fill('buy','a',1,1,25,'.01')
    with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
        row=db.execute('SELECT ledger_id FROM execution_fills').fetchone()[0]
        db.execute('UPDATE execution_fills SET price=? WHERE ledger_id=?',('24',row))
        prev=db.execute('SELECT previous FROM chain_seals WHERE ledger_id=?',(row,)).fetchone()[0]
        db.execute('UPDATE chain_seals SET digest=? WHERE ledger_id=?',(checksum(prev,row_payload(db,row)),row))
    report=audit_portfolio()
    assert 'EXECUTION_CASH_DISAGREEMENT' in report['reasons']
    assert 'EXECUTION_POSITION_DISAGREEMENT' in report['reasons']


def test_halt_can_persist_despite_portfolio_disagreement(tmp_path,monkeypatch):
    g=linked(tmp_path,monkeypatch)
    with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
        db.execute("UPDATE meta SET value='1' WHERE key='cash'")
    assert g.emergency_halt()['halted']
    with pytest.raises(ValueError):
        g.reset()


def test_accumulated_position_cap_rolls_back_partial_buy(tmp_path,monkeypatch):
    g=linked(tmp_path,monkeypatch)
    g.simulate_priced_fill('buy','a',1,1,100)
    g.submit('second','BTC-USD','BUY',100,tier='terrestrial')
    g.simulate_priced_fill('second','b',1,1,100)
    g.submit('third','BTC-USD','BUY',100,tier='terrestrial')
    before=g.recovery_report()
    with pytest.raises(ValueError,match='PAPER_EXPOSURE_LIMIT'):
        g.simulate_priced_fill('third','c',1,1,100)
    assert g.recovery_report()==before
    assert state()['positions'][0]['quantity']==2
    assert audit_portfolio()['integrity_ok']
