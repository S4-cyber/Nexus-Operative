import sqlite3

import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus import journal
from nexus.journal import record_replay, recent_replays
from nexus.replay import replay


def fixture(seed=100):
    request={"capital":10000,"closes":[seed,seed+1,seed+2,seed+3,seed+5,seed+6,seed+8],
             "tier":"solar","direction":"UP"}
    return request,replay(request['closes'],request['capital'],request['tier'],request['direction'])

def test_records_persist_and_are_sanitized(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    client=TestClient(app)
    request={"capital":10000,"closes":[100,101,102,103,105,106,108],
             "tier":"solar","direction":"UP"}
    result=client.post("/api/paper/replay/record",json=request)
    assert result.status_code==200,result.text
    record=result.json()
    assert len(record["input_sha256"])==64
    assert record["simulation_only"] is True
    history=client.get("/api/paper/replay/history").json()
    assert history["runs"][0]["run_id"]==record["run_id"]
    assert "closes" not in str(history)
    assert (tmp_path/"paper_journal.sqlite3").exists()

def test_invalid_history_limit(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    c=TestClient(app)
    assert c.get("/api/paper/replay/history?limit=101").status_code==422

def test_journal_fails_closed_on_non_replay(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    with pytest.raises(ValueError):
        record_replay({"tier":"solar","direction":"UP"},{"mode":"LIVE","simulation_only":False})


def test_missing_history_is_passive(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'missing'))
    assert recent_replays()==[]
    assert not (tmp_path/'missing').exists()


def test_replay_writer_contention_rolls_back_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));request,result=fixture()
    record_replay(request,result);path=tmp_path/'paper_journal.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(journal,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        candidate,candidate_result=fixture(200)
        with pytest.raises(ValueError,match='^PAPER_REPLAY_JOURNAL_DATABASE_BUSY$'):
            record_replay(candidate,candidate_result)
        response=TestClient(app).post('/api/paper/replay/record',json=candidate)
        assert response.status_code==503
        assert response.json()=={'detail':'PAPER_REPLAY_JOURNAL_DATABASE_BUSY'}
        assert path.read_bytes()==before
    assert len(record_replay(*fixture(200))['run_id'])==36


def test_replay_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));record_replay(*fixture())
    path=tmp_path/'paper_journal.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(journal,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN');blocker.execute('SELECT COUNT(*) FROM replay_runs').fetchone()
        with pytest.raises(ValueError,match='^PAPER_REPLAY_JOURNAL_DATABASE_BUSY$'):
            record_replay(*fixture(200))
        assert path.read_bytes()==before
    assert len(record_replay(*fixture(200))['run_id'])==36


def test_replay_exclusive_read_contention_is_503_and_recovers(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));record_replay(*fixture())
    path=tmp_path/'paper_journal.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(journal,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^PAPER_REPLAY_JOURNAL_DATABASE_BUSY$'):
            recent_replays()
        response=TestClient(app).get('/api/paper/replay/history')
        assert response.status_code==503
        assert response.json()=={'detail':'PAPER_REPLAY_JOURNAL_DATABASE_BUSY'}
        assert path.read_bytes()==before
    assert len(recent_replays())==1


def test_replay_capacity_refuses_without_changing_history(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));record_replay(*fixture())
    path=tmp_path/'paper_journal.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(journal,'MAX_REPLAY_RUNS',1)
    with pytest.raises(ValueError,match='^PAPER_REPLAY_JOURNAL_CAPACITY_REVIEW_REQUIRED$'):
        record_replay(*fixture(200))
    assert path.read_bytes()==before and len(recent_replays())==1
