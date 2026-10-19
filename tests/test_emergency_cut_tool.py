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
    assert report['passed'] and report['emergency_cut_state']=='VERIFIED'
    assert report['intent_preceded_durable_halt'] and report['emergency_intent_present']
    assert report['durable_order_halt_confirmed'] and report['halted']
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
