from fastapi.testclient import TestClient
import pytest
import threading
import time
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

def test_polymorphic_secret_is_rejected_before_vault_access(monkeypatch):
    class ForgedSecret(str):
        def __len__(self):return 40
        def startswith(self,prefix):return prefix=='sk-'
        def __iter__(self):return iter('x'*40)

    monkeypatch.setattr(vault,"_vault",lambda:pytest.fail('vault accessed'))
    monkeypatch.setattr(vault,"_observation",None)
    with pytest.raises(ValueError,match='INVALID_KEY_FORMAT'):
        vault.install(ForgedSecret('NOT_AN_OPENAI_KEY'))
    assert vault.local_status()['state']=='NOT_CHECKED'

@pytest.mark.parametrize('subclassed',[False,True],ids=['substituted','subclassed'])
@pytest.mark.parametrize('prior_observation',[None,True],ids=['unchecked-before','confirmed-before'])
def test_vault_readback_must_be_exact_and_equal_before_save_is_confirmed(
        monkeypatch,subclassed,prior_observation):
    secret='sk-' + 'explicit-test-only-placeholder-12345'
    different='sk-' + 'different-valid-placeholder-value-67890'
    class ReadbackVault(DummyVault):
        def get_password(self,service,account):
            value=different
            return type('StoredSecret',(str,),{})(value) if subclassed else value
    monkeypatch.setattr(vault,"_vault",lambda:ReadbackVault())
    monkeypatch.setattr(vault,"_observation",prior_observation)
    with pytest.raises(ValueError,match='VAULT_SAVE_FAILED') as caught:
        vault.install(secret)
    assert secret not in str(caught.value) and different not in str(caught.value)
    assert vault.local_status()['state']=='NOT_CHECKED'

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

def test_saved_request_fails_closed_when_disconnect_wins_after_selection(monkeypatch):
    store=DummyVault();monkeypatch.setattr(vault,"_vault",lambda:store)
    old="sk-test-only-saved-authority-placeholder-123"
    vault.install(old);called=[]
    def select_then_disconnect():
        selected=vault.retrieve();vault.disconnect();return 'vault',selected
    monkeypatch.setattr(provider,"_authorized_credential",select_then_disconnect)
    result=provider.request('market_regime',{},transport=lambda *args:called.append(args))
    assert result['verdict']=='ABSTAIN' and not called

def test_saved_request_fails_closed_when_rotation_wins_after_selection(monkeypatch):
    store=DummyVault();monkeypatch.setattr(vault,"_vault",lambda:store)
    old="sk-test-only-old-authority-placeholder-12345"
    new="sk-test-only-new-authority-placeholder-67890"
    vault.install(old);called=[]
    def select_then_rotate():
        selected=vault.retrieve();vault.install(new);return 'vault',selected
    monkeypatch.setattr(provider,"_authorized_credential",select_then_rotate)
    result=provider.request('market_regime',{},transport=lambda *args:called.append(args))
    assert result['verdict']=='ABSTAIN' and not called

def test_disconnect_waits_for_saved_request_lease(monkeypatch):
    store=DummyVault();monkeypatch.setattr(vault,"_vault",lambda:store)
    secret="sk-test-only-request-lease-placeholder-12345"
    vault.install(secret);started=threading.Event();release=threading.Event();done=[]
    def transport(body,token):
        assert token==secret;started.set();assert release.wait(2)
        return {"choices":[{"message":{"content":__import__('json').dumps({
            "role":"market_regime","verdict":"ABSTAIN","reason":"bounded",
            "estimated_cost_usd":0})}}]}
    worker=threading.Thread(target=lambda:done.append(provider.request(
        'market_regime',{},transport=transport)))
    worker.start();assert started.wait(2)
    disconnected=[];off=threading.Thread(target=lambda:disconnected.append(vault.disconnect()))
    off.start();time.sleep(.05);assert off.is_alive() and not disconnected
    release.set();worker.join(2);off.join(2)
    assert done[0]['provider_connected'] is True and disconnected[0]['configured'] is False
    assert store.get_password(vault.SERVICE,vault.ACCOUNT) is None

