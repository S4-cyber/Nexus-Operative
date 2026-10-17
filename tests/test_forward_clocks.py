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
