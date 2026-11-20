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

LIMITATIONS=('Serial injected quotes with synthetic time; cycle timings include runner, evidence and durable linked ledger. '
    'Fault treatments use the actual worker loop with accelerated local scheduling and abrupt child termination, but no '
    'actual feed, operating-system power loss, streaming, broker or sustained-market throughput acceptance.')
PUBLIC_KEYS=frozenset(('mode','passed','rounds','cycles','cycle_latency','recovered','application_accounts_changed',
    'commit_durability','fault_injection','scheduler_cadence','no_fill_crash',
    'isolated_temporary_storage','broker_connected','live_orders_enabled','release_approved','target_validated','limitations'))
RECOVERED_PUBLIC_KEYS=frozenset(('sessions','commands','account_sha256','coverage_review','forward_stopped',
    'ownership_released','halted','flat','portfolio_reconciled'))
SESSION_PUBLIC_KEYS=frozenset(('session','orders_sha256','evidence_sha256','quote_observations','modeled_buy_orders',
    'modeled_sell_orders','durable_event_records','event_head_sha256',
    'reconciliation_sha256','review_receipt_sha256','review_reason_codes_sha256',
    'quote_outcome_bijection','durable_order_outcomes_reconciled',
    'durable_no_fill_attempts_bound',
    'cut_evidence_stable','cut_binding_verified','order_snapshot_stable'))
LATENCY_PUBLIC_KEYS=frozenset(('p50_ms','p95_ms','p99_ms'))
FAULT_CASE_PUBLIC_KEYS=frozenset(('cycles','fetches','reasons','paused','orders_created','ownership_released',
    'worker_started','worker_paused_while_alive','cleanup_blocked_before_process_death','post_pause_fetches',
    'process_terminated','cold_reopen_disabled','cold_worker_alive','cold_fetches_before_prepare',
    'cold_ownership_available','failed_session','stored_state','counts','quote_observations',
    'rejected_observations','review_reasons','failed_orders_sha256','failed_evidence_sha256',
    'reopened_orders_sha256','reopened_evidence_sha256','order_hash_preserved',
    'evidence_hash_preserved','new_session_distinct','healthy_decision','healthy_reason',
    'healthy_orders_created','automatic_restart'))
FAULT_PUBLIC_KEYS=frozenset(('mode','passed','source_loss','stale_quote','clock_regression','invalid_clock',
    'process_control_cleanup','orders_created','sessions_paused','ownership_released','actual_provider_requests','automatic_retries',
    'broker_connected','live_orders_enabled','release_approved','limitations'))
PROCESS_CONTROL_CASE_PUBLIC_KEYS=frozenset(('signal','worker_started','signal_observed','worker_stopped',
    'runner_disabled','ownership_released','orders_created','stored_state','effective_state',
    'automatic_restart'))
PROCESS_CONTROL_PUBLIC_KEYS=frozenset(('keyboard_interrupt','system_exit'))
SCHEDULER_CADENCE_CASE_PUBLIC_KEYS=frozenset(('cycle_starts','start_intervals','fetches','cycles',
    'scheduler_reasons','overrun_records','cycle_duration_seconds','overrun_seconds','orders_created',
    'stored_state','ownership_released','worker_stopped','burst_catchup_allowed'))
SCHEDULER_CADENCE_PUBLIC_KEYS=frozenset(('mode','passed','interval_seconds','nominal','overrun',
    'invalid_clock','clock_regression','stalled_clock','actual_provider_requests','automatic_retries',
    'broker_connected','live_orders_enabled','sustained_forward_accepted','release_approved','limitations'))
NO_FILL_CRASH_PUBLIC_KEYS=frozenset(('mode','passed','process_terminated','exit_code',
    'no_fill_attempts','attempt_digest_stable','position_preserved','fill_sequence_preserved',
    'mandate_resumable','old_session_review_required','quote_outcome_gap',
    'no_fill_outcome_gap','no_fill_binding_gap','automatic_repair','actual_provider_requests',
    'broker_connected','live_orders_enabled','release_approved','limitations'))
FAULT_LIMITATIONS=('Injected local failures, accelerated worker scheduling and abrupt local child termination only; '
    'proves bounded fail-closed worker/cold-reopen behavior, not actual provider availability, operating-system power '
    'loss, clock hardware, network recovery, broker operation or sustained-market acceptance.')
SCHEDULER_CADENCE_LIMITATIONS=('Injected monotonic scheduling and public-quote-shaped local observations only; proves bounded worker '
    'deadline, overrun and fail-closed behavior, not operating-system timer accuracy, actual provider cadence, sustained-market '
    'throughput, broker operation or release acceptance.')
NO_FILL_CRASH_LIMITATIONS=('Abrupt local child termination at one injected boundary after the durable no-fill commit and before its '
    'forward VETO only; proves local replay and review-required behavior, not operating-system power loss, storage hardware durability, '
    'actual provider or broker behavior, market liquidity, exit completion or release acceptance.')
COVERAGE_REVIEW_REASONS=('COVERAGE_GAP_EXCEEDS_MAXIMUM','DECLARED_PUBLIC_COVERAGE_BELOW_95_PERCENT','EVENT_INCIDENTS_RETAINED_IN_WINDOW','EXECUTION_OWNERSHIP_NOT_CHECKED',
    'INJECTED_OBSERVATIONS_IN_WINDOW','NONSTOPPED_OR_CLEANUP_REVIEW_SESSIONS',
    'ORDER_ACCOUNT_AND_STRATEGY_ACCEPTANCE_NOT_REVIEWED','QUOTE_ERRORS_RETAINED_IN_WINDOW',
    'REJECTED_OBSERVATIONS_RETAINED_IN_WINDOW',
    'REQUESTED_WINDOW_SHORTER_THAN_72_HOURS','SOURCE_LABELS_AND_HASHES_UNATTESTED')


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
        events=evidence['durable_event_evidence']
        receipt=evidence['review_receipt']
        _require(evidence['local_consistency_ok'] and evidence['stored_state']=='PAUSED','EVIDENCE_DISAGREEMENT')
        _require(check['modeled_buy_orders']==check['modeled_sell_orders']==1 and len(orders)==2,'FILL_COUNTS')
        _require(events['integrity_ok'] and events['event_trace_complete'] and
            events['quote_outcome_bijection'] and events['durable_order_outcomes_reconciled'] and
            events['durable_no_fill_attempts_bound'] and
            events['quote_outcomes']==quotes['observations'],'EVENT_RECONCILIATION')
        _require(receipt['local_consistency_ok'] and receipt['reason_count']==0 and
            receipt['cut_evidence_stable'] and not receipt['independent_cut_binding_verified'] and
            receipt['order_snapshot_stable_during_review'],'REVIEW_RECEIPT')
        _require(quotes['source_modes']==['INJECTED_TRANSPORT_UNATTESTED'],'SOURCE_MODE')
        _require(quotes['rejected_observations']==3 and quotes['repeated_eligible_exchange_timestamps']==1,'OBSERVATION_COUNTS')
        reviews.append(dict(session=session,orders_sha256=fingerprint(orders),
                            evidence_sha256=fingerprint(evidence),quote_observations=quotes['observations'],
                            modeled_buy_orders=1,modeled_sell_orders=1,
                            durable_event_records=events['events'],
                            event_head_sha256=events['head_sha256'],
                            reconciliation_sha256=events['reconciliation_sha256'],
                            review_receipt_sha256=receipt['receipt_sha256'],
                            review_reason_codes_sha256=receipt['reason_codes_sha256'],
                            quote_outcome_bijection=True,durable_order_outcomes_reconciled=True,
                            durable_no_fill_attempts_bound=events['durable_no_fill_attempts_bound'],
                            cut_evidence_stable=True,cut_binding_verified=False,
                            order_snapshot_stable=True))
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


def _write_fault_signal(path,payload):
    """Publish one private temporary parent/child fixture handshake durably."""
    path=Path(path);temporary=path.with_suffix('.tmp')
    with open(temporary,'w',encoding='utf-8') as stream:
        json.dump(payload,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def _fault_worker_process(directory,name,signal_path):
    """Run one real worker and block ordinary cleanup after its durable pause."""
    from datetime import datetime,timezone
    from nexus.durable_orders import DurableOrderGateway
    from nexus import paper_forward
    from nexus.tier_permissions import configure
    root=Path(directory);os.environ['NEXUS_PORTABLE_DATA']=str(root)
    configure('terrestrial',True);paper_forward.INTERVAL=.02
    samples=[0];fetches=[0]
    def clock():
        sample=samples[0];samples[0]+=1
        if sample>=3 and name=='clock_regression':return 999.0
        if sample==3 and name=='invalid_clock':return float('nan')
        return 1000.0
    def fetch(product):
        fetches[0]+=1
        if name=='source_loss':return None
        received=980.0 if name=='stale_quote' else 1000.0
        return dict(product=product,provider='coinbase_exchange_public',read_only=True,
            orders_allowed=False,bid=100.0,ask=100.01,last=100.0,received_at=received,
            exchange_time=datetime.fromtimestamp(1000,timezone.utc).isoformat())
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    schedule=[0.0]
    def scheduler_wait(seconds):schedule[0]+=seconds
    runner=paper_forward.PaperForwardRunner(gateway,fetch,clock,
        scheduler_clock=lambda:schedule[0],scheduler_wait=scheduler_wait)
    threading=__import__('threading');paused=threading.Event();release=threading.Event()
    ordinary_release=runner._release_stopped_ownership
    def blocked_release():
        if runner._stop.is_set() and runner._session:
            paused.set();release.wait()
        ordinary_release()
    runner._release_stopped_ownership=blocked_release
    started=runner.start('terrestrial')
    if not paused.wait(10):raise ValueError('FORWARD_DRILL_WORKER_PAUSE_TIMEOUT')
    status=runner.status();at_pause=fetches[0]
    time.sleep(.08)
    _write_fault_signal(signal_path,dict(session=status['session'],cycles=status['cycles'],fetches=at_pause,
        post_pause_fetches=fetches[0]-at_pause,worker_started=started['worker_alive'],
        worker_paused_while_alive=status['worker_alive'] and not status['enabled'],
        cleanup_blocked_before_process_death=status['ownership_held']))
    while True:time.sleep(1)


def _fault_reasons(runner,session):
    with runner._read_db() as db:
        details=[] if db is None else db.execute('SELECT detail FROM cycles ORDER BY id').fetchall()
    return [item['reason'] for detail, in details for item in (json.loads(detail),)
            if item['session']==session and item['decision']!='START']


def _session_evidence_hash(report):
    """Hash session evidence without the database-wide observation count."""
    scoped=json.loads(json.dumps(report,sort_keys=True))
    scoped['quote_evidence'].pop('storage_total',None)
    return fingerprint(scoped)


def _run_fault_worker(root,name):
    signal=root/'worker-paused.json'
    expression=('import sys; from nexus.forward_drill import _fault_worker_process; '
        '_fault_worker_process(sys.argv[1],sys.argv[2],sys.argv[3])')
    child=subprocess.Popen([sys.executable,'-c',expression,str(root),name,str(signal)],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+20
        while not signal.is_file():
            if child.poll() is not None:
                raise ValueError('FORWARD_DRILL_FAULT_WORKER_FAILED: '+(child.stderr.read() if child.stderr else '')[-2000:])
            if time.monotonic()>=deadline:raise ValueError('FORWARD_DRILL_FAULT_WORKER_TIMEOUT')
            time.sleep(.01)
        observed=json.loads(signal.read_text())
        child.kill();child.wait(timeout=10)
        observed['process_terminated']=child.returncode is not None
        return observed
    finally:
        if child.poll() is None:
            child.kill();child.wait(timeout=10)
        if child.stderr is not None:child.stderr.close()


def _process_control_cleanup(root,name):
    """Prove a real worker exposes process control and releases ownership."""
    from nexus.durable_orders import DurableOrderGateway
    from nexus.forward_ownership import ForwardOwnership
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    root=Path(root);root.mkdir();os.environ['NEXUS_PORTABLE_DATA']=str(root)
    configure('terrestrial',True)
    signal=KeyboardInterrupt if name=='keyboard_interrupt' else SystemExit
    threading=__import__('threading');observed=[];raised=threading.Event()
    entered=threading.Event();release=threading.Event()
    previous_hook=threading.excepthook
    def hook(arguments):
        observed.append(arguments.exc_type.__name__);raised.set()
    threading.excepthook=hook
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    def fail_fetch(_product):
        entered.set()
        if not release.wait(10):raise ValueError('PROCESS_CONTROL_RELEASE_TIMEOUT')
        raise signal('DISPOSABLE_PROCESS_CONTROL')
    runner=PaperForwardRunner(gateway,fail_fetch,lambda:1000.0)
    try:
        started=runner.start('terrestrial')
        if not entered.wait(10):raise ValueError('FORWARD_DRILL_PROCESS_CONTROL_ENTRY_TIMEOUT')
        release.set()
        if not raised.wait(10):raise ValueError('FORWARD_DRILL_PROCESS_CONTROL_TIMEOUT')
        runner._thread.join(timeout=10)
        if runner._thread.is_alive():raise ValueError('FORWARD_DRILL_PROCESS_CONTROL_WORKER_ALIVE')
        status=runner.status();session=runner.sessions()['sessions'][0]
        candidate=ForwardOwnership(root);candidate.acquire();candidate.release()
        return dict(signal=signal.__name__,worker_started=started['worker_alive'],
            signal_observed=observed==[signal.__name__],worker_stopped=not status['worker_alive'],
            runner_disabled=not status['enabled'],ownership_released=not status['ownership_held'],
            orders_created=len(gateway.recovery_report()['orders']),stored_state=session['stored_state'],
            effective_state=session['effective_state'],automatic_restart=status['automatic_restart'])
    finally:
        release.set()
        threading.excepthook=previous_hook
        if runner._thread and runner._thread.is_alive():
            runner._stop.set();runner._thread.join(timeout=10)
        runner._release_stopped_ownership()


def _faults(directory):
    """Exercise actual-worker failure, process-death and cold-reopen fences."""
    from datetime import datetime,timezone
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    parent=Path(directory);parent.mkdir(parents=True,exist_ok=True)
    previous=os.environ.get('NEXUS_PORTABLE_DATA');reports={};total_orders=0
    try:
        for name in ('source_loss','stale_quote','clock_regression','invalid_clock'):
            root=parent/name;root.mkdir();observed=_run_fault_worker(root,name)
            os.environ['NEXUS_PORTABLE_DATA']=str(root);configure('terrestrial',True)
            fetches=[0]
            def healthy_fetch(product):
                fetches[0]+=1
                return dict(product=product,provider='coinbase_exchange_public',read_only=True,
                    orders_allowed=False,bid=100.0,ask=100.01,last=100.0,received_at=2000.0,
                    exchange_time=datetime.fromtimestamp(2000,timezone.utc).isoformat())
            gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
            reopened=PaperForwardRunner(gateway,healthy_fetch,lambda:2000.0)
            cold=reopened.status();session=observed['session']
            failed_orders=gateway.session_orders(session);failed_evidence=reopened.evidence_report(session)
            failed_orders_hash=fingerprint(failed_orders);failed_evidence_hash=_session_evidence_hash(failed_evidence)
            cold_fetches=fetches[0];prepared=reopened.prepare('terrestrial')
            healthy=reopened.single_cycle();reopened.stop('DISPOSABLE_FAULT_RECOVERY_CHECK')
            final_orders_hash=fingerprint(gateway.session_orders(session))
            final_evidence_hash=_session_evidence_hash(reopened.evidence_report(session))
            orders=len(failed_orders);total_orders+=orders
            reports[name]=dict(cycles=observed['cycles'],fetches=observed['fetches'],
                reasons=_fault_reasons(reopened,session),paused=failed_evidence['stored_state']=='PAUSED',
                orders_created=orders,ownership_released=prepared['ownership_held'],
                worker_started=observed['worker_started'],
                worker_paused_while_alive=observed['worker_paused_while_alive'],
                cleanup_blocked_before_process_death=observed['cleanup_blocked_before_process_death'],
                post_pause_fetches=observed['post_pause_fetches'],process_terminated=observed['process_terminated'],
                cold_reopen_disabled=not cold['enabled'],cold_worker_alive=cold['worker_alive'],
                cold_fetches_before_prepare=cold_fetches,cold_ownership_available=prepared['ownership_held'],
                failed_session=session,stored_state=failed_evidence['stored_state'],
                counts=failed_evidence['counters'],quote_observations=failed_evidence['quote_evidence']['observations'],
                rejected_observations=failed_evidence['quote_evidence']['rejected_observations'],
                review_reasons=failed_evidence['reasons'],failed_orders_sha256=failed_orders_hash,
                failed_evidence_sha256=failed_evidence_hash,reopened_orders_sha256=final_orders_hash,
                reopened_evidence_sha256=final_evidence_hash,
                order_hash_preserved=failed_orders_hash==final_orders_hash,
                evidence_hash_preserved=failed_evidence_hash==final_evidence_hash,
                new_session_distinct=prepared['session']!=session,healthy_decision=healthy['decision'],
                healthy_reason=healthy['reason'],
                healthy_orders_created=len(gateway.session_orders(prepared['session'])),
                automatic_restart=cold['automatic_restart'])
        process_control={name:_process_control_cleanup(parent/('process-control-'+name),name)
            for name in ('keyboard_interrupt','system_exit')}
    finally:
        if previous is None:os.environ.pop('NEXUS_PORTABLE_DATA',None)
        else:os.environ['NEXUS_PORTABLE_DATA']=previous
    return dict(mode='DISPOSABLE_FORWARD_FAULT_INJECTION',passed=True,
        source_loss=reports['source_loss'],stale_quote=reports['stale_quote'],
        clock_regression=reports['clock_regression'],invalid_clock=reports['invalid_clock'],
        process_control_cleanup=process_control,
        orders_created=total_orders,sessions_paused=sum(item['paused'] for item in reports.values()),
        ownership_released=all(item['ownership_released'] for item in reports.values()),
        actual_provider_requests=False,
        automatic_retries=any(item['post_pause_fetches']>0 for item in reports.values()),broker_connected=False,
        live_orders_enabled=False,release_approved=False,limitations=FAULT_LIMITATIONS)


def _scheduler_cadence_case(root,name):
    """Run one actual worker against an injected monotonic scheduler."""
    from datetime import datetime,timezone
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    root=Path(root);root.mkdir();os.environ['NEXUS_PORTABLE_DATA']=str(root)
    configure('terrestrial',True)
    scheduler={'now':100.0,'waits':[]};starts=[];holder={}
    target=2 if name=='overrun' else 3

    def scheduler_clock():
        if name=='invalid_clock':return float('nan')
        return scheduler['now']

    def scheduler_wait(seconds):
        scheduler['waits'].append(seconds)
        if name=='clock_regression':scheduler['now']-=1
        elif name!='stalled_clock':scheduler['now']+=seconds
        if name in ('nominal','overrun') and len(scheduler['waits'])==target:
            holder['runner']._pause_scheduler('DISPOSABLE_CADENCE_COMPLETE')

    def fetch(product):
        starts.append(scheduler['now'])
        if name=='overrun' and len(starts)==1:scheduler['now']+=7
        return dict(product=product,provider='coinbase_exchange_public',read_only=True,
            orders_allowed=False,bid=100.0,ask=100.01,last=100.0,received_at=1000.0,
            exchange_time=datetime.fromtimestamp(1000,timezone.utc).isoformat())

    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    runner=PaperForwardRunner(gateway,fetch,lambda:1000.0,
        scheduler_clock=scheduler_clock,scheduler_wait=scheduler_wait)
    holder['runner']=runner
    runner.start('terrestrial');runner._thread.join(10)
    _require(not runner._thread.is_alive(),'SCHEDULER_WORKER_ALIVE')
    history=list(reversed(runner.history()))
    cadence_reason_codes=frozenset(('INVALID_SCHEDULER_CLOCK','SCHEDULER_CLOCK_REGRESSION',
        'SCHEDULER_CLOCK_STALLED','SCHEDULER_OVERRUN'))
    cadence_reasons=[row['reason'] for row in history if row['reason'] in cadence_reason_codes]
    overruns=[row for row in history if row['reason']=='SCHEDULER_OVERRUN']
    session=runner.sessions()['sessions'][0]
    return dict(cycle_starts=starts,start_intervals=[starts[index]-starts[index-1]
        for index in range(1,len(starts))],fetches=len(starts),cycles=runner.status()['cycles'],
        scheduler_reasons=cadence_reasons,overrun_records=len(overruns),
        cycle_duration_seconds=overruns[0]['scheduler_cycle_duration_seconds'] if overruns else 0.0,
        overrun_seconds=overruns[0]['scheduler_overrun_seconds'] if overruns else 0.0,
        orders_created=len(gateway.recovery_report()['orders']),stored_state=session['stored_state'],
        ownership_released=not runner.status()['ownership_held'],worker_stopped=not runner.status()['worker_alive'],
        burst_catchup_allowed=any(row.get('burst_catchup_allowed') is True for row in history))


def _scheduler_cadence(directory):
    previous=os.environ.get('NEXUS_PORTABLE_DATA');parent=Path(directory);parent.mkdir(parents=True,exist_ok=True)
    try:
        cases={name:_scheduler_cadence_case(parent/name,name) for name in
            ('nominal','overrun','invalid_clock','clock_regression','stalled_clock')}
    finally:
        if previous is None:os.environ.pop('NEXUS_PORTABLE_DATA',None)
        else:os.environ['NEXUS_PORTABLE_DATA']=previous
    return dict(mode='DISPOSABLE_FORWARD_SCHEDULER_CADENCE',passed=True,interval_seconds=5,
        **cases,actual_provider_requests=False,automatic_retries=False,broker_connected=False,
        live_orders_enabled=False,sustained_forward_accepted=False,release_approved=False,
        limitations=SCHEDULER_CADENCE_LIMITATIONS)


def _no_fill_crash_worker(directory):
    """Terminate after durable expiry and before its independent forward VETO."""
    from datetime import datetime,timezone
    from decimal import Decimal
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    os.environ['NEXUS_PORTABLE_DATA']=str(root);configure('terrestrial',True)
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    gateway.initialize_account(10000);gateway.link_portfolio()
    gateway.submit('entry','BTC-USD','BUY',100,tier='terrestrial')
    gateway.simulate_priced_fill('entry','fill',1,1,100)
    clock={'now':1000,'price':250}
    def quote(product):
        return dict(product=product,provider='coinbase_exchange_public',read_only=True,
            orders_allowed=False,bid=clock['price'],ask=clock['price']+.01,
            last=clock['price'],received_at=clock['now'],
            exchange_time=datetime.fromtimestamp(clock['now'],timezone.utc).isoformat())
    first=PaperForwardRunner(gateway,quote,lambda:clock['now']);first.prepare('terrestrial')
    configure('terrestrial',False)
    _require(first.single_cycle()['decision']=='SELL','NO_FILL_CRASH_INITIAL_SLICE')
    first.stop('DISPOSABLE_NO_FILL_CRASH_SETUP')
    cold=DurableOrderGateway(gateway.path)
    resumed=PaperForwardRunner(cold,quote,lambda:clock['now'])
    resumed.prepare('terrestrial');clock.update(now=1005,price=251)
    original_restore=cold._restore;delayed=[False]
    def restore(db):
        state=original_restore(db);restored=state[0]
        pending=next((order for order in restored.intents.values()
            if order.get('intent_scope')=='POSITION_REDUCING_EXIT' and
            order['status']=='SIMULATED_REDUCTION_PENDING'),None)
        if (pending is not None and
                Decimal(restored.order_terms[pending['id']]['created_at'])==
                    Decimal(str(clock['now'])) and
                pending.get('reduction_slice_count')==1 and not delayed[0]):
            clock['now']+=2;delayed[0]=True
        return state
    cold._restore=restore
    original_record=resumed._record
    def record(decision,reason,**extra):
        if decision=='VETO' and reason=='ORDER_EXPIRED_WITHOUT_FILL':os._exit(76)
        return original_record(decision,reason,**extra)
    resumed._record=record
    resumed.single_cycle()
    raise ValueError('FORWARD_DRILL_NO_FILL_CRASH_HOOK_NOT_REACHED')


def _no_fill_crash(directory):
    """Review the exact post-death state without repairing or resuming it."""
    from decimal import Decimal
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.order_accounting import no_fill_attempt_sha256
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    expression=('import sys; from nexus.forward_drill import _no_fill_crash_worker; '
        '_no_fill_crash_worker(sys.argv[1])')
    child=subprocess.run([sys.executable,'-c',expression,str(root)],
        env={**os.environ,'NEXUS_PORTABLE_DATA':str(root)},
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=30)
    _require(child.returncode==76,'NO_FILL_CRASH_CHILD')
    previous=os.environ.get('NEXUS_PORTABLE_DATA')
    try:
        os.environ['NEXUS_PORTABLE_DATA']=str(root)
        gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
        recovery=gateway.recovery_report()
        mandate=next((order for order in recovery['orders']
            if order.get('intent_scope')=='POSITION_REDUCING_EXIT'),None)
        _require(mandate is not None,'NO_FILL_CRASH_MANDATE')
        report=gateway.order_report(mandate['id']);attempts=report['no_fill_events']
        runner=PaperForwardRunner(gateway);status=runner.status()
        interrupted=next((session for session in runner.sessions()['sessions']
            if session['effective_state']=='INTERRUPTED_OR_PRIOR_SESSION'),None)
        _require(interrupted is not None,'NO_FILL_CRASH_SESSION')
        evidence=runner.evidence_report(interrupted['session'])
        events=evidence['durable_event_evidence']
        replayed=DurableOrderGateway(root/'order_lifecycle.sqlite3').order_report(mandate['id'])
        stable=(len(attempts)==len(replayed['no_fill_events'])==1 and
            no_fill_attempt_sha256(attempts[0])==
            no_fill_attempt_sha256(replayed['no_fill_events'][0]))
        position=(recovery['execution_account']['account']['positions'] or {}).get('BTC-USD')
        return dict(mode='DISPOSABLE_DURABLE_NO_FILL_CRASH',passed=True,
            process_terminated=child.returncode==76,exit_code=child.returncode,
            no_fill_attempts=len(attempts),attempt_digest_stable=stable,
            position_preserved=(type(position) is dict and
                Decimal(position.get('quantity','0'))>0 and
                Decimal(position.get('quantity','0'))+
                    Decimal(mandate.get('reduction_filled_quantity','0'))==
                    Decimal(mandate.get('expected_position_quantity','-1'))),
            fill_sequence_preserved=(mandate.get('reduction_slice_count')==1 and
                len(report['quote_events'])==1),
            mandate_resumable=(mandate.get('status')=='SIMULATED_REDUCTION_PENDING'),
            old_session_review_required=(interrupted['requires_review'] is True and
                evidence['local_consistency_ok'] is False),
            quote_outcome_gap=events['quote_outcome_bijection'] is False,
            no_fill_outcome_gap=events['durable_no_fill_outcomes_reconciled'] is False,
            no_fill_binding_gap=events['durable_no_fill_attempts_bound'] is False,
            automatic_repair=False,actual_provider_requests=False,
            broker_connected=False,live_orders_enabled=False,release_approved=False,
            limitations=NO_FILL_CRASH_LIMITATIONS)
    finally:
        if previous is None:os.environ.pop('NEXUS_PORTABLE_DATA',None)
        else:os.environ['NEXUS_PORTABLE_DATA']=previous


def _child(directory,rounds,sessions=None):
    action=f'_exercise({rounds!r})' if sessions is None else f'_review({sessions!r})'
    expression='from nexus.forward_drill import _exercise,_review; import json; print(json.dumps('+action+'))'
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(directory)}
    child=subprocess.run([sys.executable,'-c',expression],env=env,capture_output=True,text=True,timeout=180)
    if child.returncode:raise ValueError('FORWARD_DRILL_CHILD_FAILED: '+child.stderr[-2000:])
    return json.loads(child.stdout)


def _fault_child(directory):
    expression='from nexus.forward_drill import _faults; import json; print(json.dumps(_faults('+repr(str(directory))+')))'
    child=subprocess.run([sys.executable,'-c',expression],capture_output=True,text=True,timeout=180)
    if child.returncode:raise ValueError('FORWARD_DRILL_FAULT_CHILD_FAILED: '+child.stderr[-2000:])
    return json.loads(child.stdout)


def run(rounds=3):
    if type(rounds) is not int or not 1<=rounds<=20:raise ValueError('INVALID_FORWARD_DRILL_ROUNDS')
    with tempfile.TemporaryDirectory(prefix='nexus-forward-drill-') as temporary:
        root=Path(temporary);baseline=_child(root,rounds)
        sessions=[item['session'] for item in baseline['recovered']['sessions']]
        recovered=_child(root,rounds,sessions)
        _require(baseline['recovered']==recovered,'FRESH_PROCESS_DISAGREEMENT')
        faults=_fault_child(root/'faults')
        cadence=_scheduler_cadence(root/'cadence')
        no_fill_crash=_no_fill_crash(root/'no-fill-crash')
        from nexus.paper_forward import commit_durability_fixture_report
        durability=commit_durability_fixture_report()
        _require(durability['passed'] is True,'COMMIT_DURABILITY')
    return dict(mode='DISPOSABLE_FORWARD_RUNNER_DRILL',passed=True,rounds=rounds,cycles=baseline['cycles'],
                cycle_latency=baseline['cycle_latency'],recovered=recovered,commit_durability=durability,
                fault_injection=faults,scheduler_cadence=cadence,no_fill_crash=no_fill_crash,
                application_accounts_changed=False,
                isolated_temporary_storage=True,broker_connected=False,live_orders_enabled=False,release_approved=False,
                target_validated=False,limitations=LIMITATIONS)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--rounds',type=int,default=3);args=parser.parse_args()
    try:print(json.dumps(run(args.rounds),sort_keys=True,indent=2))
    except (ValueError,OSError,subprocess.SubprocessError) as exc:parser.exit(1,f'Forward drill failed: {exc}\n')


if __name__=='__main__':main()
