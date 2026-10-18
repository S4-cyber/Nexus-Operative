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
    assert all(report[k] is False for k in trial.FLAGS)
    assert not ready.build(path,hours=168,now=START+168*3600)['quote_capacity']['room_for_additional_requested_window']


def test_retained_injected_history_remains_review_and_file_bytes_unchanged(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True),(10,True)],trial.INJECTED,'STOPPED')]);before=snapshot(tmp_path)
    report=ready.build(path,hours=1,now=START+3600)
    assert report['review']['injected_observations']==3 and not report['review']['declared_coverage_target_met']
    assert report['quote_capacity']['retained']==3 and report['quote_capacity']['remaining']==99997
    assert snapshot(tmp_path)==before
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
