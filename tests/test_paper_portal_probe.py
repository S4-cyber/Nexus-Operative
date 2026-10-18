"""Managed Portal pacing with injected vault/HTTPS; never actual broker access."""
import hashlib
import types

import pytest
from fastapi.testclient import TestClient
from nexus import paper_portal_probe as portal, paper_credentials as credentials
from nexus.paper_read_pacing import PaperReadPacing
from test_paper_credentials import vault, save, BASE, HEADERS
from test_alpaca_paper_inspection import ACCOUNT, OTHER, Opener, KEY, SECRET


@pytest.fixture
def setup(vault, tmp_path, monkeypatch):
    from nexus.app import app
    path = tmp_path / 'private' / 'pacing.sqlite3'
    monkeypatch.setattr(portal, '_managed_path', lambda account: path)
    opener = Opener()
    original = portal.inspect_saved
    monkeypatch.setattr(portal, 'inspect_saved', lambda **kw: original(
        **kw, opener=opener, wall_clock=lambda: 1000, elapsed_clock=lambda: 50))
    client = TestClient(app, base_url=HEADERS['Origin'], client=('127.0.0.1', 12345))
    return client, path, opener


def post(client, action, **data):
    return client.post(BASE + '/' + action, headers=HEADERS, json=data)


def test_pacing_status_is_vault_silent_and_keeps_missing_storage_absent(setup, vault):
    client, path, opener = setup
    response = post(client, 'pacing-status', expected_account_id=ACCOUNT)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['journal_present'] is False
    assert vault.reads == vault.writes == 0 and not opener.requests and not path.parent.exists()
    for action in ('paced-inspect', 'pacing-status'):
        assert client.get(BASE + '/' + action).status_code == 405


def test_real_saved_inspector_retains_cooldown_without_trading_permission(setup, vault):
    client, path, opener = setup
    save()
    response = post(client, 'paced-inspect', expected_account_id=ACCOUNT, confirm_read_only_paper=True)
    assert response.status_code == 200 and len(opener.requests) == 4
    data = response.json()
    assert data['account_id'] == ACCOUNT and data['durable_local_pacing'] is True
    assert data['local_pacing']['sessions'] == 1 and data['local_pacing']['events'] == 2
    assert data['local_pacing']['next_allowed_at'] == 1060
    assert data['ready_for_routing'] is False and data['live_orders_enabled'] is False
    assert data['portable_observation']['account_id'] == ACCOUNT
    assert all(value not in response.text for value in (KEY, SECRET, str(path)))
    before = path.read_bytes()
    denied = post(client, 'paced-inspect', expected_account_id=ACCOUNT, confirm_read_only_paper=True)
    assert denied.status_code == 503 and len(opener.requests) == 4 and path.read_bytes() == before
    credentials.remove(confirm_remove=True)
    reads = vault.reads
    status = post(client, 'pacing-status', expected_account_id=ACCOUNT)
    assert status.json()['sessions'] == 1 and status.json()['next_allowed_at'] == 1060
    assert vault.reads == reads and path.read_bytes() == before


@pytest.mark.parametrize('confirm', [False, None, 1, 'true'])
def test_confirmation_precedes_vault_and_file_creation(setup, vault, confirm):
    client, path, opener = setup
    response = post(client, 'paced-inspect', expected_account_id=ACCOUNT, confirm_read_only_paper=confirm)
    assert response.status_code == 503
    assert vault.reads == 0 and not path.parent.exists() and not opener.requests


@pytest.mark.parametrize('action', ['paced-inspect', 'pacing-status'])
def test_origin_fields_and_account_validation_precede_access(setup, vault, action):
    client, path, opener = setup
    data = dict(expected_account_id=ACCOUNT)
    if action == 'paced-inspect': data['confirm_read_only_paper'] = True
    for extra in ('pacing_path', 'opener', 'url', 'live', 'permission_check'):
        response = post(client, action, **data, **{extra: SECRET})
        assert response.status_code == 422 and SECRET not in response.text
    missing = dict(data); missing.pop('expected_account_id')
    assert post(client, action, **missing).status_code == 422
    assert post(client, action, **dict(data, expected_account_id=SECRET)).status_code == 503
    assert client.post(BASE + '/' + action, headers={'Origin': 'https://evil.invalid'}, content=SECRET).status_code == 403
    remote = TestClient(client.app, base_url=HEADERS['Origin'], client=('203.0.113.1', 12345))
    assert post(remote, action, **data).status_code == 403
    assert vault.reads == 0 and not path.parent.exists() and not opener.requests


def test_mismatched_account_keeps_pending_state_and_status_omits_private_fields(setup, vault):
    client, path, opener = setup
    save()
    response = post(client, 'paced-inspect', expected_account_id=OTHER, confirm_read_only_paper=True)
    assert response.status_code == 503 and len(opener.requests) == 1
    state = PaperReadPacing(path, OTHER).inspect()
    assert state['pending_token'] is not None
    before = path.read_bytes(); reads = vault.reads
    response = post(client, 'pacing-status', expected_account_id=OTHER)
    assert response.json()['pending_read'] is True
    # A BEGIN token is its public journal head digest; only the named mutation
    # ticket is omitted, not the diagnostic head (which may have equal bytes).
    assert response.json()['head_sha256'] == state['head_sha256']
    assert 'pending_token' not in response.text and str(path) not in response.text
    assert path.read_bytes() == before and vault.reads == reads


def test_managed_path_is_account_hashed_and_separate_from_portable_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, 'os', types.SimpleNamespace(name='nt', environ={'LOCALAPPDATA': str(tmp_path)}))
    path = portal._managed_path(ACCOUNT)
    assert path == tmp_path / 'NexusOperative' / 'paper-read-pacing' / (hashlib.sha256(ACCOUNT.encode()).hexdigest() + '.sqlite3')
    assert path != portal._managed_path(OTHER) and ACCOUNT not in str(path)
    assert not path.parent.exists()


def test_managed_source_directory_is_rejected_before_vault(setup, vault, monkeypatch):
    from nexus.paper_acceptance_probe import ROOT
    client, path, opener = setup
    monkeypatch.setattr(portal, '_managed_path', lambda account: ROOT / 'storage' / 'private.sqlite3')
    response = post(client, 'paced-inspect', expected_account_id=ACCOUNT, confirm_read_only_paper=True)
    assert response.status_code == 503 and vault.reads == 0 and not opener.requests


def test_passive_startup_routes_do_not_create_managed_journal(setup, vault):
    client, path, opener = setup
    for route in ('/api/status', '/portal', '/api/onboarding/tasks'):
        assert client.get(route).status_code == 200
    assert vault.reads == 0 and not opener.requests and not path.parent.exists()
