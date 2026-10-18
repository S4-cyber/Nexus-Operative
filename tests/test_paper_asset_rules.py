"""Explicit paper asset observations with injected HTTPS/vault and temp pacing."""
import json
import pytest
from fastapi.testclient import TestClient
from nexus import paper_asset_rules as module,paper_credentials as credentials,paper_portal_probe as portal
from nexus import alpaca_paper_inspection as transport
from nexus.paper_read_pacing import PaperReadPacing
from test_alpaca_paper_inspection import Opener,Response,account,ACCOUNT,OTHER,KEY,SECRET
from test_paper_credentials import vault,save
from test_paper_portal_probe import BASE,HEADERS


def asset(**changes):
    r=dict(id=OTHER,symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
        min_order_size='0.0001',min_trade_increment='0.0001',price_increment='1',name=SECRET,attributes=[])
    r.update(changes);return r


def read(opener=None,**kw):
    return module.inspect_rules(KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
        opener=opener or Opener([account(),asset(),account()]),wall_clock=lambda:1000,elapsed_clock=lambda:50,**kw)


def test_three_exact_gets_return_only_reported_fields_no_authority():
    opener=Opener([account(),asset(),account()]);r=read(opener)
    assert [req.full_url for req,_ in opener.requests]==[transport.URLS[0],transport.ASSET_URL,transport.URLS[0]]
    assert all(req.method=='GET' for req,_ in opener.requests) and r['requests_completed']==3
    assert r['asset']['min_order_size']=='0.0001' and r['review_reasons']==[]
    assert r['status']=='REVIEW_REQUIRED' and all(r[k] is False for k in module.FLAGS)
    assert SECRET not in json.dumps(r) and 'name' not in r['asset'] and 'cash' not in r


@pytest.mark.parametrize('change',[{'symbol':'ETH/USD'},{'class':'us_equity'},{'id':'private-invalid'},
    {'tradable':1},{'fractionable':None},{'status':'unknown'},{'min_order_size':'0'},
    {'min_trade_increment':'-1'},{'price_increment':'NaN'},{'min_order_size':True}])
def test_unknown_or_invalid_asset_cannot_become_observed_rules(change):
    opener=Opener([account(),asset(**change),account()])
    with pytest.raises(transport.PaperInspectionError):read(opener)
    assert len(opener.requests)==2


@pytest.mark.parametrize('first,last',[(dict(id=OTHER),{}),({},dict(cash='999'))])
def test_account_disagreement_refuses_without_acknowledging_asset(first,last):
    opener=Opener([dict(account(),**first),asset(),dict(account(),**last)])
    with pytest.raises(transport.PaperInspectionError,match='ACCOUNT'):read(opener)


def test_inactive_asset_and_unknown_account_permission_are_review_only():
    a=dict(account(),trading_blocked=None);r=read(Opener([a,asset(tradable=False,status='inactive'),a]))
    assert r['review_reasons']==['ACCOUNT_PERMISSION_FLAGS_REVIEW','ASSET_NOT_REPORTED_ACTIVE_AND_TRADABLE']
    assert r['ready_for_routing'] is False


def test_asset_reader_shares_durable_cooldown_with_inventory(tmp_path):
    gate=PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT);read(pacing=gate)
    before=gate.path.read_bytes();opener=Opener()
    with pytest.raises(transport.PaperInspectionError):
        transport.inspect_account(KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
            opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,pacing=gate)
    assert opener.requests==[] and gate.path.read_bytes()==before


def test_bad_metadata_keeps_pending_reservation_for_explicit_review(tmp_path):
    gate=PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT)
    with pytest.raises(transport.PaperInspectionError):read(Opener([account(),asset(price_increment=None)]),pacing=gate)
    assert gate.inspect()['pending_token'] is not None


@pytest.fixture
def setup(vault,tmp_path,monkeypatch):
    from nexus.app import app
    path=tmp_path/'private'/'p.sqlite3';opener=Opener([account(),asset(),account()])
    monkeypatch.setattr(portal,'_managed_path',lambda _:path)
    original=portal.inspect_rules_saved
    monkeypatch.setattr(portal,'inspect_rules_saved',lambda **kw:original(**kw,opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50))
    return TestClient(app,base_url=HEADERS['Origin'],client=('127.0.0.1',12345)),path,opener


def post(client,**changes):
    data=dict(expected_account_id=ACCOUNT,confirm_read_only_paper=True);data.update(changes)
    return client.post(BASE+'/asset-rules',headers=HEADERS,json=data)


def test_api_explicit_saved_read_and_cooldown_with_no_download(setup,vault):
    client,path,opener=setup;save();r=post(client)
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    data=r.json();assert data['durable_local_pacing'] and data['local_pacing']['sessions']==1
    assert data['local_pacing']['next_allowed_at']==1060 and 'portable_observation' not in data
    assert KEY not in r.text and SECRET not in r.text and str(path) not in r.text
    before=path.read_bytes();assert post(client).status_code==503
    assert len(opener.requests)==3 and path.read_bytes()==before


@pytest.mark.parametrize('changes,code',[({'confirm_read_only_paper':False},503),({'confirm_read_only_paper':1},503),({'expected_account_id':'bad'},503),({'pacing_path':'private'},422)])
def test_api_bad_scope_or_consent_does_not_open_vault_or_network(setup,vault,changes,code):
    client,path,opener=setup;assert post(client,**changes).status_code==code
    assert vault.reads==0 and not opener.requests and not path.parent.exists()


def test_api_get_and_foreign_origin_never_read_keys(setup,vault):
    client,path,opener=setup
    assert client.get(BASE+'/asset-rules').status_code==405
    assert client.post(BASE+'/asset-rules',headers={'Origin':'https://foreign.invalid'},json={}).status_code==403
    assert vault.reads==0 and not opener.requests and not path.parent.exists()


def test_key_removal_during_asset_read_suppresses_ack_and_keeps_pending(setup,vault,monkeypatch):
    client,path,opener=setup;save();original=opener.open
    def revoke(req,timeout):
        if len(opener.requests)==1:credentials.remove(confirm_remove=True)
        return original(req,timeout)
    monkeypatch.setattr(opener,'open',revoke)
    r=post(client);assert r.status_code==503 and SECRET not in r.text
    assert PaperReadPacing(path,ACCOUNT).inspect()['pending_token'] is not None


def test_final_saved_pair_readback_rejects_external_replacement(setup,vault,monkeypatch):
    client,path,opener=setup;save();original=opener.open
    def replace(req,timeout):
        if len(opener.requests)==2:
            key=(credentials.SERVICE,credentials.ACCOUNT)
            vault.data[key]=vault.data[key].replace(KEY,KEY.upper())
        return original(req,timeout)
    monkeypatch.setattr(opener,'open',replace)
    assert post(client).status_code==503
    assert PaperReadPacing(path,ACCOUNT).inspect()['last_outcome']=='SUCCESS'
