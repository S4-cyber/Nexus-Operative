from fastapi.testclient import TestClient
from nexus.app import app
client=TestClient(app,base_url="http://127.0.0.1",client=("127.0.0.1",50000))

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

def test_halt_requires_explicit_reset_and_preserves_engine_state(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from nexus import app as backend
    from nexus.durable_orders import DurableOrderGateway
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr(backend,'research_order_gateway',DurableOrderGateway(tmp_path/'orders.sqlite3'))
    monkeypatch.setattr(backend,'_paused',True)
    monkeypatch.setattr(backend,'_halted',False)
    monkeypatch.setattr(backend,'observer',SimpleNamespace(start=lambda:None,stop=lambda:None))
    monkeypatch.setattr(backend,'refresh_worker',SimpleNamespace(start=lambda *a,**k:None,stop=lambda *_:None))
    assert client.post('/api/simulate/halt').json()['halted'] is True
    # An emergency trading cut does not prohibit starting research.
    assert client.post('/api/portal',json={'enabled':True}).json()['portal_enabled'] is True
    assert client.post('/api/simulate/halt/reset',json={'confirm_reset':False}).status_code==422
    assert client.get('/api/status').json()['halted'] is True
    response=client.post('/api/simulate/halt/reset',json={'confirm_reset':True})
    assert response.status_code==200
    assert response.json()['halted'] is False
    assert response.json()['portal_enabled'] is True
    assert response.json()['live_orders_enabled'] is False
    assert client.post('/api/simulate/halt/reset',json={'confirm_reset':True}).status_code==409
    assert client.post('/api/portal',json={'enabled':False}).json()['portal_enabled'] is False
