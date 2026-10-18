from fastapi.testclient import TestClient
from nexus.app import app
import nexus.credential_vault as vault
import nexus.provider_research as provider

class DummyVault:
    def __init__(self):self.data={}
    def set_password(self,service,account,secret):self.data[(service,account)]=secret
    def get_password(self,service,account):return self.data.get((service,account))
    def delete_password(self,service,account):del self.data[(service,account)]

def test_secure_vault_roundtrip_without_exposing_key(monkeypatch):
    store=DummyVault()
    monkeypatch.setattr(vault,"_vault",lambda:store)
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.delenv("NEXUS_ENABLE_LLM_RESEARCH",raising=False)
    secret="sk-test-only-placeholder-not-real-1234567"
    reply=vault.install(secret)
    assert reply["stored_in"]=="WINDOWS_CREDENTIAL_MANAGER"
    assert secret not in str(reply)
    assert vault.configured() is True
    assert vault.retrieve()==secret
    assert provider.enabled() is True
    off=vault.disconnect()
    assert off["configured"] is False
    assert secret not in str(off)
    assert vault.retrieve() is None
    assert provider.enabled() is False

def test_reject_bad_vault_keys(monkeypatch):
    monkeypatch.setattr(vault,"_vault",lambda:DummyVault())
    for invalid in ("", "too-short", "sk-\n"+("x"*30), "sk-"+"z"*300):
        try:vault.install(invalid)
        except ValueError:pass
        else:assert False,"Invalid key accepted"

def test_cross_origin_and_untrusted_browser_requests_rejected(monkeypatch):
    monkeypatch.setattr(vault,"_vault",lambda:DummyVault())
    client=TestClient(app,base_url="http://127.0.0.1:8000",client=("testclient",50000))
    key="sk-test-only-placeholder-not-real-1234567"
    bad=client.post("/api/paper/ai-provider/vault/connect",json={"api_key":key})
    assert bad.status_code==403
    wrong=client.post("/api/paper/ai-provider/vault/connect",json={"api_key":key},headers={"Origin":"https://malicious.example"})
    assert wrong.status_code==403
    # TestClient uses synthetic client IP (testclient); the local-only gate
    # intentionally blocks it even if a matching Origin is supplied.
    same=client.post("/api/paper/ai-provider/vault/connect",json={"api_key":key},headers={"Origin":"http://127.0.0.1:8000"})
    assert same.status_code==403
    assert vault.configured() is False

def test_local_ui_key_controls_do_not_auto_send(monkeypatch):
    monkeypatch.setattr(vault,"_vault",lambda:DummyVault())
    html=TestClient(app).get("/portal").text
    for item in ("aikey","aiconnect","aidisconnect"):
        assert f'id="{item}"' in html
    assert "Windows Credential Manager" in html
    assert "window.confirm(" in html
    assert "key saved in local" not in html.lower() or "No model requests sent" in html

