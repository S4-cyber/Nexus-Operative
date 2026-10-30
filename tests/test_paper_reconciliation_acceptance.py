"""Accepted-history binding for supplied balance observations."""
import copy
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from nexus import paper_reconciliation_acceptance as subject
from nexus.paper_activity_acceptance_evidence import ACCOUNT, _scope
from nexus.paper_observation_review import _fixture_observation
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore

EPOCH = 1792195200  # 2026-10-17T00:00:00Z

def prepared(tmp_path):
    pacing, activity, acceptance, scope = _scope(tmp_path)
    acceptance.append(scope, admission=lambda:(pacing.inspect()['head_sha256'],
        activity.inspect()['head_sha256']))
    opening = _fixture_observation(ACCOUNT,'1000','0',EPOCH+100,EPOCH+101)
    closing = _fixture_observation(ACCOUNT,'1000','0',EPOCH+102,EPOCH+103)
    options = dict(expected_account_id=ACCOUNT,
        pacing_path=tmp_path/'pacing.sqlite3',
        activity_store_path=tmp_path/'activity.sqlite3',
        acceptance_store_path=tmp_path/'acceptance.sqlite3',
        expected_activity_head=activity.inspect()['head_sha256'],
        opening_observation=opening, closing_observation=closing)
    return options


def snapshot(root):
    return tuple((path.name,path.read_bytes()) for path in sorted(root.iterdir()) if path.is_file())


def guarded(tmp_path):
    options=prepared(tmp_path)
    options.update(pre_opening_observation=_fixture_observation(ACCOUNT,'1000','0',EPOCH+98,EPOCH+99),
        post_closing_observation=_fixture_observation(ACCOUNT,'1000','0',EPOCH+104,EPOCH+105))
    return options


def relabel_source(observation, source):
    result=copy.deepcopy(observation);result['source']=source
    timing={key:float(value) if '.' in value or 'e' in value else int(value)
        for key,value in result['read_timing'].items()}
    scope=dict(profile='nexus.paper-account-observation.v1',**{k:result[k] for k in
        ('account_fields_sha256','reported_inventory_sha256','position_count',
         'open_order_count','source')},read_timing=timing)
    result['observation_sha256']=subject.sha(scope)
    result['manifest_sha256']=subject.sha({k:v for k,v in result.items() if k!='manifest_sha256'})
    return result


def test_complete_accepted_history_binds_exact_observations_and_head_without_writes(tmp_path):
    options=prepared(tmp_path);before=snapshot(tmp_path);result=subject.review(**options)
    assert result['mode']=='PAPER_RECONCILIATION_ACCEPTANCE_REVIEW'
    assert result['complete_accepted_history_replayed'] is True
    assert result['legacy_baseline_absent'] is True
    assert result['complete_pacing_sessions_accepted'] is True
    assert result['accepted_pacing_operations_typed'] is True
    assert result['accepted_pacing_result_receipts_bound'] is True
    assert result['complete_retained_batches_accepted'] is True
    assert result['complete_collection_observations_accepted'] is True
    assert result['all_collection_cursor_sequences_complete'] is True
    assert result['declared_collection_windows_cover_observation_envelope'] is True
    assert result['requested_window_boundaries_verified'] is False
    assert len(result['declared_collection_window_union_sha256']) == 64
    assert result['journals_rechecked_unchanged'] is True
    assert result['journal_files_rechecked_unchanged'] is True
    assert result['journal_file_identities_rechecked'] is True
    assert result['journal_store_bindings_rechecked'] is True
    assert result['accepted_transitions']==1
    assert result['activity_head_sha256']==options['expected_activity_head']
    assert result['opening_manifest_sha256']==options['opening_observation']['manifest_sha256']
    assert result['observation_review']['arithmetic_evaluated'] is True
    assert snapshot(tmp_path)==before
    for flag in ('admission_reserved','source_attested','source_coverage_verified',
        'activity_coverage_verified','fee_coverage_complete','fee_execution_links_verified',
        'balance_instants_verified','observation_windows_verified_quiet','holds_reconciled',
        'broker_account_reconciled','spendable_funds_verified','ledger_applied',
        'ready_for_routing','broker_connected','live_orders_enabled','money_movement_enabled',
        'automatic_retry','automatic_continuation','release_approved'):
        assert result[flag] is False


def test_four_observation_stability_guard_is_exact_bound_read_only_and_non_authoritative(tmp_path):
    options=guarded(tmp_path);before=snapshot(tmp_path)
    result=subject.review_with_stability_guards(**options)
    assert result['mode']=='PAPER_RECONCILIATION_STABILITY_GUARD_REVIEW'
    assert result['guard_manifests_distinct'] and result['guard_windows_ordered']
    assert result['adjacent_guard_balances_stable']
    assert result['guard_declared_sources_agree']
    assert result['guard_retained_fill_events']==0
    assert result['guard_collection_observed_fill_events']==0
    assert result['guard_collection_observed_fee_events']==0
    assert result['guard_retained_fee_date_overlaps']==0
    assert result['accepted_declared_windows_cover_guard_envelope']
    assert result['journals_rechecked_unchanged']
    assert result['journal_files_rechecked_unchanged']
    assert result['journal_file_identities_rechecked']
    assert result['journal_store_bindings_rechecked']
    assert result['accepted_reconciliation_review']['observation_review']['arithmetic_evaluated']
    assert result==subject.review_with_stability_guards(**options)
    assert snapshot(tmp_path)==before
    for flag in ('admission_reserved','source_attested','source_coverage_verified',
        'activity_coverage_verified','fee_coverage_complete','fee_execution_links_verified',
        'balance_instants_verified','observation_windows_verified_quiet','holds_reconciled',
        'broker_account_reconciled','spendable_funds_verified','ledger_applied','ready_for_routing',
        'broker_connected','live_orders_enabled','money_movement_enabled','automatic_retry',
        'automatic_continuation','release_approved'):
        assert result[flag] is False


@pytest.mark.parametrize('change,code',[
    ({'pre_opening_observation':lambda:_fixture_observation(ACCOUNT,'999','0',EPOCH+98,EPOCH+99)},'GUARD_BALANCE_CHANGED'),
    ({'pre_opening_observation':lambda:_fixture_observation(ACCOUNT,'1000','0',EPOCH+99,EPOCH+100)},'GUARD_WINDOWS_NOT_ORDERED'),
    ({'pre_opening_observation':lambda:_fixture_observation(ACCOUNT,'1000','0',EPOCH-2,EPOCH-1)},'DECLARED_WINDOWS_DO_NOT_COVER_OBSERVATION_ENVELOPE'),
    ({'post_closing_observation':lambda:_fixture_observation(ACCOUNT,'1000','0',EPOCH+102.5,EPOCH+104)},'GUARD_WINDOWS_NOT_ORDERED'),
])
def test_stability_guard_rejects_changed_overlap_or_unaccepted_outer_scope(tmp_path,change,code):
    options=guarded(tmp_path);options.update({key:value() for key,value in change.items()})
    before=snapshot(tmp_path)
    with pytest.raises(ValueError,match=code):subject.review_with_stability_guards(**options)
    assert snapshot(tmp_path)==before


def test_stability_guard_rejects_manifest_reuse_and_declared_source_disagreement(tmp_path):
    base=guarded(tmp_path);options=copy.deepcopy(base)
    options['pre_opening_observation']=copy.deepcopy(options['opening_observation'])
    with pytest.raises(ValueError,match='GUARD_MANIFEST_REUSE'):
        subject.review_with_stability_guards(**options)
    options=copy.deepcopy(base);foreign=options['post_closing_observation']
    foreign['source']='PAPER_HTTPS_READS'
    timing={key:float(value) if '.' in value or 'e' in value else int(value)
        for key,value in foreign['read_timing'].items()}
    foreign['observation_sha256']=subject.sha(dict(profile='nexus.paper-account-observation.v1',
        account_fields_sha256=foreign['account_fields_sha256'],
        reported_inventory_sha256=foreign['reported_inventory_sha256'],
        position_count=foreign['position_count'],open_order_count=foreign['open_order_count'],
        source=foreign['source'],read_timing=timing))
    foreign['manifest_sha256']=subject.sha({k:v for k,v in foreign.items() if k!='manifest_sha256'})
    with pytest.raises(ValueError,match='GUARD_SOURCE_DISAGREEMENT'):
        subject.review_with_stability_guards(**options)


def test_stability_guard_rejects_activity_and_balance_declared_source_mismatch(tmp_path):
    options=guarded(tmp_path)
    for field in ('pre_opening_observation','opening_observation',
                  'closing_observation','post_closing_observation'):
        options[field]=relabel_source(options[field],'PAPER_HTTPS_READS')
    before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='GUARD_DECLARED_SOURCE_DISAGREEMENT'):
        subject.review_with_stability_guards(**options)
    assert snapshot(tmp_path)==before


def test_guard_activity_scans_are_inclusive_content_free_and_fee_date_conservative():
    windows={
        'pre_opening':{'started_at':'2026-10-17T00:00:00+00:00','completed_at':'2026-10-17T00:00:01+00:00'},
        'opening':{'started_at':'2026-10-17T00:00:02+00:00','completed_at':'2026-10-17T00:00:03+00:00'},
        'closing':{'started_at':'2026-10-17T00:00:04+00:00','completed_at':'2026-10-17T00:00:05+00:00'},
        'post_closing':{'started_at':'2026-10-17T00:00:06+00:00','completed_at':'2026-10-17T00:00:07+00:00'}}
    retained={'normalized_activities':[
        {'kind':'FILL','activity_id':'first-private-id','occurred_at':'2026-10-17T00:00:03+00:00'},
        {'kind':'FILL','activity_id':'second-private-id','occurred_at':'2026-10-17T00:00:04+00:00'},
        {'kind':'FEE','activity_id':'date-only-private-id','reported_date':'2026-10-17'},
        {'kind':'CFEE','activity_id':'timestamp-shaped-private-id','reported_date':'2026-10-17T23:00:00+00:00'},
        {'kind':'FEE','activity_id':'outside-private-id','reported_date':'2026-10-18'}]}
    assert subject._guard_fill_events(retained,windows)==['first-private-id','second-private-id']
    assert subject._guard_fee_date_events(retained,windows)==[
        'date-only-private-id','timestamp-shaped-private-id']


def test_collection_fill_scan_rejects_old_fills_read_during_or_touching_outer_envelope():
    windows={
        'pre_opening':{'started_at':'2026-10-17T00:00:00+00:00'},
        'post_closing':{'completed_at':'2026-10-17T00:00:07+00:00'}}
    retained={'normalized_activities':[
        {'kind':'FILL','activity_id':'old-private-fill','occurred_at':'2026-10-16T00:00:00+00:00'},
        {'kind':'FEE','activity_id':'private-fee','reported_date':'2026-10-17'}],
        'receipts':{
            'before':{'activity_ids':['old-private-fill'],
                'collection_observation':{'read_started_at':1792195198,'read_completed_at':1792195199}},
            'touching':{'activity_ids':['old-private-fill','private-fee'],
                'collection_observation':{'read_started_at':1792195207,'read_completed_at':1792195207}}}}
    assert subject._guard_collection_fill_events(retained,
        [{'retained_receipt_sha256':'before'}],windows)==[]
    assert subject._guard_collection_fill_events(retained,
        [{'retained_receipt_sha256':'touching'}],windows)==['old-private-fill']


def test_collection_fee_scan_rejects_old_fees_read_during_outer_envelope():
    windows={
        'pre_opening':{'started_at':'2026-10-17T00:00:00+00:00'},
        'post_closing':{'completed_at':'2026-10-17T00:00:07+00:00'}}
    retained={'normalized_activities':[
        {'kind':'FEE','activity_id':'old-private-fee','reported_date':'2026-10-01'},
        {'kind':'CFEE','activity_id':'old-private-crypto-fee','reported_date':'2026-10-01'}],
        'receipts':{
            'outside':{'activity_ids':['old-private-fee'],
                'collection_observation':{'read_started_at':1792195198,'read_completed_at':1792195199}},
            'inside':{'activity_ids':['old-private-fee','old-private-crypto-fee'],
                'collection_observation':{'read_started_at':1792195201,'read_completed_at':1792195202}}}}
    assert subject._guard_collection_fee_events(retained,
        [{'retained_receipt_sha256':'outside'}],windows)==[]
    assert subject._guard_collection_fee_events(retained,
        [{'retained_receipt_sha256':'inside'}],windows)==[
            'old-private-crypto-fee','old-private-fee']


def test_stability_guard_rejects_fill_collected_during_outer_envelope_without_writes(tmp_path,monkeypatch):
    options=guarded(tmp_path);before=snapshot(tmp_path)
    monkeypatch.setattr(subject,'_guard_collection_fill_events',
        lambda retained,scopes,windows:['private-fill-id'])
    with pytest.raises(ValueError,match='GUARD_COLLECTION_FILL_PRESENT'):
        subject.review_with_stability_guards(**options)
    assert snapshot(tmp_path)==before


def test_stability_guard_rejects_fee_collected_during_outer_envelope_without_writes(tmp_path,monkeypatch):
    options=guarded(tmp_path);before=snapshot(tmp_path)
    monkeypatch.setattr(subject,'_guard_collection_fee_events',
        lambda retained,scopes,windows:['private-fee-id'])
    with pytest.raises(ValueError,match='GUARD_COLLECTION_FEE_PRESENT'):
        subject.review_with_stability_guards(**options)
    assert snapshot(tmp_path)==before


def test_fee_date_scan_covers_multiday_inner_comparison_not_only_edge_bands():
    windows={
        'pre_opening':{'started_at':'2026-10-17T23:59:50+00:00','completed_at':'2026-10-17T23:59:51+00:00'},
        'opening':{'started_at':'2026-10-17T23:59:52+00:00','completed_at':'2026-10-17T23:59:53+00:00'},
        'closing':{'started_at':'2026-10-20T00:00:04+00:00','completed_at':'2026-10-20T00:00:05+00:00'},
        'post_closing':{'started_at':'2026-10-20T00:00:06+00:00','completed_at':'2026-10-20T00:00:07+00:00'}}
    retained={'normalized_activities':[
        {'kind':'FEE','activity_id':'inner-only-private-id','reported_date':'2026-10-18'},
        {'kind':'CFEE','activity_id':'later-inner-private-id','reported_date':'2026-10-19T18:00:00+00:00'},
        {'kind':'FEE','activity_id':'before-private-id','reported_date':'2026-10-16'},
        {'kind':'FEE','activity_id':'after-private-id','reported_date':'2026-10-21'}]}
    assert subject._guard_fee_date_events(retained,windows)==[
        'inner-only-private-id','later-inner-private-id']


def test_stability_guard_rejects_retained_fee_date_overlap_without_writes(tmp_path,monkeypatch):
    options=guarded(tmp_path);before=snapshot(tmp_path)
    monkeypatch.setattr(subject,'_guard_fee_date_events',lambda retained,windows:['private-fee-id'])
    with pytest.raises(ValueError,match='GUARD_RETAINED_FEE_TIMING_AMBIGUOUS'):
        subject.review_with_stability_guards(**options)
    assert snapshot(tmp_path)==before


def test_unbound_or_missing_acceptance_history_fails_before_balance_review(tmp_path, monkeypatch):
    _, activity, _, _ = _scope(tmp_path)
    opening = _fixture_observation(ACCOUNT,'1000','0',1800000000,1800000001)
    called=False
    def forbidden(*args,**kwargs):
        nonlocal called;called=True;raise AssertionError
    monkeypatch.setattr(subject.paper_observation_review,'review',forbidden)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
        subject.review(expected_account_id=ACCOUNT,pacing_path=tmp_path/'pacing.sqlite3',
            activity_store_path=tmp_path/'activity.sqlite3',
            acceptance_store_path=tmp_path/'acceptance.sqlite3',
            expected_activity_head=activity.inspect()['head_sha256'],
            opening_observation=opening,closing_observation=opening)
    assert called is False


def test_wrong_explicit_activity_head_is_rejected(tmp_path):
    options=prepared(tmp_path);options['expected_activity_head']='0'*64
    with pytest.raises(ValueError,match='ACTIVITY_HEAD_DISAGREEMENT'):
        subject.review(**options)


def test_any_journal_change_during_composition_is_rejected(tmp_path,monkeypatch):
    options=prepared(tmp_path);original=subject.acceptance_status;calls=0
    def changed(**kwargs):
        nonlocal calls
        value=copy.deepcopy(original(**kwargs));calls+=1
        if calls==2:value['acceptance_head_sha256']='f'*64
        return value
    monkeypatch.setattr(subject,'acceptance_status',changed)
    with pytest.raises(ValueError,match='JOURNALS_CHANGED_DURING_REVIEW'):
        subject.review(**options)


def test_same_head_sqlite_file_rewrite_during_review_is_rejected(tmp_path,monkeypatch):
    options=prepared(tmp_path);original=subject.paper_observation_review.review
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        with sqlite3.connect(options['acceptance_store_path']) as db:
            db.execute('PRAGMA user_version=1')
        return result
    monkeypatch.setattr(subject.paper_observation_review,'review',changed)
    with pytest.raises(ValueError,match='FILE_SET_CHANGED'):
        subject.review(**options)


def _mutate_metadata_and_fail(path):
    stat=os.stat(path)
    os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
    raise RuntimeError('injected semantic reader failure')


@pytest.mark.parametrize('reader',('initial_status','observation_review'))
def test_reconciliation_rechecks_journals_when_semantic_reader_fails(
        tmp_path,monkeypatch,reader):
    options=prepared(tmp_path);before=snapshot(tmp_path)
    if reader=='initial_status':
        original=subject.acceptance_status
        def failed_status(**kwargs):
            original(**kwargs)
            _mutate_metadata_and_fail(options['acceptance_store_path'])
        monkeypatch.setattr(subject,'acceptance_status',failed_status)
    else:
        original=subject.paper_observation_review.review
        def failed_review(*args,**kwargs):
            original(*args,**kwargs)
            _mutate_metadata_and_fail(options['acceptance_store_path'])
        monkeypatch.setattr(subject.paper_observation_review,'review',failed_review)
    with pytest.raises(ValueError,match='PAPER_RECONCILIATION_ACCEPTANCE_FILE_SET_CHANGED'):
        subject.review(**options)
    assert snapshot(tmp_path)==before


def test_stability_review_rechecks_journals_when_final_status_reader_fails(
        tmp_path,monkeypatch):
    options=guarded(tmp_path);before=snapshot(tmp_path)
    original=subject.acceptance_status;calls=0
    def failed_status(**kwargs):
        nonlocal calls
        value=original(**kwargs);calls+=1
        if calls==4:
            _mutate_metadata_and_fail(options['acceptance_store_path'])
        return value
    monkeypatch.setattr(subject,'acceptance_status',failed_status)
    with pytest.raises(ValueError,match='PAPER_RECONCILIATION_ACCEPTANCE_FILE_SET_CHANGED'):
        subject.review_with_stability_guards(**options)
    assert calls==4
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('field',(
    'pacing_path','activity_store_path','acceptance_store_path'))
def test_same_byte_journal_replacement_hidden_by_semantic_failure_is_rejected(
        tmp_path,monkeypatch,field):
    options=prepared(tmp_path);before=snapshot(tmp_path)
    path=options[field]
    def replaced_then_failed(*args,**kwargs):
        replacement=path.with_name(path.name+'.replacement')
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        raise RuntimeError('injected lower semantic failure')
    monkeypatch.setattr(subject,'_declared_window_coverage',replaced_then_failed)
    with pytest.raises(ValueError,
            match='PAPER_RECONCILIATION_ACCEPTANCE_FILE_IDENTITY_CHANGED'):
        subject.review(**options)
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('constructor',(
    'PaperReadPacing','BrokerActivityStore','PaperActivityAcceptanceStore'))
def test_reconciliation_rejects_retargeted_store_objects(
        tmp_path,monkeypatch,constructor):
    options=prepared(tmp_path);before=snapshot(tmp_path)
    original=getattr(subject,constructor)
    def retargeted(path,account):
        value=original(path,account)
        value.path=tmp_path/'retargeted.sqlite3'
        return value
    monkeypatch.setattr(subject,constructor,retargeted)
    with pytest.raises(ValueError,
            match='PAPER_RECONCILIATION_ACCEPTANCE_STORE_BINDING_CHANGED'):
        subject.review(**options)
    assert snapshot(tmp_path)==before


def test_reconciliation_refuses_rollback_sidecar_before_arithmetic(tmp_path,monkeypatch):
    options=prepared(tmp_path)
    sidecar=options['activity_store_path'].with_name(
        options['activity_store_path'].name+'-journal')
    sidecar.write_bytes(b'retained rollback evidence')
    monkeypatch.setattr(subject.paper_observation_review,'review',
        lambda *a,**k:pytest.fail('sidecar reached balance arithmetic'))
    with pytest.raises(ValueError,match='SQLITE_SIDECAR_REVIEW_REQUIRED'):
        subject.review(**options)


@pytest.mark.parametrize('field,value',[('cross_journal_recovery_state','ACCEPTED_HISTORY_CONTENT_DISAGREEMENT_REVIEW_REQUIRED'),
    ('latest_acceptance_record_replayed',False),('all_accepted_transitions_replayed',False),
    ('accepted_transitions',0),('preexisting_history_unverified',True),('sessions',2),
    ('retained_batches',2),('retained_collection_observations',2),
    ('all_collection_cursor_sequences_complete',False)])
def test_every_accepted_history_requirement_fails_closed(tmp_path,monkeypatch,field,value):
    options=prepared(tmp_path);actual=subject.acceptance_status(**dict(
        expected_account_id=options['expected_account_id'],pacing_path=options['pacing_path'],
        activity_store_path=options['activity_store_path'],acceptance_store_path=options['acceptance_store_path']))
    actual[field]=value
    monkeypatch.setattr(subject,'acceptance_status',lambda **kwargs:actual)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
        subject.review(**options)


def test_accepted_tail_cannot_hide_unverified_legacy_baseline(tmp_path, monkeypatch):
    pacing,activity,_,_= _scope(tmp_path)
    (tmp_path/'acceptance.sqlite3').unlink()
    acceptance=PaperActivityAcceptanceStore(tmp_path/'acceptance.sqlite3',ACCOUNT)
    acceptance.bind(pacing_head=pacing.inspect()['head_sha256'],
        retention_head=activity.inspect()['head_sha256'],pacing_sessions=1,retained_batches=1)
    pacing,activity,acceptance,scope=_scope(tmp_path,2)
    acceptance.append(scope,admission=lambda:(pacing.inspect()['head_sha256'],
        activity.inspect()['head_sha256']))
    state=subject.acceptance_status(expected_account_id=ACCOUNT,
        pacing_path=tmp_path/'pacing.sqlite3',activity_store_path=tmp_path/'activity.sqlite3',
        acceptance_store_path=tmp_path/'acceptance.sqlite3')
    assert state['preexisting_history_unverified'] is True
    assert state['accepted_transitions']==1 and state['sessions']==state['retained_batches']==2
    assert state['all_accepted_transitions_replayed'] is True
    assert state['cross_journal_recovery_state']=='LEGACY_BASELINE_ONLY_REVIEW_REQUIRED'
    opening=_fixture_observation(ACCOUNT,'1000','0',1800000000,1800000001)
    called=False
    def forbidden(*args,**kwargs):
        nonlocal called;called=True;raise AssertionError
    monkeypatch.setattr(subject.paper_observation_review,'review',forbidden)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
        subject.review(expected_account_id=ACCOUNT,pacing_path=tmp_path/'pacing.sqlite3',
            activity_store_path=tmp_path/'activity.sqlite3',
            acceptance_store_path=tmp_path/'acceptance.sqlite3',
            expected_activity_head=activity.inspect()['head_sha256'],
            opening_observation=opening,closing_observation=opening)
    assert called is False


def test_cursor_incomplete_accepted_history_fails_before_balance_review(tmp_path,monkeypatch):
    pacing,activity,acceptance,scope=_scope(tmp_path,full_page=True)
    acceptance.append(scope,admission=lambda:(pacing.inspect()['head_sha256'],
        activity.inspect()['head_sha256']))
    state=subject.acceptance_status(expected_account_id=ACCOUNT,
        pacing_path=tmp_path/'pacing.sqlite3',activity_store_path=tmp_path/'activity.sqlite3',
        acceptance_store_path=tmp_path/'acceptance.sqlite3')
    assert state['all_accepted_transitions_replayed'] is True
    assert state['cross_journal_recovery_state']=='ACCEPTED_COLLECTION_CONTINUATION_REQUIRED'
    called=False
    def forbidden(*args,**kwargs):
        nonlocal called;called=True;raise AssertionError
    monkeypatch.setattr(subject.paper_observation_review,'review',forbidden)
    opening=_fixture_observation(ACCOUNT,'1000','0',1800000000,1800000001)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
        subject.review(expected_account_id=ACCOUNT,pacing_path=tmp_path/'pacing.sqlite3',
            activity_store_path=tmp_path/'activity.sqlite3',
            acceptance_store_path=tmp_path/'acceptance.sqlite3',
            expected_activity_head=activity.inspect()['head_sha256'],
            opening_observation=opening,closing_observation=opening)
    assert called is False


def test_cli_prints_private_review_but_never_writes_journals(tmp_path):
    options=prepared(tmp_path);opening=tmp_path/'opening.json';closing=tmp_path/'closing.json'
    opening.write_text(json.dumps(options['opening_observation']));closing.write_text(json.dumps(options['closing_observation']))
    before=snapshot(tmp_path)
    command=[sys.executable,'-m','nexus.paper_reconciliation_acceptance',
        '--expected-account',ACCOUNT,'--pacing',str(options['pacing_path']),
        '--activity-store',str(options['activity_store_path']),
        '--acceptance-store',str(options['acceptance_store_path']),
        '--activity-head',options['expected_activity_head'],'--opening',str(opening),'--closing',str(closing)]
    child=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert child.returncode==0 and json.loads(child.stdout)['complete_accepted_history_replayed'] is True
    assert snapshot(tmp_path)==before


def test_cli_stability_guards_are_paired_and_never_write_journals(tmp_path):
    options=guarded(tmp_path);files={}
    for name in ('pre_opening_observation','opening_observation','closing_observation','post_closing_observation'):
        path=tmp_path/(name+'.json');path.write_text(json.dumps(options[name]));files[name]=path
    common=[sys.executable,'-m','nexus.paper_reconciliation_acceptance',
        '--expected-account',ACCOUNT,'--pacing',str(options['pacing_path']),
        '--activity-store',str(options['activity_store_path']),
        '--acceptance-store',str(options['acceptance_store_path']),
        '--activity-head',options['expected_activity_head'],
        '--opening',str(files['opening_observation']),'--closing',str(files['closing_observation'])]
    before=snapshot(tmp_path)
    child=subprocess.run(common+['--pre-opening',str(files['pre_opening_observation']),
        '--post-closing',str(files['post_closing_observation'])],capture_output=True,text=True,timeout=20)
    assert child.returncode==0
    assert json.loads(child.stdout)['mode']=='PAPER_RECONCILIATION_STABILITY_GUARD_REVIEW'
    assert snapshot(tmp_path)==before
    unpaired=subprocess.run(common+['--pre-opening',str(files['pre_opening_observation'])],
        capture_output=True,text=True,timeout=20)
    assert unpaired.returncode==2 and unpaired.stdout=='PAPER_RECONCILIATION_ACCEPTANCE_FAILED\n'
    assert snapshot(tmp_path)==before


def test_release_fixture_is_repeatable_vault_and_provider_silent():
    result=subject.fixture_report()
    assert result['passed'] and result['repeat_review_matches'] and result['journals_unchanged']
    assert result['unbound_history_rejected'] and result['private_scope_omitted']
    assert result['continuation_required_history_rejected']
    assert result['exact_activity_head_bound'] and result['complete_accepted_history_replayed']
    assert result['legacy_baseline_absent'] and result['complete_pacing_sessions_accepted']
    assert result['accepted_pacing_operations_typed']
    assert result['complete_retained_batches_accepted']
    assert result['complete_collection_observations_accepted']
    assert result['all_collection_cursor_sequences_complete']
    assert result['out_of_window_observations_rejected']
    assert result['declared_windows_cover_observation_envelope']
    assert result['requested_window_boundaries_verified'] is False
    assert result['observation_arithmetic_evaluated'] and 'review' not in result
    assert result['stability_guard_bound'] and result['stability_guard_repeat_matches']
    assert result['journal_file_fingerprints_rechecked']
    assert result['journal_file_identities_rechecked']
    assert result['journal_store_bindings_rechecked']
    assert result['identity_replacement_failure_rejected']
    assert result['store_retarget_rejected']
    assert result['changed_guard_balance_rejected'] and result['unordered_guard_windows_rejected']
    assert result['outer_guard_window_coverage_rejected']
    assert result['guard_retained_fill_events_absent']
    assert result['guard_collection_observed_fill_events_absent']
    assert result['late_collected_historical_fill_rejected']
    assert result['guard_collection_observed_fee_events_absent']
    assert result['late_collected_historical_fee_rejected']
    assert result['guard_balance_instants_verified'] is False
    assert result['guard_observation_windows_verified_quiet'] is False
    assert result['actual_provider_access'] is result['actual_vault_access'] is False


@pytest.mark.parametrize('times', [(-10,-9,-8,-7),(86400,86401,86402,86403),
    (-1,1,2,3),(86397,86398,86399,86401)])
def test_outside_or_partial_declared_envelope_fails_before_arithmetic(tmp_path, monkeypatch, times):
    options=prepared(tmp_path)
    options['opening_observation']=_fixture_observation(ACCOUNT,'1000','0',EPOCH+times[0],EPOCH+times[1])
    options['closing_observation']=_fixture_observation(ACCOUNT,'1000','0',EPOCH+times[2],EPOCH+times[3])
    monkeypatch.setattr(subject.paper_observation_review,'review',lambda *a,**k:pytest.fail('scope must be checked before arithmetic'))
    before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='DECLARED_WINDOWS_DO_NOT_COVER_OBSERVATION_ENVELOPE'):
        subject.review(**options)
    assert snapshot(tmp_path)==before


def coverage(windows, start='1792195201', end='1792281599'):
    receipts={str(i):dict(after=a,until=b) for i,(a,b) in enumerate(windows)}
    scopes=[dict(retained_receipt_sha256=str(i)) for i in range(len(windows))]
    first=dict(read_timing=dict(started_at=start,completed_at=start))
    last=dict(read_timing=dict(started_at=end,completed_at=end))
    return subject._declared_window_coverage(receipts,scopes,first,last)


def test_adjacent_overlapping_duplicate_windows_union_without_false_gaps():
    full=[('2026-10-17T00:00:00Z','2026-10-18T00:00:00Z')]
    adjacent=[('2026-10-17T00:00:00Z','2026-10-17T12:00:00Z'),
        ('2026-10-17T12:00:00Z','2026-10-18T00:00:00Z')]
    overlapping=adjacent+[('2026-10-17T10:00:00Z','2026-10-17T14:00:00Z')]
    assert coverage(full)==coverage(adjacent)==coverage(overlapping)==coverage(list(reversed(overlapping))*2)


def test_outer_hull_does_not_hide_an_interior_gap():
    with pytest.raises(ValueError,match='DECLARED_WINDOWS_DO_NOT_COVER_OBSERVATION_ENVELOPE'):
        coverage([('2026-10-17T00:00:00Z','2026-10-17T12:00:00Z'),
            ('2026-10-17T12:00:01Z','2026-10-18T00:00:00Z')])


def test_declared_geometry_keeps_exact_nanoseconds_and_boundary_authority_unverified():
    windows=[('2026-10-17T00:00:00Z','2026-10-18T00:00:00Z')]
    assert coverage(windows,'1792195200','1792281600')
    for start,end in [('1792195199.999999999','1792281599'),('1792195201','1792281600.000000001')]:
        with pytest.raises(ValueError,match='DECLARED_WINDOWS_DO_NOT_COVER_OBSERVATION_ENVELOPE'):
            coverage(windows,start,end)


def test_missing_accepted_receipt_cannot_be_replaced_with_an_unaccepted_window():
    observation=dict(read_timing=dict(started_at='1792195201',completed_at='1792195201'))
    closing=dict(read_timing=dict(started_at='1792195202',completed_at='1792195202'))
    with pytest.raises(ValueError,match='ACCEPTED_RECEIPT_CHANGED'):
        subject._declared_window_coverage({'other':dict(after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')},
            [dict(retained_receipt_sha256='accepted')],observation,closing)
