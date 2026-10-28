"""Refuse conflicting private storage before vault, SQLite or provider work."""
import json
import shutil
from pathlib import Path

import pytest
from nexus import sqlite_journal_guard as guard
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.broker_activity_store import BrokerActivityStore
from nexus.paper_read_pacing import PaperReadPacing
from test_alpaca_paper_inspection import ACCOUNT,KEY,SECRET

SUFFIXES=('-journal','-wal','-shm','.owner.lock')


@pytest.mark.parametrize('suffix',SUFFIXES)
@pytest.mark.parametrize('reverse',[False,True])
def test_related_files_are_rejected_in_both_selection_orders_without_creating_parent(tmp_path,suffix,reverse):
    root=tmp_path/'missing';base=root/'journal.sqlite3';other=base.with_name(base.name+suffix)
    with pytest.raises(ValueError,match='TEST_RELATED_PATHS_REQUIRED_DISTINCT'):
        guard.check_path_set((other,base) if reverse else (base,other),'TEST_')
    assert not root.exists()


@pytest.mark.parametrize('kind',['same','dot_alias','ancestor','sidecar_ancestor'])
def test_alias_and_file_directory_conflicts_fail_with_fixed_code(tmp_path,kind):
    base=tmp_path/'first.sqlite3'
    other={'same':base,'dot_alias':tmp_path/'missing'/'..'/'first.sqlite3',
        'ancestor':base/'child.sqlite3','sidecar_ancestor':base.with_name(base.name+'-journal')/'child.sqlite3'}[kind]
    with pytest.raises(ValueError,match='TEST_RELATED_PATHS_REQUIRED_DISTINCT'):guard.check_path_set((base,other),'TEST_')
    assert not list(tmp_path.iterdir())


def test_distinct_missing_paths_are_read_only_and_suffixes_alone_are_allowed(tmp_path):
    paths=(tmp_path/'request-journal',tmp_path/'unrelated-wal',tmp_path/'nested'/'quote.sqlite3')
    assert guard.check_path_set(paths,'TEST_')==tuple(p.resolve() for p in paths)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('paths',[[],['one'],['one']*9,None,iter(['one','two'])],ids=['empty','one','over-cap','null','iterator'])
def test_path_collection_must_be_bounded_before_resolution(paths):
    with pytest.raises(ValueError,match='TEST_RELATED_PATHS_REQUIRED_DISTINCT'):guard.check_path_set(paths,'TEST_')


def test_sampled_file_identity_rejects_byte_identical_path_replacement(
        tmp_path):
    path=tmp_path/'journal.sqlite3';path.write_bytes(b'private fixture')
    expected=guard.capture_file_identities((path,),'TEST_')
    backup=tmp_path/'original.sqlite3';path.replace(backup)
    shutil.copyfile(backup,path)
    assert path.read_bytes()==backup.read_bytes()
    with pytest.raises(ValueError,match='TEST_FILE_IDENTITY_CHANGED'):
        guard.require_file_identities((path,),expected,'TEST_')


def test_bounded_file_fingerprint_rejects_same_identity_byte_change_and_sidecar(tmp_path):
    path=tmp_path/'journal.sqlite3';path.write_bytes(b'private fixture')
    expected=guard.capture_file_fingerprints((path,),(1024,),'TEST_')
    path.write_bytes(b'changed fixture')
    with pytest.raises(ValueError,match='TEST_FILE_SET_CHANGED'):
        guard.require_file_fingerprints((path,),(1024,),expected,'TEST_')
    path.with_name(path.name+'-journal').write_bytes(b'retained rollback')
    with pytest.raises(ValueError,match='TEST_SQLITE_SIDECAR_REVIEW_REQUIRED'):
        guard.capture_file_fingerprints((path,),(1024,),'TEST_')


def test_bounded_file_fingerprint_scope_rejects_more_than_thirty_two_files(
        tmp_path):
    path=tmp_path/'journal.sqlite3';path.write_bytes(b'private fixture')
    with pytest.raises(ValueError,match='TEST_FILE_FINGERPRINT_SCOPE_INVALID'):
        guard.capture_file_fingerprints((path,)*33,(1024,)*33,'TEST_')


@pytest.mark.parametrize('operation',['recovery','collection'])
@pytest.mark.parametrize('suffix',SUFFIXES)
def test_saved_read_wrappers_refuse_collisions_before_accessing_credentials(tmp_path,monkeypatch,operation,suffix):
    from nexus import paper_credentials as credentials
    def denied(*args,**kwargs):pytest.fail('conflicting paths reached saved credentials')
    monkeypatch.setattr(credentials,'_capture',denied)
    base=tmp_path/'private'/'source.sqlite3';pacing=PaperReadPacing(base.with_name(base.name+suffix),ACCOUNT)
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
        if operation=='recovery':credentials.recover_saved(BrokerRequestJournal(base,ACCOUNT),'nx_original',expected_account_id=ACCOUNT,
            confirm_read_only_paper=True,pacing=pacing)
        else:credentials.collect_saved([],expected_account_id=ACCOUNT,after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',
            confirm_read_only_paper=True,pacing=pacing,activity_store=BrokerActivityStore(base,ACCOUNT))
    assert not base.parent.exists()


@pytest.mark.parametrize('operation',['recovery','collection','recovery_review'])
@pytest.mark.parametrize('suffix',SUFFIXES)
def test_direct_reader_and_recovery_review_preflight_precedes_session_or_journal_replay(tmp_path,monkeypatch,operation,suffix):
    from nexus import alpaca_paper_inspection as reader,broker_paper_recovery as recovery
    from nexus import alpaca_activity_collection as collection,paper_recovery_step as step
    def denied(*args,**kwargs):pytest.fail('conflicting paths reached transport or journal replay')
    monkeypatch.setattr(reader,'_ReadSession',denied);monkeypatch.setattr(BrokerRequestJournal,'lookup_review',denied)
    base=tmp_path/'missing'/'source.sqlite3';pacing=PaperReadPacing(base.with_name(base.name+suffix),ACCOUNT)
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
        if operation=='recovery':recovery.recover_original(BrokerRequestJournal(base,ACCOUNT),'nx_original',KEY,SECRET,
            expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=pacing)
        elif operation=='recovery_review':step.review_next(BrokerRequestJournal(base,ACCOUNT),expected_account_id=ACCOUNT,evaluated_at=1000,pacing=pacing)
        else:collection.collect_activities(KEY,SECRET,[],expected_account_id=ACCOUNT,after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',
            confirm_read_only_paper=True,pacing=pacing,activity_store=BrokerActivityStore(base,ACCOUNT))
    assert not base.parent.exists()


@pytest.mark.parametrize('operation',['activity_probe','activity_status','request_review','account_review','quality_review'])
@pytest.mark.parametrize('suffix',SUFFIXES)
def test_composed_acceptance_and_offline_reviews_refuse_before_any_journal_read(tmp_path,monkeypatch,operation,suffix):
    from nexus import paper_activity_acceptance_probe as activity,paper_request_activity_acceptance as requests
    from nexus import paper_account_evidence_review as account,paper_execution_quality_review as quality
    def denied(*args,**kwargs):pytest.fail('conflicting paths reached journal replay')
    monkeypatch.setattr(PaperReadPacing,'inspect',denied)
    root=tmp_path/'missing';request=root/'request.sqlite3';pacing=request.with_name(request.name+suffix)
    common=dict(expected_account_id=ACCOUNT,request_journal_path=request,pacing_path=pacing,
        activity_store_path=root/'activity.sqlite3',acceptance_store_path=root/'acceptance.sqlite3')
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
        if operation=='activity_probe':activity.probe(**common,expected_request_head='0'*64,after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',confirm_read_only_paper=True)
        elif operation=='activity_status':activity.status(**common)
        elif operation=='request_review':requests.review(**common,expected_request_head='0'*64,expected_activity_head='0'*64)
        elif operation=='account_review':account.review_current(**common,opening_observation={},closing_observation={})
        else:quality.review(**common,expected_request_head='0'*64,expected_activity_head='0'*64,client_order_id='nx_original',benchmark={})
    assert not root.exists()


def test_activity_three_journal_set_is_checked_without_optional_request_path(tmp_path,monkeypatch):
    from nexus import paper_activity_acceptance_probe as activity
    base=tmp_path/'missing'/'activity.sqlite3'
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):activity.status(expected_account_id=ACCOUNT,
        pacing_path=tmp_path/'missing'/'pacing.sqlite3',activity_store_path=base,acceptance_store_path=base.with_name(base.name+'-journal'))
    assert not base.parent.exists()


def test_quote_capture_reuses_guard_and_rejects_selected_ancestor_directory(tmp_path):
    from nexus import paper_quote_capture as capture
    base=tmp_path/'missing'/'request.sqlite3'
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):capture.status(expected_account_id=ACCOUNT,
        request_journal_path=base,quote_store_path=base/'quote.sqlite3',pacing_path=tmp_path/'missing'/'pacing.sqlite3',expected_request_head='0'*64)
    assert not base.parent.exists()


def test_fixture_exposes_only_aggregate_checks_and_false_authority():
    result=guard.path_fixture_report()
    assert set(result)=={'source','passed',*guard.PATH_CHECKS,*guard.PATH_FLAGS}
    assert result['passed'] and all(result[k] is True for k in guard.PATH_CHECKS)
    assert all(result[k] is False for k in guard.PATH_FLAGS)


@pytest.mark.parametrize('field',guard.PATH_CHECKS)
def test_release_requires_every_path_preflight_check(field):
    from nexus import release_trials,release_roadmap
    result=json.loads((Path(__file__).resolve().parents[1]/'validation'/
        ('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['pacing']
    result['path_preflight'][field]=False
    with pytest.raises(ValueError,match='JOURNAL_PATH_PREFLIGHT'):release_trials._validate('pacing',result)


@pytest.mark.parametrize('field',guard.PATH_FLAGS)
def test_path_fixture_cannot_attest_ownership_or_trading_authority(field):
    from nexus import release_trials,release_roadmap
    result=json.loads((Path(__file__).resolve().parents[1]/'validation'/
        ('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['pacing']
    result['path_preflight'][field]=True
    with pytest.raises(ValueError,match='JOURNAL_PATH_AUTHORITY'):release_trials._validate('pacing',result)


def test_fingerprint_windows_boundary_preserves_identity_but_separates_creation_time_apis():
    from types import SimpleNamespace
    fields=dict(st_dev=1,st_ino=2,st_size=3,st_mtime_ns=4,st_ctime_ns=5,st_nlink=1)
    path=SimpleNamespace(**fields);handle=SimpleNamespace(**dict(fields,st_ctime_ns=6))
    assert guard._fingerprint_boundary(path,host='nt')==guard._fingerprint_boundary(handle,host='nt')
    assert guard._fingerprint_boundary(path,host='posix')!=guard._fingerprint_boundary(handle,host='posix')
    for name in ('st_dev','st_ino','st_size','st_mtime_ns','st_nlink'):
        changed=SimpleNamespace(**dict(fields,**{name:fields[name]+1}))
        assert guard._fingerprint_boundary(path,host='nt')!=guard._fingerprint_boundary(changed,host='nt')


@pytest.mark.parametrize('change',['stable','handle_changes','path_changes'])
def test_windows_fingerprint_retains_within_api_ctime_fences(tmp_path,monkeypatch,change):
    import os
    from types import SimpleNamespace
    path=tmp_path/'private.sqlite3';path.write_bytes(b'fixture')
    boundary=guard._fingerprint_boundary
    monkeypatch.setattr(guard,'_fingerprint_boundary',lambda item:boundary(item,host='nt'))
    original=os.fstat;handle_calls=[]
    def fstat(fd):
        value=original(fd);handle_calls.append(1)
        return SimpleNamespace(**{key:getattr(value,key) for key in
            ('st_dev','st_ino','st_size','st_mtime_ns','st_nlink','st_mode')},
            st_ctime_ns=value.st_ctime_ns+1_000_000_000+(len(handle_calls) if change=='handle_changes' else 0))
    monkeypatch.setattr(guard.os,'fstat',fstat)
    lstat=Path.lstat;path_calls=[]
    def shifted(self,*args,**kwargs):
        value=lstat(self,*args,**kwargs)
        if self!=path:return value
        path_calls.append(1)
        return SimpleNamespace(**{key:getattr(value,key) for key in
            ('st_dev','st_ino','st_size','st_mtime_ns','st_nlink','st_mode')},
            st_ctime_ns=value.st_ctime_ns+(1 if change=='path_changes' and len(path_calls)>1 else 0))
    if change=='path_changes':monkeypatch.setattr(Path,'lstat',shifted)
    if change=='stable':
        expected=guard.capture_file_fingerprints((path,),(1024,),'TEST_')
        guard.require_file_fingerprints((path,),(1024,),expected,'TEST_')
    else:
        with pytest.raises(ValueError,match='FILE_FINGERPRINT_CHANGED'):
            guard.capture_file_fingerprints((path,),(1024,),'TEST_')
