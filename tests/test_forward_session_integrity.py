import json
import sqlite3
import pytest
from test_paper_forward import setup


def damage(root,column,value):
    with sqlite3.connect(root/'paper_forward.sqlite3') as db:
        db.execute(f'UPDATE sessions SET {column}=?',(value,))


@pytest.mark.parametrize('column,value',[
    ('counts_json','[]'),('counts_json','{"HOLD":true}'),('counts_json','{"HOLD":-1}'),
    ('last_json','null'),('last_json','{"at":1}'),('state','UNKNOWN'),('updated_at',0)])
def test_corrupt_session_pauses_before_fetch_and_keeps_evidence(tmp_path,monkeypatch,column,value):
    r,g,c=setup(tmp_path,monkeypatch);damage(tmp_path,column,value)
    calls=[];r.fetcher=lambda product:calls.append(product)
    assert r.single_cycle()['decision']=='PAUSED'
    assert not calls and not g.recovery_report()['orders']
    assert not r.status()['enabled'] and not r.status()['ownership_held']
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        assert db.execute(f'SELECT {column} FROM sessions').fetchone()[0]==value
        assert db.execute('SELECT COUNT(*) FROM forward_quotes').fetchone()[0]==0


def test_stop_with_bad_counts_still_fences_and_releases_owner(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);damage(tmp_path,'counts_json','null')
    status=r.stop()
    assert not status['enabled'] and not status['ownership_held']
    assert status['last_error']=='STOP_DIAGNOSTIC_STORAGE_FAILURE'
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        assert db.execute('SELECT counts_json FROM sessions').fetchone()[0]=='null'


def test_changed_policy_is_not_overwritten_by_diagnostics(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    policy=r.status()['policy'];policy['fee_bps']=0;raw=json.dumps(policy)
    damage(tmp_path,'policy_json',raw)
    status=r.stop()
    assert status['last_error']=='STOP_DIAGNOSTIC_STORAGE_FAILURE'
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        assert db.execute('SELECT policy_json FROM sessions').fetchone()[0]==raw


def test_bad_history_and_session_records_return_unavailable(tmp_path,monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    r,g,c=setup(tmp_path,monkeypatch);r.stop()
    monkeypatch.setattr(backend,'paper_forward_runner',r);client=TestClient(backend.app)
    damage(tmp_path,'last_json','[]')
    assert client.get('/api/paper/forward/sessions').status_code==503
    assert client.get('/api/paper/forward/sessions?limit=0').status_code==422
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:db.execute("UPDATE cycles SET detail='[]'")
    assert client.get('/api/paper/forward').status_code==503
