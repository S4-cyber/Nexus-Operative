"""Actual bounded child-process death and cold replay, disposable local files."""
import json
import subprocess
import sys
import pytest
from nexus import paper_recovery_drill as drill
from nexus import release_trials


def test_all_five_actual_crash_boundaries_retain_both_journal_states():
    r=drill.fixture_report()
    assert r['passed'] and r['forced_process_exits']==5 and r['fresh_process_reviews']==10
    assert [case['phase'] for case in r['cases']]==list(drill.PHASES)
    assert [case['injected_get_requests'] for case in r['cases']]==[0,0,3,3,3]
    assert r['precommit_rollback_sidecars_observed']
    assert all(all(case[k] is True for k in drill.CHECKS) for case in r['cases'])
    assert all(r[k] is False for k in (*drill.FLAGS,'actual_provider_access','actual_vault_access'))
    assert not any(word in json.dumps(r) for word in (drill.ACCOUNT,'fixture_key_only_123','fixture_secret_only_123','a_fixture','b_fixture','requests.sqlite3','pacing.sqlite3'))


def test_actual_drill_cli_exposes_only_aggregate_disposable_evidence():
    child=subprocess.run([sys.executable,'-m','nexus.paper_recovery_drill'],capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr
    r=json.loads(child.stdout)
    assert r['passed'] and r['automatic_retry'] is False and r['automatic_submission'] is False


@pytest.mark.parametrize('kind',['timeout','exit','invalid-json'])
def test_child_failures_do_not_echo_private_stderr_or_paths(monkeypatch,kind):
    private='PRIVATE_PATH_AND_ERROR_FIXTURE'
    def fail(*args,**kwargs):
        if kind=='timeout':raise subprocess.TimeoutExpired(private,drill.CHILD_TIMEOUT_SECONDS,stderr=private)
        return subprocess.CompletedProcess(private,1 if kind=='exit' else 0,stdout=private,stderr=private)
    monkeypatch.setattr(drill.subprocess,'run',fail)
    with pytest.raises(ValueError) as error:drill._child(private,private)
    assert private not in str(error.value) and str(error.value).startswith('PAPER_RECOVERY_DRILL_CHILD_')


def test_child_timeout_is_bounded_but_not_a_startup_latency_assertion(monkeypatch):
    observed=[]
    def completed(*args,**kwargs):
        observed.append(kwargs['timeout'])
        return subprocess.CompletedProcess(args[0],0,stdout='{}',stderr='')
    monkeypatch.setattr(drill.subprocess,'run',completed)
    assert drill._child('fixture') == {}
    assert observed == [drill.CHILD_TIMEOUT_SECONDS] == [60]


def test_failed_cli_emits_fixed_code_without_exception_details(monkeypatch,capsys):
    def fail():raise ValueError('PRIVATE_FIXTURE_ERROR')
    monkeypatch.setattr(drill,'fixture_report',fail)
    assert drill.main([])==2 and capsys.readouterr().out=='PAPER_RECOVERY_DRILL_FAILED\n'


def test_release_profile_rejects_missing_crash_evidence_or_promoted_authority():
    import copy
    result=release_trials._pacing_trial()
    release_trials._validate('pacing',result)
    for change in ('missing-case','missing-check','wrong-gets','authority','rollback'):
        bad=copy.deepcopy(result);e=bad['bounded_recovery_crash_drill']
        if change=='missing-case':e['cases'].pop()
        elif change=='missing-check':e['cases'][3]['another_step_denied_before_vault']=False
        elif change=='wrong-gets':e['cases'][1]['injected_get_requests']=3
        elif change=='authority':e['automatic_retry']=True
        else:e['precommit_rollback_sidecars_observed']=False
        with pytest.raises(ValueError,match='RELEASE_TRIAL_RECOVERY_CRASH_'):release_trials._validate('pacing',bad)


def test_cli_rejects_production_paths_without_echo_or_running_trials():
    child=subprocess.run([sys.executable,'-m','nexus.paper_recovery_drill','--journal','PRIVATE_PATH_FIXTURE'],capture_output=True,text=True,timeout=10)
    assert child.returncode==2 and child.stderr=='PAPER_RECOVERY_DRILL_USAGE_ERROR\n' and not child.stdout
