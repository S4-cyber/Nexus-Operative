import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from nexus.app import app, funding_preview
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

def test_funding_preview_does_not_transfer(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    r=client.post('/api/funding/preview',json={'amount':120,'cadence':'weekly'})
    assert r.status_code==200
    assert all(r.json()[key] is False for key in ('bank_link_created','transfer_scheduled',
        'funding_authorized','money_movement_enabled','live_orders_enabled'))
    assert not list(tmp_path.iterdir())
    assert client.post('/api/funding/preview',json={'amount':-3,'cadence':'once'}).status_code==422


@pytest.mark.parametrize('payload',[
    {'amount':True,'cadence':'once'}, {'amount':'120','cadence':'once'},
    {'amount':.001,'cadence':'once'}, {'amount':1.001,'cadence':'once'},
],ids=['boolean','string','sub-cent','fractional-cent'])
def test_funding_preview_rejects_coercion_without_authority(payload):
    response=client.post('/api/funding/preview',json=payload)
    assert response.status_code==422
    assert response.json()=={'detail':'FUNDING_PREVIEW_INVALID'}


def test_funding_preview_rejects_polymorphic_and_unbounded_direct_values():
    cadence=type('Cadence',(str,),{})('once')
    for payload in ({'amount':120,'cadence':cadence},
            {'amount':10**10000,'cadence':'once'}):
        with pytest.raises(HTTPException) as rejected:funding_preview(payload)
        assert rejected.value.status_code==422
        assert rejected.value.detail=='FUNDING_PREVIEW_INVALID'


def test_funding_preview_rejects_extra_sensitive_fields_without_echo():
    marker='PRIVATE-BANK-MARKER-DO-NOT-ECHO'
    response=client.post('/api/funding/preview',json={
        'amount':120,'cadence':'once','routing_number':marker})
    assert response.status_code==422 and response.json()=={'detail':'FUNDING_PREVIEW_INVALID'}
    assert marker not in response.text

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
