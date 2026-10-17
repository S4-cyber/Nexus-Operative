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
    else:
        assert Decimal(pending['simulated_filled_notional'])==0
        assert not recovery['execution_account']['account']['positions']
        assert evidence['local_consistency_ok']
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
