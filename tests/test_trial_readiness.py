"""Installed-API coverage diagnostics are not a trial pass or permission."""
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import pytest
from fastapi.testclient import TestClient
from nexus import trial_readiness as ready
from nexus import forward_trial_review as trial
from test_forward_trial_review import database,START


def snapshot(path):return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}


def test_absent_history_forecasts_quote_room_without_creating_anything(tmp_path):
    path=tmp_path/'unused'/'paper_forward.sqlite3'
    report=ready.build(path,now=START+72*3600)
    assert report['status']=='NO_RETAINED_HISTORY' and report['review'] is None and not path.parent.exists()
    cap=report['quote_capacity'];assert cap['estimated_additional_observations']==51841 and cap['room_for_additional_requested_window']
    sessions=report['session_capacity'];assert sessions['retained']==0 and sessions['remaining']==sessions['limit'] and sessions['room_for_additional_session']
    orders=report['order_capacity']
    assert not orders['journal_present'] and orders['next_cycle_reserve_clear']
    assert orders['total_commands_remaining']==10000
    assert orders['control_commands_reserved']==orders['required_control_command_reserve']==1001
    assert orders['emergency_control_reserve_clear']
    assert orders['commands_per_cycle_upper_bound']==12 and orders['intents_per_cycle_upper_bound']==2
    assert orders['estimated_upper_bound_commands']==51841*12 and not orders['room_for_conservative_requested_window']
    owner=report['local_owner_quiescence']
    assert owner['checked'] and owner['state']=='OWNER_PATH_ABSENCE_BOUND'
    assert owner['retained_history_requires_owner_lock'] and not owner['owner_file_created']
    assert all(report[k] is False for k in trial.FLAGS)
    assert not ready.build(path,hours=168,now=START+168*3600)['quote_capacity']['room_for_additional_requested_window']


def test_owner_absence_binding_survives_windows_style_path_normalization(tmp_path,monkeypatch):
    root=tmp_path/'unused';path=root/'paper_forward.sqlite3'
    owner=root/'paper_forward.owner.lock'
    normalized_owner=root/'PAPER_FORWARD.OWNER.LOCK'
    original_resolve=Path.resolve
    def windows_style_resolve(self,*args,**kwargs):
        if self==owner:return normalized_owner
        return original_resolve(self,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',windows_style_resolve)
    report=ready.build(path,now=START+72*3600)
    assert report['status']=='NO_RETAINED_HISTORY'
    assert report['local_owner_quiescence']['state']=='OWNER_PATH_ABSENCE_BOUND'
    assert not root.exists()


def test_retained_injected_history_remains_review_and_file_bytes_unchanged(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True),(10,True)],trial.INJECTED,'STOPPED')]);before=snapshot(tmp_path)
    from nexus.forward_ownership import ForwardOwnership
    owner=ForwardOwnership(tmp_path);owner.acquire();owner.release();before=snapshot(tmp_path)
    report=ready.build(path,hours=1,now=START+3600)
    assert report['review']['injected_observations']==3 and not report['review']['declared_coverage_target_met']
    assert report['review']['retained_sessions']==1 and report['session_capacity']['retained']==1
    assert report['quote_capacity']['retained']==3 and report['quote_capacity']['remaining']==99997
    assert snapshot(tmp_path)==before
    assert report['local_owner_quiescence']['state']=='LOCAL_OWNER_LEASE_HELD_DURING_REVIEW'
    assert all(report[k] is False and report['review'][k] is False for k in trial.FLAGS)


@pytest.mark.parametrize('kind',['journal','wal','shm','corrupt','hardlink','oversized'])
def test_unsafe_history_is_not_opened_recovered_or_echoed(tmp_path,monkeypatch,kind):
    path=database(tmp_path,monkeypatch,[])
    if kind in ('journal','wal','shm'):Path(str(path)+'-'+kind).write_bytes(b'PRIVATE_RETAINED_SIDECAR')
    elif kind=='corrupt':path.write_bytes(b'PRIVATE_BAD_HISTORY')
    elif kind=='hardlink':os.link(path,tmp_path/'retained-copy')
    else:
        from nexus.sqlite_journal_guard import check_existing
        monkeypatch.setattr('nexus.sqlite_journal_guard.check_existing',lambda path,maximum,prefix:check_existing(path,16,prefix))
    before=snapshot(tmp_path)
    with pytest.raises((ValueError,sqlite3.Error)):ready.build(path,now=START+3600)
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('hours',[0,2,True,169])
def test_unsupported_windows_fail_without_io(tmp_path,hours):
    with pytest.raises(ValueError):ready.build(tmp_path/'missing',hours=hours,now=START)
    assert not list(tmp_path.iterdir())


def test_api_fixed_path_errors_no_start_and_missing_history_no_creation(tmp_path,monkeypatch):
    from nexus import app as backend
    from nexus.durable_orders import DurableOrderGateway
    root=tmp_path/'unused';monkeypatch.setattr(backend,'research_order_gateway',DurableOrderGateway(root/'orders.sqlite3'))
    client=TestClient(backend.app)
    response=client.get('/api/paper/forward/trial-readiness')
    assert response.status_code==200 and response.json()['review'] is None and response.headers['cache-control']=='no-store'
    assert client.get('/api/paper/forward/trial-readiness?hours=2').status_code==422
    assert client.post('/api/paper/forward/trial-readiness').status_code==405
    assert not root.exists()
    root.mkdir();path=root/'paper_forward.sqlite3';path.write_bytes(b'PRIVATE_BROKEN_HISTORY');before=snapshot(root)
    response=client.get('/api/paper/forward/trial-readiness')
    assert response.status_code==503 and response.json()=={'detail':'TRIAL_HISTORY_REVIEW_REQUIRED'}
    assert snapshot(root)==before


def test_actual_trial_controller():
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual controller')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/trial_readiness_smoke.cjs')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_order_capacity_snapshot_is_read_only_and_uses_retained_position_reserve(tmp_path):
    class Gateway:
        def recovery_report(self):
            return dict(journal_present=True,capacity=dict(ordinary_commands_remaining=20,intents_remaining=4,
                total_commands_remaining=1021,control_commands_reserved=1001,
                lifecycle_intake_allowed=True),execution_account=dict(account=dict(positions={'BTC-USD':'fixture'})))
    report=ready.build(tmp_path/'unused'/'paper_forward.sqlite3',hours=1,now=START+3600,order_gateway=Gateway())
    capacity=report['order_capacity']
    assert capacity['retained_position'] and capacity['commands_per_cycle_upper_bound']==4
    assert capacity['intents_per_cycle_upper_bound']==1 and capacity['next_cycle_reserve_clear']
    assert not capacity['room_for_conservative_requested_window'] and not (tmp_path/'unused').exists()


def test_depleted_control_reserve_blocks_trial_capacity_without_io(tmp_path):
    class Gateway:
        def recovery_report(self):
            return dict(journal_present=True,capacity=dict(ordinary_commands_remaining=0,
                intents_remaining=4,total_commands_remaining=1000,control_commands_reserved=1000,
                lifecycle_intake_allowed=True),execution_account=dict(account=None))
    report=ready.build(tmp_path/'unused'/'paper_forward.sqlite3',hours=1,
        now=START+3600,order_gateway=Gateway())
    capacity=report['order_capacity']
    assert capacity['required_control_command_reserve']==1001
    assert not capacity['emergency_control_reserve_clear']
    assert not capacity['next_cycle_reserve_clear']
    assert not capacity['room_for_conservative_requested_window']
    assert not (tmp_path/'unused').exists()


@pytest.mark.parametrize('capacity',[
    dict(ordinary_commands_remaining=20,intents_remaining=4,
        total_commands_remaining=1021,lifecycle_intake_allowed=True),
    dict(ordinary_commands_remaining=20,intents_remaining=4,
        total_commands_remaining=1021,control_commands_reserved=1000,
        lifecycle_intake_allowed=True),
    dict(ordinary_commands_remaining=20,intents_remaining=4,
        total_commands_remaining=1021,control_commands_reserved=1001,
        lifecycle_intake_allowed=1),
])
def test_invalid_control_capacity_schema_fails_closed_without_io(tmp_path,capacity):
    class Gateway:
        def recovery_report(self):
            return dict(journal_present=False,capacity=capacity,
                execution_account=dict(account=None))
    with pytest.raises(ValueError,match='^TRIAL_READINESS_ORDER_CAPACITY_INVALID$'):
        ready.build(tmp_path/'unused'/'paper_forward.sqlite3',hours=1,
            now=START+3600,order_gateway=Gateway())
    assert not (tmp_path/'unused').exists()


def test_late_forward_history_appearance_invalidates_absent_review(tmp_path):
    path=tmp_path/'paper_forward.sqlite3'
    class Gateway:
        def recovery_report(self):
            path.write_bytes(b'LATE_RETAINED_HISTORY')
            return dict(journal_present=False,capacity=dict(ordinary_commands_remaining=20,
                intents_remaining=4,total_commands_remaining=1021,control_commands_reserved=1001,
                lifecycle_intake_allowed=True),
                execution_account=dict(account=None))
    with pytest.raises(ValueError,match='^TRIAL_READINESS_FILE_SET_CHANGED$'):
        ready.build(path,hours=1,now=START+3600,order_gateway=Gateway())


def test_late_order_journal_mutation_invalidates_composite_result(tmp_path,monkeypatch):
    from nexus.durable_orders import DurableOrderGateway
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    gateway.emergency_halt()
    original=DurableOrderGateway.recovery_report
    changed=False
    def mutate_after_review(self):
        nonlocal changed
        result=original(self)
        if not changed:
            changed=True
            self.reset();self.emergency_halt()
        return result
    monkeypatch.setattr(DurableOrderGateway,'recovery_report',mutate_after_review)
    with pytest.raises(ValueError,match='^TRIAL_READINESS_FILE_SET_CHANGED$'):
        ready.build(tmp_path/'paper_forward.sqlite3',hours=1,now=START+3600,
            order_gateway=gateway)


def test_late_portfolio_appearance_invalidates_composite_result(tmp_path,monkeypatch):
    from nexus.durable_orders import DurableOrderGateway
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    gateway.emergency_halt()
    original=DurableOrderGateway.recovery_report
    def create_after_review(self):
        result=original(self)
        (tmp_path/'paper_portfolio.sqlite3').write_bytes(b'LATE_PORTFOLIO')
        return result
    monkeypatch.setattr(DurableOrderGateway,'recovery_report',create_after_review)
    with pytest.raises(ValueError,match='^TRIAL_READINESS_FILE_SET_CHANGED$'):
        ready.build(tmp_path/'paper_forward.sqlite3',hours=1,now=START+3600,
            order_gateway=gateway)


def test_late_storage_root_replacement_invalidates_composite_result(tmp_path):
    root=tmp_path/'storage';root.mkdir();path=root/'paper_forward.sqlite3'
    class Gateway:
        def recovery_report(self):
            retired=tmp_path/'retired';root.rename(retired);root.mkdir()
            return dict(journal_present=False,capacity=dict(ordinary_commands_remaining=20,
                intents_remaining=4,total_commands_remaining=1021,control_commands_reserved=1001,
                lifecycle_intake_allowed=True),
                execution_account=dict(account=None))
    with pytest.raises(ValueError,match='^TRIAL_READINESS_STORAGE_ROOT_CHANGED$'):
        ready.build(path,hours=1,now=START+3600,order_gateway=Gateway())


def test_initial_sqlite_sidecar_is_rejected_without_mutation(tmp_path):
    path=tmp_path/'paper_forward.sqlite3';sidecar=Path(str(path)+'-journal')
    sidecar.write_bytes(b'PRIVATE_RETAINED_ROLLBACK')
    before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='^TRIAL_READINESS_SQLITE_SIDECAR_REVIEW_REQUIRED$'):
        ready.build(path,hours=1,now=START+3600)
    assert snapshot(tmp_path)==before


def test_process_control_signal_is_not_replaced_by_late_file_change(tmp_path):
    path=tmp_path/'paper_forward.sqlite3'
    class Gateway:
        def recovery_report(self):
            path.write_bytes(b'LATE_RETAINED_HISTORY')
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ready.build(path,hours=1,now=START+3600,order_gateway=Gateway())


def test_retained_history_without_existing_owner_lease_fails_closed(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[]);before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='^TRIAL_READINESS_OWNER_REVIEW_REQUIRED$'):
        ready.build(path,hours=1,now=START+3600)
    assert snapshot(tmp_path)==before


def test_active_forward_owner_blocks_trial_readiness_without_mutation(tmp_path):
    from nexus.forward_ownership import ForwardOwnership
    owner=ForwardOwnership(tmp_path);owner.acquire();before=snapshot(tmp_path)
    try:
        with pytest.raises(ValueError,match='^TRIAL_READINESS_OWNER_REVIEW_REQUIRED$'):
            ready.build(tmp_path/'paper_forward.sqlite3',hours=1,now=START+3600)
        assert snapshot(tmp_path)==before
    finally:owner.release()
