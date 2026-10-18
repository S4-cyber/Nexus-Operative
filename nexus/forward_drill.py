"""Bounded disposable exercise of the actual forward runner, never public/broker IO."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from nexus.recovery_drill import fingerprint


def _require(condition,reason):
    if not condition:raise ValueError('FORWARD_DRILL_'+reason)


def _review(sessions):
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    root=Path(os.environ['NEXUS_PORTABLE_DATA'])
    for name in ('order_lifecycle.sqlite3','paper_portfolio.sqlite3','paper_forward.sqlite3','portal_settings.sqlite3'):
        _require((root/name).is_file(),'DATABASE_MISSING')
    g=DurableOrderGateway(root/'order_lifecycle.sqlite3');r=PaperForwardRunner(g)
    report=g.recovery_report();status=r.status()
    _require(not status['enabled'] and not status['ownership_held'],'UNEXPECTED_RESUME')
    _require(report['halted'] and report['portfolio_reconciled'],'HALT_OR_RECONCILIATION')
    _require(not report['execution_account']['account']['positions'],'POSITION_REMAINING')
    reviews=[]
    for session in sessions:
        evidence=r.evidence_report(session);orders=g.session_orders(session)
        check=evidence['execution_crosscheck'];quotes=evidence['quote_evidence']
        _require(evidence['local_consistency_ok'] and evidence['stored_state']=='PAUSED','EVIDENCE_DISAGREEMENT')
        _require(check['modeled_buy_orders']==check['modeled_sell_orders']==1 and len(orders)==2,'FILL_COUNTS')
        _require(quotes['source_modes']==['INJECTED_TRANSPORT_UNATTESTED'],'SOURCE_MODE')
        _require(quotes['rejected_observations']==3 and quotes['repeated_eligible_exchange_timestamps']==1,'OBSERVATION_COUNTS')
        reviews.append(dict(session=session,orders_sha256=fingerprint(orders),
                            evidence_sha256=fingerprint(evidence),quote_observations=quotes['observations'],
                            modeled_buy_orders=1,modeled_sell_orders=1))
    from nexus.forward_trial_review import review as coverage_review
    from datetime import datetime,timezone
    with r._read_db() as db:
        start,end=db.execute('SELECT MIN(started_at),MAX(updated_at) FROM sessions').fetchone()
    stamp=lambda value:datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    coverage=coverage_review(root/'paper_forward.sqlite3',after=stamp(start),until=stamp(end))
    return dict(sessions=reviews,commands=report['commands'],account_sha256=fingerprint(report['execution_account']),coverage_review=coverage,
                forward_stopped=True,ownership_released=True,halted=True,flat=True,portfolio_reconciled=True)


def _exercise(rounds):
    from datetime import datetime,timezone
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    root=Path(os.environ['NEXUS_PORTABLE_DATA']);g=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    now=[1000];price=[100];outage=[False];sessions=[];latencies=[]
    def fetch(product):
        if outage[0]:raise TimeoutError('DISPOSABLE_INJECTED_OUTAGE')
        return dict(product=product,provider='coinbase_exchange_public',read_only=True,orders_allowed=False,
                    bid=price[0],ask=price[0]+.01,last=price[0],received_at=now[0],
                    exchange_time=datetime.fromtimestamp(now[0],timezone.utc).isoformat())
    for _ in range(rounds):
        configure('terrestrial',True)
        r=PaperForwardRunner(g,fetch,lambda:now[0]);r.prepare('terrestrial');sessions.append(r.status()['session'])
        def cycle(advance=5,quote_price=None):
            now[0]+=advance
            if quote_price is not None:price[0]=quote_price
            before=time.perf_counter();result=r.single_cycle();latencies.append((time.perf_counter()-before)*1000)
            return result
        try:
            for i in range(7):result=cycle(quote_price=100+i*.1)
            _require(result['decision']=='BUY','ENTRY')
            _require(cycle(0)['reason']=='DUPLICATE_EXCHANGE_TICK','DUPLICATE')
            _require(cycle()['reason']=='POSITION_WITHIN_EXPERIMENTAL_EXIT_BANDS','HOLD')
            configure('terrestrial',False)
            _require(cycle(quote_price=102)['decision']=='SELL','TIER_OFF_EXIT')
            # Wait for ranking without losing the six-observation warmup.
            result=cycle(quote_price=102.1)
            _require(result['reason']=='RISK_TIER_DISENGAGED','TIER_OFF_REENTRY')
            outage[0]=True
            for _ in range(3):result=cycle()
            _require(result['decision']=='PAUSED','OUTAGE_PAUSE')
            _require(not r.status()['ownership_held'],'OWNERSHIP_REMAINING')
        finally:
            r.stop('DISPOSABLE_FORWARD_DRILL');outage[0]=False
        # Reconstruct between rounds, retaining every prior session/order/quote.
        g=DurableOrderGateway(root/'order_lifecycle.sqlite3')
        _require(not PaperForwardRunner(g,fetch,lambda:now[0]).status()['enabled'],'RESTART_RESUMED')
    g.emergency_halt()
    from nexus.durable_benchmark import percentiles
    return dict(recovered=_review(sessions),cycle_latency=percentiles(latencies),cycles=len(latencies))


def _child(directory,rounds,sessions=None):
    action=f'_exercise({rounds!r})' if sessions is None else f'_review({sessions!r})'
    expression='from nexus.forward_drill import _exercise,_review; import json; print(json.dumps('+action+'))'
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(directory)}
    child=subprocess.run([sys.executable,'-c',expression],env=env,capture_output=True,text=True,timeout=180)
    if child.returncode:raise ValueError('FORWARD_DRILL_CHILD_FAILED: '+child.stderr[-2000:])
    return json.loads(child.stdout)


def run(rounds=3):
    if type(rounds) is not int or not 1<=rounds<=20:raise ValueError('INVALID_FORWARD_DRILL_ROUNDS')
    with tempfile.TemporaryDirectory(prefix='nexus-forward-drill-') as temporary:
        root=Path(temporary);baseline=_child(root,rounds)
        sessions=[item['session'] for item in baseline['recovered']['sessions']]
        recovered=_child(root,rounds,sessions)
        _require(baseline['recovered']==recovered,'FRESH_PROCESS_DISAGREEMENT')
    return dict(mode='DISPOSABLE_FORWARD_RUNNER_DRILL',passed=True,rounds=rounds,cycles=baseline['cycles'],
                cycle_latency=baseline['cycle_latency'],recovered=recovered,application_accounts_changed=False,
                isolated_temporary_storage=True,broker_connected=False,live_orders_enabled=False,release_approved=False,
                target_validated=False,
                limitations='Serial injected quotes with synthetic time; cycle timings include runner, evidence and durable linked ledger. No actual feed, worker scheduling, process-kill, streaming, broker or sustained-market throughput acceptance.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--rounds',type=int,default=3);args=parser.parse_args()
    try:print(json.dumps(run(args.rounds),sort_keys=True,indent=2))
    except (ValueError,OSError,subprocess.SubprocessError) as exc:parser.exit(1,f'Forward drill failed: {exc}\n')


if __name__=='__main__':main()
