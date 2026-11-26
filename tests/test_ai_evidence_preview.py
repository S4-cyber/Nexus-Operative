from fastapi.testclient import TestClient
import pytest
from nexus import app as api
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


def test_paid_preview_consent_is_exact_bounded_and_duplicate_free(monkeypatch):
    calls=[]
    monkeypatch.setattr(api,'ai_authorize_preview',lambda *args:calls.append(args) or {
        'live_orders_enabled':False,'money_movement_enabled':False})
    client=TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',12345))
    headers={'Origin':'http://127.0.0.1:8000','Content-Type':'application/json'}
    digest='a'*64
    valid=client.post('/api/paper/ai-provider/public-preview/send',headers=headers,
        content='{"product":"BTC-USD","preview_sha256":"'+digest+'"}')
    assert valid.status_code==200 and calls==[('BTC-USD',digest)]
    invalid=(
        '{"product":"BTC-USD","preview_sha256":"'+digest+'","api_key":"private"}',
        '{"product":"BTC-USD","preview_sha256":"'+digest+'","preview_sha256":"'+'b'*64+'"}',
        '{"product":"BTC-USD","preview_sha256":"'+digest+'","padding":"'+'x'*2048+'"}',
        '["BTC-USD","'+digest+'"]',
        '{"product":"BTC-USD"}',
    )
    for raw in invalid:
        response=client.post('/api/paper/ai-provider/public-preview/send',headers=headers,content=raw)
        assert response.status_code==422
        assert response.json()=={'detail':'PUBLIC_AI_CONSENT_BODY_INVALID'}
    assert calls==[('BTC-USD',digest)]


@pytest.mark.parametrize('content_type',['text/plain','application/x-www-form-urlencoded'])
def test_paid_preview_consent_rejects_non_json_before_provider(monkeypatch,content_type):
    monkeypatch.setattr(api,'ai_authorize_preview',lambda *_:pytest.fail('provider invoked'))
    client=TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',12345))
    response=client.post('/api/paper/ai-provider/public-preview/send',
        headers={'Origin':'http://127.0.0.1:8000','Content-Type':content_type},content='product=BTC-USD')
    assert response.status_code==422
