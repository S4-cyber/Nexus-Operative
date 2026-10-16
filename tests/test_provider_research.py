import json
from fastapi.testclient import TestClient
from nexus.app import app
import nexus.provider_research as provider

def setup(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","test-only-placeholder")
    monkeypatch.setenv("NEXUS_ENABLE_LLM_RESEARCH","1")
    monkeypatch.setattr(provider,"_calls",0)

def reply(role="market_regime",verdict="CAUTION",reason="Insufficient verified trading evidence"):
    return {"choices":[{"message":{"content":json.dumps({
      "role":role,"verdict":verdict,"reason":reason,"estimated_cost_usd":0})}}]}

def test_disabled_provider_never_calls_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.delenv("NEXUS_ENABLE_LLM_RESEARCH",raising=False)
    assert not provider.enabled()
    try:
        provider.request("market_regime",{},transport=lambda *_:(_ for _ in ()).throw(AssertionError("network")))
    except ValueError as e:
        assert "DISABLED" in str(e)
    else:assert False,"Disabled provider made a request"
    assert TestClient(app).get("/api/paper/ai-provider/status").json()["provider_connected"] is False

def test_structured_provider_has_no_tools_or_broker_routes(monkeypatch):
    setup(monkeypatch)
    def transport(body,secret):
        assert secret=="test-only-placeholder"
        data=json.loads(body)
        assert data["model"]=="gpt-4o-mini"
        assert "tools" not in data
        assert "tool_choice" not in data
        assert data["response_format"]["type"]=="json_schema"
        assert len(data["messages"])==2
        return reply()
    r=provider.request("market_regime",{"research":"read_only"},transport=transport)
    assert r["verdict"]=="CAUTION"
    assert r["live_orders_enabled"] is False
    assert r["provider_connected"] is True

def test_invalid_role_refusal_schema_and_exception_fail_closed(monkeypatch):
    setup(monkeypatch)
    assert provider.request("market_regime",{},transport=lambda *_:reply(reason="x"*400))["verdict"]=="ABSTAIN"
    assert provider.request("market_regime",{},transport=lambda *_:{"choices":[{"message":{"refusal":"no"}}]})["verdict"]=="ABSTAIN"
    assert provider.request("market_regime",{},transport=lambda *_:(_ for _ in ()).throw(RuntimeError("sensitive-token")))["reason"]=="MODEL_PROVIDER_OR_SCHEMA_FAILURE"
    try:provider.request("invalid",{})
    except ValueError:pass
    else:assert False,"Invalid role accepted"

def test_request_count_exhaustion_even_on_provider_errors(monkeypatch):
    setup(monkeypatch)
    for _ in range(provider.MAX_CALLS_PER_PROCESS):
        r=provider.request("market_regime",{},transport=lambda *_:{"bad":True})
        assert r["verdict"]=="ABSTAIN"
    try:provider.request("market_regime",{},transport=lambda *_:reply())
    except ValueError as e:assert "CAP_REACHED" in str(e)
    else:assert False,"Limit not enforced"
    assert provider._calls==provider.MAX_CALLS_PER_PROCESS

def test_api_disabled_and_no_live_orders(monkeypatch):
    monkeypatch.delenv("NEXUS_ENABLE_LLM_RESEARCH",raising=False)
    c=TestClient(app)
    assert c.post("/api/paper/ai-provider/market_regime/research",json={}).status_code==422
    assert c.get("/api/status").json()["live_orders_enabled"] is False
