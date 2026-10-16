from fastapi.testclient import TestClient
from nexus.app import app
from nexus.journal import record_replay, recent_replays

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
    import pytest
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    with pytest.raises(ValueError):
        record_replay({"tier":"solar","direction":"UP"},{"mode":"LIVE","simulation_only":False})
