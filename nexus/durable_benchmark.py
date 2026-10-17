"""Bounded temporary-database durability benchmark, never application accounts.

Measures commit-return latency of offline gateway commands with FULL sync.
Compares gateway-only and linked-ledger/tier-inclusive paths; no network or broker IO.
"""
import math
import tempfile
import threading
import time
from pathlib import Path
from nexus.durable_orders import DurableOrderGateway
from nexus import order_checkpoints
from nexus.state import isolated_settings
from nexus.tier_permissions import configure

_LOCK=threading.Lock()


def percentiles(samples):
    values=sorted(samples)
    return {label:round(values[max(0,math.ceil(len(values)*fraction)-1)],3)
            for label,fraction in (('p50_ms',.50),('p95_ms',.95),('p99_ms',.99))}


def measure(root,cycles,checkpoints,linked=False):
    with isolated_settings(root):
        return _measure(root,cycles,checkpoints,linked)


def _measure(root,cycles,checkpoints,linked):
    g=DurableOrderGateway(root/'orders.sqlite3',checkpoints_enabled=checkpoints)
    g.initialize_account(10000)
    if linked:
        configure('terrestrial',True)
        g.link_portfolio()
    samples=[]
    started=time.perf_counter()
    for i in range(cycles):
        now=1000+i*30
        for side in ('BUY','SELL'):
            oid=f'{side}_{i}'
            calls=(lambda:g.submit(oid,'BTC-USD',side,50,tier='terrestrial'),
                   lambda:g.set_execution_terms(oid,now,10),
                   lambda:g.simulate_quote_fill(oid,'fill',1,1,25,25.1,2,now+1,now+1),
                   lambda:g.cancel(oid))
            for call in calls:
                before=time.perf_counter();call();samples.append((time.perf_counter()-before)*1000)
    elapsed=time.perf_counter()-started
    # Force fresh-process-equivalent validation of persisted checkpoint state.
    order_checkpoints.clear_cache()
    before=time.perf_counter();cold=g.recovery_report();cold_ms=(time.perf_counter()-before)*1000
    before=time.perf_counter();warm=g.recovery_report();warm_ms=(time.perf_counter()-before)*1000
    valid=(not linked or (cold['portfolio_reconciled'] and warm['portfolio_reconciled'])) and cold['execution_account']==warm['execution_account'] and not warm['execution_account']['account']['positions'] and all(order['status']=='SIMULATED_CANCELLED' for order in warm['orders'])
    return dict(checkpoints_enabled=checkpoints,cycles=cycles,durable_commands=len(samples),
                commit_return_latency=percentiles(samples),elapsed_seconds=round(elapsed,6),
                commands_per_second=round(len(samples)/elapsed,3),
                cold_recovery_ms=round(cold_ms,3),warm_recovery_ms=round(warm_ms,3),
                recovery_diagnostics=warm['recovery_diagnostics'],
                accounting_consistent=valid,portfolio_linked=linked,tier_checks_included=linked,
                scope='LINKED_LEDGER_AND_TIER' if linked else 'GATEWAY_ONLY',broker_orders=0)


def benchmark(cycles=16):
    if type(cycles) is not int or not 1<=cycles<=30:raise ValueError('INVALID_BENCHMARK_CYCLES')
    if not _LOCK.acquire(blocking=False):raise ValueError('DURABLE_BENCHMARK_BUSY')
    try:
        with tempfile.TemporaryDirectory(prefix='nexus-durable-benchmark-') as directory:
            root=Path(directory)
            reports=[measure(root/'full-replay',cycles,False),measure(root/'checkpoint',cycles,True),
                     measure(root/'linked-full-replay',cycles,False,True),measure(root/'linked-checkpoint',cycles,True,True)]
        return dict(mode='OFFLINE_DURABLE_GATEWAY_BENCHMARK',simulation_only=True,live_orders_enabled=False,
                    isolated_temporary_storage=True,application_accounts_changed=False,
                    treatments=reports,target_market_moves_per_second=30,
                    target_validated=False,
                    limitations='Local commit-return timings. Linked treatments include ledger integrity, tier-entry reads and atomic reconciliation. Excludes streaming, strategy, event-queue delays, network, broker acknowledgments and actual fills. Cold validation remains full replay; all retained payloads are still hashed. No fixed speedup or market throughput is established.')
    finally:_LOCK.release()
