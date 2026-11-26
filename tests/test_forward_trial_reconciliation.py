"""Detached modeled-execution reconciliation receipts stay local and fail closed."""
import copy
import hashlib
import json

import pytest

from nexus import forward_evidence
from nexus import forward_trial_review as trial
from nexus.forward_reconciliation_review import reconciliation_sha256,review_receipt
from nexus import forward_trial_reconciliation as bundle
from test_paper_forward import setup,warmup


SESSION='0000000000000001'
QUOTE_HEAD='1'*64
EVENT_HEAD='2'*64
SNAPSHOT='4'*64
EXECUTION_SET='6'*64


def _fixture():
    policy=dict(id='btc_momentum_experiment_v1',product='BTC-USD',
        policy_sha256='5'*64)
    counts={'START':1,'BUY':1,'STOP':1}
    last=dict(at=2,decision='STOP',reason='MANUAL_STOP',session=SESSION,cycle=1,
        quote_evidence_sha256=QUOTE_HEAD,policy_id=policy['id'],
        policy_sha256=policy['policy_sha256'],product=policy['product'],
        tier='terrestrial',simulation_only=True,clock_sample_valid=True)
    cut=dict(before_status='CLEAR',after_status='CLEAR',requested_before=False,
        requested_after=False,binding_source='NONE',stable=True,
        binding_verified=False,record_sha256=None)
    events=dict(read_only=True,events=3,head_sha256=EVENT_HEAD,
        lifecycle_events=2,outcome_events=1,scheduler_events=0,
        quote_outcomes=1,modeled_order_outcomes=1,modeled_no_fill_outcomes=0,
        event_trace_complete=True,quote_outcome_bijection=True,
        durable_order_outcomes_reconciled=True,durable_fill_attempts_bound=True,
        modeled_execution_set_sha256=EXECUTION_SET,
        durable_no_fill_outcomes_reconciled=True,
        durable_no_fill_attempts_bound=True,cut_evidence_stable=True,
        cut_binding_verified=False,independent_cut_observed=False,
        latest_modeled_outcome_sha256='7'*64,integrity_ok=True)
    events['reconciliation_sha256']=reconciliation_sha256(session=SESSION,
        state='STOPPED',quote_head_sha256=QUOTE_HEAD,
        event_head_sha256=EVENT_HEAD,order_snapshot_sha256=SNAPSHOT,
        final_order_snapshot_sha256=SNAPSHOT,cut_evidence=cut,reasons=[],
        summary=events)
    snapshots=dict(snapshot_sha256=SNAPSHOT,halted=False)
    receipt=review_receipt(session=SESSION,started_at=0,updated_at=2,
        state='STOPPED',policy=policy,counts=counts,last=last,
        quotes=dict(head_sha256=QUOTE_HEAD,observations=1),events=events,
        initial_order_snapshot=snapshots,final_order_snapshot=snapshots,
        cut_evidence=cut,
        journal_stable=True,portfolio_stable=True,order_snapshot_stable=True,
        reasons=[])
    review=dict(mode='READ_ONLY_FORWARD_EVIDENCE_REVIEW',read_only=True,
        session=SESSION,started_at=0,updated_at=2,stored_state='STOPPED',
        policy=policy,counters=counts,review_receipt=receipt,
        durable_event_evidence=events,
        order_journal_snapshot=dict(snapshot_sha256=SNAPSHOT,halted=False,
            journal_stable_during_review=True,
            portfolio_stable_during_review=True,stable_during_review=True),
        provisional_active_session=False,local_consistency_ok=True,reasons=[],
        live_orders_enabled=False,release_approved=False,
        private_fixture='must-not-leak')
    facts={SESSION:dict(session_summary=dict(session=SESSION,started_at=0,
        updated_at=2,state='STOPPED',policy=policy,counts=counts,last=last),
        quote_head_sha256=QUOTE_HEAD,quote_observations=1,
        event_head_sha256=EVENT_HEAD,event_records=3,
        modeled_order_outcomes=1,modeled_execution_set_sha256=EXECUTION_SET)}
    return review,facts


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def test_build_exports_only_privacy_safe_exact_fields_and_validates():
    review,facts=_fixture()
    exported=bundle.build([review])
    assert exported['profile']=='nexus.forward-trial-reconciliation-bundle.v1'
    assert exported['read_only'] is True
    assert exported['live_orders_enabled'] is False
    assert exported['release_approved'] is False
    assert len(exported['sessions'])==1
    record=exported['sessions'][0]
    assert set(record)=={'session','review_receipt','durable_event_evidence',
        'order_journal_snapshot','local_consistency_ok','reasons',
        'live_orders_enabled','release_approved'}
    assert 'private_fixture' not in _canonical(exported)
    assert bundle.validate(exported,facts)=={SESSION:1}


@pytest.mark.parametrize('raw',[b'',b'{}\n',b'{"a":1,"a":1}',b'{"x":NaN}'])
def test_loads_rejects_empty_noncanonical_duplicate_or_nonfinite_json(raw):
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_RECONCILIATION_FAILED$'):
        bundle.loads(raw)


def test_loads_accepts_only_bounded_canonical_bytes():
    review,_=_fixture();exported=bundle.build([review]);raw=_canonical(exported).encode()
    assert bundle.loads(raw)==exported
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_RECONCILIATION_FAILED$'):
        bundle.loads(raw+b' ')
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_RECONCILIATION_FAILED$'):
        bundle.loads('not-bytes')
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_RECONCILIATION_FAILED$'):
        bundle.loads(b'0'*(bundle.MAX_BUNDLE_BYTES+1))


def _mutated(kind):
    review,facts=_fixture();exported=bundle.build([review])
    record=exported['sessions'][0]
    if kind=='receipt_digest':record['review_receipt']['receipt_sha256']='0'*64
    elif kind=='session_summary':facts[SESSION]['session_summary']['updated_at']=3
    elif kind=='quote_head':facts[SESSION]['quote_head_sha256']='0'*64
    elif kind=='event_head':facts[SESSION]['event_head_sha256']='0'*64
    elif kind=='event_count':facts[SESSION]['event_records']=4
    elif kind=='modeled_count':facts[SESSION]['modeled_order_outcomes']=2
    elif kind=='execution_set':facts[SESSION]['modeled_execution_set_sha256']='0'*64
    elif kind=='reconciliation':record['durable_event_evidence']['reconciliation_sha256']='0'*64
    elif kind=='event_integrity':record['durable_event_evidence']['integrity_ok']=False
    elif kind=='snapshot_stability':record['order_journal_snapshot']['stable_during_review']=False
    elif kind=='authority':record['live_orders_enabled']=True
    elif kind=='dirty':record['local_consistency_ok']=False
    elif kind=='duplicate':exported['sessions'].append(copy.deepcopy(record))
    elif kind=='missing':exported['sessions']=[]
    elif kind=='extra':
        extra=copy.deepcopy(record);extra['session']='0000000000000002'
        extra['review_receipt']['session']=extra['session']
        extra['review_receipt']['receipt_sha256']=hashlib.sha256(
            _canonical({k:v for k,v in extra['review_receipt'].items()
                if k!='receipt_sha256'}).encode()).hexdigest()
        exported['sessions'].append(extra)
    else:raise AssertionError(kind)
    return exported,facts


@pytest.mark.parametrize('kind',['receipt_digest','session_summary','quote_head',
    'event_head','event_count','modeled_count','execution_set','reconciliation','event_integrity',
    'snapshot_stability','authority','dirty','duplicate','missing','extra'])
def test_validate_rejects_forged_stale_incomplete_or_extra_bindings(kind):
    exported,facts=_mutated(kind)
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_RECONCILIATION_FAILED$'):
        bundle.validate(exported,facts)


@pytest.mark.parametrize('change',[dict(stored_state='RUNNING'),
    dict(local_consistency_ok=False),dict(reasons=['DISAGREEMENT']),
    dict(live_orders_enabled=True),dict(release_approved=True),
    dict(provisional_active_session=True)])
def test_build_rejects_active_dirty_or_authority_escalating_reviews(change):
    review,_=_fixture();review.update(change)
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_RECONCILIATION_FAILED$'):
        bundle.build([review])
@pytest.mark.parametrize('kind',['missing_execution_set','event_not_reconciled',
    'unstable_snapshot','forged_receipt'])
def test_build_rejects_incomplete_or_forged_local_reconciliation(kind):
    review,_=_fixture()
    if kind=='missing_execution_set':
        del review['durable_event_evidence']['modeled_execution_set_sha256']
    elif kind=='event_not_reconciled':
        review['durable_event_evidence']['durable_fill_attempts_bound']=False
    elif kind=='unstable_snapshot':
        review['order_journal_snapshot']['stable_during_review']=False
    else:
        review['review_receipt']['receipt_sha256']='0'*64
    with pytest.raises(ValueError,match=r'^FORWARD_TRIAL_RECONCILIATION_FAILED$'):
        bundle.build([review])


def test_actual_stopped_runner_bundle_is_private_and_consumable(tmp_path,monkeypatch):
    runner,_,clock=setup(tmp_path,monkeypatch)
    assert warmup(runner,clock)['decision']=='BUY'
    clock.now+=5;clock.price=102
    assert runner.single_cycle()['decision']=='SELL'
    runner.stop();report=runner.evidence_report(runner.status()['session'])
    exported=bundle.build([report]);raw=_canonical(exported).encode()
    assert b'order_id' not in raw and b'pf_' not in raw
    path=tmp_path/'trial-reconciliation.json';path.write_bytes(raw)
    result=trial.review(tmp_path/'paper_forward.sqlite3',
        after='1970-01-01T00:16:39Z',until='1970-01-01T00:17:16Z',
        reconciliation_bundle=path)
    assert result['modeled_execution_events_in_window']==2
    assert result['bundle_bound_modeled_execution_events_in_window']==2
    assert result['unreconciled_modeled_execution_events_in_window']==0
    assert all(result[key] is False for key in trial.FLAGS)
