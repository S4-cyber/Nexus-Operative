import pytest
from nexus.durable_burst import measure,CAPACITY


def test_burst_measures_linked_queue_and_bounds_overload(tmp_path):
    report=measure(tmp_path,12,clock=lambda:1)
    assert report['admitted']==report['completed_roundtrips']==CAPACITY
    assert report['overflow_rejections']==4 and report['duplicate_rejections']==1
    assert report['duplicate_fill_replays']==2*CAPACITY
    assert report['accounting_consistent'] and report['halted']
    assert report['durable_commands']==3+8*CAPACITY
    assert not report['target_validated'] and report['broker_orders']==0


def test_expired_queue_never_creates_orders(tmp_path):
    now=iter(range(50))
    report=measure(tmp_path,4,clock=lambda:next(now))
    assert report['expired_before_execution']==report['admitted']==4
    assert report['completed_roundtrips']==report['duplicate_fill_replays']==0
    assert report['durable_commands']==3 and report['accounting_consistent']
    assert report['admitted_to_completion_latency'] is None


def test_halt_flushes_pending_burst_before_execution(tmp_path):
    report=measure(tmp_path,8,clock=lambda:1,halt_after=0)
    assert report['halt_flushed']==8 and report['completed_roundtrips']==0
    assert report['accounting_consistent'] and report['halted']
    assert report['durable_commands']==3


@pytest.mark.parametrize('count',[0,31,True,'4'])
def test_burst_work_is_bounded(tmp_path,count):
    with pytest.raises(ValueError,match='INVALID_BURST_CYCLES'):measure(tmp_path,count)
    assert not list(tmp_path.iterdir())
