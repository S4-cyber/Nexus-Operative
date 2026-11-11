"""Actual local journal replay and linked-file checks, never provider access."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest
from nexus import durable_orders
from nexus.durable_orders import DurableOrderGateway
from nexus import emergency_intent, execution_portfolio
from nexus.paper_forward import PaperForwardRunner
from test_execution_portfolio import linked


def test_missing_reads_leave_no_directory_and_do_not_attest_flat_account(tmp_path,monkeypatch):
    import nexus.app as backend
    root=tmp_path/'missing';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3');runner=PaperForwardRunner(gateway)
    monkeypatch.setattr(backend,'research_order_gateway',gateway);monkeypatch.setattr(backend,'paper_forward_runner',runner)
    report=gateway.recovery_report()
    assert report['status']=='NO_RETAINED_HISTORY' and not report['journal_present']
    assert report['commands']==0 and not report['portfolio_reconciled']
    assert not report['execution_account']['initialized'] and not report['live_orders_enabled']
    assert gateway.session_orders('a'*16)==[]
    snapshot=gateway.session_orders_snapshot('a'*16)
    assert snapshot['read_only'] and not snapshot['journal_present'] and snapshot['commands']==0
    assert snapshot['orders']==[] and len(snapshot['snapshot_sha256'])==64
    with pytest.raises(ValueError,match='UNKNOWN_ORDER_ID'):gateway.order_report('absent')
    recovery=runner.recovery_status()
    assert recovery['order_history_present'] is False
    assert 'ORDER_HISTORY_MISSING_VERIFY_SEPARATE_PORTFOLIO' in recovery['suggested_actions']
    for path in ('/api/execution/recovery','/api/paper/forward/recovery'):
        assert TestClient(backend.app).get(path).status_code==200
    assert TestClient(backend.app).get('/api/execution/intent/absent').status_code==404
    assert not root.exists()


def test_missing_order_history_does_not_read_create_or_modify_separate_portfolio(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    portfolio=tmp_path/'paper_portfolio.sqlite3';portfolio.write_bytes(b'separate private fixture')
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    assert not gateway.recovery_report()['journal_present']
    assert portfolio.read_bytes()==b'separate private fixture'
    assert set(tmp_path.iterdir())=={portfolio}


def test_linked_recovery_uses_no_writable_schema_path_and_preserves_all_bytes(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch);gateway.simulate_priced_fill('buy','a',1,1,25)
    before={p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    monkeypatch.setattr(gateway,'_connect',lambda:pytest.fail('read used writable journal connection'))
    monkeypatch.setattr(execution_portfolio,'attach',lambda *_:pytest.fail('read used writable portfolio attach'))
    result=gateway.recovery_report()
    assert result['journal_present'] and result['portfolio_reconciled']
    assert result['status']=='REPLAY_VERIFIED' and gateway.order_report('buy')['read_only']
    assert gateway.session_orders('a'*16)==[]
    assert {p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}==before
    # All read handles must close for Windows backup/replacement workflows.
    for path in before:
        moved=path.with_suffix('.moved');path.rename(moved);moved.rename(path)


def test_session_order_snapshot_is_stable_read_only_and_changes_with_history(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    gateway.initialize_account(10000)
    before_files={path:path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    first=gateway.session_orders_snapshot('a'*16)
    second=gateway.session_orders_snapshot('a'*16)
    assert first==second and first['journal_present'] and first['commands']==1
    assert {path:path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}==before_files
    gateway.emergency_halt()
    final=gateway.session_orders_snapshot('a'*16)
    assert final['commands']==2 and final['snapshot_sha256']!=first['snapshot_sha256']


def test_write_operation_pins_dynamic_root_before_emergency_check(tmp_path,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(first));gateway=DurableOrderGateway();seen=[]
    def retarget(path):
        seen.append(path);monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(second));return False
    monkeypatch.setattr(emergency_intent,'requested',retarget)
    gateway.submit('one','BTC-USD','BUY',100,tier='terrestrial')
    assert seen==[first/'order_lifecycle.sqlite3']
    assert (first/'order_lifecycle.sqlite3').exists()
    assert (first/'paper_portfolio.sqlite3').exists() and not second.exists()


def test_write_operation_keeps_portfolio_with_opened_order_root(tmp_path,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(first));gateway=DurableOrderGateway()
    original=gateway._connect
    def retarget_after_connect(path=None):
        db=original(path);monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(second));return db
    monkeypatch.setattr(gateway,'_connect',retarget_after_connect)
    gateway.initialize_account(10000)
    assert (first/'order_lifecycle.sqlite3').exists()
    assert (first/'paper_portfolio.sqlite3').exists() and not second.exists()


def test_linked_read_keeps_portfolio_with_opened_order_root(tmp_path,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(first));gateway=DurableOrderGateway()
    gateway.initialize_account(10000);gateway.link_portfolio();original=gateway._restore
    def retarget_after_restore(db):
        restored=original(db);monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(second));return restored
    monkeypatch.setattr(gateway,'_restore',retarget_after_restore)
    report=gateway.recovery_report()
    assert report['portfolio_reconciled'] and report['journal_present']
    assert not second.exists()


def test_write_fails_closed_if_storage_root_replaced_during_cut_check(tmp_path,monkeypatch):
    root=tmp_path/'state';root.mkdir();retired=tmp_path/'retired'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root));gateway=DurableOrderGateway()
    def replace_root(path):
        root.rename(retired);root.mkdir();return False
    monkeypatch.setattr(emergency_intent,'requested',replace_root)
    with pytest.raises(ValueError,match='ORDER_STORAGE_ROOT_CHANGED'):
        gateway.submit('one','BTC-USD','BUY',100,tier='terrestrial')
    assert not (root/'order_lifecycle.sqlite3').exists()
    assert not (root/'paper_portfolio.sqlite3').exists()
    assert not (retired/'order_lifecycle.sqlite3').exists()


def test_write_fails_closed_if_storage_root_replaced_after_order_open(tmp_path,monkeypatch):
    root=tmp_path/'state';root.mkdir();retired=tmp_path/'retired'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root));gateway=DurableOrderGateway();original=gateway._connect
    def replace_after_connect(path=None):
        db=original(path);root.rename(retired);root.mkdir();return db
    monkeypatch.setattr(gateway,'_connect',replace_after_connect)
    with pytest.raises(ValueError,match='ORDER_STORAGE_ROOT_CHANGED'):
        gateway.initialize_account(10000)
    assert not (root/'order_lifecycle.sqlite3').exists()
    assert not (root/'paper_portfolio.sqlite3').exists()
    assert (retired/'order_lifecycle.sqlite3').exists()
    with sqlite3.connect(retired/'order_lifecycle.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM commands').fetchone()==(0,)


def test_linked_read_fails_closed_if_storage_root_replaced_during_restore(tmp_path,monkeypatch):
    root=tmp_path/'state';root.mkdir();retired=tmp_path/'retired'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root));gateway=DurableOrderGateway()
    gateway.initialize_account(10000);gateway.link_portfolio();original=gateway._restore
    def replace_after_restore(db):
        restored=original(db);root.rename(retired);root.mkdir();return restored
    monkeypatch.setattr(gateway,'_restore',replace_after_restore)
    with pytest.raises(ValueError,match='ORDER_STORAGE_ROOT_CHANGED'):
        gateway.recovery_report()
    assert not any(root.iterdir())
    assert (retired/'order_lifecycle.sqlite3').exists()
    assert (retired/'paper_portfolio.sqlite3').exists()


def test_write_rejects_symlink_storage_root(tmp_path,monkeypatch):
    target=tmp_path/'target';target.mkdir();alias=tmp_path/'alias'
    try:alias.symlink_to(target,target_is_directory=True)
    except (OSError,NotImplementedError):pytest.skip('directory symlink unavailable')
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(alias));gateway=DurableOrderGateway()
    with pytest.raises(ValueError,match='ORDER_STORAGE_ROOT_SYMLINK'):
        gateway.initialize_account(10000)
    assert not any(target.iterdir())


def test_write_rejects_non_directory_storage_root(tmp_path,monkeypatch):
    root=tmp_path/'state';root.write_text('not a directory')
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root));gateway=DurableOrderGateway()
    with pytest.raises(ValueError,match='ORDER_STORAGE_ROOT_DIRECTORY_REQUIRED'):
        gateway.initialize_account(10000)
    assert root.read_text()=='not a directory'


@pytest.mark.parametrize('kind',['missing','corrupt','incomplete','symlink','fifo'])
def test_linked_accounting_failure_never_recreates_or_repairs_portfolio(tmp_path,monkeypatch,kind):
    if kind in ('symlink','fifo') and os.name=='nt':pytest.skip('No Windows symlink privilege or FIFO assumed')
    gateway=linked(tmp_path,monkeypatch);path=tmp_path/'paper_portfolio.sqlite3';path.unlink()
    if kind=='corrupt':path.write_bytes(b'corrupt fixture')
    elif kind=='incomplete':
        with closing(sqlite3.connect(path)) as db:db.execute('CREATE TABLE unrelated(value TEXT)')
    elif kind=='symlink':
        target=tmp_path/'target';target.write_bytes(b'private fixture');path.symlink_to(target)
    elif kind=='fifo':os.mkfifo(path)
    before={p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    for read in (gateway.recovery_report,lambda:gateway.session_orders('a'*16)):
        with pytest.raises((OSError,ValueError,sqlite3.Error)):read()
    assert {p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}==before
    if kind=='missing':assert not path.exists()


@pytest.mark.parametrize('kind',['corrupt','incomplete','directory','symlink','fifo'])
def test_bad_journal_fails_with_private_fixed_http_error_and_no_schema_repair(tmp_path,monkeypatch,kind):
    import nexus.app as backend
    if kind in ('symlink','fifo') and os.name=='nt':pytest.skip('No Windows symlink privilege or FIFO assumed')
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));path=tmp_path/'order_lifecycle.sqlite3'
    if kind=='corrupt':path.write_bytes(b'corrupt private fixture')
    elif kind=='incomplete':
        with closing(sqlite3.connect(path)) as db:db.execute('CREATE TABLE unrelated(value TEXT)')
    elif kind=='directory':path.mkdir()
    elif kind=='symlink':
        target=tmp_path/'target';target.write_bytes(b'private fixture');path.symlink_to(target)
    else:os.mkfifo(path)
    before={p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    gateway=DurableOrderGateway(path);monkeypatch.setattr(backend,'research_order_gateway',gateway)
    response=TestClient(backend.app).get('/api/execution/recovery')
    assert response.status_code==503 and response.json()=={'detail':'ORDER_RECOVERY_UNAVAILABLE'}
    assert {p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}==before


def test_main_and_attached_portfolio_reject_sql_writes_during_read(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch)
    with gateway._read_connection() as db:
        portfolio=execution_portfolio.attach_read_only(db,tmp_path/'paper_portfolio.sqlite3')
        with pytest.raises(sqlite3.OperationalError,match='readonly'):db.execute('DELETE FROM commands')
        with pytest.raises(sqlite3.OperationalError,match='readonly'):portfolio.execute('DELETE FROM ledger')
    assert gateway.recovery_report()['portfolio_reconciled']


def test_linked_read_rejects_identical_leaf_replacement_during_attach(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch);path=tmp_path/'paper_portfolio.sqlite3'
    replacement=tmp_path/'replacement.sqlite3';replacement.write_bytes(path.read_bytes())
    retired=tmp_path/'retired.sqlite3';original=Path.resolve;changed=[]
    def replace_then_resolve(self,*args,**kwargs):
        if self==path and not changed:
            path.rename(retired);replacement.rename(path);changed.append(True)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',replace_then_resolve)
    with pytest.raises(ValueError,match='LINKED_PORTFOLIO_CHANGED'):
        gateway.recovery_report()
    assert changed and path.read_bytes()==retired.read_bytes()


@pytest.mark.skipif(os.name=='nt',reason='Windows denies replacing an attached SQLite file')
def test_linked_snapshot_rechecks_leaf_identity_after_attach(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch);path=tmp_path/'paper_portfolio.sqlite3'
    replacement=tmp_path/'replacement.sqlite3';replacement.write_bytes(path.read_bytes())
    retired=tmp_path/'retired.sqlite3'
    with gateway._read_connection() as db:
        portfolio=execution_portfolio.attach_read_only(db,path)
        path.rename(retired);replacement.rename(path)
        with pytest.raises(ValueError,match='LINKED_PORTFOLIO_CHANGED'):
            execution_portfolio.snapshot(portfolio,durable_orders.MAX_PORTFOLIO_SNAPSHOT_ROWS)


@pytest.mark.skipif(os.name=='nt',reason='Windows denies replacing an attached SQLite file')
def test_linked_identity_change_overrides_semantic_reader_failure(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch);path=tmp_path/'paper_portfolio.sqlite3'
    replacement=tmp_path/'replacement.sqlite3';replacement.write_bytes(path.read_bytes())
    retired=tmp_path/'retired.sqlite3'
    with gateway._read_connection() as db:
        portfolio=execution_portfolio.attach_read_only(db,path)
        def replace_then_fail(_):
            path.rename(retired);replacement.rename(path)
            raise ValueError('lower-priority semantic failure')
        monkeypatch.setattr(execution_portfolio,'require_safe_ledger',replace_then_fail)
        with pytest.raises(ValueError,match='^LINKED_PORTFOLIO_CHANGED$'):
            execution_portfolio.compare(portfolio,{'initial':'10000','cash':'10000','positions':{}})


def test_session_snapshot_binds_exact_linked_portfolio_content(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch);session='a'*16
    before=gateway.session_orders_snapshot(session)
    assert before['portfolio_linked'] and before['portfolio_rows']>0
    assert len(before['journal_sha256'])==len(before['portfolio_sha256'])==64
    with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
        db.execute("INSERT INTO meta VALUES ('review_fixture','changed')")
    after=gateway.session_orders_snapshot(session)
    assert after['journal_sha256']==before['journal_sha256']
    assert after['portfolio_sha256']!=before['portfolio_sha256']
    assert after['snapshot_sha256']!=before['snapshot_sha256']


def test_session_snapshot_caps_linked_ledger_before_semantic_audit(tmp_path,monkeypatch):
    gateway=linked(tmp_path,monkeypatch)
    monkeypatch.setattr(durable_orders,'MAX_PORTFOLIO_SNAPSHOT_ROWS',1)
    monkeypatch.setattr(execution_portfolio,'compare',lambda *_:pytest.fail('semantic audit ran before intake cap'))
    with pytest.raises(ValueError,match='PORTFOLIO_SNAPSHOT_CAP_EXCEEDED'):
        gateway.session_orders_snapshot('a'*16)


def test_unlinked_recovery_does_not_create_missing_portfolio(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    gateway.initialize_account(10000)
    portfolio=tmp_path/'paper_portfolio.sqlite3';portfolio.unlink()
    assert not gateway.recovery_report()['portfolio_reconciled'] and not portfolio.exists()
