"""Disposable serial burst treatment; no application accounts or market IO."""
from collections import deque
import math
import time
from nexus.durable_orders import DurableOrderGateway
from nexus.state import isolated_settings
from nexus.tier_permissions import configure
from nexus import order_checkpoints

CAPACITY=8
MAX_CYCLES=30
MAX_QUEUE_AGE_SECONDS=.250


def measure(root,cycles,clock=time.perf_counter,halt_after=None):
    if type(cycles) is not int or not 1<=cycles<=MAX_CYCLES:raise ValueError('INVALID_BURST_CYCLES')
    if halt_after is not None and (type(halt_after) is not int or not 0<=halt_after<=cycles):raise ValueError('INVALID_BURST_HALT')
    from nexus.durable_benchmark import percentiles
    def sample():
        value=clock()
        if type(value) not in (float,int) or not math.isfinite(value):raise ValueError('INVALID_BURST_CLOCK')
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
            if wait<0 or wait>MAX_QUEUE_AGE_SECONDS:expired+=1;continue
            # Fixed synthetic market time isolates local queue/durability timing.
            # No real quote is refreshed or admitted by this fixture treatment.
            stamp=1000+seq*30
            for side in ('BUY','SELL'):
                oid=f'burst_{seq}_{side}'
                g.submit(oid,'BTC-USD',side,50,tier='terrestrial')
                g.set_execution_terms(oid,stamp,10)
                args=(oid,'fill',1,1,25,25.1,2,stamp+1,stamp+1)
                g.simulate_quote_fill(*args)
                if not g.simulate_quote_fill(*args)['idempotent_replay']:raise ValueError('BURST_DUPLICATE_NOT_IDEMPOTENT')
                replays+=1;g.cancel(oid)
            finished=sample()
            if finished<began:raise ValueError('BURST_CLOCK_REGRESSION')
            execution.append((finished-began)*1000);end_to_end.append((finished-offered)*1000);completed+=1
        g.emergency_halt();order_checkpoints.clear_cache()
        cold_started=time.perf_counter()
        restored=DurableOrderGateway(root/'orders.sqlite3').recovery_report()
        cold_ms=(time.perf_counter()-cold_started)*1000
        consistent=(restored['halted'] and restored['portfolio_reconciled'] and
                    not restored['execution_account']['account']['positions'] and
                    len(restored['orders'])==2*completed and restored['commands']==3+8*completed)
        return dict(scope='TEMPORARY_LINKED_LEDGER_FIXTURE_BURST',offered=cycles,queue_capacity=CAPACITY,
                    admitted=len(seen),overflow_rejections=overflow,duplicate_rejections=duplicates,
                    completed_roundtrips=completed,expired_before_execution=expired,halt_flushed=flushed,
                    duplicate_fill_replays=replays,queue_age_limit_ms=MAX_QUEUE_AGE_SECONDS*1000,
                    queue_wait_latency=percentiles(waits) if waits else None,
                    durable_roundtrip_latency=percentiles(execution) if execution else None,
                    admitted_to_completion_latency=percentiles(end_to_end) if end_to_end else None,
                    accounting_consistent=consistent,halted=restored['halted'],durable_commands=restored['commands'],cold_recovery_ms=round(cold_ms,3),
                    application_accounts_changed=False,broker_orders=0,target_validated=False,
                    limitations='Serially drained bounded fixture burst. Includes real local queue residence, linked ledger/tier writes, duplicate replay; cold validation is checked separately after timing. Uses fixed synthetic quote time. Excludes feed, strategy, streaming, network and broker timing; roundtrips are not market moves.')
