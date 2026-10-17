import copy
from decimal import Decimal
import pytest
from test_paper_forward import setup,warmup


def fixture(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c)
    c.now+=5;c.price=102;r.single_cycle();r.stop()
    session=r.status()['session'];orders=g.session_orders(session)
    return r,g,session,orders


def test_review_reports_exact_modeled_cost_totals(tmp_path,monkeypatch):
    r,g,sid,orders=fixture(tmp_path,monkeypatch)
    report=r.evidence_report(sid);checked=report['execution_crosscheck']
    assert report['local_consistency_ok']
    expected=sum((Decimal(item['order']['simulated_fee_total']) for item in orders),Decimal(0))
    assert Decimal(checked['modeled_fees'])==expected>0
    assert Decimal(checked['modeled_buy_notional'])>0
    assert Decimal(checked['modeled_sell_notional'])>0
    assert not report['strategy_validated']


@pytest.mark.parametrize('field,value',[
    ('price','1'),('quantity','-1'),('fee','0'),('quantity','NaN'),('sequence',0),
    ('fee_bps','0'),('slippage_bps','0'),('available_quantity','0'),('evaluated_at','999999')])
def test_quote_cost_or_policy_disagreement_is_reported(tmp_path,monkeypatch,field,value):
    r,g,sid,orders=fixture(tmp_path,monkeypatch);altered=copy.deepcopy(orders)
    event=altered[0]['quote_events'][0]
    if field in event:event[field]=value
    else:event['assumptions'][field]=value
    monkeypatch.setattr(g,'session_orders',lambda session:altered)
    report=r.evidence_report(sid)
    assert not report['local_consistency_ok']
    assert 'FILL_MODEL_OR_POLICY_DISAGREEMENT' in report['reasons']


def test_order_totals_must_match_quote_event_totals(tmp_path,monkeypatch):
    r,g,sid,orders=fixture(tmp_path,monkeypatch)
    orders[0]['order']['simulated_fee_total']='99'
    monkeypatch.setattr(g,'session_orders',lambda session:orders)
    assert 'FILL_MODEL_OR_POLICY_DISAGREEMENT' in r.evidence_report(sid)['reasons']


@pytest.mark.parametrize('column,payload',[('counts_json','[]'),('counts_json','{"BUY":true}'),('policy_json','null'),('policy_json','{}')])
def test_malformed_session_metadata_returns_unavailable(tmp_path,monkeypatch,column,payload):
    import sqlite3
    import nexus.app as backend
    from fastapi.testclient import TestClient
    r,g,sid,orders=fixture(tmp_path,monkeypatch)
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        db.execute(f'UPDATE sessions SET {column}=? WHERE id=?',(payload,sid))
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    response=TestClient(backend.app).get(f'/api/paper/forward/sessions/{sid}/evidence')
    assert response.status_code==503
    assert response.json()['detail']=='FORWARD_EVIDENCE_UNAVAILABLE'
