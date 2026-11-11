"""Local start diagnostics never create accounting or reserve permission."""
import os
from pathlib import Path
import shutil
import sqlite3
import pytest
from fastapi.testclient import TestClient
from nexus import forward_preflight as pre, state
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure


@pytest.fixture
def local(tmp_path,monkeypatch):
    root=tmp_path/'data';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    runner=PaperForwardRunner(gateway,lambda *_:pytest.fail('Preflight fetched a quote'))
    return root,gateway,runner


def check(local,**options):
    root,gateway,runner=local
    return pre.build(gateway,runner,tier='terrestrial',portal_active=options.get('portal_active',True),halted=options.get('halted',False))


def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_pristine_preflight_never_creates_storage_or_selects_tier(local):
    root,_,_=local
    assert check(local,portal_active=False)['blockers']==['PORTAL_STOPPED'] and not root.exists()
    result=check(local);assert 'TIER_NOT_SELECTED' in result['blockers'] and not root.exists()
    assert all(result[k] is False for k in ('automatic_start','automatic_repair','running_permission_granted','external_owner_checked','fresh_prices_checked','provider_contacted','live_orders_enabled','money_movement_enabled'))


def test_ready_native_defaults_remain_only_configuration(local,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *_a,**_k:pytest.fail('Preflight contacted a service'))
    state.load_settings();root,_,runner=local;before=snapshot(root)
    result=check(local)
    assert result['local_checks_clear'] and result['will_prepare_unused_account'] and result['entry_mode']=='NEW_ENTRIES'
    assert snapshot(root)==before and set(before)=={'portal_settings.sqlite3'}
    assert not runner.status()['enabled'] and not runner.status()['ownership_held']


@pytest.mark.parametrize('kind',['off','small','bad-settings','cut','journal','wal','corrupt-order'])
def test_local_blockers_do_not_mutate_retained_files(local,kind):
    root,gateway,_=local;state.load_settings()
    expected={'off':'TIER_NOT_SELECTED','small':'TIER_LIMIT_TOO_SMALL','bad-settings':'SETTINGS_REVIEW_REQUIRED','cut':'TRADING_CUT_ACTIVE',
        'journal':'LOCAL_HISTORY_REVIEW_REQUIRED','wal':'LOCAL_HISTORY_REVIEW_REQUIRED','corrupt-order':'LOCAL_HISTORY_REVIEW_REQUIRED'}[kind]
    if kind=='off':configure('terrestrial',False)
    elif kind=='small':state.save_settings({**state.DEFAULTS,'terrestrial_allocation':0,'solar_allocation':85})
    elif kind=='bad-settings':
        with sqlite3.connect(root/'portal_settings.sqlite3') as db:db.execute("UPDATE configuration SET value='PRIVATE-FIXTURE' WHERE key='paper_budget'")
    elif kind=='cut':(root/'emergency_cut.intent').write_bytes(b'CUT')
    elif kind=='journal':(root/'order_lifecycle.sqlite3-journal').write_bytes(b'RETAINED')
    elif kind=='wal':(root/'portal_settings.sqlite3-wal').write_bytes(b'RETAINED')
    else:gateway._path().write_bytes(b'PRIVATE-CORRUPT-HISTORY')
    before=snapshot(root);result=check(local)
    assert not result['local_checks_clear'] and expected in result['blockers'] and snapshot(root)==before
    assert 'PRIVATE' not in str(result)


@pytest.mark.parametrize('capital',[1000,10000])
def test_existing_unused_manual_portfolio_checked_without_initializing_orders(local,capital):
    from nexus.paper_portfolio import initialize
    root,gateway,_=local;state.load_settings();initialize(capital);before=snapshot(root)
    result=check(local)
    assert result['local_checks_clear']==(capital==10000)
    if capital!=10000:assert 'ACCOUNT_LINK_REVIEW_REQUIRED' in result['blockers']
    assert snapshot(root)==before and not gateway._path().exists()


def test_preflight_rejects_identical_leaf_replacement_before_sqlite_open(local,monkeypatch):
    root,_,_=local;state.load_settings();path=root/'portal_settings.sqlite3'
    replacement=root/'replacement.sqlite3';replacement.write_bytes(path.read_bytes())
    retired=root/'retired.sqlite3';original=Path.resolve;changed=[]
    def replace_then_resolve(self,*args,**kwargs):
        if self==path and not changed:
            path.rename(retired);replacement.rename(path);changed.append(True)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',replace_then_resolve)
    with pytest.raises(ValueError,match='^PREFLIGHT_FILE_CHANGED$'):
        pre._read(path,lambda _db:pytest.fail('replacement reached semantic reader'))
    assert changed and path.read_bytes()==retired.read_bytes()


@pytest.mark.skipif(os.name=='nt',reason='Windows denies replacing an open SQLite file')
def test_preflight_rechecks_leaf_after_semantic_read(local):
    root,_,_=local;state.load_settings();path=root/'portal_settings.sqlite3'
    replacement=root/'replacement.sqlite3';replacement.write_bytes(path.read_bytes());retired=root/'retired.sqlite3'
    def replace(db):
        assert db.execute('SELECT COUNT(*) FROM configuration').fetchone()[0]
        path.rename(retired);replacement.rename(path)
        return 'must-not-return'
    with pytest.raises(ValueError,match='^PREFLIGHT_FILE_CHANGED$'):pre._read(path,replace)


@pytest.mark.skipif(os.name=='nt',reason='Windows denies replacing an open SQLite file')
def test_preflight_identity_change_overrides_ordinary_reader_failure(local):
    root,_,_=local;state.load_settings();path=root/'portal_settings.sqlite3'
    replacement=root/'replacement.sqlite3';replacement.write_bytes(path.read_bytes());retired=root/'retired.sqlite3'
    def replace_then_fail(_db):
        path.rename(retired);replacement.rename(path)
        raise ValueError('lower-priority semantic failure')
    with pytest.raises(ValueError,match='^PREFLIGHT_FILE_CHANGED$'):pre._read(path,replace_then_fail)


def test_preflight_process_control_signal_remains_observable(local):
    root,_,_=local;state.load_settings();path=root/'portal_settings.sqlite3'
    with pytest.raises(KeyboardInterrupt):pre._read(path,lambda _db:(_ for _ in ()).throw(KeyboardInterrupt()))


def test_preflight_rejects_sqlite_reported_path_mismatch(local,monkeypatch):
    root,_,_=local;state.load_settings();path=root/'portal_settings.sqlite3'
    other=root/'other.sqlite3';other.write_bytes(path.read_bytes());connect=sqlite3.connect
    class MismatchedConnection:
        def __init__(self,*args,**kwargs):self.db=connect(*args,**kwargs)
        def set_progress_handler(self,*args):return self.db.set_progress_handler(*args)
        def close(self):return self.db.close()
        def execute(self,statement,*args):
            if statement=='PRAGMA database_list':return [(0,'main',str(other))]
            return self.db.execute(statement,*args)
    monkeypatch.setattr(pre.sqlite3,'connect',MismatchedConnection)
    with pytest.raises(ValueError,match='^PREFLIGHT_CONNECTION_MISMATCH$'):
        pre._read(path,lambda _db:pytest.fail('mismatched connection reached semantic reader'))


@pytest.mark.skipif(os.name=='nt',reason='Windows directory replacement semantics differ')
def test_preflight_rejects_identical_storage_root_replacement_between_snapshots(local,monkeypatch):
    root,gateway,_=local;state.load_settings();retired=root.parent/'retired-data'
    original=gateway.recovery_report
    def replace_after_recovery():
        report=original();root.rename(retired);shutil.copytree(retired,root)
        return report
    monkeypatch.setattr(gateway,'recovery_report',replace_after_recovery)
    result=check(local)
    assert result['blockers']==['LOCAL_HISTORY_REVIEW_REQUIRED']
    assert not result['running_permission_granted'] and not result['live_orders_enabled']


@pytest.mark.skipif(os.name=='nt',reason='Windows directory replacement semantics differ')
def test_preflight_root_change_overrides_ordinary_failure(local):
    root,_,_=local;state.load_settings();retired=root.parent/'retired-data'
    with pytest.raises(ValueError,match='^PREFLIGHT_STORAGE_ROOT_CHANGED$'):
        with pre._root_scope(root):
            root.rename(retired);root.mkdir()
            raise ValueError('lower-priority semantic failure')


def test_preflight_root_scope_does_not_create_missing_storage(local):
    root,_,_=local
    with pytest.raises(ValueError,match='^PREFLIGHT_STORAGE_ROOT_CHANGED$'):
        with pre._root_scope(root):
            assert not root.exists();root.mkdir()


def test_preflight_root_scope_preserves_process_control_signal(local):
    root,_,_=local;state.load_settings()
    with pytest.raises(KeyboardInterrupt):
        with pre._root_scope(root):raise KeyboardInterrupt


def test_linked_holdings_can_start_exit_monitoring_with_bad_budget_or_tier_off(tmp_path,monkeypatch):
    from test_paper_forward import setup,warmup
    runner,gateway,clock=setup(tmp_path,monkeypatch);assert warmup(runner,clock)['decision']=='BUY';runner.stop()
    configure('terrestrial',False)
    with sqlite3.connect(tmp_path/'portal_settings.sqlite3') as db:db.execute("INSERT OR REPLACE INTO configuration VALUES('paper_budget','PRIVATE-FIXTURE')")
    before=snapshot(tmp_path)
    result=pre.build(gateway,runner,tier='terrestrial',portal_active=True,halted=False)
    assert result['local_checks_clear'] and result['entry_mode']=='RETAINED_EXIT_MONITORING'
    assert not result['will_prepare_unused_account'] and snapshot(tmp_path)==before


def test_active_worker_and_api_diagnostics_never_authorize_or_write(local,monkeypatch):
    from nexus import app as backend
    root,gateway,runner=local;state.load_settings();before=snapshot(root)
    monkeypatch.setattr(backend,'research_order_gateway',gateway);monkeypatch.setattr(backend,'paper_forward_runner',runner)
    monkeypatch.setattr(backend,'_paused',False);monkeypatch.setattr(backend,'_halted',False)
    client=TestClient(backend.app)
    response=client.get('/api/paper/forward/preflight')
    assert response.status_code==200 and response.json()['local_checks_clear'] and response.headers['cache-control']=='no-store'
    assert client.get('/api/paper/forward/preflight?tier=unknown').status_code==422
    assert client.post('/api/paper/forward/preflight').status_code==405
    assert snapshot(root)==before
    original=runner.status
    monkeypatch.setattr(runner,'status',lambda:{**original(),'worker_alive':True})
    assert check(local)['blockers']==['RUNNER_ALREADY_ACTIVE'] and snapshot(root)==before
