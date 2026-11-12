"""Disposable real SQLite chains; declared public labels are never attestation."""
from contextlib import closing,contextmanager
from decimal import localcontext
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from nexus import forward_trial_review as trial, forward_evidence as evidence
from nexus.paper_policy import admit
from nexus.paper_forward import PaperForwardRunner

START=1767225600  # 2026-01-01 UTC
AFTER='2026-01-01T00:00:00Z'


def until(seconds):
    from datetime import datetime,timezone
    return datetime.fromtimestamp(START+seconds,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def database(tmp_path,monkeypatch,sessions):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));runner=PaperForwardRunner();policy=admit('btc_momentum_experiment_v1')
    with closing(runner._db()) as db,db:
        for index,(points,source,state) in enumerate(sessions):
            sid=f'{index+1:016x}';previous=evidence.GENESIS;rows=[]
            for cycle,(offset,eligible) in enumerate(points,1):
                payload=evidence.canonical(dict(session=sid,cycle=cycle,evaluated_at=START+offset,
                    quote=dict(provider='coinbase_exchange_public',product='BTC-USD',bid='100',ask='100.1',last='100',received_at=START+offset,exchange_time=until(offset),read_only=True,orders_allowed=False),quote_eligible=eligible,source_mode=source,
                    policy_sha256=policy['policy_sha256'],error_type=None))
                digest=evidence.digest(previous,payload);rows.append((sid,cycle,payload,previous,digest));previous=digest
            first=START+points[0][0] if points else START;last=START+points[-1][0] if points else START
            decision='STOP' if state=='STOPPED' else 'PAUSED' if state=='PAUSED' else 'HOLD'
            reason='MANUAL_STOP' if state=='STOPPED' else 'TEST_'+state
            terminal=dict(at=last,decision=decision,reason=reason,session=sid,cycle=len(rows),
                quote_evidence_sha256=previous if rows else None,policy_id=policy['id'],
                policy_sha256=policy['policy_sha256'],product=policy['product'],
                simulation_only=True,clock_sample_valid=True)
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',(sid,first,last,state,evidence.canonical(policy),
                evidence.canonical({decision:1}),evidence.canonical(terminal)))
            db.executemany('INSERT INTO forward_quotes VALUES (?,?,?,?,?)',rows)
            if rows:db.execute('INSERT INTO forward_quote_heads VALUES (?,?,?)',(sid,len(rows),previous))
    return tmp_path/'paper_forward.sqlite3'


def run(path,seconds=60):return trial.review(path,after=AFTER,until=until(seconds))


def rewrite(path, change, *, policy_change=None):
    """Self-consistent local rewriting is not source attestation."""
    with closing(sqlite3.connect(path)) as db,db:
        rows=db.execute('SELECT session,cycle,payload FROM forward_quotes ORDER BY session,cycle').fetchall()
        policy=None
        if policy_change is not None:
            policy=json.loads(db.execute('SELECT policy_json FROM sessions').fetchone()[0])
            policy.update(policy_change);policy.pop('policy_sha256')
            import hashlib
            policy['policy_sha256']=hashlib.sha256(evidence.canonical(policy).encode()).hexdigest()
            db.execute('UPDATE sessions SET policy_json=?',(evidence.canonical(policy),))
        previous={};counts={}
        for sid,cycle,raw in rows:
            point=json.loads(raw)
            if cycle==2:change(point)
            if policy is not None:point['policy_sha256']=policy['policy_sha256']
            payload=evidence.canonical(point);parent=previous.get(sid,evidence.GENESIS)
            digest=evidence.digest(parent,payload)
            db.execute('UPDATE forward_quotes SET payload=?,previous=?,digest=? WHERE session=? AND cycle=?',
                (payload,parent,digest,sid,cycle))
            previous[sid]=digest;counts[sid]=counts.get(sid,0)+1
        for sid,head in previous.items():
            db.execute('UPDATE forward_quote_heads SET observations=?,head=? WHERE session=?',(counts[sid],head,sid))
            last=json.loads(db.execute('SELECT last_json FROM sessions WHERE id=?',(sid,)).fetchone()[0])
            last.update(cycle=counts[sid],quote_evidence_sha256=head)
            if policy is not None:
                last.update(policy_id=policy['id'],policy_sha256=policy['policy_sha256'],product=policy['product'])
            db.execute('UPDATE sessions SET last_json=? WHERE id=?',(evidence.canonical(last),sid))


@pytest.mark.parametrize('changes', [
    {'received_at':START}, {'received_at':START+6}, {'received_at':False},
    {'exchange_time':until(-10)}, {'exchange_time':until(6)},
    {'exchange_time':until(-1)}, {'exchange_time':'invalid'}, {'exchange_time':until(5)[:-1]},
    {'provider':'other'}, {'product':'ETH-USD'}, {'read_only':False}, {'orders_allowed':True},
    {'bid':'NaN'}, {'bid':'200'}, {'ask':'102'}, {'last':None}, {'last':'125'},
    {'bid':'1e999999999'},
], ids=['stale-receipt','future-receipt','bool-receipt','stale-exchange','future-exchange',
    'exchange-regression','bad-time','naive-time','provider','product','read-permission',
    'order-permission','nonfinite','crossed','spread','missing-price','jump','decimal-overflow'])
def test_claimed_eligible_bad_quotes_cannot_inflate_coverage(tmp_path,monkeypatch,changes):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True),(10,True)],trial.PUBLIC,'STOPPED')])
    rewrite(path,lambda p:p['quote'].update(changes));before=path.read_bytes()
    result=run(path,10)
    assert result['eligible_observations_in_window']==3  # Original declared flags remain visible.
    assert result['revalidated_eligible_observations_in_window']==2
    assert result['recorded_eligibility_disagreements_in_window']==1
    assert result['declared_public_covered_seconds']=='0'
    assert 'RECORDED_ELIGIBILITY_FAILS_RETAINED_QUOTE_RECHECK' in result['review_reasons']
    assert all(result[k] is False for k in trial.FLAGS) and path.read_bytes()==before


@pytest.mark.parametrize('kind',['missing-fields','recorded-error'])
def test_minimal_or_failed_quote_does_not_qualify_even_with_valid_chain(tmp_path,monkeypatch,kind):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True),(10,True)],trial.PUBLIC,'STOPPED')])
    def changed(point):
        if kind=='missing-fields':point['quote']={'provider':'coinbase_exchange_public'}
        else:point['error_type']='ValueError'
    rewrite(path,changed)
    result=run(path,10)
    assert result['recorded_eligibility_disagreements_in_window']==1
    assert result['revalidated_rejected_observations_in_window']==1
    assert result['declared_public_covered_seconds']=='0'


def test_rejected_flags_are_never_upgraded_by_a_valid_quote(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,False),(10,True)],trial.PUBLIC,'STOPPED')])
    result=run(path,10)
    assert result['revalidated_eligible_observations_in_window']==2
    assert result['recorded_eligibility_disagreements_in_window']==0
    assert result['declared_public_covered_seconds']=='0'


@pytest.mark.parametrize('change',[{'receive_age_seconds':9999},{'id':'unadmitted_experiment'}])
def test_self_consistent_modified_policy_cannot_relax_quote_replay(tmp_path,monkeypatch,change):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True)],trial.PUBLIC,'STOPPED')])
    rewrite(path,lambda p:None,policy_change=change);before=path.read_bytes()
    with pytest.raises(ValueError):run(path,10)
    assert path.read_bytes()==before


def test_high_coverage_target_still_requires_no_eligibility_contradictions(tmp_path,monkeypatch):
    points=[(i,True) for i in range(0,72*3600+1,10)]
    path=database(tmp_path,monkeypatch,[(points,trial.PUBLIC,'STOPPED')])
    rewrite(path,lambda p:p['quote'].update(read_only=False))
    result=run(path,72*3600)
    assert float(result['coverage_fraction'])>0.99 and not result['declared_coverage_target_met']
    assert result['recorded_eligibility_disagreements_in_window']==1


def test_sparse_endpoints_never_turn_calendar_span_into_covered_uptime(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(72*3600,True)],trial.PUBLIC,'STOPPED')]);before=path.read_bytes()
    result=run(path,72*3600)
    assert result['declared_public_covered_seconds']=='0' and result['coverage_fraction']=='0.000000'
    assert result['uncovered_seconds']=='259200' and not result['declared_coverage_target_met']
    assert path.read_bytes()==before


def test_even_complete_declared_public_coverage_cannot_accept_market_or_broker_trial(tmp_path,monkeypatch):
    points=[(i,True) for i in range(0,72*3600+1,10)]
    path=database(tmp_path,monkeypatch,[(points,trial.PUBLIC,'STOPPED')]);result=run(path,72*3600)
    assert result['coverage_fraction']=='1.000000' and result['declared_coverage_target_met']
    assert result['status']=='REVIEW_REQUIRED' and all(result[k] is False for k in trial.FLAGS)
    assert result['observations_in_window']==25921
    assert result['incident_free_coverage_target_met'] is True
    assert result['operational_incidents_present'] is False
    assert result['over_limit_coverage_gaps']==0
    assert result['over_limit_coverage_gap_seconds']=='0'


def test_high_aggregate_coverage_does_not_hide_over_limit_feed_gap(tmp_path,monkeypatch):
    points=[(i,True) for i in range(0,72*3600+1,5) if i not in (105,110)]
    path=database(tmp_path,monkeypatch,[(points,trial.PUBLIC,'STOPPED')])
    result=run(path,72*3600)
    assert result['declared_coverage_target_met'] is True
    assert result['coverage_fraction']=='0.999942'
    assert result['over_limit_coverage_gaps']==1
    assert result['over_limit_coverage_gap_seconds']=='15'
    assert result['operational_incidents_present'] is True
    assert result['incident_free_coverage_target_met'] is False
    assert 'COVERAGE_GAP_EXCEEDS_MAXIMUM' in result['review_reasons']


def test_high_aggregate_coverage_does_not_hide_retained_rejection(tmp_path,monkeypatch):
    points=[(i,i!=100) for i in range(0,72*3600+1,5)]
    path=database(tmp_path,monkeypatch,[(points,trial.PUBLIC,'STOPPED')])
    result=run(path,72*3600)
    assert result['declared_coverage_target_met'] is True
    assert result['coverage_fraction']=='0.999961'
    assert result['rejected_observations_in_window']==1
    assert result['quote_error_observations']==0
    assert result['over_limit_coverage_gaps']==0
    assert result['operational_incidents_present'] is True
    assert result['incident_free_coverage_target_met'] is False
    assert 'REJECTED_OBSERVATIONS_RETAINED_IN_WINDOW' in result['review_reasons']


def test_coverage_target_does_not_hide_retained_quote_incidents(tmp_path,monkeypatch):
    points=[(i,True) for i in range(0,72*3600+1,5)]
    path=database(tmp_path,monkeypatch,[(points,trial.PUBLIC,'STOPPED')])
    rewrite(path,lambda p:p.update(error_type='TimeoutError',quote_eligible=False))
    result=run(path,72*3600)
    assert result['declared_coverage_target_met'] is True
    assert result['incident_free_coverage_target_met'] is False
    assert result['operational_incidents_present'] is True
    assert result['sessions_with_quote_errors']==1
    assert result['quote_error_observations']==1
    assert 'QUOTE_ERRORS_RETAINED_IN_WINDOW' in result['review_reasons']


def test_coverage_target_does_not_hide_nonmanual_termination(tmp_path,monkeypatch):
    points=[(i,True) for i in range(0,72*3600+1,5)]
    path=database(tmp_path,monkeypatch,[(points,trial.PUBLIC,'STOPPED')])
    with closing(sqlite3.connect(path)) as db,db:
        last=json.loads(db.execute('SELECT last_json FROM sessions').fetchone()[0])
        last['reason']='EMERGENCY_STOP';db.execute('UPDATE sessions SET last_json=?',(evidence.canonical(last),))
    result=run(path,72*3600)
    assert result['declared_coverage_target_met'] is True
    assert result['incident_free_coverage_target_met'] is False
    assert result['other_stopped_sessions']==1 and result['clean_manual_stop_sessions']==0
    assert 'SESSION_TERMINATION_REQUIRES_REVIEW' in result['review_reasons']


def test_overlap_is_unioned_once_and_session_boundaries_never_bridged(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True),(10,True)],trial.PUBLIC,'STOPPED'),
        ([(5,True),(10,True),(15,True)],trial.PUBLIC,'STOPPED'), ([(20,True),(25,True)],trial.PUBLIC,'STOPPED')])
    result=run(path,30)
    assert result['declared_public_covered_seconds']=='20' and result['uncovered_seconds']=='10'
    assert result['uncovered_intervals']==2 and result['longest_uncovered_seconds']=='5'
    assert result['over_limit_coverage_gaps']==0 and result['over_limit_coverage_gap_seconds']=='0'


def test_intervals_clip_to_window_without_rounding_or_double_counting(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(-5,True),(5,True),(15,True)],trial.PUBLIC,'STOPPED')])
    result=run(path,10)
    assert result['declared_public_covered_seconds']=='10' and result['observations_in_window']==1
    with localcontext() as context:
        context.prec=4
        assert run(path,10)==result


@pytest.mark.parametrize('source',[trial.INJECTED,'UNKNOWN_DECLARED_SOURCE'])
def test_nonpublic_labels_never_count_as_public_coverage(tmp_path,monkeypatch,source):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True),(10,True)],source,'STOPPED')])
    result=run(path,10)
    assert result['declared_public_covered_seconds']=='0' and not result['declared_coverage_target_met']
    assert not result['source_attested']


def test_rejected_points_break_coverage_instead_of_bridging_outage(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,False),(10,True),(15,True)],trial.PUBLIC,'PAUSED')])
    result=run(path,15)
    assert result['declared_public_covered_seconds']=='5' and result['rejected_observations_in_window']==1
    assert result['nonstopped_or_cleanup_review_sessions']==1


@pytest.mark.parametrize('kind',['chain','policy','orphan','duplicate_json','regression','terminal_time',
    'terminal_state','terminal_count','terminal_session','terminal_policy','terminal_scope','terminal_clock',
    'terminal_quote_head','point_before_session','point_after_session','metadata_budget'])
def test_corrupt_or_incompatible_evidence_is_not_certified_or_repaired(tmp_path,monkeypatch,kind):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True)],trial.PUBLIC,'STOPPED')])
    with closing(sqlite3.connect(path)) as db,db:
        if kind=='chain':db.execute("UPDATE forward_quotes SET digest='bad' WHERE cycle=1")
        elif kind=='policy':db.execute("UPDATE sessions SET policy_json='{}'")
        elif kind=='orphan':db.execute('DELETE FROM sessions')
        elif kind=='duplicate_json':
            payload=db.execute('SELECT payload FROM forward_quotes WHERE cycle=1').fetchone()[0]
            db.execute('UPDATE forward_quotes SET payload=? WHERE cycle=1',('{"cycle":999,'+payload[1:],))
        elif kind=='regression':
            db.execute('UPDATE sessions SET updated_at=started_at-1')
        elif kind=='terminal_time':
            last=json.loads(db.execute('SELECT last_json FROM sessions').fetchone()[0])
            last['at']+=1;db.execute('UPDATE sessions SET last_json=?',(evidence.canonical(last),))
        elif kind=='terminal_state':
            last=json.loads(db.execute('SELECT last_json FROM sessions').fetchone()[0])
            last['decision']='PAUSED';db.execute('UPDATE sessions SET counts_json=?,last_json=?',
                (evidence.canonical({'PAUSED':1}),evidence.canonical(last)))
        elif kind=='terminal_count':
            db.execute('UPDATE sessions SET counts_json=?',(evidence.canonical({}),))
        elif kind.startswith('terminal_'):
            last=json.loads(db.execute('SELECT last_json FROM sessions').fetchone()[0])
            field={'terminal_session':'session','terminal_policy':'policy_sha256',
                'terminal_scope':'simulation_only','terminal_clock':'clock_sample_valid',
                'terminal_quote_head':'quote_evidence_sha256'}[kind]
            last[field]=False if kind in ('terminal_scope','terminal_clock') else 'bad'
            db.execute('UPDATE sessions SET last_json=?',(evidence.canonical(last),))
        elif kind=='point_before_session':
            db.execute('UPDATE sessions SET started_at=started_at+1')
        elif kind=='point_after_session':
            last=json.loads(db.execute('SELECT last_json FROM sessions').fetchone()[0])
            last['at']-=1
            db.execute('UPDATE sessions SET updated_at=updated_at-1,last_json=?',(evidence.canonical(last),))
        else:db.execute('UPDATE sessions SET counts_json=?',(' '*65537,))
    before=path.read_bytes()
    with pytest.raises((ValueError,KeyError)):run(path)
    assert path.read_bytes()==before


def test_missing_history_and_invalid_window_never_initialize_paths(tmp_path):
    path=tmp_path/'missing'/'history.db'
    with pytest.raises(OSError):run(path)
    with pytest.raises(ValueError):trial.review(path,after=AFTER,until=AFTER)
    with pytest.raises(ValueError):trial.review(path,after=AFTER,until=until(8*86400))
    assert not path.parent.exists()


def test_explicit_cli_emits_private_diagnostics_and_fixed_failure(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True)],trial.INJECTED,'STOPPED')])
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path/'unused')}
    command=[sys.executable,'-m','nexus.forward_trial_review','--history',str(path),'--after',AFTER,'--until',until(60)]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['injected_observations']==2 and not (tmp_path/'unused').exists()
    path.write_bytes(b'private corrupt fixture')
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and result.stdout.strip()=='FORWARD_TRIAL_REVIEW_FAILED' and not result.stderr


def test_read_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    path=database(tmp_path,monkeypatch,[([(0,True),(5,True)],trial.PUBLIC,'STOPPED')])
    expected=run(path);before=path.read_bytes()
    monkeypatch.setattr(trial,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_REVIEW_FAILED$'):run(path)
    assert path.read_bytes()==before and run(path)==expected


def test_path_replacement_before_snapshot_open_is_rejected(tmp_path,monkeypatch):
    path=database(tmp_path/'first',monkeypatch,[([(0,True),(5,True)],trial.PUBLIC,'STOPPED')])
    replacement=database(tmp_path/'second',monkeypatch,[([(0,True),(6,True)],trial.PUBLIC,'STOPPED')])
    original_connect=trial.sqlite3.connect;retired=tmp_path/'retired.sqlite3';changed=False
    def replace_then_connect(*args,**kwargs):
        nonlocal changed
        if not changed:
            changed=True;os.replace(path,retired);os.replace(replacement,path)
        return original_connect(*args,**kwargs)
    monkeypatch.setattr(trial.sqlite3,'connect',replace_then_connect)
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_REVIEW_FAILED$'):run(path)
    assert changed and path.exists() and retired.exists()


def test_path_replacement_after_snapshot_close_is_rejected(tmp_path,monkeypatch):
    path=database(tmp_path/'first',monkeypatch,[([(0,True),(5,True)],trial.PUBLIC,'STOPPED')])
    replacement=database(tmp_path/'second',monkeypatch,[([(0,True),(6,True)],trial.PUBLIC,'STOPPED')])
    original_snapshot=trial._snapshot;retired=tmp_path/'retired.sqlite3'
    @contextmanager
    def replace_after_close(bound,stable_source):
        with original_snapshot(bound,stable_source) as db:yield db
        os.replace(path,retired);os.replace(replacement,path)
    monkeypatch.setattr(trial,'_snapshot',replace_after_close)
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_REVIEW_FAILED$'):run(path)
    assert path.exists() and retired.exists()
