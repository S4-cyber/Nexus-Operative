import copy
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
import pytest
from test_paper_forward import setup
from test_forward_cost_review import fixture
from nexus import forward_evidence


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('lag,delay,allowed',[(9,1,True),(9.5,1,False),(9.5,.4,True)])
def test_exchange_age_rechecked_after_state_replay(tmp_path,monkeypatch,side,lag,delay,allowed):
    r,g,c=setup(tmp_path,monkeypatch);original=g._restore;delayed=[False]
    def restore(db):
        state=original(db)
        pending=[o for o in state[0].intents.values() if o['side']==side and o['status']=='SIMULATED_ACK_ONLY' and o['id'] in state[0].order_terms]
        if pending and not delayed[0]:c.now+=delay;delayed[0]=True
        return state
    if side=='BUY':monkeypatch.setattr(g,'_restore',restore)
    for i in range(7):
        c.now=1000+i*5;c.price=100+i*.1;c.stamp=c.now-lag;result=r.single_cycle()
    if side=='SELL':
        from nexus.tier_permissions import configure
        assert result['decision']=='BUY'
        configure('terrestrial',False)
        monkeypatch.setattr(g,'_restore',restore)
        c.now=1035;c.price=102;c.stamp=c.now-lag;result=r.single_cycle()
    assert delayed[0]
    report=g.recovery_report()
    if allowed:
        assert result['decision']==side
        assert r.evidence_report(r.status()['session'])['local_consistency_ok']
    else:
        assert result['reason']=='STALE_OR_FUTURE_EXCHANGE_QUOTE_AT_FILL'
        assert not r._prices
        pending=[o for o in report['orders'] if o['side']==side][-1]
        assert pending['status']=='SIMULATED_CANCELLED' and pending['simulated_filled_notional']=='0'
    assert bool(report['execution_account']['account']['positions'])==((side=='BUY')==allowed)
    assert report['portfolio_reconciled']
    r.stop()


@pytest.mark.parametrize('age,valid',[(10,True),(10.01,False),(-.01,False)])
def test_review_checks_exchange_age_at_execution_not_only_intake(tmp_path,monkeypatch,age,valid):
    r,g,sid,orders=fixture(tmp_path,monkeypatch)
    with closing(r._db()) as db:quotes=forward_evidence.review(db,sid)
    altered=copy.deepcopy(quotes)
    for item in orders:
        cycle=int(item['order']['id'].split('_')[2])
        evaluation=Decimal(item['quote_events'][0]['assumptions']['evaluated_at'])
        point=next(p for p in altered['points'] if p['cycle']==cycle)
        point['quote']['exchange_time']=datetime.fromtimestamp(float(evaluation)-age,timezone.utc).isoformat()
    checked=forward_evidence.crosscheck(altered,orders,r.evidence_report(sid)['counters'],r._policy)
    assert ('FILL_EXCHANGE_TIME_DISAGREEMENT' not in checked['reasons'])==valid


@pytest.mark.parametrize('stamp',['missing','2026-10-17T10:00:00',None])
def test_review_rejects_invalid_exchange_timestamp(tmp_path,monkeypatch,stamp):
    r,g,sid,orders=fixture(tmp_path,monkeypatch)
    with closing(r._db()) as db:quotes=forward_evidence.review(db,sid)
    for point in quotes['points']:point['quote']['exchange_time']=stamp
    checked=forward_evidence.crosscheck(quotes,orders,r.evidence_report(sid)['counters'],r._policy)
    assert 'FILL_EXCHANGE_TIME_DISAGREEMENT' in checked['reasons']


def test_export_keeps_exchange_age_disagreement_as_review_blocker(tmp_path,monkeypatch):
    from nexus import forward_review
    r,g,sid,orders=fixture(tmp_path,monkeypatch)
    with closing(r._db()) as db:quotes=forward_evidence.review(db,sid)
    before=g.recovery_report()['execution_account']
    for point in quotes['points']:point['quote']['exchange_time']='1970-01-01T00:00:00+00:00'
    monkeypatch.setattr(forward_evidence,'review',lambda *args:quotes)
    bundle=forward_review.build(r.evidence_report(sid))
    assert 'FILL_EXCHANGE_TIME_DISAGREEMENT' in bundle['payload']['review_blockers']
    assert not bundle['payload']['report']['local_consistency_ok']
    assert forward_review.verify(bundle)['summary_digest_ok']
    assert g.recovery_report()['execution_account']==before
