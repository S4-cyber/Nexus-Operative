"""Injected AI OS store and local ASGI requests only; no real keys or provider IO."""
from types import SimpleNamespace
from fastapi.testclient import TestClient
import pytest
from nexus import app as api, credential_vault as vault

SECRET='sk-test-only-private-placeholder-123456789'
ORIGIN={'Origin':'http://127.0.0.1:8000'}
BASE='/api/paper/ai-provider/vault'


class Store:
    def __init__(self):self.secret=None;self.calls=[];self.save_ok=True;self.delete_ok=True
    def set_password(self,service,account,secret):
        self.calls.append('set')
        if self.save_ok:self.secret=secret
    def get_password(self,*args):self.calls.append('get');return self.secret
    def delete_password(self,*args):
        self.calls.append('delete')
        if self.delete_ok:self.secret=None


@pytest.fixture
def local(monkeypatch):
    store=Store();monkeypatch.setattr(vault,'_vault',lambda:store)
    monkeypatch.setattr(vault,'_observation',None)
    client=TestClient(api.app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',12345))
    return client,store


def check(response,status):
    assert response.status_code==status
    assert response.headers['cache-control']=='no-store'
    assert SECRET not in response.text and 'PRIVATE_BACKEND_ERROR' not in response.text
    assert '"input"' not in response.text


def test_explicit_save_verified_readback_and_idempotent_removal(local):
    client,store=local
    result=client.post(BASE+'/connect',json={'api_key':SECRET},headers=ORIGIN);check(result,200)
    assert store.calls==['set','get'] and vault.local_status()['configured']
    result=client.post(BASE+'/disconnect',headers=ORIGIN);check(result,200)
    assert store.secret is None and store.calls[-3:]==['get','delete','get']
    result=client.post(BASE+'/disconnect',json={},headers=ORIGIN);check(result,200)
    assert store.calls[-2:]==['get','get'] and not vault.local_status()['configured']


@pytest.mark.parametrize('headers',[{}, {'Origin':'https://untrusted.example'}, {'Origin':'http://localhost:8000'}])
def test_origin_gate_precedes_parsing_invalid_secret_body(local,headers):
    client,store=local
    response=client.post(BASE+'/connect',content='{"api_key":"'+SECRET,headers=headers)
    check(response,403);assert store.calls==[]


def test_remote_peer_is_denied_before_body_or_storage(local):
    _,store=local
    client=TestClient(api.app,base_url='http://127.0.0.1:8000',client=('203.0.113.10',12345))
    check(client.post(BASE+'/connect',json={'api_key':SECRET},headers=ORIGIN),403)
    assert store.calls==[]


@pytest.mark.parametrize('data',[{}, {'api_key':None},{'api_key':17},{'api_key':['private']},
    {'api_key':'short-private'}, {'api_key':'sk-'+('p'*300)}, {'api_key':SECRET,'extra':SECRET},
    {'api_key':'sk-'+('p'*25)+'\x00'}, {'api_key':'sk-'+('p'*25)+'\t'}, {'api_key':'sk-'+('p'*25)+'é'}])
def test_bad_secret_shapes_never_echo_validation_inputs_or_open_store(local,data):
    client,store=local
    check(client.post(BASE+'/connect',json=data,headers=ORIGIN),422)
    assert store.calls==[]


@pytest.mark.parametrize('content,content_type',[
    ('{"api_key":"'+SECRET+'","api_key":"'+SECRET+'"}','application/json'),
    ('{"api_key":"'+SECRET+'"','application/json'),
    ('x'*2049,'application/json'),
    (SECRET,'text/plain'),
    ('{"api_key":"'+SECRET+'"}',None)])
def test_json_duplicate_encoding_type_and_body_caps(local,content,content_type):
    client,store=local;headers=dict(ORIGIN)
    if content_type:headers['Content-Type']=content_type
    check(client.post(BASE+'/connect',content=content,headers=headers),422)
    assert store.calls==[]


@pytest.mark.parametrize('action', ['connect','disconnect'])
def test_backend_failure_sanitized_and_hint_unknown(local,monkeypatch,action):
    client,store=local
    def failed():raise RuntimeError('PRIVATE_BACKEND_ERROR '+SECRET)
    monkeypatch.setattr(vault,'_vault',failed)
    data={'api_key':SECRET} if action=='connect' else {}
    check(client.post(BASE+'/'+action,json=data,headers=ORIGIN),503)
    assert vault.local_status()['state']=='NOT_CHECKED'


def test_save_without_retention_or_matching_readback_cannot_acknowledge(local):
    client,store=local;store.save_ok=False
    check(client.post(BASE+'/connect',json={'api_key':SECRET},headers=ORIGIN),503)
    assert store.calls==['set','get'] and not vault.local_status()['configured']


def test_failed_delete_confirmation_cannot_report_absence(local):
    client,store=local;store.secret=SECRET;store.delete_ok=False
    check(client.post(BASE+'/disconnect',headers=ORIGIN),503)
    assert store.secret==SECRET and vault.local_status()['state']=='NOT_CHECKED'


def test_malformed_stored_secret_is_not_returned_as_usable_configuration(local):
    _,store=local;store.secret='PRIVATE_MALFORMED_RECORD'
    assert vault.retrieve() is None and vault.local_status()['state']=='NOT_CHECKED'


@pytest.mark.parametrize('module,name', [('keyring.backends.WindowsFallback','WinVaultKeyring'),
    ('keyring.backends.Windows','Subclass'),('thirdparty','WinVaultKeyring')])
def test_only_exact_builtin_windows_backend_allowed(monkeypatch,module,name):
    import keyring
    monkeypatch.setattr(vault,'os',SimpleNamespace(name='nt'))
    backend=type(name,(),{'__module__':module})()
    monkeypatch.setattr(keyring,'get_keyring',lambda:backend)
    with pytest.raises(ValueError,match='TRUSTED_WINDOWS_VAULT_UNAVAILABLE'):vault._vault()


def test_checked_backend_object_used_without_second_global_dispatch(monkeypatch):
    monkeypatch.setattr(vault,'_observation',None)
    import keyring
    monkeypatch.setattr(vault,'os',SimpleNamespace(name='nt'))
    backend=type('WinVaultKeyring',(Store,),{'__module__':'keyring.backends.Windows'})()
    calls=[]
    def get():
        calls.append(True)
        if len(calls)>1:raise RuntimeError('backend changed')
        return backend
    monkeypatch.setattr(keyring,'get_keyring',get)
    vault.install(SECRET)
    assert len(calls)==1 and backend.calls==['set','get']


@pytest.mark.parametrize('method',['get','put','patch','delete'])
def test_secret_controls_post_only(local,method):
    client,store=local;response=getattr(client,method)(BASE+'/connect',headers=ORIGIN)
    assert response.status_code==405 and store.calls==[]


def test_disconnect_rejects_unexpected_secret_body(local):
    client,store=local
    check(client.post(BASE+'/disconnect',json={'api_key':SECRET},headers=ORIGIN),422)
    assert store.calls==[]


def test_disconnect_rejects_nonempty_untyped_body_before_os_work(local):
    client,store=local
    check(client.post(BASE+'/disconnect',content=SECRET,headers=ORIGIN),422)
    assert store.calls==[]


def test_passive_ai_status_is_not_cached(local):
    client,store=local
    response=client.get('/api/paper/ai-provider/status')
    check(response,200)
    assert store.calls==[]


def test_lost_save_acknowledgment_does_not_claim_failure_means_absence(local,monkeypatch):
    client,store=local;original=store.set_password
    def lost(*args):
        original(*args);raise RuntimeError('PRIVATE_BACKEND_ERROR')
    monkeypatch.setattr(store,'set_password',lost)
    check(client.post(BASE+'/connect',json={'api_key':SECRET},headers=ORIGIN),503)
    assert store.secret==SECRET and vault.local_status()['state']=='NOT_CHECKED'
    monkeypatch.setattr(store,'set_password',original)
    check(client.post(BASE+'/connect',json={'api_key':SECRET},headers=ORIGIN),200)


def test_lost_removal_acknowledgment_requires_explicit_idempotent_retry(local,monkeypatch):
    client,store=local;store.secret=SECRET;original=store.delete_password
    def lost(*args):
        original(*args);raise RuntimeError('PRIVATE_BACKEND_ERROR')
    monkeypatch.setattr(store,'delete_password',lost)
    check(client.post(BASE+'/disconnect',headers=ORIGIN),503)
    assert store.secret is None and vault.local_status()['state']=='NOT_CHECKED'
    monkeypatch.setattr(store,'delete_password',original)
    check(client.post(BASE+'/disconnect',headers=ORIGIN),200)


def test_slow_os_save_does_not_block_event_loop_or_trigger_passive_vault_read(local,monkeypatch):
    import asyncio
    import threading
    import httpx
    _,store=local;entered=threading.Event();release=threading.Event();original=store.set_password
    def slow(*args):
        entered.set()
        if not release.wait(10):raise RuntimeError('fixture deadline')
        original(*args)
    monkeypatch.setattr(store,'set_password',slow)
    async def exercise():
        transport=httpx.ASGITransport(app=api.app,client=('127.0.0.1',12345))
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8000') as client:
            pending=asyncio.create_task(client.post(BASE+'/connect',json={'api_key':SECRET},headers=ORIGIN))
            try:
                assert await asyncio.wait_for(asyncio.to_thread(entered.wait,3),4)
                status=await asyncio.wait_for(client.get('/api/status'),3)
                assert status.status_code==200 and not status.json()['live_orders_enabled']
                passive=await asyncio.wait_for(client.get('/api/paper/ai-provider/status'),3)
                assert passive.json()['credential_observation']['state']=='NOT_CHECKED'
                assert store.calls==[]
            finally: release.set()
            check(await pending,200)
    asyncio.run(exercise())
