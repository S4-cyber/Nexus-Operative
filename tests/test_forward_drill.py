import json
import os
import sqlite3
import subprocess
import sys
import pytest
from nexus import forward_drill as drill


@pytest.mark.parametrize('rounds',[0,21,-1,True,None,1.5,'3'])
def test_round_bound_rejects_before_launch(monkeypatch,rounds):
    monkeypatch.setattr(drill,'_child',lambda *args:pytest.fail('invalid input launched child'))
    with pytest.raises(ValueError,match='INVALID_FORWARD_DRILL_ROUNDS'):drill.run(rounds)


def test_repeated_runner_drill_isolated_and_recovered(tmp_path,monkeypatch):
    data=tmp_path/'caller';data.mkdir();sentinel=data/'keep.txt';sentinel.write_text('untouched')
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(data));before=dict(os.environ)
    report=drill.run(2)
    assert dict(os.environ)==before and list(data.iterdir())==[sentinel]
    assert report['passed'] and report['rounds']==2 and report['cycles']==28
    assert not report['application_accounts_changed'] and not report['target_validated']
    assert not report['release_approved'] and not report['broker_connected']
    recovered=report['recovered']
    assert recovered['flat'] and recovered['portfolio_reconciled'] and recovered['halted']
    assert recovered['forward_stopped'] and recovered['ownership_released']
    assert len(recovered['sessions'])==2
    assert all(s['quote_observations']==14 for s in recovered['sessions'])
    assert all(s['modeled_buy_orders']==s['modeled_sell_orders']==1 for s in recovered['sessions'])
    assert set(report['cycle_latency'])=={'p50_ms','p95_ms','p99_ms'}


@pytest.mark.parametrize('damage',['ledger','quotes','missing'])
def test_fresh_review_rejects_damaged_retained_evidence(tmp_path,damage):
    baseline=drill._child(tmp_path,1);sessions=[s['session'] for s in baseline['recovered']['sessions']]
    if damage=='missing':(tmp_path/'paper_forward.sqlite3').unlink()
    elif damage=='ledger':
        with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
            db.execute("UPDATE meta SET value='1' WHERE key='cash'")
    else:
        with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
            db.execute("UPDATE forward_quote_heads SET head='corrupt'")
    with pytest.raises(ValueError,match='FORWARD_DRILL_CHILD_FAILED'):drill._child(tmp_path,1,sessions)


def test_cross_process_disagreement_is_not_passed(monkeypatch):
    calls=[]
    def child(*args):
        calls.append(args)
        return {'recovered':{'sessions':[{'session':'fixture'}]}} if len(calls)==1 else {'changed':True}
    monkeypatch.setattr(drill,'_child',child)
    with pytest.raises(ValueError,match='FRESH_PROCESS_DISAGREEMENT'):drill.run(1)


def test_cli_reports_injected_fixture_scope():
    result=subprocess.run([sys.executable,'-m','nexus.forward_drill','--rounds','1'],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['mode']=='DISPOSABLE_FORWARD_RUNNER_DRILL' and report['cycles']==14
def test_fault_injection_is_bounded_orderless_and_fail_closed(tmp_path):
    result=drill._faults(tmp_path)
    assert set(result)==drill.FAULT_PUBLIC_KEYS
    assert result['passed'] and result['orders_created']==0
    assert result['sessions_paused']==4 and result['ownership_released']
    assert result['actual_provider_requests'] is result['automatic_retries'] is False
    assert set(result['process_control_cleanup'])==drill.PROCESS_CONTROL_PUBLIC_KEYS
    for name,signal in (('keyboard_interrupt','KeyboardInterrupt'),
            ('system_exit','SystemExit')):
        case=result['process_control_cleanup'][name]
        assert set(case)==drill.PROCESS_CONTROL_CASE_PUBLIC_KEYS
        assert case['signal']==signal and case['worker_started']
        assert case['signal_observed'] and case['worker_stopped']
        assert case['runner_disabled'] and case['ownership_released']
        assert case['orders_created']==0 and case['stored_state']=='RUNNING'
        assert case['effective_state']=='STOPPED_WITHOUT_FINAL_RECORD'
        assert case['automatic_restart'] is False
    assert result['source_loss']['reasons']==['QUOTE_UNAVAILABLE_OR_INVALID']*3
    assert result['stale_quote']['reasons']==['QUOTE_UNAVAILABLE_OR_INVALID']*3
    assert result['clock_regression']['reasons']==['RUNTIME_CLOCK_DISCONTINUITY']
    assert result['invalid_clock']['reasons']==['INVALID_RUNTIME_CLOCK']
    for name in ('source_loss','stale_quote','clock_regression','invalid_clock'):
        case=result[name]
        assert case['worker_started'] and case['worker_paused_while_alive']
        assert case['cleanup_blocked_before_process_death'] and case['process_terminated']
        assert case['post_pause_fetches']==0 and case['automatic_restart'] is False
        assert case['stored_state']=='PAUSED' and case['paused']
        assert case['cold_reopen_disabled'] and case['cold_worker_alive'] is False
        assert case['cold_fetches_before_prepare']==0 and case['cold_ownership_available']
        assert case['ownership_released'] and case['new_session_distinct']
        assert case['order_hash_preserved'] and case['evidence_hash_preserved']
        assert case['failed_orders_sha256']==case['reopened_orders_sha256']
        assert case['failed_evidence_sha256']==case['reopened_evidence_sha256']
        assert len(case['failed_session'])==16 and len(case['failed_orders_sha256'])==64
        assert case['healthy_decision']=='HOLD'
        assert case['healthy_reason']=='WARMUP_REQUIRES_SIX_DISTINCT_TICKS'
        assert case['healthy_orders_created']==0
    for name in ('source_loss','stale_quote'):
        case=result[name]
        assert case['counts']=={'START':1,'VETO':2,'PAUSED':1}
        assert case['quote_observations']==case['rejected_observations']==3
        assert case['review_reasons']==[]
    for name in ('clock_regression','invalid_clock'):
        case=result[name]
        assert case['counts']=={'START':1,'PAUSED':1}
        assert case['quote_observations']==case['rejected_observations']==0
        assert case['review_reasons']==['NO_RECORDED_QUOTE_EVIDENCE']
