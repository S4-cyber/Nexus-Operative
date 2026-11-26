import pytest
from nexus.durable_burst import measure,CAPACITY,TIMING_ATTESTATION_KEYS


def test_burst_measures_linked_queue_and_bounds_overload(tmp_path):
    report=measure(tmp_path,12,clock=lambda:1)
    assert report['admitted']==report['completed_roundtrips']==CAPACITY
    assert report['overflow_rejections']==4 and report['duplicate_rejections']==1
    assert report['duplicate_fill_replays']==2*CAPACITY
    assert report['accounting_consistent'] and report['halted']
    assert report['durable_commands']==3+8*CAPACITY
    capacity=report['durable_capacity']
    assert report['capacity_bounds_verified']
    assert capacity['command_count']==capacity['retained_commands']==report['durable_commands']
    assert capacity['intent_count']==2*report['completed_roundtrips']
    assert capacity['control_commands_reserved']==1001
    assert capacity['reasons']==['EMERGENCY_HALT_ACTIVE']
    assert not capacity['new_intents_allowed'] and not capacity['lifecycle_intake_allowed']
    timing=report['timing_attestation']
    assert frozenset(timing)==TIMING_ATTESTATION_KEYS
    assert timing['scope']=='PROCESS_LOCAL_BOUNDED_BURST_WINDOW'
    assert timing['clock_samples']==2+CAPACITY*2
    assert timing['queue_wait_sample_count']==timing['dequeued_outcomes']==CAPACITY
    assert timing['durable_roundtrip_sample_count']==timing['admitted_to_completion_sample_count']==CAPACITY
    assert timing['completed_outcomes']==CAPACITY and timing['expired_outcomes']==timing['flushed_outcomes']==0
    assert timing['outcome_partition_verified'] and timing['finite_nonnegative_samples']
    assert timing['monotonic_sequence_verified'] and timing['emergency_halt_in_window']
    assert not timing['market_or_broker_latency'] and not timing['throughput_claimed']
    assert not report['target_validated'] and report['broker_orders']==0


def test_expired_queue_never_creates_orders(tmp_path):
    now=iter(range(50))
    report=measure(tmp_path,4,clock=lambda:next(now))
    assert report['expired_before_execution']==report['admitted']==4
    assert report['completed_roundtrips']==report['duplicate_fill_replays']==0
    assert report['durable_commands']==3 and report['accounting_consistent']
    assert report['capacity_bounds_verified'] and report['durable_capacity']['intent_count']==0
    assert report['admitted_to_completion_latency'] is None
    timing=report['timing_attestation']
    assert timing['dequeued_outcomes']==timing['expired_outcomes']==4
    assert timing['completed_outcomes']==timing['durable_roundtrip_sample_count']==0


def test_halt_flushes_pending_burst_before_execution(tmp_path):
    report=measure(tmp_path,8,clock=lambda:1,halt_after=0)
    assert report['halt_flushed']==8 and report['completed_roundtrips']==0
    assert report['accounting_consistent'] and report['halted']
    assert report['durable_commands']==3
    assert report['capacity_bounds_verified'] and report['durable_capacity']['intent_count']==0
    timing=report['timing_attestation']
    assert timing['dequeued_outcomes']==0 and timing['flushed_outcomes']==8
    assert timing['clock_samples']==2


@pytest.mark.parametrize('count',[0,31,True,'4'])
def test_burst_work_is_bounded(tmp_path,count):
    with pytest.raises(ValueError,match='INVALID_BURST_CYCLES'):measure(tmp_path,count)
    assert not list(tmp_path.iterdir())


def test_atomic_order_treatment_preserves_logical_commands_and_accounting(tmp_path):
    report=measure(tmp_path,8,clock=lambda:1,atomic_orders=True)
    assert report['atomic_order_transactions']
    assert report['completed_roundtrips']==8 and report['durable_commands']==67
    assert report['duplicate_fill_replays']==16 and report['accounting_consistent']


def test_burst_clock_regression_fails_closed(tmp_path):
    now=iter((2,1))
    with pytest.raises(ValueError,match='BURST_CLOCK_REGRESSION'):
        measure(tmp_path,1,clock=lambda:next(now))
