"""OS-store and HTTPS seams are injected; no actual keys, broker or OS vault."""
import json
import threading
import types
import sys

import pytest
from fastapi.testclient import TestClient
from nexus import paper_credentials as credentials
from nexus import alpaca_paper_inspection as inspection
from test_alpaca_paper_inspection import Opener, KEY, SECRET, ACCOUNT as BROKER_ACCOUNT


class Vault:
    def __init__(self):
        self.data = {}
        self.reads = self.writes = self.deletes = 0

    def get_password(self, service, account):
        self.reads += 1
        return self.data.get((service, account))

    def set_password(self, service, account, value):
        self.writes += 1
        self.data[(service, account)] = value

    def delete_password(self, service, account):
        self.deletes += 1
        del self.data[(service, account)]


@pytest.fixture
def vault(monkeypatch):
    store = Vault()
    monkeypatch.setattr(credentials, '_vault', lambda: store)
    monkeypatch.setattr(credentials, '_epoch', 0)
    monkeypatch.setattr(credentials, '_blocked', False)
    return store


def save():
    return credentials.install(KEY, SECRET, confirm_store=True)


def inspect(**kwargs):
    return credentials.inspect_saved(confirm_read_only_paper=True, opener=kwargs.pop('opener', Opener()),
                                     wall_clock=lambda: 1000, elapsed_clock=lambda: 50, **kwargs)


def test_pair_roundtrip_is_separate_from_ai_and_never_starts_io(vault, monkeypatch):
    from nexus import credential_vault as ai
    vault.data[(ai.SERVICE, ai.ACCOUNT)] = 'sk-fixture-ai-only'
    monkeypatch.setenv('APCA_API_KEY_ID', 'DO_NOT_USE_ENV')
    before = dict(__import__('os').environ)
    result = save()
    assert len(vault.data) == 2 and vault.writes == 1
    assert credentials.SERVICE != ai.SERVICE and credentials.ACCOUNT != ai.ACCOUNT
    assert KEY not in str(result) and SECRET not in str(result)
    assert result['configured'] is True and result['ready_for_routing'] is False
    assert credentials.status()['configured'] is True
    report = inspect(expected_account_id=BROKER_ACCOUNT)
    assert report['observed_usd_ceiling'] == '900' and report['ready_for_routing'] is False
    assert credentials.remove(confirm_remove=True)['configured'] is False
    assert credentials.remove(confirm_remove=True)['configured'] is False
    assert credentials.status()['configured'] is False
    assert vault.data == {(ai.SERVICE, ai.ACCOUNT): 'sk-fixture-ai-only'}
    assert dict(__import__('os').environ) == before


@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_strict_confirmations_before_any_vault_access(vault, value):
    for operation in (lambda: credentials.install(KEY, SECRET, confirm_store=value),
                      lambda: credentials.remove(confirm_remove=value),
                      lambda: credentials.inspect_saved(confirm_read_only_paper=value)):
        with pytest.raises(credentials.PaperVaultError, match='CONFIRMATION_REQUIRED'):
            operation()
    assert vault.reads == vault.writes == vault.deletes == 0


@pytest.mark.parametrize('bad', [None, 1, '', 'x' * 257, 'x' * 20 + '\r\n', 'é' * 20],
                         ids=['none', 'int', 'empty', 'long', 'crlf', 'unicode'])
def test_bad_keys_never_touch_vault(vault, bad):
    with pytest.raises(credentials.PaperVaultError, match='FORMAT_INVALID'):
        credentials.install(KEY, bad, confirm_store=True)
    assert vault.reads == vault.writes == 0


def test_missing_or_invalid_record_never_inspects(vault):
    with pytest.raises(credentials.PaperVaultError, match='NOT_CONFIGURED'):
        inspect()
    vault.data[(credentials.SERVICE, credentials.ACCOUNT)] = SECRET
    with pytest.raises(credentials.PaperVaultError, match='RECORD_INVALID'):
        inspect()
    assert credentials.remove(confirm_remove=True)['configured'] is False


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 2}, {'environment': 'live'}, {'provider': 'other'},
    {'scope': 'trade'}, {'revision': 'invalid'}, {'revision': BROKER_ACCOUNT},
    {'api_key': 'bad'}, {'api_secret': None}, {'url': 'https://api.alpaca.markets'},
])
def test_malformed_or_wider_scope_records_fail_without_io(vault, change):
    save()
    key = (credentials.SERVICE, credentials.ACCOUNT)
    record = json.loads(vault.data[key]); record.update(change)
    vault.data[key] = json.dumps(record)
    opener = Opener()
    for operation in (credentials.status, lambda: inspect(opener=opener)):
        with pytest.raises(credentials.PaperVaultError):
            operation()
    assert not opener.requests


@pytest.mark.parametrize('raw', ['x' * 1025, '{"version":1,"version":1}', '[]', 'null'])
def test_bounded_duplicate_key_record_validation(vault, raw):
    vault.data[(credentials.SERVICE, credentials.ACCOUNT)] = raw
    with pytest.raises(credentials.PaperVaultError, match='RECORD_INVALID'):
        credentials.status()


def test_save_readback_mismatch_is_unconfirmed_and_blocks_reads(vault, monkeypatch):
    monkeypatch.setattr(vault, 'set_password', lambda *args: None)
    with pytest.raises(credentials.PaperVaultError, match='SAVE_UNCONFIRMED'):
        save()
    assert credentials.status()['review_required'] is True
    with pytest.raises(credentials.PaperVaultError, match='LOCAL_REVIEW_REQUIRED'):
        inspect()
    assert credentials.remove(confirm_remove=True)['configured'] is False


def test_lost_save_ack_does_not_claim_success_or_automatically_rearm(vault, monkeypatch):
    original = vault.set_password
    def lost(*args):
        original(*args)
        raise RuntimeError(SECRET)
    monkeypatch.setattr(vault, 'set_password', lost)
    with pytest.raises(credentials.PaperVaultError, match='SAVE_UNCONFIRMED') as caught:
        save()
    assert SECRET not in str(caught.value)
    assert credentials.status()['configured'] is True and credentials.status()['review_required'] is True
    with pytest.raises(credentials.PaperVaultError, match='LOCAL_REVIEW_REQUIRED'):
        inspect()
    credentials.remove(confirm_remove=True)


@pytest.mark.parametrize('failure', ['raise', 'retain', 'lost_ack'])
def test_delete_failures_never_acknowledge_success_and_keep_local_read_veto(vault, monkeypatch, failure):
    save(); original = vault.delete_password
    def broken(*args):
        if failure == 'lost_ack':
            original(*args)
        if failure != 'retain':
            raise RuntimeError(SECRET)
    monkeypatch.setattr(vault, 'delete_password', broken)
    with pytest.raises(credentials.PaperVaultError, match='DELETE_UNCONFIRMED') as caught:
        credentials.remove(confirm_remove=True)
    assert SECRET not in str(caught.value)
    assert credentials.status()['review_required'] is True
    with pytest.raises(credentials.PaperVaultError, match='LOCAL_REVIEW_REQUIRED'):
        inspect()
    monkeypatch.setattr(vault, 'delete_password', original)
    assert credentials.remove(confirm_remove=True)['configured'] is False


@pytest.mark.parametrize('action', ['remove', 'replace', 'failed_remove'])
def test_local_mutation_during_transport_revokes_and_closes_read(vault, monkeypatch, action):
    save(); opener = Opener(); original = opener.open
    def mutate(request, timeout):
        response = original(request, timeout)
        if action == 'replace':
            save()
        elif action == 'remove':
            credentials.remove(confirm_remove=True)
        else:
            monkeypatch.setattr(vault, 'delete_password', lambda *args: (_ for _ in ()).throw(RuntimeError(SECRET)))
            with pytest.raises(credentials.PaperVaultError):
                credentials.remove(confirm_remove=True)
        return response
    opener.open = mutate
    with pytest.raises(inspection.PaperInspectionError, match='PERMISSION_REVOKED'):
        inspect(opener=opener)
    assert len(opener.requests) == 1 and opener.responses[0].closed


def test_external_entry_change_discards_completed_bundle(vault):
    save(); opener = Opener(); original = opener.open
    def changed(request, timeout):
        response = original(request, timeout)
        if len(opener.requests) == 4:
            key = (credentials.SERVICE, credentials.ACCOUNT)
            record = json.loads(vault.data[key]); record['api_key'] = 'different_fixture_1234'
            vault.data[key] = json.dumps(record)
        return response
    opener.open = changed
    with pytest.raises(credentials.PaperVaultError, match='READ_REVOKED'):
        inspect(opener=opener)
    assert len(opener.requests) == 4 and all(r.closed for r in opener.responses)


def test_busy_mutation_revokes_inflight_generation_without_waiting(vault):
    save(); epoch = credentials._epoch
    assert credentials._operations.acquire(False)
    try:
        with pytest.raises(credentials.PaperVaultError, match='BUSY_REVIEW_REQUIRED'):
            credentials.remove(confirm_remove=True)
        assert credentials._current(epoch) is False
    finally:
        credentials._operations.release()
    assert credentials.status()['review_required'] is True
    credentials.remove(confirm_remove=True)


def test_overlapping_mutation_cannot_rearm_a_newer_removal_intent(vault, monkeypatch):
    started, resume = threading.Event(), threading.Event()
    original = vault.set_password; outcomes = []
    def blocked(*args):
        started.set(); assert resume.wait(10); original(*args)
    monkeypatch.setattr(vault, 'set_password', blocked)
    def run():
        try: save()
        except credentials.PaperVaultError as exc: outcomes.append(str(exc))
    worker = threading.Thread(target=run); worker.start()
    try:
        assert started.wait(10)
        with pytest.raises(credentials.PaperVaultError, match='BUSY_REVIEW_REQUIRED'):
            credentials.remove(confirm_remove=True)
    finally:
        resume.set(); worker.join(10)
    assert outcomes == ['PAPER_VAULT_MUTATION_SUPERSEDED']
    assert credentials.status()['review_required'] is True
    with pytest.raises(credentials.PaperVaultError, match='LOCAL_REVIEW_REQUIRED'):
        inspect()
    credentials.remove(confirm_remove=True)


def test_backend_selector_rejects_nonwindows_and_fallbacks(monkeypatch):
    original = credentials._vault
    monkeypatch.setattr(credentials, 'os', types.SimpleNamespace(name='posix'))
    with pytest.raises(credentials.PaperVaultError, match='WINDOWS_ONLY'):
        original()
    monkeypatch.setattr(credentials, 'os', types.SimpleNamespace(name='nt'))
    for module, name in [('keyring.backends.fail', 'Keyring'), ('keyring.backends.WindowsUnsafe', 'WinVaultKeyring'),
                         ('keyring.backends.Windows', 'Other')]:
        backend = type(name, (), {'__module__': module})()
        monkeypatch.setitem(sys.modules, 'keyring', types.SimpleNamespace(get_keyring=lambda: backend))
        with pytest.raises(credentials.PaperVaultError, match='TRUSTED_BACKEND_REQUIRED'):
            original()
    backend = type('WinVaultKeyring', (), {'__module__': 'keyring.backends.Windows'})()
    monkeypatch.setitem(sys.modules, 'keyring', types.SimpleNamespace(get_keyring=lambda: backend))
    assert original() is backend


@pytest.fixture
def client(vault):
    from nexus.app import app
    return TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 12345))


BASE = '/api/execution/alpaca-paper/local-credentials'
HEADERS = {'Origin': 'http://127.0.0.1:8000'}


def test_local_routes_require_confirmation_no_echo_no_store_and_no_get(client, vault):
    response = client.post(BASE + '/save', headers=HEADERS,
        json=dict(api_key=KEY, api_secret=SECRET, confirm_store=True))
    assert response.status_code == 200 and response.json()['configured'] is True
    assert SECRET not in response.text and KEY not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert client.post(BASE + '/status', headers=HEADERS, json={}).json()['configured'] is True
    assert client.post(BASE + '/remove', headers=HEADERS, json={'confirm_remove': True}).json()['configured'] is False
    for action in ('save', 'status', 'remove', 'inspect'):
        assert client.get(BASE + '/' + action).status_code == 405


@pytest.mark.parametrize('headers', [{}, {'Origin': 'https://evil.invalid'}, {'Origin': 'null'},
                                    {'Origin': 'http://localhost:8000'}, {'Origin': HEADERS['Origin'], 'Host': 'evil.invalid'}])
def test_origin_and_rebinding_rejection_happens_before_vault_access(client, vault, headers):
    response = client.post(BASE + '/save', headers=headers, content=SECRET)
    assert response.status_code == 403 and response.headers['cache-control'] == 'no-store'
    assert not vault.reads and not vault.writes and SECRET not in response.text


def test_remote_peer_cannot_use_local_host_and_origin(vault):
    from nexus.app import app
    client = TestClient(app, base_url='http://127.0.0.1:8000', client=('203.0.113.1', 12345))
    assert client.post(BASE + '/status', headers=HEADERS, json={}).status_code == 403
    assert vault.reads == 0


@pytest.mark.parametrize('body,content_type', [
    (b'x' * 2049, 'application/json'), (b'{', 'application/json'), (b'[]', 'application/json'),
    (b'{"confirm_store":true,"confirm_store":true}', 'application/json'),
    (b'{}', 'application/x-www-form-urlencoded'), (b'{"api_secret":"private"}', 'application/json'),
    (b'{"api_key":"x","api_secret":"y","confirm_store":true,"url":"http://evil.invalid"}', 'application/json'),
], ids=['cap', 'json', 'array', 'duplicate', 'form', 'missing', 'extra'])
def test_bounded_manual_body_errors_never_echo_inputs_or_initialize_vault(client, vault, body, content_type):
    response = client.post(BASE + '/save', headers={**HEADERS, 'Content-Type': content_type}, content=body)
    assert response.status_code == 422 and response.json() == {'detail': 'PAPER_ONBOARDING_BODY_INVALID'}
    assert response.headers['cache-control'] == 'no-store' and vault.writes == 0


def test_inspection_endpoint_requires_separate_consent_and_uses_actual_reader(client, vault, monkeypatch):
    save(); opener = Opener()
    original = credentials.inspect_saved
    monkeypatch.setattr(credentials, 'inspect_saved', lambda **kw: original(**kw, opener=opener,
                      wall_clock=lambda: 1000, elapsed_clock=lambda: 50))
    denied = client.post(BASE + '/inspect', headers=HEADERS, json={'confirm_read_only_paper': False})
    assert denied.status_code == 503 and not opener.requests
    response = client.post(BASE + '/inspect', headers=HEADERS,
        json={'confirm_read_only_paper': True, 'expected_account_id': BROKER_ACCOUNT})
    assert response.status_code == 200 and len(opener.requests) == 4
    assert response.json()['ready_for_routing'] is False
    assert KEY not in response.text and SECRET not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_backend_failure_remains_unknown_and_private(client, vault, monkeypatch):
    def broken(*args): raise RuntimeError(SECRET)
    monkeypatch.setattr(vault, 'get_password', broken)
    response = client.post(BASE + '/status', headers=HEADERS, json={})
    assert response.status_code == 503 and SECRET not in response.text
    assert response.json() == {'detail': 'PAPER_ONBOARDING_ACTION_UNAVAILABLE'}


def test_status_and_startup_never_read_paper_credentials_automatically(vault):
    save()
    vault.reads = vault.writes = vault.deletes = 0
    from nexus.app import app
    client = TestClient(app)
    client.get('/api/status'); client.get('/portal'); client.get('/api/onboarding/tasks')
    assert vault.reads == vault.writes == vault.deletes == 0


def test_actual_paper_credential_controller():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for actual PORTAL controller acceptance')
    script = Path(__file__).resolve().parents[1] / 'tools' / 'paper_credentials_smoke.cjs'
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_body_stream_cap_without_content_length_stops_intake():
    import asyncio
    from starlette.requests import Request
    from nexus.paper_onboarding import _body
    calls = []
    async def receive():
        calls.append(1)
        return {'type': 'http.request', 'body': b'x' * 1025, 'more_body': True}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/',
                       'headers': [(b'content-type', b'application/json')]}, receive)
    with pytest.raises(ValueError):
        asyncio.run(_body(request))
    assert len(calls) == 2


def test_read_endpoint_rejects_transport_and_authority_overrides_before_access(client, vault):
    for field in ('opener', 'wall_clock', 'permission_check', 'url', 'live', 'submit'):
        response = client.post(BASE + '/inspect', headers=HEADERS,
            json={'confirm_read_only_paper': True, field: SECRET})
        assert response.status_code == 422 and SECRET not in response.text
    assert vault.reads == vault.writes == 0
