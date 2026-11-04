"""Provider/account read-history composition and durable receipt tests."""
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from nexus import (paper_provider_read_review as subject,
    paper_provider_read_review_store as store_subject,paper_read_review,
    sqlite_journal_guard as guard_subject)
from nexus.alpaca_paper_contract import sha
from nexus.paper_provider_read_budget import PaperProviderReadBudget,ZERO
from nexus.paper_provider_read_review_store import PaperProviderReadReviewStore
from nexus.paper_read_pacing import OPERATION_PATTERNS,PaperReadPacing
from test_alpaca_paper_inspection import ACCOUNT,OTHER


def build(tmp_path,count=5,pending=False):
    gate,_=paper_read_review._fixture(tmp_path/'account')
    history=gate.operation_history();budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    for index,record in enumerate(history[:count]):
        with budget.ownership() as owner:
            token=budget.begin(account_id=ACCOUNT,account_before_head_sha256=record['before_head_sha256'],
                operation=record['operation'],admission=lambda index=index:1000+index*61,ownership=owner)
            budget.finish(token,account_after_head_sha256=record['after_head_sha256'],
                actual_gets=len(record['request_kinds']),admission=lambda index=index:1001+index*61,
                ownership=owner,status_code=200)
    if pending:
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=gate.inspect()['head_sha256'],
                operation='ACCOUNT_INVENTORY',admission=lambda:1400,ownership=owner)
    return gate,budget


def heads(gate,budget):
    return dict(expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={gate.account_digest:gate.inspect()['head_sha256']})


def files(root):return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_active_fingerprint_boundary_uses_windows_portable_fields():
    common=dict(st_dev=1,st_ino=2,st_size=3,st_mtime_ns=4,st_nlink=1)
    path_view=SimpleNamespace(**common,st_ctime_ns=5)
    handle_view=SimpleNamespace(**common,st_ctime_ns=6)
    assert (store_subject._active_fingerprint_boundary(path_view,host='nt')==
        store_subject._active_fingerprint_boundary(handle_view,host='nt'))
    assert (store_subject._active_fingerprint_boundary(path_view,host='posix')!=
        store_subject._active_fingerprint_boundary(handle_view,host='posix'))


def add_complete_peer(tmp_path,budget):
    gate,_=paper_read_review._fixture(tmp_path/'other',provider_budget=budget,
        account_id=OTHER,epoch=1792370000)
    return gate


def add_inventory_peer(tmp_path,budget):
    other=PaperReadPacing(tmp_path/'other.sqlite3',OTHER)
    kinds=list(OPERATION_PATTERNS['ACCOUNT_INVENTORY'][0])
    receipt=dict(operation='ACCOUNT_INVENTORY',request_kinds=kinds,
        request_sha256=[sha(dict(request=i)) for i in range(len(kinds))],
        response_sha256=[sha(dict(response=i)) for i in range(len(kinds))])
    with other.ownership() as owner:
        token=other.begin(admission=lambda:2000,ownership=owner,
            operation='ACCOUNT_INVENTORY')
        other.finish(token,admission=lambda:2001,status_code=200,
            ownership=owner,receipt=receipt)
    record=other.operation_history()[0]
    with budget.ownership() as owner:
        token=budget.begin(account_id=OTHER,
            account_before_head_sha256=record['before_head_sha256'],
            operation=record['operation'],admission=lambda:1400,ownership=owner)
        budget.finish(token,account_after_head_sha256=record['after_head_sha256'],
            actual_gets=len(kinds),admission=lambda:1401,ownership=owner,
            status_code=200)
    return other


def test_complete_provider_account_history_matches_without_authority_or_writes(tmp_path):
    gate,budget=build(tmp_path);before=files(tmp_path)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert result['local_provider_account_history_consistent'] and result['all_five_purposes_observed']
    assert result['matched_sessions']==result['provider_sessions_replayed']==result['account_sessions_replayed']==5
    assert result['review_reasons']==[] and result['status']=='REVIEW_REQUIRED'
    assert result['journal_files_rechecked_unchanged']
    assert result['semantic_reader_failures_rechecked']
    assert all(result[key] is False for key in subject.FLAGS) and files(tmp_path)==before


@pytest.mark.parametrize(('first_recovery','remaining_reason'),(
    ('provider','ACCOUNT_REVIEW_BLOCK'),('account','PROVIDER_REVIEW_BLOCK')))
def test_split_recovery_remains_inconsistent_until_both_journals_are_cleared(
        tmp_path,first_recovery,remaining_reason):
    gate,budget=build(tmp_path);operation='ACCOUNT_INVENTORY'
    kinds=list(OPERATION_PATTERNS[operation][0])
    blocked_at=max(gate.inspect()['next_allowed_at'],budget.inspect()['next_allowed_at'])
    with budget.ownership() as provider_owner:
        provider_token=budget.begin(account_id=ACCOUNT,
            account_before_head_sha256=gate.inspect()['head_sha256'],operation=operation,
            admission=lambda:blocked_at,ownership=provider_owner)
        with gate.ownership() as account_owner:
            account_token=gate.begin(admission=lambda:blocked_at,ownership=account_owner,
                operation=operation)
            gate.finish(account_token,admission=lambda:blocked_at+1,status_code=401,
                ownership=account_owner,receipt=dict(operation=operation,
                    request_kinds=kinds[:1],request_sha256=[sha(dict(request=0))],
                    response_sha256=[]))
        budget.finish(provider_token,
            account_after_head_sha256=gate.inspect()['head_sha256'],actual_gets=1,
            admission=lambda:blocked_at+1,ownership=provider_owner,status_code=401)
    blocked=subject.review(budget,[gate],**heads(gate,budget))
    assert blocked['review_reasons']==[
        'ACCOUNT_REVIEW_BLOCK','PROVIDER_REVIEW_BLOCK']
    assert blocked['local_provider_account_history_consistent'] is False

    def recover_provider(when):
        provider_state=budget.inspect()
        with budget.ownership() as owner:
            budget.recover_review_block(expected_head=provider_state['head_sha256'],
                expected_account_id=ACCOUNT,confirm_block_reviewed=True,
                confirm_uncertainty_preserved=True,admission=lambda:when,ownership=owner)

    def recover_account(when):
        account_state=gate.inspect()
        with gate.ownership() as owner:
            gate.recover_review_block(expected_head=account_state['head_sha256'],
                expected_account_id=ACCOUNT,confirm_block_reviewed=True,
                confirm_uncertainty_preserved=True,admission=lambda:when,ownership=owner)

    first=recover_provider if first_recovery=='provider' else recover_account
    second=recover_account if first_recovery=='provider' else recover_provider
    first(blocked_at+2)
    split=subject.review(budget,[gate],**heads(gate,budget))
    assert split['review_reasons']==[remaining_reason]
    assert split['local_provider_account_history_consistent'] is False

    second(blocked_at+3)
    complete=subject.review(budget,[gate],**heads(gate,budget))
    assert complete['review_reasons']==[]
    assert complete['local_provider_account_history_consistent'] is True
    assert complete['matched_sessions']==6


def _mutate_metadata_and_fail(path):
    stat=os.stat(path)
    os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
    raise RuntimeError('injected provider read review failure')


@pytest.mark.parametrize('target',('provider','account'))
def test_initial_reader_failure_rechecks_every_selected_journal(
        tmp_path,monkeypatch,target):
    gate,budget=build(tmp_path);options=heads(gate,budget)
    paths=dict(provider=budget.path,account=gate.path)
    before={path:path.read_bytes() for path in paths.values()}
    original=budget.inspect

    def failed():
        original();_mutate_metadata_and_fail(paths[target])

    monkeypatch.setattr(budget,'inspect',failed)
    with pytest.raises(ValueError,match=
            'PAPER_PROVIDER_READ_REVIEW_FILE_SET_CHANGED'):
        subject.review(budget,[gate],**options)
    assert before=={path:path.read_bytes() for path in before}


@pytest.mark.parametrize(('reader','target'),(
    ('provider_history','account'),('account_history','provider'),
    ('final_provider','account'),('final_account','provider')))
def test_later_reader_failure_rechecks_every_selected_journal(
        tmp_path,monkeypatch,reader,target):
    gate,budget=build(tmp_path);options=heads(gate,budget)
    paths=dict(provider=budget.path,account=gate.path)
    before={path:path.read_bytes() for path in paths.values()}
    if reader=='provider_history':
        original=budget.history

        def failed():
            original();_mutate_metadata_and_fail(paths[target])

        monkeypatch.setattr(budget,'history',failed)
    elif reader=='account_history':
        original=gate.operation_history

        def failed():
            original();_mutate_metadata_and_fail(paths[target])

        monkeypatch.setattr(gate,'operation_history',failed)
    else:
        store=budget if reader=='final_provider' else gate
        original=store.inspect;calls=0

        def failed():
            nonlocal calls
            result=original();calls+=1
            if calls==2:_mutate_metadata_and_fail(paths[target])
            return result

        monkeypatch.setattr(store,'inspect',failed)
    with pytest.raises(ValueError,match=
            'PAPER_PROVIDER_READ_REVIEW_FILE_SET_CHANGED'):
        subject.review(budget,[gate],**options)
    assert before=={path:path.read_bytes() for path in before}


@pytest.mark.parametrize('target',('provider','account'))
def test_store_objects_cannot_retarget_during_provider_history(
        tmp_path,monkeypatch,target):
    gate,budget=build(tmp_path);options=heads(gate,budget)
    store=budget if target=='provider' else gate
    original_path=store.path;clone=tmp_path/f'{target}-clone.sqlite3'
    original=budget.history

    def retarget():
        result=original();shutil.copyfile(original_path,clone);store.path=clone
        return result

    monkeypatch.setattr(budget,'history',retarget)
    with pytest.raises(ValueError,match=
            'PAPER_PROVIDER_READ_REVIEW_STORE_BINDING_CHANGED'):
        subject.review(budget,[gate],**options)
    assert original_path.exists() and clone.exists()


def test_one_provider_history_can_exactly_compose_multiple_account_journals(tmp_path):
    gate,budget=build(tmp_path);other=add_complete_peer(tmp_path,budget)
    result=subject.review(budget,[gate,other],expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={gate.account_digest:gate.inspect()['head_sha256'],other.account_digest:other.inspect()['head_sha256']})
    assert result['local_provider_account_history_consistent'] and result['account_journals']==2
    assert result['matched_sessions']==10
    assert all(result['operation_counts'][operation]==2 for operation in OPERATION_PATTERNS)


def test_pending_account_cannot_hide_behind_complete_peer_history(tmp_path):
    gate,budget=build(tmp_path);other=add_complete_peer(tmp_path,budget)
    pending_at=max(gate.inspect()['next_allowed_at'],budget.inspect()['next_allowed_at'])
    with gate.ownership() as owner:
        gate.begin(admission=lambda:pending_at,ownership=owner,
            operation='ACCOUNT_INVENTORY')
    before=files(tmp_path)
    result=subject.review(budget,[gate,other],
        expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={gate.account_digest:gate.inspect()['head_sha256'],
            other.account_digest:other.inspect()['head_sha256']})
    assert result['review_reasons']==[
        'INCOMPLETE_ACCOUNT_HISTORY','PENDING_ACCOUNT_HISTORY']
    assert result['account_journals']==2
    assert result['provider_sessions_replayed']==result['account_sessions_replayed']==10
    assert result['operation_counts']['ACCOUNT_INVENTORY']==2
    assert result['local_provider_account_history_consistent'] is False
    assert result['matched_sessions']==0 and files(tmp_path)==before


def test_incomplete_account_cannot_hide_behind_complete_peer_history(tmp_path):
    gate,budget=build(tmp_path);other=add_inventory_peer(tmp_path,budget)
    before=files(tmp_path)
    result=subject.review(budget,[gate,other],
        expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={gate.account_digest:gate.inspect()['head_sha256'],
            other.account_digest:other.inspect()['head_sha256']})
    assert result['review_reasons']==['FIVE_PURPOSE_HISTORY_INCOMPLETE']
    assert result['provider_sessions_replayed']==result['account_sessions_replayed']==6
    assert result['all_five_purposes_observed'] is False
    assert result['local_provider_account_history_consistent'] is False
    assert result['matched_sessions']==0 and files(tmp_path)==before


def test_account_reader_failure_rechecks_every_peer_journal(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);other=add_complete_peer(tmp_path,budget)
    options=dict(expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={gate.account_digest:gate.inspect()['head_sha256'],
            other.account_digest:other.inspect()['head_sha256']})
    before={path:path.read_bytes() for path in (budget.path,gate.path,other.path)}
    original=gate.operation_history

    def failed():
        original();_mutate_metadata_and_fail(other.path)

    monkeypatch.setattr(gate,'operation_history',failed)
    with pytest.raises(ValueError,match=
            'PAPER_PROVIDER_READ_REVIEW_FILE_SET_CHANGED'):
        subject.review(budget,[gate,other],**options)
    assert before=={path:path.read_bytes() for path in before}


@pytest.mark.parametrize('count,reason',[(4,'PROVIDER_ACCOUNT_SESSION_MISMATCH'),(0,'FIVE_PURPOSE_HISTORY_INCOMPLETE')])
def test_missing_provider_history_never_matches_account_history(tmp_path,count,reason):
    gate,budget=build(tmp_path,count=count)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent'] and reason in result['review_reasons']


def test_pending_provider_history_is_preserved_as_review_block(tmp_path):
    gate,budget=build(tmp_path,pending=True);before=files(tmp_path)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent']
    assert result['review_reasons']==['PENDING_PROVIDER_HISTORY']
    assert result['matched_sessions']==0 and files(tmp_path)==before


def test_pending_account_history_is_preserved_as_review_block(tmp_path):
    gate,budget=build(tmp_path)
    at=gate.inspect()['next_allowed_at']
    with gate.ownership() as owner:
        gate.begin(admission=lambda:at,ownership=owner,operation='ACCOUNT_INVENTORY')
    before=files(tmp_path)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent']
    assert result['review_reasons']==[
        'INCOMPLETE_ACCOUNT_HISTORY','PENDING_ACCOUNT_HISTORY']
    assert result['matched_sessions']==0 and files(tmp_path)==before


def test_aligned_provider_and_account_pending_histories_never_promote(tmp_path):
    gate,budget=build(tmp_path)
    at=max(gate.inspect()['next_allowed_at'],budget.inspect()['next_allowed_at'])
    with budget.ownership() as provider_owner:
        budget.begin(account_id=ACCOUNT,
            account_before_head_sha256=gate.inspect()['head_sha256'],
            operation='ACCOUNT_INVENTORY',admission=lambda:at,
            ownership=provider_owner)
    with gate.ownership() as account_owner:
        gate.begin(admission=lambda:at,ownership=account_owner,
            operation='ACCOUNT_INVENTORY')
    before=files(tmp_path)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert result['review_reasons']==[
        'INCOMPLETE_ACCOUNT_HISTORY','PENDING_ACCOUNT_HISTORY',
        'PENDING_PROVIDER_HISTORY']
    assert result['local_provider_account_history_consistent'] is False
    assert result['matched_sessions']==0 and files(tmp_path)==before


def test_legacy_account_history_is_never_promoted_to_typed_match(tmp_path):
    gate,budget=build(tmp_path)
    at=gate.inspect()['next_allowed_at'];token=gate.begin(admission=lambda:at)
    gate.finish(token,admission=lambda:at+1,status_code=200)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent']
    assert 'LEGACY_ACCOUNT_HISTORY' in result['review_reasons']


@pytest.mark.parametrize('which',['provider','account'])
def test_stale_exact_heads_fail_before_review(tmp_path,which):
    gate,budget=build(tmp_path);options=heads(gate,budget)
    if which=='provider':options['expected_provider_head']=ZERO
    else:options['expected_account_heads'][gate.account_digest]=ZERO
    with pytest.raises(ValueError,match='HEAD_CHANGED'):subject.review(budget,[gate],**options)


@pytest.mark.parametrize('change',['duplicate','foreign','missing','bad_head'])
def test_account_scope_is_bounded_and_exact(tmp_path,change):
    gate,budget=build(tmp_path);options=heads(gate,budget);gates=[gate]
    if change=='duplicate':gates.append(gate)
    if change=='foreign':options['expected_account_heads']['f'*64]=ZERO
    if change=='missing':options['expected_account_heads']={}
    if change=='bad_head':options['expected_account_heads'][gate.account_digest]=True
    with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_REVIEW_'):
        subject.review(budget,gates,**options)


def test_durable_receipt_binds_both_heads_without_private_content(tmp_path):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    receipt=subject.retain_review(path,budget,[gate],**heads(gate,budget))
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect();content=path.read_bytes()
    assert replay['retained_receipts']==1 and replay['head_sha256']==receipt['head_sha256']
    scope=replay['receipts'][0]['scope']
    assert scope['provider_head_sha256']==budget.inspect()['head_sha256']
    assert scope['sessions_replayed']==scope['matched_sessions']==5
    assert ACCOUNT.encode() not in content and b'BTC/USD' not in content
    assert not replay['ready_for_routing'] and not replay['release_approved']


@pytest.mark.parametrize(('seam','expected'),(
    ('connect','STORE_BINDING_CHANGED'),('load','STORE_BINDING_CHANGED'),
    ('fingerprint','FILE_SET_CHANGED'),
    ('connection','CONNECTION_BINDING_CHANGED'),
    ('connection-content','CONNECTION_CONTENT_CHANGED')))
def test_receipt_inspection_pins_store_binding_and_fingerprint(
        tmp_path,monkeypatch,seam,expected):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    foreign=tmp_path/'foreign.sqlite3';shutil.copyfile(path,foreign)
    foreign_db=sqlite3.connect(foreign)
    try:foreign_db.execute('PRAGMA user_version=1');foreign_db.commit()
    finally:foreign_db.close()
    store=PaperProviderReadReviewStore(path,budget,[gate])
    original_path=store.path;original_connect=store._connect
    original_load=store._load
    if seam=='connection-content':
        class SpoofedConnection(sqlite3.Connection):
            def execute(self,statement,*args,**kwargs):
                if statement=='PRAGMA database_list':
                    return SimpleNamespace(fetchall=lambda:[
                        (0,'main',str(path.resolve()))])
                return super().execute(statement,*args,**kwargs)
        def substitute_content_connection(*args,**kwargs):
            db=sqlite3.connect(foreign.resolve().as_uri()+'?mode=ro',
                uri=True,timeout=10,factory=SpoofedConnection)
            return db,False
        monkeypatch.setattr(store,'_connect',substitute_content_connection)
    elif seam=='connection':
        def substitute_connection(*args,**kwargs):
            db=sqlite3.connect(foreign.resolve().as_uri()+'?mode=ro',
                uri=True,timeout=10)
            return db,False
        monkeypatch.setattr(store,'_connect',substitute_connection)
    elif seam=='connect':
        def retarget_connect(*args,**kwargs):
            store.path=foreign
            return original_connect(*args,**kwargs)
        monkeypatch.setattr(store,'_connect',retarget_connect)
    else:
        def changed_load(db):
            result=original_load(db)
            if seam=='load':store.path=foreign
            else:
                state=path.stat()
                os.utime(path,ns=(state.st_atime_ns,
                    state.st_mtime_ns+1_000_000_000))
            return result
        monkeypatch.setattr(store,'_load',changed_load)
    before={candidate:candidate.read_bytes() for candidate in (path,foreign)}
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_'+expected+'$')):
        store.inspect()
    store.path=original_path
    assert before=={candidate:candidate.read_bytes() for candidate in before}


def test_receipt_inspection_rechecks_path_content_after_serialization(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    store=PaperProviderReadReviewStore(path,budget,[gate])
    before=path.read_bytes();original_connect=store._connect
    original_require=store_subject.require_file_fingerprints
    original_load=store._load;content_changed=False;load_calls=0
    class SerializationRaceConnection(sqlite3.Connection):
        def serialize(self,*args,**kwargs):
            nonlocal content_changed
            result=super().serialize(*args,**kwargs)
            content_changed=True
            return result
    def read_connection(*args,**kwargs):
        assert not args and not kwargs
        return (sqlite3.connect(path.resolve().as_uri()+'?mode=ro',
            uri=True,timeout=10,factory=SerializationRaceConnection),False)
    def changed_require(*args,**kwargs):
        if content_changed:store_subject._fail('FILE_SET_CHANGED')
        return original_require(*args,**kwargs)
    def counted_load(db):
        nonlocal load_calls
        load_calls+=1
        return original_load(db)
    monkeypatch.setattr(store,'_connect',read_connection)
    monkeypatch.setattr(store,'_load',counted_load)
    monkeypatch.setattr(store_subject,'require_file_fingerprints',changed_require)
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_FILE_SET_CHANGED$')):
        store.inspect()
    monkeypatch.setattr(store,'_connect',original_connect)
    monkeypatch.setattr(store,'_load',original_load)
    monkeypatch.setattr(store_subject,'require_file_fingerprints',original_require)
    assert load_calls==0 and path.read_bytes()==before
    assert store.inspect()['retained_receipts']==1


@pytest.mark.parametrize(('seam','expected'),(
    ('binding','CONNECTION_BINDING_CHANGED'),
    ('content','CONNECTION_CONTENT_CHANGED')))
def test_receipt_writer_refuses_foreign_connection_before_mutation(
        tmp_path,monkeypatch,seam,expected):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    store=PaperProviderReadReviewStore(path,budget,[gate])
    state=store.inspect();scope=state['receipts'][-1]['scope']
    receipt=state['receipts'][-1]['receipt_sha256']
    foreign=tmp_path/'foreign.sqlite3';shutil.copyfile(path,foreign)
    foreign_db=sqlite3.connect(foreign)
    try:foreign_db.execute('PRAGMA user_version=1');foreign_db.commit()
    finally:foreign_db.close()
    original_connect=store._connect
    class SpoofedConnection(sqlite3.Connection):
        def execute(self,statement,*args,**kwargs):
            if seam=='content' and statement=='PRAGMA database_list':
                return SimpleNamespace(fetchall=lambda:[
                    (0,'main',str(path.resolve()))])
            return super().execute(statement,*args,**kwargs)
    def substitute(write=False):
        if not write:return original_connect(write=write)
        db=sqlite3.connect(foreign.resolve().as_uri()+'?mode=rw',uri=True,
            timeout=10,factory=SpoofedConnection)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        return db,False
    before=(path.read_bytes(),foreign.read_bytes())
    monkeypatch.setattr(store,'_connect',substitute)
    with pytest.raises(ValueError,match=(
            f'^PAPER_PROVIDER_READ_REVIEW_STORE_{expected}$')):
        store.append(scope,expected_head=state['head_sha256'],
            admission=lambda:receipt)
    monkeypatch.setattr(store,'_connect',original_connect)
    assert (path.read_bytes(),foreign.read_bytes())==before
    assert store.inspect()['retained_receipts']==1


def test_receipt_writer_rechecks_path_content_after_connection_serialization(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    store=PaperProviderReadReviewStore(path,budget,[gate])
    state=store.inspect();scope=state['receipts'][-1]['scope']
    receipt=state['receipts'][-1]['receipt_sha256'];before=path.read_bytes()
    original_connect=store._connect
    original_fingerprint=store_subject._main_fingerprint
    serialized=False
    class SerializationRaceConnection(sqlite3.Connection):
        def serialize(self,*args,**kwargs):
            nonlocal serialized
            result=super().serialize(*args,**kwargs);serialized=True
            return result
    def observed_connection(write=False):
        if not write:return original_connect(write=write)
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,
            timeout=10,factory=SerializationRaceConnection)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        return db,False
    def changed_fingerprint(value):
        result=original_fingerprint(value)
        if serialized:
            replacement='f'*64 if result[-1]!='f'*64 else 'e'*64
            return (*result[:-1],replacement)
        return result
    monkeypatch.setattr(store,'_connect',observed_connection)
    monkeypatch.setattr(store_subject,'_main_fingerprint',changed_fingerprint)
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED$')):
        store.append(scope,expected_head=state['head_sha256'],
            admission=lambda:receipt)
    monkeypatch.setattr(store,'_connect',original_connect)
    monkeypatch.setattr(store_subject,'_main_fingerprint',original_fingerprint)
    assert serialized and path.read_bytes()==before
    assert store.inspect()['retained_receipts']==1


def test_fresh_receipt_connection_binding_precedes_schema_mutation(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path/'source')
    foreign=tmp_path/'foreign.sqlite3'
    subject.retain_review(foreign,budget,[gate],**heads(gate,budget))
    path=tmp_path/'target'/'review.sqlite3';options=heads(gate,budget)
    original_connect=store_subject.sqlite3.connect
    target_uri=path.resolve().as_uri()+'?mode=rw'
    def substitute(*args,**kwargs):
        if args and args[0]==target_uri:
            return original_connect(
                foreign.resolve().as_uri()+'?mode=rw',uri=True,timeout=10)
        return original_connect(*args,**kwargs)
    before=foreign.read_bytes()
    monkeypatch.setattr(store_subject.sqlite3,'connect',substitute)
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_BINDING_CHANGED$')):
        subject.retain_review(path,budget,[gate],**options)
    monkeypatch.setattr(store_subject.sqlite3,'connect',original_connect)
    assert not path.exists() and foreign.read_bytes()==before
    receipt=subject.retain_review(path,budget,[gate],**options)
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert replay['retained_receipts']==1
    assert receipt['head_sha256']==replay['head_sha256']


def test_existing_receipt_content_is_pinned_before_write_setup(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    store=PaperProviderReadReviewStore(path,budget,[gate])
    state=store.inspect();scope=state['receipts'][-1]['scope']
    receipt=state['receipts'][-1]['receipt_sha256']
    foreign=tmp_path/'foreign.sqlite3';shutil.copyfile(path,foreign)
    foreign_db=sqlite3.connect(foreign)
    try:foreign_db.execute('PRAGMA user_version=1');foreign_db.commit()
    finally:foreign_db.close()
    original_connect=store_subject.sqlite3.connect
    target_uri=path.resolve().as_uri()+'?mode=rw'
    class SpoofedConnection(sqlite3.Connection):
        def execute(self,statement,*args,**kwargs):
            if statement=='PRAGMA database_list':
                return SimpleNamespace(fetchall=lambda:[
                    (0,'main',str(path.resolve()))])
            return super().execute(statement,*args,**kwargs)
    def substitute(*args,**kwargs):
        if args and args[0]==target_uri:
            return original_connect(foreign.resolve().as_uri()+'?mode=rw',
                uri=True,timeout=10,factory=SpoofedConnection)
        return original_connect(*args,**kwargs)
    before=(path.read_bytes(),foreign.read_bytes())
    monkeypatch.setattr(store_subject.sqlite3,'connect',substitute)
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED$')):
        store.append(scope,expected_head=state['head_sha256'],
            admission=lambda:receipt)
    monkeypatch.setattr(store_subject.sqlite3,'connect',original_connect)
    assert (path.read_bytes(),foreign.read_bytes())==before
    assert store.inspect()['retained_receipts']==1


def test_receipt_writer_rechecks_committed_file_after_connection_close(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    store=PaperProviderReadReviewStore(path,budget,[gate])
    state=store.inspect();scope=state['receipts'][-1]['scope']
    receipt=state['receipts'][-1]['receipt_sha256'];before=path.read_bytes()
    original_connect=store._connect;close_mutated=False
    class CloseMutationConnection(sqlite3.Connection):
        def close(self):
            nonlocal close_mutated
            super().close()
            if not close_mutated:
                current=path.stat()
                os.utime(path,ns=(current.st_atime_ns,
                    current.st_mtime_ns+1_000_000_000))
                close_mutated=True
    def observed_connection(write=False):
        if not write:return original_connect(write=write)
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,
            timeout=10,factory=CloseMutationConnection)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        return db,False
    monkeypatch.setattr(store,'_connect',observed_connection)
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_FILE_SET_CHANGED$')):
        store.append(scope,expected_head=state['head_sha256'],
            admission=lambda:receipt)
    monkeypatch.setattr(store,'_connect',original_connect)
    replay=store.inspect()
    retry=store.append(scope,expected_head=replay['head_sha256'],
        admission=lambda:receipt)
    final=store.inspect()
    assert close_mutated and path.read_bytes()==before
    assert replay['retained_receipts']==final['retained_receipts']==1
    assert retry['duplicate'] is True
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


def test_receipt_writer_close_error_requires_explicit_duplicate_recovery(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    first=subject.retain_review(path,budget,[gate],**heads(gate,budget))
    store=PaperProviderReadReviewStore(path,budget,[gate])
    state=store.inspect();scope=dict(state['receipts'][-1]['scope'])
    scope['review_sha256']='b'*64
    receipt=store_subject.sha(scope);original_connect=store._connect
    class CloseFailureConnection(sqlite3.Connection):
        def close(self):
            super().close()
            raise RuntimeError('private close failure')
    def close_failure(write=False):
        if not write:return original_connect(write=write)
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,
            timeout=10,factory=CloseFailureConnection)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        return db,False
    monkeypatch.setattr(store,'_connect',close_failure)
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$')):
        store.append(scope,expected_head=first['head_sha256'],
            admission=lambda:receipt)
    monkeypatch.setattr(store,'_connect',original_connect)
    replay=store.inspect()
    retry=store.append(scope,expected_head=replay['head_sha256'],
        admission=lambda:receipt)
    final=store.inspect()
    assert replay['retained_receipts']==final['retained_receipts']==2
    assert retry['duplicate'] is True
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


def test_receipt_reader_close_error_requires_explicit_retry(tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    first=subject.retain_review(path,budget,[gate],**heads(gate,budget))
    store=PaperProviderReadReviewStore(path,budget,[gate])
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
        return db,False
    monkeypatch.setattr(store,'_connect',close_failure)
    with pytest.raises(ValueError,match=(
            '^PAPER_PROVIDER_READ_REVIEW_STORE_READ_ACKNOWLEDGEMENT_UNCERTAIN$')):
        store.inspect()
    monkeypatch.setattr(store,'_connect',original_connect)
    retry=store.inspect()
    assert retry['retained_receipts']==1
    assert retry['head_sha256']==first['head_sha256']


def test_exact_duplicate_is_idempotent_and_revocation_fails_closed(tmp_path):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';options=heads(gate,budget)
    first=subject.retain_review(path,budget,[gate],**options);saved=path.read_bytes()
    second=subject.retain_review(path,budget,[gate],expected_store_head=first['head_sha256'],**options)
    assert second['duplicate'] and path.read_bytes()==saved
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        subject.retain_review(path,budget,[gate],expected_store_head=first['head_sha256'],precommit_check=lambda:False,**options)
    assert path.read_bytes()==saved


@pytest.mark.parametrize('callback_error',(False,True))
def test_retained_receipt_pins_sources_across_precommit_callback(
        tmp_path,callback_error):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    before={source:source.read_bytes() for source in (budget.path,gate.path)}
    def mutate_source():
        _mutate_metadata_and_fail(gate.path) if callback_error else os.utime(
            gate.path,ns=(os.stat(gate.path).st_atime_ns,
                os.stat(gate.path).st_mtime_ns+1_000_000_000))
        return True
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_FILE_SET_CHANGED$'):
        subject.retain_review(path,budget,[gate],precommit_check=mutate_source,
            **heads(gate,budget))
    assert not path.exists()
    assert before=={source:source.read_bytes() for source in before}


def test_retained_receipt_recovers_exact_lost_acknowledgement(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';calls=0
    original=PaperProviderReadReviewStore.append
    def lost_ack(store,*args,**kwargs):
        nonlocal calls
        result=original(store,*args,**kwargs);calls+=1
        raise RuntimeError('private lost receipt acknowledgement')
    monkeypatch.setattr(PaperProviderReadReviewStore,'append',lost_ack)
    receipt=subject.retain_review(path,budget,[gate],**heads(gate,budget))
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert calls==1 and not receipt['duplicate']
    assert replay['retained_receipts']==1
    assert receipt['head_sha256']==replay['head_sha256']
    assert receipt['receipt_sha256']==replay['receipts'][0]['receipt_sha256']


def test_retained_receipt_rejects_fabricated_write_return(tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    monkeypatch.setattr(PaperProviderReadReviewStore,'append',lambda *args,**kwargs:
        dict(mode='LOCAL_PROVIDER_READ_REVIEW_RECEIPT',head_sha256='f'*64,
            receipt_sha256='e'*64,duplicate=False,read_only=True,
            ready_for_routing=False,release_approved=False))
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_DURABLE_STATE_CHANGED$'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    assert not path.exists()


@pytest.mark.parametrize('callback_error',(False,True))
def test_failed_first_receipt_append_restores_absence_and_exact_retry(
        tmp_path,callback_error):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';calls=0
    options=heads(gate,budget)
    def revoke_inside_transaction():
        nonlocal calls
        calls+=1
        if calls==2:
            if callback_error:raise RuntimeError('private callback failure')
            return False
        return True
    expected=('PAPER_PROVIDER_READ_REVIEW_DURABLE_STATE_CHANGED' if
        callback_error else 'PAPER_PROVIDER_READ_REVIEW_STORE_INPUT_REVOKED')
    with pytest.raises(ValueError,match=f'^{expected}$'):
        subject.retain_review(path,budget,[gate],
            precommit_check=revoke_inside_transaction,**options)
    assert calls==2 and not path.exists()
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    receipt=subject.retain_review(path,budget,[gate],**options)
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert replay['retained_receipts']==1
    assert receipt['head_sha256']==replay['head_sha256']


def test_uncommitted_cleanup_never_deletes_replacement(tmp_path):
    path=tmp_path/'review.sqlite3';path.touch(mode=0o600)
    identity=guard_subject.capture_file_identities((path,),
        'PAPER_PROVIDER_READ_REVIEW_STORE_')
    replacement=tmp_path/'replacement.sqlite3';replacement.touch(mode=0o600)
    os.replace(replacement,path)
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$'):
        store_subject._discard_uncommitted_new(path,identity)
    assert path.exists() and path.stat().st_size==0


def test_uncommitted_cleanup_retains_exact_nonempty_file(tmp_path):
    path=tmp_path/'review.sqlite3';content=b'private uncommitted state'
    path.write_bytes(content)
    identity=guard_subject.capture_file_identities((path,),
        'PAPER_PROVIDER_READ_REVIEW_STORE_')
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$'):
        store_subject._discard_uncommitted_new(path,identity)
    assert path.read_bytes()==content


def test_fresh_receipt_identity_is_required_before_sqlite_connect(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    original=store_subject.require_file_identities;substituted=False
    def require(paths,expected,prefix):
        nonlocal substituted
        if not substituted:
            substituted=True
            replacement=tmp_path/'replacement.sqlite3'
            replacement.touch(mode=0o600);os.replace(replacement,path)
        return original(paths,expected,prefix)
    monkeypatch.setattr(store_subject,'require_file_identities',require)
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$'):
        PaperProviderReadReviewStore(path,budget,[gate])._connect(write=True)
    assert substituted and path.exists() and path.stat().st_size==0


@pytest.mark.parametrize('failure_after',(None,'PRAGMA database_list',
    'PRAGMA busy_timeout','PRAGMA journal_mode','PRAGMA synchronous',
    'PRAGMA page_size','PRAGMA max_page_count','BEGIN IMMEDIATE',
    'CREATE TABLE'))
def test_fresh_receipt_identity_is_rechecked_through_sqlite_setup(
        tmp_path,monkeypatch,failure_after):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
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
            state['created_identity']=guard_subject.capture_file_identities(
                (path,),'PAPER_PROVIDER_READ_REVIEW_STORE_')
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
        PaperProviderReadReviewStore(path,budget,[gate])._connect(write=True)
    assert state['failed'] and state['connects']==1
    if path.exists():
        assert str(raised.value)==(
            'PAPER_PROVIDER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN')
        assert path.stat().st_size>0
        assert guard_subject.capture_file_identities((path,),
            'PAPER_PROVIDER_READ_REVIEW_STORE_')==state['created_identity']
    else:
        assert str(raised.value)==(
            'PAPER_PROVIDER_READ_REVIEW_STORE_FILE_IDENTITY_CHANGED')
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))


@pytest.mark.parametrize('failure_sql',(None,'PRAGMA busy_timeout',
    'PRAGMA journal_mode','PRAGMA synchronous','PRAGMA page_size',
    'PRAGMA max_page_count','BEGIN IMMEDIATE','CREATE TABLE'))
def test_fresh_receipt_initialization_failure_restores_absence_and_exact_retry(
        tmp_path,monkeypatch,failure_sql):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    options=heads(gate,budget);target=path.resolve().as_uri()
    original=store_subject.sqlite3.connect;failed=False
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
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_DURABLE_STATE_CHANGED$'):
        subject.retain_review(path,budget,[gate],**options)
    assert failed and not path.exists()
    assert all(not Path(str(path)+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    receipt=subject.retain_review(path,budget,[gate],**options)
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert replay['retained_receipts']==1
    assert receipt['head_sha256']==replay['head_sha256']


def test_fresh_receipt_initialization_close_uncertainty_retains_path(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    options=heads(gate,budget);target=path.resolve().as_uri()
    original=store_subject.sqlite3.connect
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
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN$'):
        subject.retain_review(path,budget,[gate],**options)
    assert path.exists() and path.stat().st_size==0


def test_receipt_commit_flushes_main_file_and_new_posix_directory(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    original=store_subject.os.fsync;calls=[]
    def fsync(descriptor):
        mode=os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    first=subject.retain_review(path,budget,[gate],**heads(gate,budget))
    assert calls==(['file'] if os.name=='nt' else ['file','directory'])
    calls.clear()
    duplicate=subject.retain_review(path,budget,[gate],
        expected_store_head=first['head_sha256'],**heads(gate,budget))
    assert duplicate['duplicate'] is True and calls==['file']
    assert PaperProviderReadReviewStore(
        path,budget,[gate]).inspect()['retained_receipts']==1


def test_receipt_main_file_flush_uncertainty_retains_exact_commit_for_retry(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';failed=False
    original=store_subject.os.fsync
    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed=True
            raise OSError('private main-file flush failure')
        return original(descriptor)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert failed and replay['retained_receipts']==1
    monkeypatch.setattr(store_subject.os,'fsync',original)
    retry=subject.retain_review(path,budget,[gate],
        expected_store_head=replay['head_sha256'],**heads(gate,budget))
    final=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert retry['duplicate'] is True and final['retained_receipts']==1
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


@pytest.mark.parametrize('signal',(KeyboardInterrupt,SystemExit))
def test_receipt_commit_sync_preserves_process_control_signals(
        tmp_path,monkeypatch,signal):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    original=store_subject.os.fsync
    monkeypatch.setattr(store_subject.os,'fsync',lambda _descriptor:(
        (_ for _ in ()).throw(signal())))
    with pytest.raises(signal):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    monkeypatch.setattr(store_subject.os,'fsync',original)
    retained=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    retry=subject.retain_review(path,budget,[gate],
        expected_store_head=retained['head_sha256'],**heads(gate,budget))
    assert retained['retained_receipts']==1
    assert retry['duplicate'] is True
    assert retry['head_sha256']==retained['head_sha256']


@pytest.mark.skipif(os.name=='nt',reason='Python exposes no portable Windows directory fsync')
def test_receipt_parent_flush_uncertainty_retains_exact_commit_for_retry(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';failed=False
    original=store_subject.os.fsync
    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed=True
            raise OSError('private directory flush failure')
        return original(descriptor)
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert failed and replay['retained_receipts']==1
    monkeypatch.setattr(store_subject.os,'fsync',original)
    retry=subject.retain_review(path,budget,[gate],
        expected_store_head=replay['head_sha256'],**heads(gate,budget))
    final=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert retry['duplicate'] is True and final['retained_receipts']==1
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


@pytest.mark.skipif(os.name=='nt',reason='Python exposes no portable Windows directory fsync')
def test_receipt_parent_flush_binds_exact_directory_descriptor(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
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
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    assert substituted and directory_syncs==0
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert replay['retained_receipts']==1
    monkeypatch.setattr(store_subject.os,'open',original_open)
    monkeypatch.setattr(store_subject.os,'fsync',original_fsync)
    retry=subject.retain_review(path,budget,[gate],
        expected_store_head=replay['head_sha256'],**heads(gate,budget))
    final=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert retry['duplicate'] is True and final['retained_receipts']==1
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


def test_receipt_flush_rejects_same_identity_change_before_fingerprint_recheck(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';fsynced=False
    changed=False;original_fsync=store_subject.os.fsync
    original_require=store_subject.require_file_identities
    def fsync(descriptor):
        nonlocal fsynced
        result=original_fsync(descriptor)
        if stat.S_ISREG(os.fstat(descriptor).st_mode):fsynced=True
        return result
    def require(*args,**kwargs):
        nonlocal changed
        result=original_require(*args,**kwargs)
        if fsynced and not changed:
            changed=True;state=path.stat()
            os.utime(path,ns=(state.st_atime_ns,
                state.st_mtime_ns+1_000_000_000))
        return result
    monkeypatch.setattr(store_subject.os,'fsync',fsync)
    monkeypatch.setattr(store_subject,'require_file_identities',require)
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert changed and replay['retained_receipts']==1
    monkeypatch.setattr(store_subject.os,'fsync',original_fsync)
    monkeypatch.setattr(store_subject,'require_file_identities',original_require)
    retry=subject.retain_review(path,budget,[gate],
        expected_store_head=replay['head_sha256'],**heads(gate,budget))
    final=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert retry['duplicate'] is True and final['retained_receipts']==1
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


def test_receipt_flush_hashes_exact_writable_descriptor_before_sync(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
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
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN$'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    assert tampered and saved and sync_calls==0
    monkeypatch.setattr(store_subject.os,'open',original_open)
    monkeypatch.setattr(store_subject.os,'fsync',original_fsync)
    path.write_bytes(saved)
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert replay['retained_receipts']==1
    retry=subject.retain_review(path,budget,[gate],
        expected_store_head=replay['head_sha256'],**heads(gate,budget))
    final=PaperProviderReadReviewStore(path,budget,[gate]).inspect()
    assert retry['duplicate'] is True and final['retained_receipts']==1
    assert retry['head_sha256']==replay['head_sha256']==final['head_sha256']


@pytest.mark.parametrize('callback_error',(False,True))
def test_receipt_store_fingerprint_is_rechecked_after_commit(
        tmp_path,callback_error):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';calls=0
    def mutate_store():
        nonlocal calls
        calls+=1
        if calls==3:
            state=os.stat(path)
            os.utime(path,ns=(state.st_atime_ns,state.st_mtime_ns+1_000_000_000))
            if callback_error:raise RuntimeError('private postcommit callback failure')
        return True
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_FILE_SET_CHANGED$'):
        subject.retain_review(path,budget,[gate],precommit_check=mutate_store,
            **heads(gate,budget))
    assert calls==3 and PaperProviderReadReviewStore(
        path,budget,[gate]).inspect()['retained_receipts']==1


def test_receipt_store_fingerprint_is_rechecked_inside_transaction(tmp_path):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';calls=0
    first=subject.retain_review(path,budget,[gate],**heads(gate,budget))
    def mutate_store():
        nonlocal calls
        calls+=1
        if calls==2:
            state=os.stat(path)
            os.utime(path,ns=(state.st_atime_ns,state.st_mtime_ns+1_000_000_000))
        return True
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_FILE_SET_CHANGED$'):
        subject.retain_review(path,budget,[gate],
            expected_store_head=first['head_sha256'],
            precommit_check=mutate_store,**heads(gate,budget))
    assert calls==2 and PaperProviderReadReviewStore(
        path,budget,[gate]).inspect()['retained_receipts']==1


def test_receipt_store_identity_failure_wins_during_return_finalization(
        tmp_path,monkeypatch):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';calls=0;late=False
    original=guard_subject.require_file_identities
    def require(*args,**kwargs):
        if late:
            raise ValueError(
                'PAPER_PROVIDER_READ_REVIEW_STORE_FILE_IDENTITY_CHANGED')
        return original(*args,**kwargs)
    def late_failure():
        nonlocal calls,late
        calls+=1
        if calls==3:late=True
        return True
    monkeypatch.setattr(guard_subject,'require_file_identities',require)
    with pytest.raises(ValueError,match=
            '^PAPER_PROVIDER_READ_REVIEW_STORE_FILE_IDENTITY_CHANGED$'):
        subject.retain_review(path,budget,[gate],precommit_check=late_failure,
            **heads(gate,budget))
    assert calls==3


def test_store_rejects_non_digest_head_with_store_scoped_error(tmp_path):
    with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_REVIEW_STORE_DIGEST_INVALID'):
        store_subject._digest(True)


def test_incomplete_review_never_creates_receipt_store(tmp_path):
    gate,budget=build(tmp_path,count=4);path=tmp_path/'review.sqlite3'
    with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_REVIEW_INCOMPLETE'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    assert not path.exists()


def test_store_path_cannot_collide_with_any_source_journal(tmp_path):
    gate,budget=build(tmp_path);options=heads(gate,budget)
    for path in (gate.path,budget.path,budget.path.with_name(budget.path.name+'-wal')):
        with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
            subject.retain_review(path,budget,[gate],**options)


def test_tampered_receipt_chain_is_rejected(tmp_path):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    with sqlite3.connect(path) as db:db.execute("UPDATE records SET previous=? WHERE seq=1",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):
        PaperProviderReadReviewStore(path,budget,[gate]).inspect()


def contended_append(tmp_path,seed):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';options=heads(gate,budget)
    first=subject.retain_review(path,budget,[gate],**options)
    store=PaperProviderReadReviewStore(path,budget,[gate])
    scope=dict(store.inspect()['receipts'][0]['scope'],review_sha256=sha(dict(seed=seed)))
    receipt=sha(scope)
    return store,scope,receipt,first['head_sha256']


def test_writer_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'writer');before=store.path.read_bytes()
    monkeypatch.setattr(store_subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match=r'^PAPER_PROVIDER_READ_REVIEW_STORE_DATABASE_BUSY$'):
            store.append(scope,expected_head=head,admission=lambda:receipt)
    assert store.path.read_bytes()==before
    assert not store.append(scope,expected_head=head,admission=lambda:receipt)['duplicate']


def test_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    store,scope,receipt,head=contended_append(tmp_path,'commit');before=store.path.read_bytes()
    monkeypatch.setattr(store_subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN');blocker.execute('SELECT count(*) FROM records').fetchone()
        with pytest.raises(ValueError,match=r'^PAPER_PROVIDER_READ_REVIEW_STORE_DATABASE_BUSY$'):
            store.append(scope,expected_head=head,admission=lambda:receipt)
    assert store.path.read_bytes()==before
    assert not store.append(scope,expected_head=head,admission=lambda:receipt)['duplicate']


def test_read_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    store,_,_,_=contended_append(tmp_path,'read');expected=store.inspect()
    monkeypatch.setattr(store_subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match=r'^PAPER_PROVIDER_READ_REVIEW_STORE_DATABASE_BUSY$'):store.inspect()
    assert store.inspect()==expected
