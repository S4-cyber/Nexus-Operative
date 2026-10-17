import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.decision_lane import BoundedDecisionLane,benchmark

def test_bounded_queue_drops_overflow_and_stale_events():
    now=[100.0]
    lane=BoundedDecisionLane(clock=lambda:now[0],queue_capacity=2)
    assert lane.offer(1,100.0,"BTC-USD","BUY",10)=="QUEUED"
    assert lane.offer(1,100.0,"BTC-USD","BUY",10)=="DUPLICATE"
    assert lane.offer(2,100.0,"ETH-USD","SELL",20)=="QUEUED"
    assert lane.offer(3,100.0,"SOL-USD","BUY",10)=="QUEUE_FULL"
    assert lane.decide_one()=="RESEARCH_SIGNAL_ACCEPTED_NO_ORDER"
    now[0]=100.3
    assert lane.decide_one()=="EXPIRED"
    assert lane.metrics["overflow"]==1 and lane.metrics["duplicates"]==1

def test_halt_flushes_queue_without_replaying_orders():
    lane=BoundedDecisionLane(clock=lambda:10)
    lane.offer(1,10,"BTC-USD","BUY",10)
    lane.halt()
    assert lane.decide_one()=="HALTED"
    assert lane.offer(2,10,"BTC-USD","BUY",10)=="HALTED"
    assert not lane.queue

def test_invalid_signals_do_not_approve_trade():
    lane=BoundedDecisionLane(clock=lambda:10)
    for index,p,side,amount in ((1,"FAKE-USD","BUY",10),(2,"BTC-USD","HACK",10),
                                 (3,"BTC-USD","BUY",float("nan"))):
        lane.offer(index,10,p,side,amount)
        assert lane.decide_one()=="NOT_ELIGIBLE"
    assert lane.metrics["accepted"]==0

def test_benchmark_has_separate_local_speed_and_zero_trades():
    report=benchmark(300)
    assert report["target_decisions_per_second"]==30
    assert report["p99_ms"]>=report["p95_ms"]>=report["p50_ms"]>=0
    assert report["broker_orders_submitted"]==report["broker_fills_confirmed"]==0
    assert report["paper_orders_created"] is False
    assert report["live_orders_enabled"] is False
    with pytest.raises(ValueError):benchmark(100000)

def test_benchmark_api_is_offline_and_visible():
    client=TestClient(app)
    result=client.get("/api/execution/decision-benchmark?count=300")
    assert result.status_code==200
    assert result.json()["broker_orders_submitted"]==0
    assert client.get("/api/execution/decision-benchmark?count=20000").status_code==422
    html=client.get("/portal").text
    assert 'id="decision-benchmark"' in html
    assert "Does NOT measure exchange execution" in html



def test_retained_ids_are_bounded_without_silent_eviction():
    lane=BoundedDecisionLane(clock=lambda:10,queue_capacity=1,event_capacity=2)
    for seq in (1,2):
        assert lane.offer(seq,10,'BTC-USD','BUY',10)=='QUEUED'
        assert lane.decide_one()=='RESEARCH_SIGNAL_ACCEPTED_NO_ORDER'
    assert lane.offer(3,10,'BTC-USD','BUY',10)=='EVENT_RETENTION_FULL'
    assert lane.offer(1,10,'BTC-USD','BUY',10)=='DUPLICATE'
    assert lane.snapshot()['retained_event_ids']==lane.snapshot()['event_retention_capacity']==2
    assert not lane.snapshot()['automatic_id_deletion'] and not lane.queue
    lane.halt()
    assert lane.snapshot()['retained_event_ids']==2


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),float('-inf'),True,'10',None])
def test_invalid_clock_never_accepts_research_signal(bad):
    lane=BoundedDecisionLane(clock=lambda:bad)
    lane.offer(1,10,'BTC-USD','BUY',10)
    assert lane.decide_one()=='INVALID_CLOCK'
    assert lane.metrics['accepted']==0 and lane.metrics['invalid_clock']==1
    assert lane.offer(1,10,'BTC-USD','BUY',10)=='DUPLICATE'


@pytest.mark.parametrize('capacity',[0,-1,3001,True,1.5])
def test_event_retention_configuration_is_bounded(capacity):
    with pytest.raises(ValueError,match='INVALID_EVENT_RETENTION_CAPACITY'):
        BoundedDecisionLane(event_capacity=capacity)


def test_overflow_does_not_consume_retained_id_and_snapshot_is_defensive():
    lane=BoundedDecisionLane(clock=lambda:10,queue_capacity=1,event_capacity=2)
    lane.offer(1,10,'BTC-USD','BUY',10)
    assert lane.offer(2,10,'BTC-USD','BUY',10)=='QUEUE_FULL'
    assert lane.snapshot()['retained_event_ids']==1
    snapshot=lane.snapshot();snapshot['metrics']['accepted']=999
    assert lane.metrics['accepted']==0
    lane.decide_one()
    assert lane.offer(2,10,'BTC-USD','BUY',10)=='QUEUED'
