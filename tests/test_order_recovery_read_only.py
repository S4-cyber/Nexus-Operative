"""Actual local journal replay and linked-file checks, never provider access."""
from contextlib import closing
import os
import sqlite3

from fastapi.testclient import TestClient
import pytest
from nexus.durable_orders import DurableOrderGateway
from nexus import execution_portfolio
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


def test_unlinked_recovery_does_not_create_missing_portfolio(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    gateway.initialize_account(10000)
    portfolio=tmp_path/'paper_portfolio.sqlite3';portfolio.unlink()
    assert not gateway.recovery_report()['portfolio_reconciled'] and not portfolio.exists()
