from contextlib import closing
import pytest
from nexus import forward_evidence,paper_forward
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure
from test_paper_forward import setup


@pytest.mark.parametrize('full',['sessions','quotes'])
def test_preflight_rejects_before_account_initialization(tmp_path,monkeypatch,full):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));configure('terrestrial',True)
    r=PaperForwardRunner(DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3'))
    if full=='sessions':
        monkeypatch.setattr(paper_forward,'SESSION_LIMIT',1)
        with closing(r._db()) as db,db:
            db.execute("INSERT INTO sessions VALUES ('old',1,1,'STOPPED','{}','{}','{}')")
    else:monkeypatch.setattr(forward_evidence,'LIMIT',0)
    with pytest.raises(ValueError,match='FORWARD_.*_CAP'):r.prepare('terrestrial')
    assert not (tmp_path/'order_lifecycle.sqlite3').exists()
    assert not (tmp_path/'paper_portfolio.sqlite3').exists()
    assert not r.status()['enabled'] and not r.status()['ownership_held']


def test_exhausted_quote_capacity_stops_before_fetch(tmp_path,monkeypatch):
    monkeypatch.setattr(forward_evidence,'LIMIT',1)
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle()
    calls=[];r.fetcher=lambda product:calls.append(product)
    result=r.single_cycle()
    assert result['reason']=='FORWARD_QUOTE_EVIDENCE_CAP'
    assert not calls and not r.status()['ownership_held']
    assert r.storage_capacity()['quote_observations_retained']==1
    assert not g.recovery_report()['orders']


def test_capacity_api_is_diagnostic_and_warns_near_limit(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import nexus.app as backend
    monkeypatch.setattr(forward_evidence,'LIMIT',2)
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle()
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    before=g.recovery_report();session_before=r.sessions()
    data=TestClient(backend.app).get('/api/paper/forward').json()['storage_capacity']
    assert data['quote_observations_remaining']==1
    assert 'QUOTE_EVIDENCE_NEAR_LIMIT' in data['warnings']
    assert not data['automatic_deletion'] and data['new_session_storage_available']
    assert g.recovery_report()==before and r.sessions()==session_before
    r.stop()
