"""Disposable serial burst treatment; no application accounts or market IO."""
from collections import deque
import math
import time
from nexus.durable_orders import DurableOrderGateway
from nexus.execution_capacity import CONTROL_RESERVE,PUBLIC_KEYS as CAPACITY_KEYS
from nexus.state import isolated_settings
from nexus.tier_permissions import configure
from nexus import order_checkpoints

CAPACITY=8
MAX_CYCLES=30
MAX_QUEUE_AGE_SECONDS=.250
LIMITATIONS=('Serially drained bounded fixture burst. Includes real local queue residence, linked '
    'ledger/tier writes, duplicate replay; cold validation is checked separately after timing. Uses '
    'fixed synthetic quote time. Excludes feed, strategy, streaming, network and broker timing; '
    'the attested process-local window and percentiles are not market moves, throughput or broker latency.')
PUBLIC_KEYS=frozenset(('scope','atomic_order_transactions','offered','queue_capacity','admitted',
    'overflow_rejections','duplicate_rejections','completed_roundtrips','expired_before_execution',
    'halt_flushed','duplicate_fill_replays','queue_age_limit_ms','queue_wait_latency',
    'durable_roundtrip_latency','admitted_to_completion_latency','timing_attestation',
    'accounting_consistent','halted',
    'durable_commands','durable_capacity','capacity_bounds_verified','cold_recovery_ms',
    'application_accounts_changed','broker_orders',
    'target_validated','limitations'))
TIMING_ATTESTATION_KEYS=frozenset(('scope','clock_samples','window_elapsed_ms',
    'queue_wait_sample_count','durable_roundtrip_sample_count',
    'admitted_to_completion_sample_count','dequeued_outcomes','completed_outcomes',
    'expired_outcomes','flushed_outcomes','outcome_partition_verified',
    'finite_nonnegative_samples','monotonic_sequence_verified','emergency_halt_in_window',
    'market_or_broker_latency','throughput_claimed'))


def measure(root,cycles,clock=time.perf_counter,halt_after=None,atomic_orders=False):
    if type(atomic_orders) is not bool:raise ValueError('INVALID_ATOMIC_ORDER_OPTION')
    if type(cycles) is not int or not 1<=cycles<=MAX_CYCLES:raise ValueError('INVALID_BURST_CYCLES')
    if halt_after is not None and (type(halt_after) is not int or not 0<=halt_after<=cycles):raise ValueError('INVALID_BURST_HALT')
    from nexus.durable_benchmark import percentiles
    last_clock=None;clock_samples=0
    def sample():
        nonlocal last_clock,clock_samples
        value=clock()
        if type(value) not in (float,int) or not math.isfinite(value):raise ValueError('INVALID_BURST_CLOCK')
        if last_clock is not None and value<last_clock:raise ValueError('BURST_CLOCK_REGRESSION')
        last_clock=value;clock_samples+=1
        return value
    with isolated_settings(root):
        g=DurableOrderGateway(root/'orders.sqlite3')
        g.initialize_account(10000);configure('terrestrial',True);g.link_portfolio()
        pending=deque();seen=set();overflow=duplicates=0
        arrived=sample()
        def offer(seq):
            nonlocal overflow,duplicates
            if seq in seen:duplicates+=1;return
            if len(pending)>=CAPACITY:overflow+=1;return
            seen.add(seq);pending.append((seq,arrived))
        for seq in range(cycles):offer(seq)
        offer(0)  # Deliberate duplicate while the admitted ID is retained.
        waits=[];execution=[];end_to_end=[];completed=expired=flushed=replays=0
        while pending:
            if halt_after is not None and completed>=halt_after:
                g.emergency_halt();flushed=len(pending);pending.clear();break
            seq,offered=pending.popleft();began=sample();wait=began-offered
            waits.append(max(0,wait*1000))
            if wait>MAX_QUEUE_AGE_SECONDS:expired+=1;continue
            # Fixed synthetic market time isolates local queue/durability timing.
            # No real quote is refreshed or admitted by this fixture treatment.
            stamp=1000+seq*30
            for side in ('BUY','SELL'):
                oid=f'burst_{seq}_{side}'
                args=(oid,'fill',1,1,25,25.1,2,stamp+1,stamp+1)
                if atomic_orders:
                    g.execute_quote_order(oid,'BTC-USD',side,50,tier='terrestrial',created_at=stamp,ttl_seconds=10,
                                          quantity=1,bid=25,ask=25.1,available_quantity=2,quote_at=stamp+1,evaluated_at=stamp+1)
                else:
                    g.submit(oid,'BTC-USD',side,50,tier='terrestrial')
                    g.set_execution_terms(oid,stamp,10)
                    g.simulate_quote_fill(*args)
                if not g.simulate_quote_fill(*args)['idempotent_replay']:raise ValueError('BURST_DUPLICATE_NOT_IDEMPOTENT')
                replays+=1
                if not atomic_orders:g.cancel(oid)
            finished=sample()
            execution.append((finished-began)*1000);end_to_end.append((finished-offered)*1000);completed+=1
        g.emergency_halt();window_closed=sample();order_checkpoints.clear_cache()
        cold_started=time.perf_counter()
        restored=DurableOrderGateway(root/'orders.sqlite3').recovery_report()
        cold_ms=(time.perf_counter()-cold_started)*1000
        consistent=(restored['halted'] and restored['portfolio_reconciled'] and
                    not restored['execution_account']['account']['positions'] and
                    len(restored['orders'])==2*completed and restored['commands']==3+8*completed)
        capacity=restored['capacity']
        capacity_bounds=(type(capacity) is dict and frozenset(capacity)==CAPACITY_KEYS and
            capacity['command_count']==capacity['retained_commands']==restored['commands'] and
            capacity['intent_count']==2*completed and
            capacity['ordinary_commands_remaining']+capacity['command_count']==
                capacity['ordinary_command_limit'] and
            capacity['total_commands_remaining']+capacity['command_count']==
                capacity['command_limit'] and
            capacity['intents_remaining']+capacity['intent_count']==capacity['intent_limit'] and
            capacity['ordinary_intent_limit']==capacity['intent_limit']-1 and
            capacity['ordinary_intents_remaining']+capacity['intent_count']==
                capacity['ordinary_intent_limit'] and
            capacity['exit_intent_reserve_active'] is True and
            capacity['exit_intents_remaining']==1 and
            capacity['control_commands_reserved']==CONTROL_RESERVE and
            capacity['new_intents_allowed'] is capacity['lifecycle_intake_allowed'] is False and
            capacity['reasons']==['EMERGENCY_HALT_ACTIVE'] and
            capacity['history_deleted'] is capacity['automatic_archival_available'] is False)
        dequeued=completed+expired
        timing_attestation=dict(scope='PROCESS_LOCAL_BOUNDED_BURST_WINDOW',
            clock_samples=clock_samples,window_elapsed_ms=round((window_closed-arrived)*1000,3),
            queue_wait_sample_count=len(waits),durable_roundtrip_sample_count=len(execution),
            admitted_to_completion_sample_count=len(end_to_end),dequeued_outcomes=dequeued,
            completed_outcomes=completed,expired_outcomes=expired,flushed_outcomes=flushed,
            outcome_partition_verified=(len(seen)==dequeued+flushed and len(waits)==dequeued and
                len(execution)==len(end_to_end)==completed),
            finite_nonnegative_samples=all(math.isfinite(value) and value>=0
                for values in (waits,execution,end_to_end) for value in values),
            monotonic_sequence_verified=True,emergency_halt_in_window=True,
            market_or_broker_latency=False,throughput_claimed=False)
        return dict(scope='TEMPORARY_LINKED_LEDGER_FIXTURE_BURST',atomic_order_transactions=atomic_orders,offered=cycles,queue_capacity=CAPACITY,
                    admitted=len(seen),overflow_rejections=overflow,duplicate_rejections=duplicates,
                    completed_roundtrips=completed,expired_before_execution=expired,halt_flushed=flushed,
                    duplicate_fill_replays=replays,queue_age_limit_ms=MAX_QUEUE_AGE_SECONDS*1000,
                    queue_wait_latency=percentiles(waits) if waits else None,
                    durable_roundtrip_latency=percentiles(execution) if execution else None,
                    admitted_to_completion_latency=percentiles(end_to_end) if end_to_end else None,
                    timing_attestation=timing_attestation,
                    accounting_consistent=consistent,halted=restored['halted'],durable_commands=restored['commands'],
                    durable_capacity=capacity,capacity_bounds_verified=capacity_bounds,
                    cold_recovery_ms=round(cold_ms,3),
                    application_accounts_changed=False,broker_orders=0,target_validated=False,
                    limitations=LIMITATIONS)
