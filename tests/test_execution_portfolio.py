from decimal import Decimal
import json
import os
import shutil
import sqlite3
import threading
import pytest
from nexus import execution_portfolio,tier_permissions
from nexus.durable_orders import DurableOrderGateway,emergency_halt_replayed
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


def test_tier_off_committed_while_buy_waits_blocks_new_position(
        tmp_path,monkeypatch):
    """An OFF commit that wins the ordering must veto the waiting BUY."""
    g=linked(tmp_path,monkeypatch)
    outcomes=[]
    with sqlite3.connect(g.path,timeout=10) as blocker:
        blocker.execute('BEGIN IMMEDIATE')

        def fill():
            try:g.simulate_priced_fill('buy','off_won',1,1,25)
            except Exception as error:outcomes.append(error)

        worker=threading.Thread(target=fill)
        worker.start()
        configure('terrestrial',False)
        blocker.rollback()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert len(outcomes)==1
    assert str(outcomes[0])=='RISK_TIER_DISENGAGED'
    cold=DurableOrderGateway(g.path).recovery_report()
    assert cold['orders'][0]['simulated_filled_notional']=='0'
    assert cold['execution_account']['account']['positions']=={}
    assert cold['portfolio_reconciled']


def test_tier_off_ack_waits_for_inflight_buy_commit_and_then_blocks_next_buy(
        tmp_path,monkeypatch):
    """A BUY that passed admission commits before OFF can acknowledge."""
    g=linked(tmp_path,monkeypatch)
    admitted=threading.Event();release_fill=threading.Event()
    off_started=threading.Event();off_attempted=threading.Event();off_done=threading.Event()
    outcomes=[]
    original_apply=execution_portfolio.apply_fill
    original_db=tier_permissions._db

    def paused_apply(*args,**kwargs):
        admitted.set()
        if not release_fill.wait(10):raise RuntimeError('test fill release timeout')
        return original_apply(*args,**kwargs)

    monkeypatch.setattr(execution_portfolio,'apply_fill',paused_apply)

    def tracked_db():
        if threading.current_thread().name=='tier-off':off_attempted.set()
        return original_db()

    monkeypatch.setattr(tier_permissions,'_db',tracked_db)

    def fill():
        try:outcomes.append(('fill',g.simulate_priced_fill('buy','buy_won',1,1,25)))
        except Exception as error:outcomes.append(('fill_error',error))

    def disable():
        off_started.set()
        try:outcomes.append(('off',configure('terrestrial',False)))
        except Exception as error:outcomes.append(('off_error',error))
        finally:off_done.set()

    fill_worker=threading.Thread(target=fill)
    off_worker=threading.Thread(target=disable,name='tier-off')
    fill_worker.start()
    assert admitted.wait(10)
    off_worker.start();assert off_started.wait(10) and off_attempted.wait(10)
    try:
        with sqlite3.connect(tmp_path/'portal_settings.sqlite3',timeout=.05) as probe:
            probe.execute('PRAGMA busy_timeout=50')
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                probe.execute('BEGIN IMMEDIATE')
        assert not off_done.is_set(), 'tier OFF acknowledged before the admitted BUY commit'
    finally:
        release_fill.set()
        fill_worker.join(timeout=10);off_worker.join(timeout=10)

    assert not fill_worker.is_alive() and not off_worker.is_alive()
    assert sorted(kind for kind,_ in outcomes)==['fill','off']
    cold=DurableOrderGateway(g.path)
    report=cold.recovery_report()
    assert report['orders'][0]['simulated_filled_notional']=='25'
    assert report['execution_account']['account']['positions']['BTC-USD']['quantity']=='1'
    assert report['portfolio_reconciled']

    cold.submit('next','BTC-USD','BUY',25,tier='terrestrial')
    before=cold.recovery_report()
    with pytest.raises(ValueError,match='^RISK_TIER_DISENGAGED$'):
        cold.simulate_priced_fill('next','blocked',1,1,25)
    assert DurableOrderGateway(g.path).recovery_report()==before

    cold.submit('exit','BTC-USD','SELL',25,tier='terrestrial')
    cold.simulate_priced_fill('exit','reducing',1,1,25)
    reduced=DurableOrderGateway(g.path).recovery_report()
    assert reduced['execution_account']['account']['positions']=={}
    assert reduced['portfolio_reconciled']


def test_settings_reader_cannot_turn_committed_buy_into_reported_failure(
        tmp_path,monkeypatch):
    """Guard release is rollback-only, so readers cannot block its exit."""
    g=linked(tmp_path,monkeypatch)
    admitted=threading.Event();release_fill=threading.Event();outcomes=[]
    original_apply=execution_portfolio.apply_fill
    monkeypatch.setattr(tier_permissions,'DB_BUSY_TIMEOUT_MS',50)

    def paused_apply(*args,**kwargs):
        admitted.set()
        if not release_fill.wait(10):raise RuntimeError('test fill release timeout')
        return original_apply(*args,**kwargs)

    monkeypatch.setattr(execution_portfolio,'apply_fill',paused_apply)

    def fill():
        try:outcomes.append(g.simulate_priced_fill('buy','reader',1,1,25))
        except Exception as error:outcomes.append(error)

    worker=threading.Thread(target=fill);worker.start()
    assert admitted.wait(10)
    with sqlite3.connect(tmp_path/'portal_settings.sqlite3') as reader:
        reader.execute('BEGIN')
        reader.execute('SELECT COUNT(*) FROM tier_permissions').fetchone()
        release_fill.set();worker.join(timeout=10)

    assert not worker.is_alive()
    assert len(outcomes)==1 and isinstance(outcomes[0],dict)
    cold=DurableOrderGateway(g.path).recovery_report()
    assert cold['orders'][0]['simulated_filled_notional']=='25'
    assert cold['execution_account']['account']['positions']['BTC-USD']['quantity']=='1'
    assert cold['portfolio_reconciled']


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
def connect(self,path=None):
    db=original(self,path)
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


def test_renamed_durable_fill_cannot_claim_linked_reconciliation(
        tmp_path,monkeypatch):
    """Exact economics cannot hide a cross-store fill identity mismatch."""
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    order_path=tmp_path/'order_lifecycle.sqlite3'
    portfolio_path=tmp_path/'paper_portfolio.sqlite3'
    gateway=DurableOrderGateway(order_path)
    gateway.initialize_account(10000)
    gateway.link_portfolio()
    session='0123456789abcdef'
    order_id=f'pf_{session}_buy'
    gateway.submit(order_id,'BTC-USD','BUY',100,tier='terrestrial')
    gateway.simulate_priced_fill(order_id,'original',1,1,25,'.01')
    assert gateway.recovery_report()['portfolio_reconciled']

    with sqlite3.connect(order_path) as db:
        sequence,payload=db.execute(
            'SELECT sequence,payload FROM commands ORDER BY sequence DESC LIMIT 1'
        ).fetchone()
        command=json.loads(payload)
        command['args'][1]='different'
        db.execute('UPDATE commands SET payload=? WHERE sequence=?',
            (json.dumps(command),sequence))

    before=(order_path.read_bytes(),portfolio_path.read_bytes())
    cold=DurableOrderGateway(order_path)
    operations=(
        cold.recovery_report,
        lambda:cold.session_orders_snapshot(session),
        lambda:cold.simulate_priced_fill(order_id,'different',1,1,25,'.01'),
        lambda:cold.simulate_priced_fill(order_id,'another',2,1,25,'.01'))
    for operation in operations:
        with pytest.raises(ValueError,match='^EXECUTION_PORTFOLIO_DISAGREEMENT$'):
            operation()
        assert (order_path.read_bytes(),portfolio_path.read_bytes())==before

    # The independent journal-only stop remains available despite the
    # pre-existing linked-ledger disagreement.
    halt=cold.emergency_halt()
    assert halt['halted']
    assert emergency_halt_replayed(order_path,halt)
    with sqlite3.connect(order_path) as db:
        method=json.loads(db.execute(
            'SELECT payload FROM commands ORDER BY sequence DESC LIMIT 1'
        ).fetchone()[0])['method']
    assert method=='emergency_halt'
    assert portfolio_path.read_bytes()==before[1]


def test_linked_safety_only_transactions_do_not_attach_portfolio(
        tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch)
    gateway.submit('expire','BTC-USD','BUY',25,tier='terrestrial')
    gateway.set_execution_terms('expire',1000,5)
    gateway.submit('due','BTC-USD','BUY',25,tier='terrestrial')
    gateway.set_execution_terms('due',1000,5)

    def forbidden_attach(*_args,**_kwargs):
        raise AssertionError('safety operation attached the linked portfolio')

    monkeypatch.setattr(execution_portfolio,'attach',forbidden_attach)
    assert gateway.cancel('buy')['status']=='SIMULATED_CANCELLED'
    assert gateway.expire_order('expire',1005)['status']=='SIMULATED_EXPIRED'
    assert gateway.expire_due(1005)['expired_order_ids']==['due']
    halt=gateway.emergency_halt()
    assert halt['halted'] and emergency_halt_replayed(gateway.path,halt)


@pytest.mark.parametrize('replacement_kind',['symlink','hardlink'])
def test_linked_fill_rejects_alias_portfolio_leaf_without_mutation(
        tmp_path,monkeypatch,replacement_kind):
    gateway=linked(tmp_path,monkeypatch)
    portfolio_path=tmp_path/'paper_portfolio.sqlite3'
    retained=tmp_path/'retained_portfolio.sqlite3'
    portfolio_path.rename(retained)
    if replacement_kind=='symlink':
        try:portfolio_path.symlink_to(retained.name)
        except (OSError,NotImplementedError):
            pytest.skip('symlink creation unavailable for this platform profile')
    else:
        os.link(retained,portfolio_path)
    before=(gateway.path.read_bytes(),retained.read_bytes())

    with pytest.raises(ValueError,match='^LINKED_PORTFOLIO_'):
        gateway.simulate_priced_fill('buy','aliased',1,1,25)

    assert (gateway.path.read_bytes(),retained.read_bytes())==before


def test_linked_fill_rechecks_portfolio_leaf_after_attach_before_commit(
        tmp_path,monkeypatch):
    if os.name=='nt':pytest.skip('Open SQLite replacement requires POSIX semantics')
    gateway=linked(tmp_path,monkeypatch)
    portfolio_path=tmp_path/'paper_portfolio.sqlite3'
    original_attach=execution_portfolio.attach
    replaced=[]

    def replace_after_attach(db,path):
        portfolio=original_attach(db,path)
        replacement=tmp_path/'replacement_portfolio.sqlite3'
        shutil.copy2(portfolio_path,replacement)
        os.replace(replacement,portfolio_path)
        replaced.append(True)
        return portfolio

    monkeypatch.setattr(execution_portfolio,'attach',replace_after_attach)
    before_order=gateway.path.read_bytes()
    before_portfolio=portfolio_path.read_bytes()
    with pytest.raises(ValueError,match='^LINKED_PORTFOLIO_CHANGED$'):
        gateway.simulate_priced_fill('buy','replaced',1,1,25)

    assert replaced==[True]
    assert gateway.path.read_bytes()==before_order
    assert portfolio_path.read_bytes()==before_portfolio


def test_linked_fill_rechecks_portfolio_leaf_after_final_comparison(
        tmp_path,monkeypatch):
    if os.name=='nt':pytest.skip('Open SQLite replacement requires POSIX semantics')
    gateway=linked(tmp_path,monkeypatch)
    portfolio_path=tmp_path/'paper_portfolio.sqlite3'
    before_order=gateway.path.read_bytes()
    before_portfolio=portfolio_path.read_bytes()
    original_compare=execution_portfolio.compare_linked
    comparisons=[]

    def replace_after_post_fill_compare(portfolio,account):
        result=original_compare(portfolio,account)
        comparisons.append(True)
        if len(comparisons)==2:
            replacement=tmp_path/'replacement_after_compare.sqlite3'
            replacement.write_bytes(before_portfolio)
            os.replace(replacement,portfolio_path)
        return result

    monkeypatch.setattr(execution_portfolio,'compare_linked',
        replace_after_post_fill_compare)
    with pytest.raises(ValueError,match='^LINKED_PORTFOLIO_CHANGED$'):
        gateway.simulate_priced_fill('buy','post_compare_replaced',1,1,25)

    assert comparisons==[True,True]
    assert gateway.path.read_bytes()==before_order
    assert portfolio_path.read_bytes()==before_portfolio


def test_linked_replay_rejects_retained_unpriced_fill(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch)
    with sqlite3.connect(gateway.path.resolve().as_uri(),uri=True) as db:
        sequence=db.execute('SELECT MAX(sequence) FROM commands').fetchone()[0]+1
        payload=json.dumps(dict(method='simulate_event',
            args=['buy','unpriced',1,'FILL',25],kwargs={}))
        db.execute('INSERT INTO commands VALUES (?,?)',(sequence,payload))
    before=(gateway.path.read_bytes(),
        (tmp_path/'paper_portfolio.sqlite3').read_bytes())
    with pytest.raises(ValueError,match='^ORDER_JOURNAL_RECOVERY_FAILED$'):
        DurableOrderGateway(gateway.path).recovery_report()
    assert (gateway.path.read_bytes(),
        (tmp_path/'paper_portfolio.sqlite3').read_bytes())==before


@pytest.mark.parametrize('disagreement',(
    'missing','extra','duplicate','side','product','quantity','price','fee',
    'archived_product'))
def test_exact_linked_fill_comparator_rejects_each_identity_or_economic_field(
        tmp_path,monkeypatch,disagreement):
    gateway=linked(tmp_path,monkeypatch)
    gateway.simulate_priced_fill('buy','fill',1,1,25,'.01')
    with sqlite3.connect(gateway.path.resolve().as_uri(),uri=True) as db:
        replayed,_=gateway._restore(db)
        if disagreement=='missing':
            replayed.executions.pop(('buy','fill'))
        elif disagreement=='extra':
            replayed.executions[('buy','extra')]=dict(
                quantity='1',price='25',fee='.01',sequence=1)
        elif disagreement=='duplicate':
            class DuplicateItems(dict):
                def items(self):
                    item=next(super().items().__iter__())
                    return (item,item)
            replayed.executions=DuplicateItems(replayed.executions)
        elif disagreement in ('side','product'):
            replayed.intents['buy'][disagreement]=(
                'SELL' if disagreement=='side' else 'ETH-USD')
        elif disagreement in ('quantity','price','fee'):
            replayed.executions[('buy','fill')][disagreement]='2'
        else:
            replayed.archived_intents['buy']=replayed.intents.pop('buy')
            replayed.archived_intents['buy']['product']='ETH-USD'
        portfolio=execution_portfolio.attach_read_only(
            db,tmp_path/'paper_portfolio.sqlite3')
        with pytest.raises(ValueError,
                match='^EXECUTION_PORTFOLIO_DISAGREEMENT$'):
            execution_portfolio.compare_linked(portfolio,replayed)


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
