from fastapi.testclient import TestClient
from nexus.app import app
from nexus.ai_evidence_preview import build, authorize_preview
from nexus.historical_candles import collect

BASE=3600*1000
def rows(n=150):
    return [[BASE+i*3600,90,110,100,101,10] for i in range(n)]

def seed():
    collect("BTC-USD",3600,now=BASE+151*3600,transport=lambda _:rows())

def test_preview_no_provider_and_data_is_public_only(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed()
    d=build("BTC-USD")
    assert d["ready"] is True
    assert d["provider_request_sent"] is False
    assert d["snapshot"]["bars"]==150
    assert set(d["snapshot"])=={"purpose","product","provider","bars","granularity_seconds","first_close_usd","last_close_usd","observed_change_pct","independent_source_verified","suitable_for_live_orders","no_account_or_personal_data_included"}
    assert d["live_orders_enabled"] is False

def test_hash_mismatch_fails_before_paid_provider(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed()
    d=build("BTC-USD")
    calls=[]
    def dummy(*args):
        calls.append(args)
        return {"verdict":"CAUTION"}
    try:authorize_preview("BTC-USD","f"*64,sender=dummy)
    except ValueError:pass
    else:assert False,"Tampered preview was accepted"
    assert calls==[]
    correct=authorize_preview("BTC-USD",d["sha256"],sender=dummy)
    assert correct["live_orders_enabled"] is False
    assert len(calls)==1
    assert calls[0][1]==d["snapshot"]

def test_empty_history_does_not_prepare_external_request(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    d=build("BTC-USD")
    assert d["ready"] is False
    assert d["provider_request_sent"] is False
    assert d["live_orders_enabled"] is False

def test_preview_endpoint_never_invokes_paid_provider(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    seed()
    client=TestClient(app)
    d=client.get("/api/paper/ai-provider/public-preview/BTC-USD")
    assert d.status_code==200 and d.json()["ready"]
    assert client.post("/api/paper/ai-provider/public-preview/send",json={"product":"BTC-USD","preview_sha256":d.json()["sha256"]}).status_code==403
    html=client.get("/portal").text
    assert 'id="aipreview"' in html and 'id="aisendpreview"' in html
    assert "window.confirm(" in html and "May Cost Money" in html
