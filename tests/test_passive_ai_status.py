"""Exercise Windows-only credential policy using denied/injected stores, never OS credentials."""
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from nexus import app as api, credential_vault as vault, provider_research as provider


@pytest.fixture
def passive(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'missing'))
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('NEXUS_ENABLE_LLM_RESEARCH',raising=False)
    monkeypatch.setattr(vault,'_observation',None)
    monkeypatch.setattr(vault,'os',SimpleNamespace(name='nt'))
    import keyring
    def denied(*args,**kwargs): pytest.fail('Passive path attempted Windows credential access')
    for name in ('get_keyring','get_password','set_password','delete_password'):
        monkeypatch.setattr(keyring,name,denied)
    return tmp_path/'missing'


@pytest.mark.parametrize('endpoint',['/api/paper/ai-provider/status','/api/onboarding/readiness','/api/onboarding/tasks'])
def test_passive_endpoints_never_enter_windows_vault(passive,endpoint):
    response=TestClient(api.app).get(endpoint)
    assert response.status_code==200
    assert not passive.exists()


@pytest.mark.parametrize('observed,state,configured',[(None,'NOT_CHECKED',False),(False,'OBSERVED_ABSENT',False),(True,'OBSERVED_PRESENT',True)])
def test_cached_metadata_is_explicitly_unverified(passive,monkeypatch,observed,state,configured):
    monkeypatch.setattr(vault,'_observation',observed)
    result=provider.status()
    assert result['configured'] is configured and result['credential_observation']['state']==state
    assert result['credential_observation']['current_vault_checked'] is False
    assert result['provider_connected'] is result['live_orders_enabled'] is False


def test_opted_in_environment_hint_does_not_open_vault(passive,monkeypatch):
    monkeypatch.setenv('NEXUS_ENABLE_LLM_RESEARCH','1');monkeypatch.setenv('OPENAI_API_KEY','PRIVATE_PLACEHOLDER')
    result=provider.status()
    assert result['configured'] and result['environment_configuration_present']
    assert 'PRIVATE_PLACEHOLDER' not in str(result)
    assert result['credential_observation']['state']=='NOT_CHECKED'


def test_passive_status_does_not_wait_for_active_os_operation(passive):
    acquired=threading.Event();release=threading.Event()
    def active():
        with vault._lock:
            acquired.set();assert release.wait(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(active);assert acquired.wait(2)
        try:
            result=pool.submit(provider.status).result(timeout=1)
            assert not result['configured']
        finally: release.set()
        future.result(timeout=2)


class Store:
    def __init__(self):self.secret=None;self.reads=0
    def set_password(self,*args):self.secret=args[-1]
    def get_password(self,*args):self.reads+=1;return self.secret
    def delete_password(self,*args):self.secret=None


def test_explicit_actions_update_hint_but_cached_presence_never_authorizes_request(passive,monkeypatch):
    store=Store();monkeypatch.setattr(vault,'_vault',lambda:store)
    vault.install('sk-explicit-test-only-placeholder-12345')
    assert provider.status()['configured'] and store.reads==0
    store.secret=None  # External removal after observed install.
    called=[]
    with pytest.raises(ValueError,match='LLM_RESEARCH_DISABLED'):
        provider.request('market_regime',{},transport=lambda *args:called.append(True))
    assert called==[] and store.reads==1
    assert not provider.status()['configured']
    vault.install('sk-explicit-test-only-placeholder-12345')
    vault.disconnect();assert vault.local_status()['state']=='OBSERVED_ABSENT'


def test_failed_explicit_read_is_unknown_not_cached_absence(passive,monkeypatch):
    monkeypatch.setattr(vault,'_observation',True)
    def failed():raise RuntimeError('PRIVATE_STORE_ERROR')
    monkeypatch.setattr(vault,'_vault',failed)
    assert vault.retrieve() is None
    result=provider.status()
    assert not result['configured'] and result['credential_observation']['state']=='NOT_CHECKED'
    assert 'PRIVATE' not in str(result)
