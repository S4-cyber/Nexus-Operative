"""Standalone installed cut and running-loop latch acceptance; local paper only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from fastapi.testclient import TestClient
import pytest

from nexus import emergency_intent
from nexus import emergency_priority
from nexus.emergency_cut_tool import PUBLIC_KEYS
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure
from test_paper_forward import Clock,warmup

ROOT=Path(__file__).resolve().parents[1]


def command(data):
    return subprocess.run([sys.executable,str(ROOT/'portable/desktop.py'),'--emergency-stop'],
        cwd=ROOT,env={**os.environ,'NEXUS_PORTABLE_DATA':str(data)},capture_output=True,text=True,timeout=20)


def test_standalone_command_durably_cuts_without_starting_desktop_or_network(tmp_path):
    result=command(tmp_path)
    assert result.returncode==0 and not result.stderr
    report=json.loads(result.stdout)
    assert type(report) is dict and frozenset(report)==PUBLIC_KEYS
    assert report['passed'] and report['emergency_cut_state']=='VERIFIED'
    assert report['intent_preceded_durable_halt'] and report['emergency_intent_present']
    assert report['durable_order_halt_confirmed'] and report['halted']
    assert report['emergency_priority_attempted'] is (os.name=='nt')
    assert report['emergency_priority_confirmed'] is (os.name=='nt')
    assert report['emergency_priority_class']==('HIGH' if os.name=='nt' else 'STANDARD')
    assert report['data_root_path_pinned'] is True
    assert all(report[key] is False for key in ('desktop_started','network_requested',
        'credential_access_requested','broker_connected','live_orders_enabled',
        'money_movement_enabled','reset_available','automatic_restart'))
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    assert gateway.recovery_report()['halted']
    with pytest.raises(ValueError,match='EMERGENCY_CUT_REQUESTED'):
        gateway.submit('after-cut','BTC-USD','BUY',1,tier='terrestrial')


def test_external_cut_vetoes_inflight_quote_before_submit(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));configure('terrestrial',True)
    clock=Clock();gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    runner=PaperForwardRunner(gateway,clock.quote,lambda:clock.now);runner.prepare('terrestrial')
    warmup(runner,clock)
    before=[row['id'] for row in gateway.recovery_report()['orders']]
    entered=threading.Event();release=threading.Event();outcome=[]
    def blocked(product):
        entered.set();assert release.wait(10);return clock.quote(product)
    runner.fetcher=blocked;clock.now+=5;clock.price=102
    worker=threading.Thread(target=lambda:outcome.append(runner.single_cycle()))
    worker.start();assert entered.wait(5)
    stopped=command(tmp_path)
    assert stopped.returncode==0 and json.loads(stopped.stdout)['passed']
    assert runner.status()['cut_requested'] and not runner.status()['enabled']
    release.set();worker.join(5);assert not worker.is_alive()
    assert outcome[0]['reason']=='STOPPED_DURING_FETCH'
    after=gateway.recovery_report()
    assert after['halted'] and [row['id'] for row in after['orders']]==before
    # The still-running API adopts the external latch without an app/event-loop
    # restart and exposes the existing explicit, disengaged reset workflow.
    from nexus import app as api
    monkeypatch.setattr(api,'research_order_gateway',gateway)
    monkeypatch.setattr(api,'paper_forward_runner',runner)
    monkeypatch.setattr(api,'_halted',False);monkeypatch.setattr(api,'_cut_confirmed',False)
    monkeypatch.setattr(api,'_cut_pending',0);monkeypatch.setattr(api,'_paused',True)
    state=api.status()
    assert state['halted'] and state['emergency_cut_state']=='UNCONFIRMED'
    reset=TestClient(api.app).post('/api/simulate/halt/reset',json={'confirm_reset':True})
    assert reset.status_code==200 and reset.json()['emergency_cut_state']=='CLEAR'
    assert not emergency_intent.requested(gateway._path())
    assert not gateway.recovery_report()['halted'] and not runner.status()['enabled']


def test_unreadable_external_latch_check_fails_closed(tmp_path,monkeypatch):
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    runner=PaperForwardRunner(gateway)
    monkeypatch.setattr(emergency_intent,'requested',lambda *_:(_ for _ in ()).throw(PermissionError('PRIVATE')))
    assert runner.status()['cut_requested'] and not runner.status()['enabled']


def test_command_failure_does_not_echo_private_data_or_start_desktop(tmp_path):
    blocked=tmp_path/'PRIVATE_DATA_ROOT';blocked.write_text('not a directory')
    result=command(blocked)
    assert result.returncode==2 and result.stdout=='NEXUS_EMERGENCY_STOP_FAILED\n'
    assert not result.stderr and 'PRIVATE_DATA_ROOT' not in result.stdout


def test_priority_failure_never_prevents_latch_or_durable_halt(tmp_path,monkeypatch):
    monkeypatch.setattr(emergency_priority,'request',lambda:dict(
        attempted=True,confirmed=False,priority_class='STANDARD'))
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert report['passed'] and report['durable_order_halt_confirmed']
    assert report['emergency_priority_attempted'] and not report['emergency_priority_confirmed']
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


@pytest.mark.parametrize('failure',[RuntimeError('PRIORITY_API_FAILED'),None,
    {'attempted':True,'confirmed':True,'priority_class':'STANDARD'},
    {'attempted':False,'confirmed':False,'priority_class':'HIGH'},
    {'attempted':1,'confirmed':False,'priority_class':'STANDARD'}])
def test_unexpected_or_malformed_priority_result_never_prevents_cut(
        tmp_path,monkeypatch,failure):
    def broken_priority():
        if isinstance(failure,BaseException):raise failure
        return failure
    monkeypatch.setattr(emergency_priority,'request',broken_priority)
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert report['passed'] and report['durable_order_halt_confirmed']
    assert report['emergency_priority_attempted'] is (os.name=='nt')
    assert not report['emergency_priority_confirmed']
    assert report['emergency_priority_class']=='STANDARD'
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


def test_data_root_leaf_symlink_is_rejected_before_priority_or_storage(tmp_path,monkeypatch):
    actual=tmp_path/'actual';actual.mkdir();alias=tmp_path/'alias'
    try:alias.symlink_to(actual,target_is_directory=True)
    except OSError:pytest.skip('directory symlink unavailable')
    monkeypatch.setattr(emergency_priority,'request',lambda:pytest.fail('priority requested'))
    from nexus.emergency_cut_tool import activate
    with pytest.raises(ValueError,match='DATA_ROOT_SYMLINK'):activate(alias)
    assert not list(actual.iterdir())


def test_parent_alias_is_pinned_before_priority_and_cut_storage(tmp_path,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second';first.mkdir();second.mkdir()
    alias=tmp_path/'alias'
    try:alias.symlink_to(first,target_is_directory=True)
    except OSError:pytest.skip('directory symlink unavailable')
    original=emergency_priority.request
    def retarget():
        alias.unlink();alias.symlink_to(second,target_is_directory=True)
        return original()
    monkeypatch.setattr(emergency_priority,'request',retarget)
    from nexus.emergency_cut_tool import activate
    report=activate(alias/'nested')
    assert report['passed'] and report['data_root_path_pinned'] is True
    assert emergency_intent.requested(first/'nested'/'order_lifecycle.sqlite3')
    assert DurableOrderGateway(first/'nested'/'order_lifecycle.sqlite3').recovery_report()['halted']
    assert not list(second.iterdir())


def test_new_data_root_is_not_created_until_after_priority_request(tmp_path,monkeypatch):
    root=tmp_path/'new-state';events=[]
    def priority():
        assert not root.exists();events.append('priority')
        return dict(attempted=False,confirmed=False,priority_class='STANDARD')
    monkeypatch.setattr(emergency_priority,'request',priority)
    from nexus.emergency_cut_tool import activate
    report=activate(root)
    assert events==['priority'] and report['passed']
    assert report['data_root_path_pinned'] is True and root.is_dir()


def test_data_root_replacement_during_priority_cannot_claim_verified_cut(tmp_path,monkeypatch):
    root=tmp_path/'state';retired=tmp_path/'retired';root.mkdir()
    original=emergency_priority.request
    def replace_root():
        root.rename(retired);root.mkdir()
        return original()
    monkeypatch.setattr(emergency_priority,'request',replace_root)
    from nexus.emergency_cut_tool import activate
    report=activate(root)
    assert not report['passed'] and not report['halted']
    assert report['emergency_cut_state']=='UNCONFIRMED'
    assert report['data_root_path_pinned'] is False
    assert not list(root.iterdir()) and not list(retired.iterdir())


def test_data_root_replacement_after_intent_never_targets_replacement_store(tmp_path,monkeypatch):
    root=tmp_path/'state';retired=tmp_path/'retired';root.mkdir()
    original=emergency_intent.persist;calls=[]
    def replace_after_intent(order_path):
        original(order_path);calls.append(1)
        if len(calls)==1:
            root.rename(retired);root.mkdir()
    monkeypatch.setattr(emergency_intent,'persist',replace_after_intent)
    from nexus.emergency_cut_tool import activate
    report=activate(root)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert not report['intent_preceded_durable_halt']
    assert not report['durable_order_halt_confirmed']
    assert not report['emergency_intent_present']
    assert report['data_root_path_pinned'] is False
    assert emergency_intent.requested(retired/'order_lifecycle.sqlite3')
    assert not list(root.iterdir())


def test_storage_failure_retains_cut_until_explicit_retry_finishes_cleanup(tmp_path,monkeypatch):
    gateway=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    gateway.submit('storage-fault','BTC-USD','BUY',1,tier='terrestrial')
    original=DurableOrderGateway.emergency_halt
    monkeypatch.setattr(DurableOrderGateway,'emergency_halt',
        lambda self:(_ for _ in ()).throw(OSError('PRIVATE_STORAGE_FAULT')))
    from nexus.emergency_cut_tool import activate
    first=activate(tmp_path)
    assert not first['passed'] and first['halted']
    assert first['emergency_cut_state']=='UNCONFIRMED'
    assert first['intent_preceded_durable_halt'] and first['emergency_intent_present']
    assert not first['durable_order_halt_confirmed']
    with pytest.raises(ValueError,match='EMERGENCY_CUT_REQUESTED'):
        gateway.submit('post-fault','BTC-USD','BUY',1,tier='terrestrial')
    monkeypatch.setattr(DurableOrderGateway,'emergency_halt',original)
    before=gateway.recovery_report()
    assert not before['halted'] and before['orders'][0]['status']=='SIMULATED_ACK_ONLY'
    second=activate(tmp_path)
    assert second['passed'] and second['emergency_cut_state']=='VERIFIED'
    after=gateway.recovery_report()
    assert after['halted'] and after['orders'][0]['status']=='SIMULATED_CANCELLED'
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


def test_unexpected_first_intent_fault_still_attempts_halt_and_final_latch(
        tmp_path,monkeypatch):
    original=emergency_intent.persist;calls=[]
    def fail_once(order_path):
        calls.append(1)
        if len(calls)==1:raise LookupError('UNEXPECTED_INTENT_HELPER_FAULT')
        return original(order_path)
    monkeypatch.setattr(emergency_intent,'persist',fail_once)
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert not report['intent_preceded_durable_halt']
    assert report['durable_order_halt_confirmed'] and report['emergency_intent_present']
    assert report['halted'] and report['data_root_path_pinned']
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


def test_silent_first_intent_write_cannot_claim_pre_halt_ordering(
        tmp_path,monkeypatch):
    original=emergency_intent.persist;calls=[]
    def silently_drop_once(order_path):
        calls.append(1)
        if len(calls)==1:return None
        return original(order_path)
    monkeypatch.setattr(emergency_intent,'persist',silently_drop_once)
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert not report['intent_preceded_durable_halt']
    assert report['durable_order_halt_confirmed'] and report['emergency_intent_present']
    assert report['halted'] and report['data_root_path_pinned']
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


def test_malformed_pre_halt_intent_read_cannot_claim_ordering(
        tmp_path,monkeypatch):
    original=emergency_intent.requested;calls=[]
    def malformed_once(order_path):
        calls.append(1)
        if len(calls)==1:return 1
        return original(order_path)
    monkeypatch.setattr(emergency_intent,'requested',malformed_once)
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert not report['intent_preceded_durable_halt']
    assert report['durable_order_halt_confirmed'] and report['emergency_intent_present']
    assert report['halted'] and report['data_root_path_pinned']


def test_cleared_pre_halt_intent_cannot_claim_handoff_ordering(
        tmp_path,monkeypatch):
    original=emergency_intent.requested;calls=[]
    def clear_after_observation(order_path):
        calls.append(1)
        present=original(order_path)
        if len(calls)==1:emergency_intent.clear(order_path)
        return present
    monkeypatch.setattr(emergency_intent,'requested',clear_after_observation)
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert not report['intent_preceded_durable_halt']
    assert report['durable_order_halt_confirmed'] and report['emergency_intent_present']
    assert report['halted'] and report['data_root_path_pinned']
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


def test_cleared_final_intent_cannot_claim_presence_or_verified_cut(
        tmp_path,monkeypatch):
    from nexus import emergency_cut_tool
    original=emergency_cut_tool._intent_identity;calls=[]
    def clear_after_final_identity_sample(order_path):
        calls.append(1)
        identity=original(order_path)
        if len(calls)==2:emergency_intent.clear(order_path)
        return identity
    monkeypatch.setattr(emergency_cut_tool,'_intent_identity',
        clear_after_final_identity_sample)
    report=emergency_cut_tool.activate(tmp_path)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert report['intent_preceded_durable_halt']
    assert report['durable_order_halt_confirmed'] and report['halted']
    assert not report['emergency_intent_present']
    assert report['data_root_path_pinned'] and len(calls)==2
    assert not emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


@pytest.mark.parametrize('outcome',[LookupError('UNEXPECTED_GATEWAY_FAULT'),None,
    {'halted':1}])
def test_unexpected_or_malformed_halt_acknowledgement_keeps_latch_unconfirmed(
        tmp_path,monkeypatch,outcome):
    def broken_halt(_self):
        if isinstance(outcome,BaseException):raise outcome
        return outcome
    monkeypatch.setattr(DurableOrderGateway,'emergency_halt',broken_halt)
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert report['intent_preceded_durable_halt']
    assert not report['durable_order_halt_confirmed']
    assert report['emergency_intent_present'] and report['halted']
    assert report['data_root_path_pinned']
    assert emergency_intent.requested(tmp_path/'order_lifecycle.sqlite3')


def test_unexpected_intent_status_fault_never_escapes_emergency_boundary(
        tmp_path,monkeypatch):
    monkeypatch.setattr(DurableOrderGateway,'emergency_halt',
        lambda _self:(_ for _ in ()).throw(LookupError('UNEXPECTED_GATEWAY_FAULT')))
    monkeypatch.setattr(emergency_intent,'requested',
        lambda *_:(_ for _ in ()).throw(LookupError('UNEXPECTED_STATUS_FAULT')))
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert not report['passed'] and report['emergency_cut_state']=='UNCONFIRMED'
    assert not report['intent_preceded_durable_halt']
    assert not report['durable_order_halt_confirmed']
    assert not report['emergency_intent_present'] and not report['halted']
    assert report['data_root_path_pinned']


@pytest.mark.parametrize('outcome',[None,1,'present',{},[]])
def test_malformed_intent_status_cannot_confirm_or_change_boolean_schema(
        tmp_path,monkeypatch,outcome):
    monkeypatch.setattr(emergency_intent,'requested',lambda *_:outcome)
    from nexus.emergency_cut_tool import activate
    report=activate(tmp_path)
    assert not report['intent_preceded_durable_halt']
    assert report['durable_order_halt_confirmed']
    assert report['halted'] is True
    assert report['emergency_intent_present'] is False
    assert report['passed'] is False
    assert report['emergency_cut_state']=='UNCONFIRMED'
    assert all(type(report[key]) is bool for key in (
        'passed','halted','intent_preceded_durable_halt',
        'emergency_intent_present','durable_order_halt_confirmed',
        'data_root_path_pinned'))
