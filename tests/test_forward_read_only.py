"""Passive forward diagnostics must never create, repair or migrate evidence."""
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient
import pytest
from nexus.paper_forward import PaperForwardRunner
from test_paper_forward import setup


def test_missing_reads_and_unknown_session_leave_storage_absent(tmp_path,monkeypatch):
    import nexus.app as backend
    root=tmp_path/'missing';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    runner=PaperForwardRunner();monkeypatch.setattr(backend,'paper_forward_runner',runner)
    assert runner.history()==[] and runner.sessions()['sessions']==[]
    capacity=runner.storage_capacity()
    assert capacity['sessions_retained']==capacity['quote_observations_retained']==0
    assert capacity['read_only'] and not capacity['automatic_deletion']
    assert runner.diagnostics()==dict(history=[],storage_capacity=capacity)
    with pytest.raises(ValueError,match='UNKNOWN_FORWARD_SESSION'):runner.evidence_report('a'*16)
    for path in ('/api/paper/forward','/api/paper/forward/sessions'):
        response=TestClient(backend.app).get(path)
        assert response.status_code==200 and not response.json().get('enabled',False)
    assert not root.exists()


@pytest.mark.parametrize('kind',['corrupt','incomplete','directory','symlink'])
def test_bad_existing_evidence_is_not_reported_as_empty_or_repaired(tmp_path,monkeypatch,kind):
    import nexus.app as backend
    import os
    if kind=='symlink' and os.name=='nt':pytest.skip('Windows CI does not assume symlink privilege')
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));path=tmp_path/'paper_forward.sqlite3'
    if kind=='corrupt':path.write_bytes(b'corrupt fixture')
    elif kind=='incomplete':
        with sqlite3.connect(path) as db:db.execute('CREATE TABLE unrelated (value TEXT)')
    elif kind=='directory':path.mkdir()
    else:
        target=tmp_path/'target';target.write_bytes(b'private fixture');path.symlink_to(target)
    before=path.read_bytes() if path.is_file() else None
    runner=PaperForwardRunner();monkeypatch.setattr(backend,'paper_forward_runner',runner)
    for method in (runner.history,runner.sessions,runner.storage_capacity,runner.diagnostics):
        with pytest.raises((ValueError,OSError,sqlite3.Error)):method()
    response=TestClient(backend.app).get('/api/paper/forward')
    assert response.status_code==503 and response.json()=={'detail':'FORWARD_DIAGNOSTICS_UNAVAILABLE'}
    if before is not None:assert path.read_bytes()==before


def test_existing_diagnostics_do_not_run_schema_setup_and_close_connections(tmp_path,monkeypatch):
    runner,gateway,clock=setup(tmp_path,monkeypatch);runner.single_cycle();runner.stop()
    path=tmp_path/'paper_forward.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(runner,'_db',lambda:pytest.fail('diagnostic attempted a writable/schema connection'))
    assert runner.sessions()['sessions'] and runner.history()
    result=runner.diagnostics()
    assert result['history']==runner.history()
    assert result['storage_capacity']==runner.storage_capacity()
    assert runner.evidence_report(runner.status()['session'])['read_only']
    assert path.read_bytes()==before
    # Windows refuses to replace an open SQLite handle; deterministic closure
    # also matters to backup/reinstall workflows on that platform.
    moved=tmp_path/'moved.sqlite3';path.rename(moved);moved.rename(path)


def test_combined_diagnostics_share_one_sqlite_snapshot_during_real_wal_append(tmp_path,monkeypatch):
    runner,gateway,clock=setup(tmp_path,monkeypatch)
    path=tmp_path/'paper_forward.sqlite3'
    with closing(sqlite3.connect(path)) as db:db.execute('PRAGMA journal_mode=WAL')
    before=runner.diagnostics();original=runner._capacity_snapshot;called=[]
    def append_after_counts(db):
        result=original(db)
        with closing(sqlite3.connect(path)) as writer,writer:
            writer.execute("INSERT INTO sessions SELECT ?,started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions LIMIT 1",('b'*16,))
            writer.execute('INSERT INTO cycles(at,detail) SELECT at,detail FROM cycles LIMIT 1')
        called.append(True)
        return result
    monkeypatch.setattr(runner,'_capacity_snapshot',append_after_counts)
    assert runner.diagnostics()==before and called==[True]
    monkeypatch.setattr(runner,'_capacity_snapshot',original)
    after=runner.diagnostics()
    assert after['storage_capacity']['sessions_retained']==before['storage_capacity']['sessions_retained']+1
    assert len(after['history'])==len(before['history'])+1
    runner.stop()
