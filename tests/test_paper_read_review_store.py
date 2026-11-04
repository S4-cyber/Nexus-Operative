"""Durable content-free receipts for complete local mixed-read review."""
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from nexus import paper_read_review as review,paper_read_review_store as store_subject
from nexus.alpaca_paper_contract import sha
from nexus.paper_read_review_store import PaperReadReviewStore,ZERO


def complete(tmp_path):
    gate,bindings=review._fixture(tmp_path,full_activity=True)
    return gate,bindings,gate.inspect()['head_sha256']


def string_values(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for key,item in value.items():yield from string_values(key);yield from string_values(item)
    elif isinstance(value,list):
        for item in value:yield from string_values(item)


def test_complete_review_is_retained_without_private_observations(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    receipt=review.retain_review(path,gate,bindings,expected_head=head)
    replay=PaperReadReviewStore(path,gate).inspect()
    assert replay['retained_receipts']==1 and replay['head_sha256']==receipt['head_sha256']
    assert replay['receipts'][0]['scope']['pacing_head_sha256']==head
    content=path.read_bytes()
    for private in (b'fixture_key_only_123',b'fixture_secret_only_123',b'nx_review_fixture',b'BTC/USD'):
        assert private not in content
    with sqlite3.connect(path) as db:
        bodies=[json.loads(row[0]) for row in db.execute('SELECT body FROM records')]
    assert '1000' not in set(string_values(bodies))
    assert not replay['ready_for_routing'] and not replay['release_approved']


def test_exact_duplicate_is_idempotent(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    first=review.retain_review(path,gate,bindings,expected_head=head)
    saved=path.read_bytes()
    second=review.retain_review(path,gate,bindings,expected_head=head,expected_store_head=first['head_sha256'])
    assert second['duplicate'] and second['head_sha256']==first['head_sha256'] and path.read_bytes()==saved


@pytest.mark.parametrize('change',[lambda b:b.pop(),lambda b:b.__setitem__(0,dict(b[0],normalized_result={} ))])
def test_incomplete_or_changed_review_never_creates_store(tmp_path,change):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3';change(bindings)
    with pytest.raises(ValueError,match='PAPER_READ_REVIEW_INCOMPLETE'):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert not path.exists()


def test_stale_store_head_and_revoked_input_do_not_append(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    first=review.retain_review(path,gate,bindings,expected_head=head)
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        review.retain_review(path,gate,bindings,expected_head=head,expected_store_head=ZERO)
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        review.retain_review(path,gate,bindings,expected_head=head,expected_store_head=first['head_sha256'],precommit_check=lambda:False)
    assert PaperReadReviewStore(path,gate).inspect()['retained_receipts']==1


def test_pacing_path_and_sqlite_companions_are_rejected_before_write(tmp_path):
    gate,bindings,head=complete(tmp_path)
    for path in (gate.path,gate.path.with_name(gate.path.name+'-wal'),gate.path.with_name(gate.path.name+'.owner.lock')):
        with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
            review.retain_review(path,gate,bindings,expected_head=head)


def test_tampered_chain_is_rejected(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    review.retain_review(path,gate,bindings,expected_head=head)
    with sqlite3.connect(path) as db:db.execute("UPDATE records SET previous=? WHERE seq=1",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):
        PaperReadReviewStore(path,gate).inspect()


def test_pacing_change_suppresses_retention(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    gate.begin(admission=lambda:1792369000)
    with pytest.raises(ValueError,match='PAPER_READ_REVIEW_HEAD_CHANGED'):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert not path.exists()


def contended_append(tmp_path,seed):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    first=review.retain_review(path,gate,bindings,expected_head=head)
    store=PaperReadReviewStore(path,gate);scope=dict(store.inspect()['receipts'][0]['scope'],review_sha256=sha(dict(seed=seed)))
    receipt=sha(scope)
    return store,scope,receipt,first['head_sha256']


def test_writer_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'writer');before=store.path.read_bytes()
    monkeypatch.setattr(store_subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match=r'^PAPER_READ_REVIEW_STORE_DATABASE_BUSY$'):
            store.append(scope,expected_head=head,admission=lambda:receipt)
    assert store.path.read_bytes()==before
    assert not store.append(scope,expected_head=head,admission=lambda:receipt)['duplicate']


def test_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'commit');before=store.path.read_bytes()
    monkeypatch.setattr(store_subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN');blocker.execute('SELECT count(*) FROM records').fetchone()
        with pytest.raises(ValueError,match=r'^PAPER_READ_REVIEW_STORE_DATABASE_BUSY$'):
            store.append(scope,expected_head=head,admission=lambda:receipt)
    assert store.path.read_bytes()==before
    assert not store.append(scope,expected_head=head,admission=lambda:receipt)['duplicate']


def test_read_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    store,_,_,_=contended_append(tmp_path,'read');expected=store.inspect()
    monkeypatch.setattr(store_subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match=r'^PAPER_READ_REVIEW_STORE_DATABASE_BUSY$'):store.inspect()
    assert store.inspect()==expected


def test_receipt_reader_close_error_requires_explicit_retry(tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    first=review.retain_review(path,gate,bindings,expected_head=head)
    store=PaperReadReviewStore(path,gate);saved=path.read_bytes()
    original_connect=store._connect
    class CloseFailureConnection(sqlite3.Connection):
        def close(self):
            super().close()
            raise RuntimeError('private read close failure')
    def close_failure(write=False):
        if write:return original_connect(write=write)
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,
            timeout=10,factory=CloseFailureConnection)
        db.execute('PRAGMA query_only=ON')
        return db
    monkeypatch.setattr(store,'_connect',close_failure)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_READ_ACKNOWLEDGEMENT_UNCERTAIN$')):
        store.inspect()
    assert path.read_bytes()==saved
    monkeypatch.setattr(store,'_connect',original_connect)
    retry=store.inspect()
    assert retry['retained_receipts']==1
    assert retry['head_sha256']==first['head_sha256']


def test_receipt_writer_close_error_requires_explicit_duplicate_recovery(
        tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'close')
    original_connect=store._connect
    class CloseFailureConnection(sqlite3.Connection):
        def close(self):
            super().close()
            raise RuntimeError('private write close failure')
    def close_failure(write=False):
        if not write:return original_connect(write=write)
        db=sqlite3.connect(store.path.resolve().as_uri()+'?mode=rw',uri=True,
            timeout=10,factory=CloseFailureConnection)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        return db
    monkeypatch.setattr(store,'_connect',close_failure)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        store.append(scope,expected_head=head,admission=lambda:receipt)
    monkeypatch.setattr(store,'_connect',original_connect)
    replay=store.inspect()
    retry=store.append(scope,expected_head=replay['head_sha256'],
        admission=lambda:receipt)
    final=store.inspect()
    assert replay['retained_receipts']==final['retained_receipts']==2
    assert retry['duplicate'] is True
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


def test_receipt_commit_flushes_main_file_and_new_posix_directory(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    original=store_subject.os.fsync;calls=[]
    def fsync(descriptor):
        mode=os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    first=review.retain_review(path,gate,bindings,expected_head=head)
    assert calls==(['file'] if os.name=='nt' else ['file','directory'])
    calls.clear()
    duplicate=review.retain_review(path,gate,bindings,expected_head=head,
        expected_store_head=first['head_sha256'])
    assert duplicate['duplicate'] is True and calls==['file']
    assert PaperReadReviewStore(path,gate).inspect()['retained_receipts']==1


def test_receipt_main_file_flush_uncertainty_retains_exact_commit_for_retry(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    failed=False;original=store_subject.os.fsync
    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed=True
            raise OSError('private main-file flush failure')
        return original(descriptor)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        review.retain_review(path,gate,bindings,expected_head=head)
    replay=PaperReadReviewStore(path,gate).inspect()
    assert failed and replay['retained_receipts']==1
    monkeypatch.setattr(store_subject.os,'fsync',original)
    retry=review.retain_review(path,gate,bindings,expected_head=head,
        expected_store_head=replay['head_sha256'])
    final=PaperReadReviewStore(path,gate).inspect()
    assert retry['duplicate'] is True and final['retained_receipts']==1
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


@pytest.mark.parametrize('signal',(KeyboardInterrupt,SystemExit))
def test_receipt_commit_sync_preserves_process_control_signals(
        tmp_path,monkeypatch,signal):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    original=store_subject.os.fsync
    monkeypatch.setattr(store_subject.os,'fsync',lambda _descriptor:(
        (_ for _ in ()).throw(signal())))
    with pytest.raises(signal):
        review.retain_review(path,gate,bindings,expected_head=head)
    monkeypatch.setattr(store_subject.os,'fsync',original)
    retained=PaperReadReviewStore(path,gate).inspect()
    retry=review.retain_review(path,gate,bindings,expected_head=head,
        expected_store_head=retained['head_sha256'])
    assert retained['retained_receipts']==1
    assert retry['duplicate'] is True
    assert retry['head_sha256']==retained['head_sha256']


@pytest.mark.skipif(os.name=='nt',reason='Python exposes no portable Windows directory fsync')
def test_receipt_parent_flush_uncertainty_retains_exact_commit_for_retry(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    failed=False;original=store_subject.os.fsync
    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed=True
            raise OSError('private directory flush failure')
        return original(descriptor)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        review.retain_review(path,gate,bindings,expected_head=head)
    replay=PaperReadReviewStore(path,gate).inspect()
    assert failed and replay['retained_receipts']==1
    monkeypatch.setattr(store_subject.os,'fsync',original)
    retry=review.retain_review(path,gate,bindings,expected_head=head,
        expected_store_head=replay['head_sha256'])
    assert retry['duplicate'] is True
    assert PaperReadReviewStore(path,gate).inspect()['retained_receipts']==1


@pytest.mark.skipif(os.name=='nt',reason='Python exposes no portable Windows directory fsync')
def test_receipt_parent_flush_binds_exact_directory_descriptor(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    foreign=tmp_path/'foreign';foreign.mkdir()
    original_open=store_subject.os.open;original_fsync=store_subject.os.fsync
    substituted=False;directory_syncs=0
    def open_file(value,flags,*args,**kwargs):
        nonlocal substituted
        if (not substituted and flags&getattr(os,'O_DIRECTORY',0) and
                Path(value).resolve()==path.resolve().parent):
            substituted=True
            return original_open(foreign,flags,*args,**kwargs)
        return original_open(value,flags,*args,**kwargs)
    def fsync(descriptor):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):directory_syncs+=1
        return original_fsync(descriptor)
    monkeypatch.setattr(store_subject.os,'open',open_file)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert substituted and directory_syncs==0
    replay=PaperReadReviewStore(path,gate).inspect()
    assert replay['retained_receipts']==1
    monkeypatch.setattr(store_subject.os,'open',original_open)
    monkeypatch.setattr(store_subject.os,'fsync',original_fsync)
    retry=review.retain_review(path,gate,bindings,expected_head=head,
        expected_store_head=replay['head_sha256'])
    assert retry['duplicate'] is True


def test_receipt_flush_hashes_exact_writable_descriptor_before_sync(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    original_open=store_subject.os.open;original_fsync=store_subject.os.fsync
    saved=None;tampered=False;sync_calls=0
    def open_file(value,flags,*args,**kwargs):
        nonlocal saved,tampered
        descriptor=original_open(value,flags,*args,**kwargs)
        if (not tampered and flags&os.O_RDWR and
                Path(value).resolve()==path.resolve()):
            size=os.fstat(descriptor).st_size
            os.lseek(descriptor,0,os.SEEK_SET);saved=os.read(descriptor,size)
            offset=min(100,size-1);os.lseek(descriptor,offset,os.SEEK_SET)
            os.write(descriptor,bytes((saved[offset]^1,)));tampered=True
            os.lseek(descriptor,0,os.SEEK_SET)
        return descriptor
    def fsync(descriptor):
        nonlocal sync_calls
        sync_calls+=1
        return original_fsync(descriptor)
    monkeypatch.setattr(store_subject.os,'open',open_file)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert tampered and saved and sync_calls==0
    monkeypatch.setattr(store_subject.os,'open',original_open)
    monkeypatch.setattr(store_subject.os,'fsync',original_fsync)
    path.write_bytes(saved)
    replay=PaperReadReviewStore(path,gate).inspect()
    retry=review.retain_review(path,gate,bindings,expected_head=head,
        expected_store_head=replay['head_sha256'])
    assert retry['duplicate'] is True


def test_read_connection_retarget_is_rejected_before_receipt_load(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    review.retain_review(path,gate,bindings,expected_head=head)
    foreign=tmp_path/'foreign.sqlite3'
    with sqlite3.connect(foreign) as db:db.execute('CREATE TABLE private (v)')
    before=path.read_bytes();foreign_before=foreign.read_bytes()
    original=store_subject.sqlite3.connect
    def retarget(database,*args,**kwargs):
        if '?mode=ro' in str(database):
            database=foreign.resolve().as_uri()+'?mode=ro';kwargs['uri']=True
        return original(database,*args,**kwargs)
    monkeypatch.setattr(store_subject.sqlite3,'connect',retarget)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_CONNECTION_BINDING_CHANGED$')):
        PaperReadReviewStore(path,gate).inspect()
    assert path.read_bytes()==before and foreign.read_bytes()==foreign_before


def test_new_write_connection_retarget_removes_only_empty_target_and_retries(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    foreign=tmp_path/'foreign.sqlite3'
    with sqlite3.connect(foreign) as db:db.execute('CREATE TABLE private (v)')
    foreign_before=foreign.read_bytes();original=store_subject.sqlite3.connect
    def retarget(database,*args,**kwargs):
        if '?mode=rw' in str(database):
            database=foreign.resolve().as_uri()+'?mode=rw';kwargs['uri']=True
        return original(database,*args,**kwargs)
    monkeypatch.setattr(store_subject.sqlite3,'connect',retarget)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_CONNECTION_BINDING_CHANGED$')):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert not path.exists() and foreign.read_bytes()==foreign_before
    monkeypatch.setattr(store_subject.sqlite3,'connect',original)
    assert review.retain_review(path,gate,bindings,
        expected_head=head)['duplicate'] is False


def test_uncommitted_cleanup_never_deletes_replacement(tmp_path):
    path=tmp_path/'review.sqlite3';path.touch(mode=0o600)
    identity=store_subject.capture_file_identities((path,),
        'PAPER_READ_REVIEW_STORE_')
    replacement=tmp_path/'replacement.sqlite3';replacement.touch(mode=0o600)
    os.replace(replacement,path)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$')):
        store_subject._discard_uncommitted_new(path,identity)
    assert path.exists() and path.stat().st_size==0


def test_uncommitted_cleanup_retains_exact_nonempty_file(tmp_path):
    path=tmp_path/'review.sqlite3';content=b'private uncommitted state'
    path.write_bytes(content)
    identity=store_subject.capture_file_identities((path,),
        'PAPER_READ_REVIEW_STORE_')
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$')):
        store_subject._discard_uncommitted_new(path,identity)
    assert path.read_bytes()==content


def test_fresh_receipt_identity_is_required_before_sqlite_connect(
        tmp_path,monkeypatch):
    gate,_,_=complete(tmp_path);path=tmp_path/'review.sqlite3'
    original=store_subject.require_file_identities;substituted=False
    def require(paths,expected,prefix):
        nonlocal substituted
        if not substituted:
            substituted=True
            replacement=tmp_path/'replacement.sqlite3'
            replacement.touch(mode=0o600);os.replace(replacement,path)
        return original(paths,expected,prefix)
    monkeypatch.setattr(store_subject,'require_file_identities',require)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$')):
        PaperReadReviewStore(path,gate)._connect(write=True)
    assert substituted and path.exists() and path.stat().st_size==0


@pytest.mark.parametrize('failure_after',(None,'PRAGMA database_list',
    'PRAGMA busy_timeout','PRAGMA journal_mode','PRAGMA synchronous',
    'PRAGMA page_size','PRAGMA max_page_count','BEGIN IMMEDIATE',
    'CREATE TABLE'))
def test_fresh_receipt_identity_is_rechecked_through_sqlite_setup(
        tmp_path,monkeypatch,failure_after):
    gate,_,_=complete(tmp_path);path=tmp_path/'review.sqlite3'
    target=path.resolve().as_uri();original_connect=store_subject.sqlite3.connect
    original_require=store_subject.require_file_identities
    state={'connected':False,'connects':0,'created_identity':None,
        'sql':None,'failed':False}
    class Connection:
        def __init__(self,db):self.db=db
        def execute(self,sql,*args,**kwargs):
            result=self.db.execute(sql,*args,**kwargs);state['sql']=sql
            return result
        def close(self):return self.db.close()
        def __getattr__(self,name):return getattr(self.db,name)
    def connect(*args,**kwargs):
        if str(args[0]).startswith(target):
            state['connects']+=1
            state['created_identity']=store_subject.capture_file_identities(
                (path,),'PAPER_READ_REVIEW_STORE_')
        db=original_connect(*args,**kwargs)
        if str(args[0]).startswith(target):
            state['connected']=True;return Connection(db)
        return db
    def require(paths,expected,prefix):
        trigger=(state['connected'] if failure_after is None else
            type(state['sql']) is str and state['sql'].startswith(failure_after))
        if trigger and not state['failed']:
            state['failed']=True
            raise ValueError(prefix+'FILE_IDENTITY_CHANGED')
        return original_require(paths,expected,prefix)
    monkeypatch.setattr(store_subject.sqlite3,'connect',connect)
    monkeypatch.setattr(store_subject,'require_file_identities',require)
    with pytest.raises(ValueError) as raised:
        PaperReadReviewStore(path,gate)._connect(write=True)
    assert state['failed'] and state['connects']==1
    if path.exists():
        assert str(raised.value)==(
            'PAPER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN')
        assert path.stat().st_size>0
        assert store_subject.capture_file_identities((path,),
            'PAPER_READ_REVIEW_STORE_')==state['created_identity']
    else:
        assert str(raised.value)==(
            'PAPER_READ_REVIEW_STORE_FILE_IDENTITY_CHANGED')
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))


@pytest.mark.parametrize('failure_sql',(None,'PRAGMA busy_timeout',
    'PRAGMA journal_mode','PRAGMA synchronous','PRAGMA page_size',
    'PRAGMA max_page_count','BEGIN IMMEDIATE','CREATE TABLE'))
def test_fresh_receipt_initialization_failure_restores_absence_and_exact_retry(
        tmp_path,monkeypatch,failure_sql):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    target=path.resolve().as_uri();original=store_subject.sqlite3.connect
    failed=False
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
            raise RuntimeError('private connection failure')
        db=original(*args,**kwargs)
        return Connection(db) if not failed else db
    monkeypatch.setattr(store_subject.sqlite3,'connect',connect)
    with pytest.raises(RuntimeError,match='private (connection|initialization) failure'):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert failed and not path.exists()
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    receipt=review.retain_review(path,gate,bindings,expected_head=head)
    replay=PaperReadReviewStore(path,gate).inspect()
    assert replay['retained_receipts']==1
    assert receipt['head_sha256']==replay['head_sha256']


def test_fresh_receipt_initialization_close_uncertainty_retains_path(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    target=path.resolve().as_uri();original=store_subject.sqlite3.connect
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
    monkeypatch.setattr(store_subject.sqlite3,'connect',connect)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$')):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert path.exists() and path.stat().st_size==0


def spoof_connection(target):
    class SpoofConnection(sqlite3.Connection):
        def execute(self,statement,*args,**kwargs):
            if statement=='PRAGMA database_list':
                class Rows:
                    @staticmethod
                    def fetchall():return [(0,'main',str(target.resolve()))]
                return Rows()
            return super().execute(statement,*args,**kwargs)
    return SpoofConnection


def test_read_connection_content_spoof_is_rejected_before_receipt_load(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    review.retain_review(path,gate,bindings,expected_head=head)
    foreign=tmp_path/'foreign.sqlite3'
    with sqlite3.connect(foreign) as db:db.execute('CREATE TABLE private (v)')
    before=path.read_bytes();foreign_before=foreign.read_bytes()
    original=store_subject.sqlite3.connect
    def retarget(database,*args,**kwargs):
        if '?mode=ro' in str(database):
            database=foreign.resolve().as_uri()+'?mode=ro';kwargs['uri']=True
            kwargs['factory']=spoof_connection(path)
        return original(database,*args,**kwargs)
    monkeypatch.setattr(store_subject.sqlite3,'connect',retarget)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED$')):
        PaperReadReviewStore(path,gate).inspect()
    assert path.read_bytes()==before and foreign.read_bytes()==foreign_before


def test_write_connection_content_spoof_is_rejected_before_transaction(
        tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'content')
    foreign=tmp_path/'foreign.sqlite3'
    with sqlite3.connect(foreign) as db:db.execute('CREATE TABLE private (v)')
    before=store.path.read_bytes();foreign_before=foreign.read_bytes()
    original=store_subject.sqlite3.connect
    def retarget(database,*args,**kwargs):
        if '?mode=rw' in str(database):
            database=foreign.resolve().as_uri()+'?mode=rw';kwargs['uri']=True
            kwargs['factory']=spoof_connection(store.path)
        return original(database,*args,**kwargs)
    monkeypatch.setattr(store_subject.sqlite3,'connect',retarget)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED$')):
        store.append(scope,expected_head=head,admission=lambda:receipt)
    assert store.path.read_bytes()==before and foreign.read_bytes()==foreign_before


def continuity_connection(counter):
    class ContinuityConnection(sqlite3.Connection):
        def serialize(self,*args,**kwargs):
            result=super().serialize(*args,**kwargs)
            counter.append(len(result))
            return result
    return ContinuityConnection


def changed_continuity_fingerprint(original,counter):
    def changed(path):
        result=original(path)
        if len(counter)>=2:
            altered=list(result);altered[7]='f'*64
            return tuple(altered)
        return result
    return changed


def test_read_connection_content_is_reproved_after_complete_replay(
        tmp_path,monkeypatch):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    first=review.retain_review(path,gate,bindings,expected_head=head)
    before=path.read_bytes();counter=[]
    original_connect=store_subject.sqlite3.connect
    original_fingerprint=store_subject._main_fingerprint
    def connect(database,*args,**kwargs):
        if '?mode=ro' in str(database):
            kwargs['factory']=continuity_connection(counter)
        return original_connect(database,*args,**kwargs)
    monkeypatch.setattr(store_subject.sqlite3,'connect',connect)
    monkeypatch.setattr(store_subject,'_main_fingerprint',
        changed_continuity_fingerprint(original_fingerprint,counter))
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED$')):
        PaperReadReviewStore(path,gate).inspect()
    assert len(counter)==2 and path.read_bytes()==before
    monkeypatch.setattr(store_subject.sqlite3,'connect',original_connect)
    monkeypatch.setattr(store_subject,'_main_fingerprint',original_fingerprint)
    retry=PaperReadReviewStore(path,gate).inspect()
    assert retry['head_sha256']==first['head_sha256']


def test_write_connection_content_is_reproved_after_commit_and_recovers(
        tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'continuity')
    before=store.inspect()['retained_receipts'];counter=[]
    original_connect=store_subject.sqlite3.connect
    original_fingerprint=store_subject._main_fingerprint
    def connect(database,*args,**kwargs):
        if '?mode=rw' in str(database):
            kwargs['factory']=continuity_connection(counter)
        return original_connect(database,*args,**kwargs)
    monkeypatch.setattr(store_subject.sqlite3,'connect',connect)
    monkeypatch.setattr(store_subject,'_main_fingerprint',
        changed_continuity_fingerprint(original_fingerprint,counter))
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED$')):
        store.append(scope,expected_head=head,admission=lambda:receipt)
    assert len(counter)==2
    monkeypatch.setattr(store_subject.sqlite3,'connect',original_connect)
    monkeypatch.setattr(store_subject,'_main_fingerprint',original_fingerprint)
    replay=store.inspect()
    retry=store.append(scope,expected_head=replay['head_sha256'],
        admission=lambda:receipt)
    assert replay['retained_receipts']==before+1 and retry['duplicate'] is True
    assert store.inspect()['retained_receipts']==before+1


def test_write_postcommit_callback_cannot_hide_file_set_change(tmp_path):
    store,scope,receipt,head=contended_append(tmp_path,'finalization')
    before=store.inspect()['retained_receipts'];calls=0
    def admission():
        nonlocal calls
        calls+=1
        if calls==3:
            state=store.path.stat()
            os.utime(store.path,ns=(state.st_atime_ns,
                state.st_mtime_ns+1_000_000_000))
        return receipt
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_FILE_SET_CHANGED$')):
        store.append(scope,expected_head=head,admission=admission)
    replay=store.inspect()
    retry=store.append(scope,expected_head=replay['head_sha256'],
        admission=lambda:receipt)
    final=store.inspect()
    assert calls==3 and replay['retained_receipts']==before+1
    assert retry['duplicate'] is True and final==replay


def test_write_close_time_file_change_requires_explicit_duplicate_recovery(
        tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'close-finalization')
    before=store.inspect()['retained_receipts'];original_connect=store._connect
    class CloseMutationConnection(sqlite3.Connection):
        def close(self):
            super().close();state=store.path.stat()
            os.utime(store.path,ns=(state.st_atime_ns,
                state.st_mtime_ns+1_000_000_000))
    def close_mutation(write=False):
        if not write:return original_connect(write=write)
        db=sqlite3.connect(store.path.resolve().as_uri()+'?mode=rw',uri=True,
            timeout=10,factory=CloseMutationConnection)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        return db
    monkeypatch.setattr(store,'_connect',close_mutation)
    with pytest.raises(ValueError,match=(
            '^PAPER_READ_REVIEW_STORE_FILE_SET_CHANGED$')):
        store.append(scope,expected_head=head,admission=lambda:receipt)
    monkeypatch.setattr(store,'_connect',original_connect)
    replay=store.inspect()
    retry=store.append(scope,expected_head=replay['head_sha256'],
        admission=lambda:receipt)
    final=store.inspect()
    assert replay['retained_receipts']==before+1
    assert retry['duplicate'] is True and final==replay
