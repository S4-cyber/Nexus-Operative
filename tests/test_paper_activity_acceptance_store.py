"""Durable cross-journal acceptance history stays private and fail closed."""
import os
import sqlite3
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.alpaca_paper_contract import sha
from nexus import paper_activity_acceptance_store as storage
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore, PROFILE, ZERO
from test_alpaca_paper_inspection import ACCOUNT, OTHER


def heads(seed):
    return [sha(dict(seed=seed,index=index)) for index in range(4)]


def scope(seed='one'):
    pace_before,pace_after,store_before,store_after=heads(seed)
    return dict(profile=PROFILE,account_sha256=hashlib.sha256(ACCOUNT.encode()).hexdigest(),
        collection_scope_sha256=sha(dict(seed=seed,kind='scope')),
        account_fields_sha256=sha(dict(seed=seed,kind='account')),
        collection_observation_sha256=sha(dict(seed=seed,kind='observation')),
        activity_input_sha256=sha(dict(seed=seed,kind='input')),
        retained_receipt_sha256=sha(dict(seed=seed,kind='receipt')),
        pacing_before_head_sha256=pace_before,pacing_after_head_sha256=pace_after,
        retention_before_head_sha256=store_before,retention_after_head_sha256=store_after)


def bound(tmp_path,value=None,*,sessions=0,batches=0):
    value=value or scope();store=PaperActivityAcceptanceStore(tmp_path/'private'/'acceptance.sqlite3',ACCOUNT)
    store.bind(pacing_head=value['pacing_before_head_sha256'],retention_head=value['retention_before_head_sha256'],
        pacing_sessions=sessions,retained_batches=batches)
    return store


def test_missing_inspection_is_read_only_and_baseline_retains_legacy_uncertainty(tmp_path):
    path=tmp_path/'missing'/'acceptance.sqlite3';store=PaperActivityAcceptanceStore(path,ACCOUNT)
    report=store.inspect();assert not report['initialized'] and not path.parent.exists()
    store=bound(tmp_path,sessions=2,batches=1);report=store.inspect()
    assert report['initialized'] and report['records']==0 and report['preexisting_history_unverified']
    assert report['baseline_pacing_sessions']==2 and report['baseline_retained_batches']==1
    heads_only=bound(tmp_path/'heads-only',sessions=0,batches=0).inspect()
    assert heads_only['preexisting_history_unverified']


def test_record_replay_binds_both_head_transitions_and_duplicate_is_idempotent(tmp_path):
    value=scope();store=bound(tmp_path,value)
    admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    first=store.append(value,admission=lambda:admitted);before=store.path.read_bytes()
    duplicate=store.append(value,admission=lambda:admitted)
    report=PaperActivityAcceptanceStore(store.path,ACCOUNT).inspect()
    assert not first['duplicate'] and duplicate['duplicate'] and before==store.path.read_bytes()
    assert report['records']==1 and report['last_record_sha256']==sha(value)
    assert report['last_record_scope']==value
    assert report['pacing_bound_head_sha256']==admitted[0] and report['retention_bound_head_sha256']==admitted[1]


def test_continuity_account_and_sampled_admission_fail_closed(tmp_path):
    value=scope();store=bound(tmp_path,value);admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    bad=dict(value,pacing_before_head_sha256=sha(dict(wrong='pace')))
    with pytest.raises(ValueError,match='CONTINUITY_DISAGREEMENT'):store.append(bad,admission=lambda:admitted)
    with pytest.raises(ValueError,match='ADMISSION_CHANGED'):store.append(value,admission=lambda:(ZERO,ZERO))
    foreign=dict(value,account_sha256=sha(OTHER))
    with pytest.raises(ValueError,match='SCOPE_INVALID'):store.append(foreign,admission=lambda:admitted)
    assert store.inspect()['records']==0


def test_lost_postcommit_sample_retains_record_for_replay(tmp_path):
    value=scope();store=bound(tmp_path,value);admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256']);calls=[]
    def changed():
        calls.append(1);return admitted if len(calls)==1 else (ZERO,ZERO)
    with pytest.raises(ValueError,match='ADMISSION_CHANGED'):store.append(value,admission=changed)
    assert store.inspect()['records']==1 and store.inspect()['last_record_sha256']==sha(value)


def test_chain_tamper_foreign_account_and_unsafe_paths_are_rejected(tmp_path):
    value=scope();store=bound(tmp_path,value)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE records SET previous=? WHERE seq=0",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):store.inspect()
    with pytest.raises(ValueError,match='ACCOUNT_OR_SCHEMA'):PaperActivityAcceptanceStore(bound(tmp_path/'foreign',value).path,OTHER).inspect()
    if os.name!='nt':
        target=tmp_path/'target.sqlite3';target.write_text('x');link=tmp_path/'link.sqlite3';link.symlink_to(target)
        with pytest.raises(ValueError,match='SYMLINK'):PaperActivityAcceptanceStore(link,ACCOUNT).inspect()


def test_distinct_second_transition_requires_exact_previous_heads(tmp_path):
    first=scope();store=bound(tmp_path,first)
    store.append(first,admission=lambda:(first['pacing_after_head_sha256'],first['retention_after_head_sha256']))
    second=scope('two');second['pacing_before_head_sha256']=first['pacing_after_head_sha256']
    second['retention_before_head_sha256']=first['retention_after_head_sha256']
    admitted=(second['pacing_after_head_sha256'],second['retention_after_head_sha256'])
    store.append(second,admission=lambda:admitted)
    report=store.inspect();assert report['records']==2 and report['last_record_sha256']==sha(second)
    assert report['last_record_scope']==second
    assert store.replay_scopes()==(first,second)
    assert 'record_scopes' not in report


@pytest.mark.parametrize('reader',('inspect','replay_scopes'))
@pytest.mark.parametrize(('seam','expected'),(
    ('connect','STORE_BINDING_CHANGED'),('load','STORE_BINDING_CHANGED'),
    ('fingerprint','FILE_SET_CHANGED'),
    ('connection','CONNECTION_BINDING_CHANGED'),
    ('connection-content','CONNECTION_CONTENT_CHANGED')))
def test_readers_pin_store_connection_and_exact_content(
        tmp_path,monkeypatch,reader,seam,expected):
    store=bound(tmp_path,scope('source'))
    foreign=bound(tmp_path/'foreign',scope('foreign'))
    original_path=store.path;original_connect=store._connect
    original_load=store._load
    before={path:path.read_bytes() for path in (store.path,foreign.path)}
    if seam=='connect':
        def retarget_connect(*args,**kwargs):
            store.path=foreign.path
            return original_connect(*args,**kwargs)
        monkeypatch.setattr(store,'_connect',retarget_connect)
    elif seam=='load':
        def retarget_load(db):
            result=original_load(db);store.path=foreign.path;return result
        monkeypatch.setattr(store,'_load',retarget_load)
    elif seam=='fingerprint':
        def mutate_fingerprint(db):
            result=original_load(db);state=store.path.stat()
            os.utime(store.path,ns=(state.st_atime_ns,
                state.st_mtime_ns+1_000_000_000))
            return result
        monkeypatch.setattr(store,'_load',mutate_fingerprint)
    elif seam=='connection':
        monkeypatch.setattr(store,'_connect',foreign._connect)
    else:
        class SpoofedConnection(sqlite3.Connection):
            def execute(self,statement,*args,**kwargs):
                if statement=='PRAGMA database_list':
                    return SimpleNamespace(fetchall=lambda:[
                        (0,'main',str(original_path.resolve()))])
                return super().execute(statement,*args,**kwargs)
        def substitute_content_connection(*args,**kwargs):
            db=sqlite3.connect(foreign.path.resolve().as_uri()+'?mode=ro',
                uri=True,timeout=10,factory=SpoofedConnection)
            return db,False
        monkeypatch.setattr(store,'_connect',substitute_content_connection)
    try:
        with pytest.raises(ValueError,match=(
                '^PAPER_ACTIVITY_ACCEPTANCE_STORE_'+expected+'$')):
            getattr(store,reader)()
    finally:store.path=original_path
    assert before=={path:path.read_bytes() for path in before}


@pytest.mark.parametrize('verdict',[False,1])
def test_optional_input_guard_rolls_back_new_record_before_commit(tmp_path,verdict):
    value=scope();store=bound(tmp_path,value);before=store.path.read_bytes()
    admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        store.append(value,admission=lambda:admitted,precommit_check=lambda:verdict)
    assert store.inspect()['records']==0 and store.path.read_bytes()==before


@pytest.mark.parametrize(('retarget_call','records'),((1,0),(2,1)))
def test_append_pins_store_binding_across_untrusted_admission(
        tmp_path,retarget_call,records):
    value=scope();store=bound(tmp_path,value)
    foreign=bound(tmp_path/'foreign',scope('foreign'))
    original_path=store.path;calls=0
    admitted=(value['pacing_after_head_sha256'],
        value['retention_after_head_sha256'])
    def retarget():
        nonlocal calls
        calls+=1
        if calls==retarget_call:store.path=foreign.path
        return admitted
    try:
        with pytest.raises(ValueError,match=(
                '^PAPER_ACTIVITY_ACCEPTANCE_STORE_STORE_BINDING_CHANGED$')):
            store.append(value,admission=retarget)
    finally:store.path=original_path
    report=store.inspect()
    assert report['records']==records
    if records:
        recovered=store.append(value,admission=lambda:admitted)
        assert recovered['duplicate'] is True
        assert recovered['head_sha256']==report['head_sha256']


def test_append_distrusts_insert_return_and_uses_exact_cold_read(
        tmp_path,monkeypatch):
    value=scope();store=bound(tmp_path,value)
    admitted=(value['pacing_after_head_sha256'],
        value['retention_after_head_sha256'])
    original=storage.PaperActivityAcceptanceStore._insert
    def fabricated(db,seq,body,previous):
        original(db,seq,body,previous)
        return 'f'*64
    monkeypatch.setattr(storage.PaperActivityAcceptanceStore,'_insert',
        staticmethod(fabricated))
    result=store.append(value,admission=lambda:admitted)
    inspected=store.inspect()
    assert result['head_sha256']==inspected['head_sha256']!='f'*64
    assert result['record_sha256']==sha(value)
    assert inspected['records']==1


def test_append_requires_two_stable_cold_acknowledgement_reads(
        tmp_path,monkeypatch):
    value=scope();store=bound(tmp_path,value)
    admitted=(value['pacing_after_head_sha256'],
        value['retention_after_head_sha256'])
    original=store._read_state;calls=0
    def unstable():
        nonlocal calls
        calls+=1;result=original()
        if calls==1:result=dict(result,head_sha256=ZERO)
        return result
    monkeypatch.setattr(store,'_read_state',unstable)
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_ACKNOWLEDGEMENT_UNCERTAIN$')):
        store.append(value,admission=lambda:admitted)
    report=store.inspect()
    assert report['records']==1
    recovered=store.append(value,admission=lambda:admitted)
    assert recovered['duplicate'] is True
    assert recovered['head_sha256']==report['head_sha256']


def test_bind_refuses_foreign_write_connection_before_mutation(
        tmp_path,monkeypatch):
    value=scope();foreign=bound(tmp_path/'foreign',value)
    target=storage.PaperActivityAcceptanceStore(
        tmp_path/'target'/'acceptance.sqlite3',ACCOUNT)
    before=foreign.path.read_bytes()
    monkeypatch.setattr(target,'_connect',foreign._connect)
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_CONNECTION_BINDING_CHANGED$')):
        target.bind(pacing_head=value['pacing_before_head_sha256'],
            retention_head=value['retention_before_head_sha256'],
            pacing_sessions=0,retained_batches=0)
    assert foreign.path.read_bytes()==before
    assert not target.path.exists()


def test_append_refuses_foreign_write_connection_before_mutation(
        tmp_path,monkeypatch):
    value=scope();target=bound(tmp_path/'target',value)
    foreign=bound(tmp_path/'foreign',value)
    before=(target.path.read_bytes(),foreign.path.read_bytes())
    monkeypatch.setattr(target,'_connect',foreign._connect)
    admitted=(value['pacing_after_head_sha256'],
        value['retention_after_head_sha256'])
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_CONNECTION_BINDING_CHANGED$')):
        target.append(value,admission=lambda:admitted)
    assert (target.path.read_bytes(),foreign.path.read_bytes())==before


def test_bind_fsync_uncertainty_retains_baseline_for_explicit_replay(
        tmp_path,monkeypatch):
    value=scope();store=storage.PaperActivityAcceptanceStore(
        tmp_path/'acceptance.sqlite3',ACCOUNT)
    original=storage.os.fsync
    monkeypatch.setattr(storage.os,'fsync',lambda _descriptor:(
        (_ for _ in ()).throw(OSError('injected'))))
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        store.bind(pacing_head=value['pacing_before_head_sha256'],
            retention_head=value['retention_before_head_sha256'],
            pacing_sessions=0,retained_batches=0)
    monkeypatch.setattr(storage.os,'fsync',original)
    retained=store.inspect();assert retained['initialized']
    replayed=store.bind(pacing_head=value['pacing_before_head_sha256'],
        retention_head=value['retention_before_head_sha256'],
        pacing_sessions=0,retained_batches=0)
    assert replayed['head_sha256']==retained['head_sha256']
    assert replayed['records']==0


def test_append_fsync_uncertainty_retains_one_for_explicit_duplicate(
        tmp_path,monkeypatch):
    value=scope();store=bound(tmp_path,value)
    admitted=(value['pacing_after_head_sha256'],
        value['retention_after_head_sha256'])
    original=storage.os.fsync
    monkeypatch.setattr(storage.os,'fsync',lambda _descriptor:(
        (_ for _ in ()).throw(OSError('injected'))))
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        store.append(value,admission=lambda:admitted)
    monkeypatch.setattr(storage.os,'fsync',original)
    retained=store.inspect();assert retained['records']==1
    replayed=store.append(value,admission=lambda:admitted)
    assert replayed['duplicate'] is True
    assert replayed['head_sha256']==retained['head_sha256']


def test_append_requires_stable_descriptor_bytes_across_fsync(
        tmp_path,monkeypatch):
    value=scope();store=bound(tmp_path,value)
    admitted=(value['pacing_after_head_sha256'],
        value['retention_after_head_sha256'])
    original=storage._descriptor_fingerprint;calls=0
    def unstable(descriptor,resolved):
        nonlocal calls
        calls+=1;result=original(descriptor,resolved)
        if calls==2:return (*result[:-1],'f'*64)
        return result
    monkeypatch.setattr(storage,'_descriptor_fingerprint',unstable)
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        store.append(value,admission=lambda:admitted)
    monkeypatch.setattr(storage,'_descriptor_fingerprint',original)
    retained=store.inspect();assert retained['records']==1
    replayed=store.append(value,admission=lambda:admitted)
    assert replayed['duplicate'] is True
    assert replayed['head_sha256']==retained['head_sha256']


def test_uncommitted_cleanup_never_deletes_replacement(tmp_path):
    path=tmp_path/'acceptance.sqlite3';path.touch(mode=0o600)
    identity=storage.capture_file_identities((path,),
        'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
    replacement=tmp_path/'replacement.sqlite3';replacement.touch(mode=0o600)
    os.replace(replacement,path)
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$')):
        storage._discard_uncommitted_new(path,identity)
    assert path.exists() and path.stat().st_size==0


@pytest.mark.parametrize('failure_sql',(None,'PRAGMA busy_timeout',
    'PRAGMA journal_mode','PRAGMA synchronous','PRAGMA page_size',
    'PRAGMA max_page_count','BEGIN IMMEDIATE','CREATE TABLE'))
def test_fresh_initialization_failure_restores_absence_and_exact_bind_retry(
        tmp_path,monkeypatch,failure_sql):
    value=scope();path=tmp_path/'acceptance.sqlite3'
    store=PaperActivityAcceptanceStore(path,ACCOUNT)
    target=path.resolve().as_uri();original=storage.sqlite3.connect;failed=False
    class Connection:
        def __init__(self,db):self.db=db
        def execute(self,sql,*args,**kwargs):
            nonlocal failed
            if not failed and sql.startswith(failure_sql):
                failed=True
                raise RuntimeError('private initialization failure')
            return self.db.execute(sql,*args,**kwargs)
        def close(self):return self.db.close()
        def __getattr__(self,name):return getattr(self.db,name)
    def connect(*args,**kwargs):
        nonlocal failed
        if not str(args[0]).startswith(target):
            return original(*args,**kwargs)
        if failure_sql is None and not failed:
            failed=True
            raise RuntimeError('private initialization failure')
        db=original(*args,**kwargs)
        return Connection(db) if not failed else db
    monkeypatch.setattr(storage.sqlite3,'connect',connect)
    with pytest.raises(RuntimeError,match='^private initialization failure$'):
        store.bind(pacing_head=value['pacing_before_head_sha256'],
            retention_head=value['retention_before_head_sha256'],
            pacing_sessions=0,retained_batches=0)
    assert failed and not path.exists()
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    report=store.bind(pacing_head=value['pacing_before_head_sha256'],
        retention_head=value['retention_before_head_sha256'],
        pacing_sessions=0,retained_batches=0)
    assert report['records']==0 and store.inspect()['initialized']


def test_fresh_transaction_failure_restores_absence_for_explicit_retry(
        tmp_path,monkeypatch):
    value=scope();path=tmp_path/'acceptance.sqlite3'
    store=PaperActivityAcceptanceStore(path,ACCOUNT);original=store._insert
    monkeypatch.setattr(store,'_insert',lambda *args,**kwargs:(
        (_ for _ in ()).throw(RuntimeError('private insert failure'))))
    with pytest.raises(RuntimeError,match='^private insert failure$'):
        store.bind(pacing_head=value['pacing_before_head_sha256'],
            retention_head=value['retention_before_head_sha256'],
            pacing_sessions=0,retained_batches=0)
    assert not path.exists()
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    monkeypatch.setattr(store,'_insert',original)
    report=store.bind(pacing_head=value['pacing_before_head_sha256'],
        retention_head=value['retention_before_head_sha256'],
        pacing_sessions=0,retained_batches=0)
    assert report['records']==0 and store.inspect()['initialized']


def test_unbound_append_failure_restores_absence_for_explicit_bind(tmp_path):
    value=scope();path=tmp_path/'acceptance.sqlite3'
    store=PaperActivityAcceptanceStore(path,ACCOUNT)
    admitted=(value['pacing_after_head_sha256'],
        value['retention_after_head_sha256'])
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_BINDING_MISSING$')):
        store.append(value,admission=lambda:admitted)
    assert not path.exists()
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    report=store.bind(pacing_head=value['pacing_before_head_sha256'],
        retention_head=value['retention_before_head_sha256'],
        pacing_sessions=0,retained_batches=0)
    assert report['records']==0 and store.inspect()['initialized']


def test_fresh_initialization_close_uncertainty_retains_path(
        tmp_path,monkeypatch):
    value=scope();path=tmp_path/'acceptance.sqlite3'
    store=PaperActivityAcceptanceStore(path,ACCOUNT)
    target=path.resolve().as_uri();original=storage.sqlite3.connect
    class Connection:
        def __init__(self,db):self.db=db
        def execute(self,sql,*args,**kwargs):
            if sql.startswith('PRAGMA busy_timeout'):
                raise RuntimeError('private initialization failure')
            return self.db.execute(sql,*args,**kwargs)
        def close(self):
            self.db.close()
            raise RuntimeError('private close acknowledgement failure')
    def connect(*args,**kwargs):
        db=original(*args,**kwargs)
        return Connection(db) if str(args[0]).startswith(target) else db
    monkeypatch.setattr(storage.sqlite3,'connect',connect)
    with pytest.raises(ValueError,match=(
            '^PAPER_ACTIVITY_ACCEPTANCE_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$')):
        store.bind(pacing_head=value['pacing_before_head_sha256'],
            retention_head=value['retention_before_head_sha256'],
            pacing_sessions=0,retained_batches=0)
    assert path.exists() and path.stat().st_size==0


def test_writer_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    value=scope();store=bound(tmp_path,value);before=store.path.read_bytes()
    admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    monkeypatch.setattr(storage,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match=r'^PAPER_ACTIVITY_ACCEPTANCE_STORE_DATABASE_BUSY$'):
            store.append(value,admission=lambda:admitted)
    assert store.path.read_bytes()==before
    assert not store.append(value,admission=lambda:admitted)['duplicate']


def test_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    value=scope();store=bound(tmp_path,value);before=store.path.read_bytes()
    admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    monkeypatch.setattr(storage,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN')
        blocker.execute('SELECT count(*) FROM records').fetchone()
        with pytest.raises(ValueError,match=r'^PAPER_ACTIVITY_ACCEPTANCE_STORE_DATABASE_BUSY$'):
            store.append(value,admission=lambda:admitted)
    assert store.path.read_bytes()==before
    assert not store.append(value,admission=lambda:admitted)['duplicate']


def test_read_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    value=scope();store=bound(tmp_path,value);expected=store.inspect()
    monkeypatch.setattr(storage,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match=r'^PAPER_ACTIVITY_ACCEPTANCE_STORE_DATABASE_BUSY$'):
            store.inspect()
        with pytest.raises(ValueError,match=r'^PAPER_ACTIVITY_ACCEPTANCE_STORE_DATABASE_BUSY$'):
            store.replay_scopes()
    assert store.inspect()==expected and store.replay_scopes()==()
