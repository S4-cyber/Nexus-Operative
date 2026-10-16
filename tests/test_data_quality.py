import sqlite3
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.market_evidence import _write,recent
from nexus.data_quality import quality

def record(at,decision="PROVISIONAL_AGREEMENT",product="BTC-USD"):
    return _write(product,{"decision":decision,"issues":[],"independent_venues":2},at)

def test_chronological_gap_and_regression(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    record(1000);record(1030);record(1150)
    d=quality("BTC-USD",gap_seconds=60)
    assert d["sampling_gap_count"]==1
    assert "OBSERVATION_SAMPLING_GAPS" in d["issues"]
    assert not d["suitable_for_backtesting"]
    record(1140)
    d=quality("BTC-USD",gap_seconds=60)
    assert len(d["clock_regressions"])==1
    assert "OBSERVATION_CLOCK_REGRESSION" in d["issues"]

def test_metadata_tampering_not_hidden_by_payload_checksum(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    record(1000);record(1010)
    with sqlite3.connect(tmp_path/"market_evidence.sqlite3") as db:
        db.execute("UPDATE observations SET product='ETH-USD' WHERE id=2")
    d=recent("ETH-USD")
    assert d["observations"][0]["local_record_integrity"] is False
    assert d["observations"][0]["decision"]=="VETO"
    report=quality("ETH-USD")
    assert "LOCAL_EVIDENCE_INTEGRITY_FAILURE" in report["issues"]

def test_veto_and_missing_history_are_explicit(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    assert "INSUFFICIENT_CHRONOLOGICAL_SAMPLES" in quality("BTC-USD")["issues"]
    record(1000,"VETO");record(1030)
    d=quality("BTC-USD")
    assert d["veto_observations"]==1
    assert d["quality_pass"] is False
    client=TestClient(app)
    res=client.get("/api/market-evidence/BTC-USD/quality")
    assert res.status_code==200
    assert res.json()["live_orders_enabled"] is False
    assert client.get("/api/market-evidence/INVALID/quality").status_code==422
