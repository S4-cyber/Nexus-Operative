from fastapi.testclient import TestClient
from nexus.app import app
client=TestClient(app)

def test_status_fail_closed():
    r=client.get('/api/status')
    assert r.status_code==200
    body=r.json()
    assert body['live_orders_enabled'] is False
    assert body['money_movement_enabled'] is False
    assert body['broker_connected'] is False

def test_portal_assets():
    r=client.get('/portal')
    assert r.status_code==200
    assert 'PORTAL' in r.text
    assert 'No bank, card or brokerage is connected' in r.text

def test_three_tiers_disclose_simulation():
    t=client.get('/api/tiers').json()['tiers']
    assert [x['tier'] for x in t]==['terrestrial','solar','universal']
    assert all(x['score_is_win_probability'] is False for x in t)

def test_funding_preview_does_not_transfer():
    r=client.post('/api/funding/preview',json={'amount':120,'cadence':'weekly'})
    assert r.status_code==200
    assert r.json()['transfer_scheduled'] is False
    assert client.post('/api/funding/preview',json={'amount':-3,'cadence':'once'}).status_code==422

def test_halt_requires_explicit_reset_and_stays_paused(monkeypatch):
    from nexus import app as backend
    monkeypatch.setattr(backend.observer,"start",lambda:None)
    monkeypatch.setattr(backend.observer,"stop",lambda:None)
    assert client.post('/api/simulate/halt').json()['halted'] is True
    assert client.post('/api/portal',json={'enabled':True}).status_code==409
    assert client.post('/api/simulate/halt/reset',json={'confirm_reset':False}).status_code==422
    assert client.get('/api/status').json()['halted'] is True
    response=client.post('/api/simulate/halt/reset',json={'confirm_reset':True})
    assert response.status_code==200
    assert response.json()['halted'] is False
    assert response.json()['portal_enabled'] is False
    assert response.json()['live_orders_enabled'] is False
    assert client.post('/api/simulate/halt/reset',json={'confirm_reset':True}).status_code==409
    assert client.post('/api/portal',json={'enabled':True}).json()['portal_enabled'] is True
    assert client.post('/api/portal',json={'enabled':False}).json()['portal_enabled'] is False
