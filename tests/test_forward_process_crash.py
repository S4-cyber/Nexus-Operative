"""Actual process death across the forward quote/fill/diagnostic boundaries."""
import os
import subprocess
import sys
from decimal import Decimal
import pytest
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.forward_review import build
from nexus.tier_permissions import configure
from test_paper_forward import Clock

SCRIPT=r'''
import os,sys
from datetime import datetime,timezone
from pathlib import Path
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure
configure('terrestrial',True)
g=DurableOrderGateway(Path(os.environ['NEXUS_PORTABLE_DATA'])/'order_lifecycle.sqlite3')
clock={'now':1000,'price':100}
def quote(product):
    return dict(product=product,provider='coinbase_exchange_public',read_only=True,orders_allowed=False,
        bid=clock['price'],ask=clock['price']+.01,last=clock['price'],received_at=clock['now'],
        exchange_time=datetime.fromtimestamp(clock['now'],timezone.utc).isoformat())
r=PaperForwardRunner(g,quote,lambda:clock['now'])
r.prepare('terrestrial')
if sys.argv[1]=='before_fill':
    g.simulate_quote_fill=lambda *args,**kwargs:os._exit(74)
else:
    original=r._record
    def record(decision,*args,**kwargs):
        if decision=='BUY':os._exit(75)
        return original(decision,*args,**kwargs)
    r._record=record
for i in range(7):
    clock.update(now=1000+i*5,price=100+i*.1)
    r.single_cycle()
raise SystemExit('crash hook was not reached')
'''


NO_FILL_SCRIPT=r'''
import os
from datetime import datetime,timezone
from pathlib import Path
from decimal import Decimal
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure

configure('terrestrial',True)
root=Path(os.environ['NEXUS_PORTABLE_DATA'])
g=DurableOrderGateway(root/'order_lifecycle.sqlite3')
g.initialize_account(10000);g.link_portfolio()
g.submit('entry','BTC-USD','BUY',100,tier='terrestrial')
g.simulate_priced_fill('entry','fill',1,1,100)
clock={'now':1000,'price':250}
def quote(product):
    return dict(product=product,provider='coinbase_exchange_public',read_only=True,
        orders_allowed=False,bid=clock['price'],ask=clock['price']+.01,
        last=clock['price'],received_at=clock['now'],
        exchange_time=datetime.fromtimestamp(clock['now'],timezone.utc).isoformat())

first=PaperForwardRunner(g,quote,lambda:clock['now']);first.prepare('terrestrial')
configure('terrestrial',False)
assert first.single_cycle()['decision']=='SELL'
first.stop()

cold=DurableOrderGateway(g.path)
resumed=PaperForwardRunner(cold,quote,lambda:clock['now'])
resumed.prepare('terrestrial');clock.update(now=1005,price=251)
original=cold._restore;delayed=[False]
def restore(db):
    state=original(db);gateway=state[0]
    pending=next((order for order in gateway.intents.values()
        if order.get('intent_scope')=='POSITION_REDUCING_EXIT' and
        order['status']=='SIMULATED_REDUCTION_PENDING'),None)
    if (pending is not None and
            Decimal(gateway.order_terms[pending['id']]['created_at'])==
                Decimal(str(clock['now'])) and
            pending.get('reduction_slice_count')==1 and not delayed[0]):
        clock['now']+=2;delayed[0]=True
    return state
cold._restore=restore
original_record=resumed._record
def record(decision,reason,**extra):
    if decision=='VETO' and reason=='ORDER_EXPIRED_WITHOUT_FILL':
        os._exit(76)
    return original_record(decision,reason,**extra)
resumed._record=record
resumed.single_cycle()
raise SystemExit('no-fill crash hook was not reached')
'''


@pytest.mark.parametrize('phase,exit_code',[('before_fill',74),('after_fill',75)])
def test_process_death_preserves_authoritative_ledger_and_requires_review(tmp_path,monkeypatch,phase,exit_code):
    result=subprocess.run([sys.executable,'-c',SCRIPT,phase],
        env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path)},capture_output=True,text=True,timeout=30)
    assert result.returncode==exit_code,result.stderr
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    g=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    c=Clock();c.now=1035;c.price=102
    restarted=PaperForwardRunner(g,c.quote,lambda:c.now)
    assert not restarted.status()['enabled'] and not restarted.status()['ownership_held']
    recovery=g.recovery_report();assert recovery['portfolio_reconciled']
    assert len(recovery['orders'])==1
    pending=recovery['orders'][0]
    session=restarted.sessions()['sessions'][0]
    assert session['effective_state']=='INTERRUPTED_OR_PRIOR_SESSION'
    assert session['requires_review'] and session['counts'].get('BUY',0)==0
    evidence=restarted.evidence_report(session['session'])
    assert evidence['quote_evidence']['integrity_ok'] and evidence['quote_evidence']['observations']==7
    assert 'SESSION_INTERRUPTION_REQUIRES_REVIEW' in build(evidence)['payload']['review_blockers']
    if phase=='after_fill':
        assert Decimal(pending['simulated_filled_notional'])>0
        assert recovery['execution_account']['account']['positions']
        assert 'SESSION_COUNTER_FILL_DISAGREEMENT' in evidence['reasons']
        assert not evidence['durable_event_evidence']['durable_fill_attempts_bound']
        assert 'DURABLE_FILL_ATTEMPT_DIGEST_DISAGREEMENT' in evidence['reasons']
    else:
        assert Decimal(pending['simulated_filled_notional'])==0
        assert not recovery['execution_account']['account']['positions']
        # The quote committed before process death has no durable outcome.
        # Keep the evidence readable, but never promote that gap to a
        # complete per-session reconciliation.
        assert not evidence['local_consistency_ok']
        assert not evidence['durable_event_evidence']['quote_outcome_bijection']
        assert 'QUOTE_OUTCOME_BIJECTION_DISAGREEMENT' in evidence['reasons']
    # Ownership was released by actual process death, but normal admission still
    # refuses the unreviewed pending order rather than resuming the old session.
    with pytest.raises(ValueError,match='REVIEW_OUTSTANDING_PAPER_ORDERS_FIRST'):
        restarted.prepare('terrestrial')
    assert not restarted.status()['ownership_held']
    commands=g.recovery_report()['commands']
    assert g.submit(pending['id'],pending['product'],pending['side'],pending['notional'],tier=pending['tier'])['idempotent_replay']
    assert g.recovery_report()['commands']==commands
    # Explicitly retire the reviewed pending order; never rewrite old counters.
    g.cancel(pending['id']);configure('terrestrial',False)
    if phase=='after_fill':
        restarted.prepare('terrestrial')
        assert restarted.single_cycle()['decision']=='SELL'
        restarted.stop()
        final=g.recovery_report()
        assert final['portfolio_reconciled'] and not final['execution_account']['account']['positions']
        assert 'SESSION_COUNTER_FILL_DISAGREEMENT' in restarted.evidence_report(session['session'])['reasons']
    else:
        with pytest.raises(ValueError,match='RISK_TIER_DISENGAGED'):restarted.prepare('terrestrial')


def test_process_death_after_durable_no_fill_keeps_attempt_and_review_gap(
        tmp_path,monkeypatch):
    result=subprocess.run([sys.executable,'-c',NO_FILL_SCRIPT],
        env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path)},
        capture_output=True,text=True,timeout=30)
    assert result.returncode==76,result.stderr
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    g=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3')
    recovery=g.recovery_report();assert recovery['portfolio_reconciled']
    mandate=next(order for order in recovery['orders']
        if order.get('intent_scope')=='POSITION_REDUCING_EXIT')
    assert mandate['status']=='SIMULATED_REDUCTION_PENDING'
    assert mandate['reduction_slice_count']==1
    position=recovery['execution_account']['account']['positions']['BTC-USD']
    assert Decimal(position['quantity'])>0
    attempts=g.order_report(mandate['id'])['no_fill_events']
    assert len(attempts)==1 and attempts[0]['reason']=='ORDER_EXPIRED_WITHOUT_FILL'

    restarted=PaperForwardRunner(g,lambda product:None,lambda:1010)
    assert not restarted.status()['enabled'] and not restarted.status()['ownership_held']
    interrupted=next(session for session in restarted.sessions()['sessions']
        if session['effective_state']=='INTERRUPTED_OR_PRIOR_SESSION')
    evidence=restarted.evidence_report(interrupted['session'])
    assert evidence['quote_evidence']['observations']==1
    events=evidence['durable_event_evidence']
    assert not events['quote_outcome_bijection']
    assert not events['durable_no_fill_outcomes_reconciled']
    assert not events['durable_no_fill_attempts_bound']
    assert 'QUOTE_OUTCOME_BIJECTION_DISAGREEMENT' in evidence['reasons']
    assert 'DURABLE_NO_FILL_OUTCOME_DISAGREEMENT' in evidence['reasons']
    assert 'DURABLE_NO_FILL_ATTEMPT_DIGEST_DISAGREEMENT' in evidence['reasons']
    assert not evidence['local_consistency_ok']
