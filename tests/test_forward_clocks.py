import math
import pytest
from test_paper_forward import setup,warmup


def test_wall_jump_pauses_before_quote_fetch(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    steady=[1000];r.monotonic_clock=lambda:steady[0]
    r._clock_anchor=(1000,1000)
    calls=[];r.fetcher=lambda product:calls.append(product)
    c.now=1100;steady[0]=1005
    result=r.single_cycle()
    assert result['reason']=='RUNTIME_CLOCK_DISCONTINUITY'
    assert not calls and not g.recovery_report()['orders']
    assert not r.status()['enabled'] and not r.status()['ownership_held']


@pytest.mark.parametrize('wall,steady',[(999,1005),(1005,999),(math.nan,1005),(1005,math.inf)])
def test_invalid_or_backward_clocks_pause(tmp_path,monkeypatch,wall,steady):
    r,g,c=setup(tmp_path,monkeypatch);r.monotonic_clock=lambda:steady
    r._clock_anchor=(1000,1000);c.now=wall
    result=r.single_cycle()
    assert result['decision']=='PAUSED'
    assert not r.status()['enabled'] and not g.recovery_report()['orders']
    assert r.sessions()['sessions'][0]['stored_state']=='PAUSED'


def test_clock_jump_during_transport_blocks_entry(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    steady=[1000];r.monotonic_clock=lambda:steady[0];r._clock_anchor=(1000,1000)
    def fetch(product):
        c.now+=100;steady[0]+=1
        return c.quote(product)
    r.fetcher=fetch
    assert r.single_cycle()['reason']=='RUNTIME_CLOCK_DISCONTINUITY'
    assert not g.recovery_report()['orders']


def test_clock_jump_after_submit_cancels_without_fill(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    steady=[c.now];r.monotonic_clock=lambda:steady[0]
    r._clock_anchor=(c.now,steady[0])
    original=g.set_execution_terms
    def jump(*args,**kwargs):
        result=original(*args,**kwargs);c.now+=100;return result
    monkeypatch.setattr(g,'set_execution_terms',jump)
    for i in range(7):
        c.now=1000+i*5;steady[0]=c.now;c.price=100+i*.1
        result=r.single_cycle()
    assert result['reason']=='RUNTIME_CLOCK_DISCONTINUITY'
    orders=g.recovery_report()['orders']
    assert len(orders)==1 and orders[0]['status']=='SIMULATED_CANCELLED'
    assert orders[0]['simulated_filled_notional']=='0'
    assert g.recovery_report()['portfolio_reconciled']


def test_exchange_regressions_clear_warmup_and_eventually_pause(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    for i in range(5):c.now=1000+i*5;c.price=100+i*.1;r.single_cycle()
    for i in range(3):
        c.now=1021+i;c.stamp=1019
        result=r.single_cycle()
        assert result['reason']=='EXCHANGE_TIME_REGRESSION'
        assert not r._prices
    assert result['decision']=='PAUSED' and not r.status()['ownership_held']
    assert not g.recovery_report()['orders']
    assert r.evidence_report(r.status()['session'])['quote_evidence']['rejected_observations']==3


def test_normal_elapsed_gap_rewarms_without_clock_fault(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle()
    c.now+=30
    result=r.single_cycle()
    assert result['decision']=='HOLD' and r.status()['enabled']
    assert len(r._prices)==1
    r.stop()


@pytest.mark.parametrize('failure',['overflow','callback'])
def test_runtime_clock_failure_pauses_without_escaping_or_exposing_detail(tmp_path,monkeypatch,failure):
    r,g,c=setup(tmp_path,monkeypatch)
    if failure=='overflow':r.clock=lambda:10**1000
    else:r.clock=lambda:(_ for _ in ()).throw(RuntimeError('private callback detail'))
    result=r.single_cycle();session=r.sessions()['sessions'][0]
    assert result['decision']=='PAUSED' and result['reason']=='INVALID_RUNTIME_CLOCK'
    assert not r.status()['enabled'] and r.status()['last_error']=='INVALID_RUNTIME_CLOCK'
    assert session['stored_state']=='PAUSED' and session['last_event']['reason']=='INVALID_RUNTIME_CLOCK'
    assert 'private callback detail' not in str(session) and not g.recovery_report()['orders']


def test_final_record_clock_failure_converts_hold_to_durable_pause(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);samples=iter((c.now,c.now))
    def clock():
        try:return next(samples)
        except StopIteration:raise RuntimeError('private final-record detail')
    r.clock=clock
    result=r.single_cycle();session=r.sessions()['sessions'][0]
    assert result['decision']=='PAUSED' and result['reason']=='INVALID_RUNTIME_CLOCK'
    assert not r.status()['enabled'] and session['stored_state']=='PAUSED'
    assert session['last_event']['clock_sample_valid'] is False
    assert r.evidence_report(session['session'])['quote_evidence']['observations']==1
    assert 'private final-record detail' not in str(session)


def test_runtime_clock_process_control_signal_remains_observable(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    r.clock=lambda:(_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):r.single_cycle()
    assert r.status()['enabled'] and not g.recovery_report()['orders']
    r.clock=lambda:c.now;r.stop()


def test_stop_remains_durable_and_sanitized_when_runtime_clock_fails(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    r.clock=lambda:(_ for _ in ()).throw(RuntimeError('private stop-clock detail'))
    status=r.stop();session=r.sessions()['sessions'][0]
    assert not status['enabled'] and not status['ownership_held']
    assert session['stored_state']=='STOPPED' and session['last_event']['decision']=='STOP'
    assert session['last_event']['clock_sample_valid'] is False
    assert 'private stop-clock detail' not in str(session) and not g.recovery_report()['orders']
