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
