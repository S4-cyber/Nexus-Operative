"""Fast, bounded, OFFLINE order-intent decision benchmark.

The simulator does not connect to market data, brokers, accounts or funds.
It measures local admitted/rejected event decisions, never executions or fills.
"""
from collections import deque
from decimal import Decimal,InvalidOperation
import math,time
from nexus.order_gateway import OrderGateway

TARGET_DECISIONS_PER_SECOND=30
MAX_QUEUE=256
MAX_AGE_MS=250
MAX_EVENTS=3000
MAX_NOTIONAL=Decimal("200")

def finite_clock(value):
    try:return type(value) in (int,float) and math.isfinite(value)
    except OverflowError:return False


class BoundedDecisionLane:
    def __init__(self,clock=time.monotonic,queue_capacity=MAX_QUEUE,event_capacity=MAX_EVENTS):
        if type(queue_capacity)!=int or not 1<=queue_capacity<=MAX_QUEUE:
            raise ValueError("INVALID_QUEUE_CAPACITY")
        if type(event_capacity) is not int or not 1<=event_capacity<=MAX_EVENTS:
            raise ValueError("INVALID_EVENT_RETENTION_CAPACITY")
        self.clock=clock
        self.capacity=queue_capacity
        self.event_capacity=event_capacity
        self.queue=deque()
        self.seen=set()
        self.halted=False
        self.metrics={"queued":0,"accepted":0,"rejected":0,"overflow":0,
                      "expired":0,"duplicates":0,"processed":0,"retention_full":0,"invalid_clock":0}

    def offer(self,sequence,created_at,product,side,notional):
        if self.halted:return "HALTED"
        if type(sequence)!=int or sequence<0:raise ValueError("INVALID_SEQUENCE")
        if sequence in self.seen:
            self.metrics["duplicates"]+=1
            return "DUPLICATE"
        if len(self.seen)>=self.event_capacity:
            self.metrics["retention_full"]+=1
            return "EVENT_RETENTION_FULL"
        if len(self.queue)>=self.capacity:
            self.metrics["overflow"]+=1
            return "QUEUE_FULL"
        if not finite_clock(created_at):
            raise ValueError("INVALID_TIMESTAMP")
        self.seen.add(sequence)
        self.queue.append((sequence,created_at,product,side,notional))
        self.metrics["queued"]+=1
        return "QUEUED"

    def decide_one(self):
        if self.halted:return "HALTED"
        if not self.queue:return "EMPTY"
        seq,created,product,side,notional=self.queue.popleft()
        self.metrics["processed"]+=1
        now=self.clock()
        if not finite_clock(now):
            self.metrics["invalid_clock"]+=1
            self.metrics["rejected"]+=1
            return "INVALID_CLOCK"
        delta=now-created
        if not math.isfinite(delta) or delta<0 or delta*1000>MAX_AGE_MS:
            self.metrics["expired"]+=1
            self.metrics["rejected"]+=1
            return "EXPIRED"
        if product not in ("BTC-USD","ETH-USD","SOL-USD") or side not in ("BUY","SELL"):
            self.metrics["rejected"]+=1
            return "NOT_ELIGIBLE"
        try:
            if isinstance(notional,bool):raise ValueError
            value=Decimal(str(notional))
            if not value.is_finite() or not 0<value<=MAX_NOTIONAL:raise ValueError
        except (ValueError,TypeError,InvalidOperation):
            self.metrics["rejected"]+=1
            return "NOT_ELIGIBLE"
        # Event acceptance is NOT permission to create an order or trade.
        self.metrics["accepted"]+=1
        return "RESEARCH_SIGNAL_ACCEPTED_NO_ORDER"

    def snapshot(self):
        return dict(read_only=True,queue_depth=len(self.queue),queue_capacity=self.capacity,
                    retained_event_ids=len(self.seen),event_retention_capacity=self.event_capacity,
                    metrics=dict(self.metrics),halted=self.halted,automatic_id_deletion=False,
                    orders_created=False,live_orders_enabled=False)

    def halt(self):
        self.halted=True
        self.metrics["rejected"]+=len(self.queue)
        self.queue.clear()

def benchmark(count=300,clock=time.perf_counter):
    if type(count)!=int or not 30<=count<=MAX_EVENTS:
        raise ValueError("INVALID_BENCHMARK_SIZE")
    lane=BoundedDecisionLane(clock=clock)
    latencies=[]
    wall_start=clock()
    outcomes={}
    for seq in range(count):
        # Bounded batches emulate a feed without creating an unbounded backlog.
        start=clock()
        lane.offer(seq,start,"BTC-USD","BUY",10)
        result=lane.decide_one()
        latencies.append(max(0,(clock()-start)*1000))
        outcomes[result]=outcomes.get(result,0)+1
    elapsed=max(clock()-wall_start,1e-9)
    series=sorted(latencies)
    def pct(p):return round(series[min(len(series)-1,math.ceil(p*len(series))-1)],4)
    return {"mode":"OFFLINE_DECISION_CAPACITY_BENCHMARK",
        "events":count,"target_decisions_per_second":TARGET_DECISIONS_PER_SECOND,
        "local_decisions_per_second":round(count/elapsed,1),
        "p50_ms":pct(.5),"p95_ms":pct(.95),"p99_ms":pct(.99),
        "measurement_duration_seconds":round(elapsed,5),
        "target_met_in_this_synthetic_run":count/elapsed>=TARGET_DECISIONS_PER_SECOND,
        "results":outcomes,"queue_capacity":lane.capacity,"retention":lane.snapshot(),
        "broker_orders_submitted":0,"broker_fills_confirmed":0,
        "live_orders_enabled":False,"paper_orders_created":False,
        "limitations":"Single-process synthetic CPU benchmark only. Does NOT measure event stream ingest, network, broker acknowledgments, actual fills, sustained load, or market profitability."}

