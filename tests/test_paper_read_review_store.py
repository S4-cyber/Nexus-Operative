"""Durable content-free receipts for complete local mixed-read review."""
import json
import sqlite3

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
