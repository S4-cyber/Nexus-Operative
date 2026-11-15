"""Durable shared-local provider POST pacing and recovery fences."""
import os
import sqlite3

import pytest

from nexus import paper_provider_post_budget as subject
from nexus.paper_provider_post_budget import PaperProviderPostBudget, ZERO
from test_broker_request_journal import ACCOUNT

DIGEST = 'a' * 64


def begin(budget, *, at=1000, head=ZERO, client='nx_one'):
    owner = budget.ownership(); owner.__enter__()
    try:
        token = budget.begin(account_id=ACCOUNT, request_before_head_sha256=DIGEST,
            payload_sha256='b' * 64, client_order_id=client, admission=lambda:at,
            ownership=owner, expected_head=head)
    except BaseException:
        owner.__exit__(); raise
    return owner, token


def finish(budget, owner, token, *, at=1001, outcome='RESPONSE_OBSERVED', posts=1):
    try:
        return budget.finish(token, outcome=outcome,
            request_after_head_sha256='c' * 64, actual_posts=posts,
            admission=lambda:at, ownership=owner)
    finally:
        owner.__exit__()


def test_missing_budget_is_read_only_and_first_exact_zero_head_is_accepted(tmp_path):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    assert budget.inspect()['head_sha256']==ZERO and not budget.path.exists()
    owner,token=begin(budget);head=finish(budget,owner,token)
    state=budget.inspect();history=budget.history()
    assert state['head_sha256']==head and state['completed_attempts']==1
    assert state['next_allowed_at']==1061 and not state['review_required']
    assert history[0]['actual_posts']==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert history[0]['request_before_head_sha256']==DIGEST


def test_exact_head_initialization_creates_only_provider_binding(tmp_path):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    result=budget.ensure_initialized(expected_head=ZERO)
    state=budget.inspect()
    assert result['provider_binding_initialized'] is True
    assert result['attempt_reserved'] is result['automatic_retry'] is False
    assert result['ready_for_routing'] is result['release_approved'] is False
    assert result['head_sha256']==ZERO and state['attempts']==0
    assert state['head_sha256']!=ZERO
    assert state['completed_attempts']==0 and budget.history()==()
    before=budget.path.read_bytes()
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        budget.ensure_initialized(expected_head='f'*64)
    assert budget.path.read_bytes()==before


def test_failed_first_transaction_restores_absence_and_exact_retry(tmp_path):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        begin(budget,head='f'*64)
    assert not path.exists()
    assert all(not path.with_name(path.name+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    owner,token=begin(budget);head=finish(budget,owner,token)
    assert path.is_file() and budget.inspect()['head_sha256']==head


def test_constructor_pins_parent_alias_before_first_write(tmp_path):
    original=tmp_path/'original';replacement=tmp_path/'replacement'
    original.mkdir();replacement.mkdir();alias=tmp_path/'selected'
    try:alias.symlink_to(original,target_is_directory=True)
    except OSError:pytest.skip('directory symlink unavailable')
    budget=PaperProviderPostBudget(alias/'post.sqlite3')
    alias.unlink();alias.symlink_to(replacement,target_is_directory=True)
    assert budget.path==original.resolve()/'post.sqlite3'
    result=budget.ensure_initialized(expected_head=ZERO)
    assert result['provider_binding_initialized'] is True
    assert budget.path.is_file()
    assert not (replacement/'post.sqlite3').exists()


def test_first_write_cleanup_uses_captured_path_after_object_retarget(
        tmp_path,monkeypatch):
    path=tmp_path/'post.sqlite3';decoy=tmp_path/'decoy.sqlite3'
    decoy.write_bytes(b'do-not-delete');budget=PaperProviderPostBudget(path)
    def connect(*_args,**_kwargs):
        budget.path=decoy
        raise sqlite3.OperationalError('injected retargeted connect failure')
    monkeypatch.setattr(subject.sqlite3,'connect',connect)
    with pytest.raises(ValueError,match='STORE_BINDING_CHANGED'):
        budget.ensure_initialized(expected_head=ZERO)
    assert not path.exists()
    assert decoy.read_bytes()==b'do-not-delete'


def test_first_write_refuses_main_file_replacement_before_sqlite_setup(
        tmp_path,monkeypatch):
    path=tmp_path/'post.sqlite3';replacement=tmp_path/'replacement.sqlite3'
    sqlite3.connect(replacement).close();replacement_bytes=replacement.read_bytes()
    budget=PaperProviderPostBudget(path);original=subject.sqlite3.connect
    def replace_then_connect(*args,**kwargs):
        os.replace(replacement,path)
        return original(*args,**kwargs)
    monkeypatch.setattr(subject.sqlite3,'connect',replace_then_connect)
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        budget.ensure_initialized(expected_head=ZERO)
    assert path.read_bytes()==replacement_bytes


def test_read_refuses_valid_foreign_sqlite_connection(tmp_path,monkeypatch):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    budget.ensure_initialized(expected_head=ZERO)
    foreign=PaperProviderPostBudget(tmp_path/'foreign.sqlite3')
    foreign.ensure_initialized(expected_head=ZERO)
    original=subject.sqlite3.connect;before=budget.path.read_bytes()
    def connect_foreign(*_args,**_kwargs):
        return original(foreign.path)
    monkeypatch.setattr(subject.sqlite3,'connect',connect_foreign)
    with pytest.raises(ValueError,match='CONNECTION_BINDING_CHANGED'):
        budget.inspect()
    assert budget.path.read_bytes()==before


def test_active_ownership_retarget_rolls_back_exact_first_write(tmp_path):
    path=tmp_path/'post.sqlite3';decoy=tmp_path/'decoy.sqlite3'
    budget=PaperProviderPostBudget(path)
    def retarget():
        budget.path=decoy
        return 1000
    with pytest.raises(ValueError,match='STORE_BINDING_CHANGED'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,
                request_before_head_sha256=DIGEST,payload_sha256='b'*64,
                client_order_id='nx_retarget',admission=retarget,
                ownership=owner,expected_head=ZERO)
    assert not path.exists() and not decoy.exists()


def test_existing_write_context_rejects_uncommitted_return(tmp_path):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    budget.ensure_initialized(expected_head=ZERO);before=budget.path.read_bytes()
    with pytest.raises(ValueError,match='UNCOMMITTED_TRANSACTION'):
        with budget._write_connection():
            pass
    assert budget.path.read_bytes()==before


def test_committed_write_ack_rechecks_main_identity(tmp_path,monkeypatch):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    budget.ensure_initialized(expected_head=ZERO)
    armed=False
    original=subject._require_file_identity
    def require(path,identity):
        original(path,identity)
        if armed:subject._fail('FILE_CHANGED')
    monkeypatch.setattr(subject,'_require_file_identity',require)
    def admission():
        nonlocal armed
        armed=True
        return 1000
    with budget.ownership() as owner:
        with pytest.raises(ValueError,match='FILE_CHANGED'):
            budget.begin(account_id=ACCOUNT,
                request_before_head_sha256=DIGEST,payload_sha256='b'*64,
                client_order_id='nx_ack_identity',admission=admission,
                ownership=owner,expected_head=ZERO)
    armed=False
    state=budget.inspect()
    assert state['attempts']==1 and state['pending_token'] is not None
    assert state['ready_for_routing'] is False


def test_main_replacement_during_transaction_cannot_acknowledge(
        tmp_path):
    path=tmp_path/'post.sqlite3';replacement=tmp_path/'replacement.sqlite3'
    budget=PaperProviderPostBudget(path)
    budget.ensure_initialized(expected_head=ZERO)
    with sqlite3.connect(replacement) as db:
        db.execute('CREATE TABLE decoy (value TEXT)')
        db.execute('INSERT INTO decoy VALUES (\'retain\')')
    replacement_bytes=replacement.read_bytes()
    def admission():
        try:os.replace(replacement,path)
        except OSError:pytest.skip('open SQLite main replacement denied')
        return 1000
    with pytest.raises(ValueError,match='FILE_CHANGED'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,
                request_before_head_sha256=DIGEST,payload_sha256='b'*64,
                client_order_id='nx_replace_during_write',admission=admission,
                ownership=owner,expected_head=ZERO)
    assert path.read_bytes()==replacement_bytes


def test_fresh_connection_failure_restores_absence_and_exact_retry(
        tmp_path,monkeypatch):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    original=subject.sqlite3.connect;failed=False
    def connect(*args,**kwargs):
        nonlocal failed
        if not failed:
            failed=True
            raise sqlite3.OperationalError('injected first connect failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(subject.sqlite3,'connect',connect)
    with pytest.raises(sqlite3.OperationalError,
            match='injected first connect failure'):
        budget.ensure_initialized(expected_head=ZERO)
    assert failed and not path.exists()
    result=budget.ensure_initialized(expected_head=ZERO)
    assert result['provider_binding_initialized'] is True
    assert path.is_file() and budget.inspect()['attempts']==0


def test_uncommitted_cleanup_never_deletes_ambiguous_state(tmp_path):
    path=tmp_path/'post.sqlite3';path.touch(mode=0o600)
    observed=path.lstat()
    identity=(str(path.resolve()),observed.st_dev,observed.st_ino)

    replacement=tmp_path/'replacement.sqlite3'
    replacement.touch(mode=0o600);os.replace(replacement,path)
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        subject._discard_uncommitted_new(path,identity)
    assert path.exists()

    observed=path.lstat()
    identity=(str(path.resolve()),observed.st_dev,observed.st_ino)
    sidecar=path.with_name(path.name+'-journal');sidecar.write_bytes(b'private')
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        subject._discard_uncommitted_new(path,identity)
    assert path.exists() and sidecar.read_bytes()==b'private'

    sidecar.unlink();path.write_bytes(b'private uncommitted state')
    observed=path.lstat()
    identity=(str(path.resolve()),observed.st_dev,observed.st_ino)
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        subject._discard_uncommitted_new(path,identity)
    assert path.read_bytes()==b'private uncommitted state'

    linked=tmp_path/'linked.sqlite3';linked.touch(mode=0o600)
    alias=tmp_path/'linked-alias.sqlite3';os.link(linked,alias)
    observed=linked.lstat()
    identity=(str(linked.resolve()),observed.st_dev,observed.st_ino)
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        subject._discard_uncommitted_new(linked,identity)
    assert linked.exists() and alias.exists()


def test_pending_receipt_is_read_only_hash_bound_and_requires_active_lease(tmp_path):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    owner,token=begin(budget);before=path.read_bytes()
    receipt=budget.pending_receipt()
    assert path.read_bytes()==before
    assert receipt['token']==receipt['head_sha256']==token
    assert receipt['request_before_head_sha256']==DIGEST
    assert receipt['payload_sha256']=='b'*64
    assert receipt['admitted_at']==1000
    assert receipt['exact_pending_reservation_replayed'] is True
    assert receipt['read_only'] is True
    assert receipt['automatic_retry'] is receipt['ready_for_routing'] is False
    assert receipt['release_approved'] is False
    confirmation=dict(account_id=ACCOUNT,
        request_before_head_sha256=DIGEST,payload_sha256='b'*64,
        client_order_id='nx_one',admitted_at=1000,ownership=owner)
    assert budget.confirm_active_pending(token,**confirmation)==token
    budget._active_completion=None
    with pytest.raises(ValueError,match='ORIGINAL_LEASE_REQUIRED'):
        budget.confirm_active_pending(token,**confirmation)
    owner.__exit__()


@pytest.mark.parametrize('body_error',(False,True))
def test_ownership_rechecks_lock_path_on_context_exit(
        tmp_path,body_error,monkeypatch):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    lock_path=budget.path.resolve().with_name(budget.path.name+'.owner.lock')
    displaced=tmp_path/'displaced-owner.lock'
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_BUDGET_OWNER_FILE_CHANGED'):
        with budget.ownership() as owner:
            assert owner.identity()
            if os.name=='nt':
                # Windows denies renaming an open locked file. Inject the
                # equivalent late identity failure so this platform still
                # exercises the exit recheck and error priority.
                monkeypatch.setattr(owner,'validate',
                    lambda:subject._fail('OWNER_FILE_CHANGED'))
            else:
                lock_path.rename(displaced)
                fd=os.open(lock_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                os.write(fd,b'0');os.close(fd)
            if body_error:
                raise RuntimeError('private lower context failure')
    assert lock_path.is_file()
    assert displaced.is_file() is (os.name!='nt')


@pytest.mark.parametrize('body_error',(False,True))
def test_ownership_normalizes_missing_path_on_context_exit(
        tmp_path,body_error,monkeypatch):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    original=subject.Path.stat
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_BUDGET_OWNER_FILE_CHANGED'):
        with budget.ownership() as owner:
            def missing(path,*args,**kwargs):
                if path==owner.path:
                    raise FileNotFoundError('private lock path')
                return original(path,*args,**kwargs)
            monkeypatch.setattr(subject.Path,'stat',missing)
            if body_error:
                raise RuntimeError('private lower context failure')


def test_exact_head_and_cooldown_block_without_appending(tmp_path):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    owner,token=begin(budget);finish(budget,owner,token)
    before=budget.path.read_bytes();head=budget.inspect()['head_sha256']
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        begin(budget,at=1061,head=ZERO,client='stale')
    with pytest.raises(ValueError,match='COOLDOWN'):
        begin(budget,at=1060,head=head,client='early')
    assert budget.path.read_bytes()==before


def test_unknown_post_retains_full_day_review_block(tmp_path):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    owner,token=begin(budget);finish(budget,owner,token,
        outcome='OUTCOME_UNKNOWN',posts=1)
    state=budget.inspect()
    assert state['review_required'] and state['next_allowed_at']==87401
    assert state['automatic_retry'] is state['automatic_recovery'] is False
    with pytest.raises(ValueError,match='BLOCKED_REVIEW'):
        begin(budget,at=90000,head=state['head_sha256'],client='blocked')


def test_zero_post_result_must_be_explicit_not_sent(tmp_path):
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    owner,token=begin(budget)
    with pytest.raises(ValueError,match='POST_COUNT_INVALID'):
        budget.finish(token,outcome='OUTCOME_UNKNOWN',request_after_head_sha256=DIGEST,
            actual_posts=0,admission=lambda:1001,ownership=owner)
    owner.__exit__()
    assert budget.inspect()['pending_token']==token


def test_abandoned_lease_requires_exact_confirmed_interruption(tmp_path):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    owner,token=begin(budget);owner.__exit__()
    cold=PaperProviderPostBudget(path);head=cold.inspect()['head_sha256']
    before=path.read_bytes()
    with pytest.raises(ValueError,match='CONFIRMATION_REQUIRED'):
        cold.interrupt(token,expected_head=head,admission=lambda:1002)
    with pytest.raises(ValueError,match='HEAD_DISAGREEMENT'):
        cold.interrupt(token,expected_head='f'*64,confirm_interrupted=True,
            admission=lambda:1002)
    assert path.read_bytes()==before
    result=cold.interrupt(token,expected_head=head,confirm_interrupted=True,
        admission=lambda:1002)
    state=cold.inspect();history=cold.history()
    assert result['uncertainty_preserved'] and state['review_required']
    assert state['next_allowed_at']==87402
    assert history[0]['outcome']=='INTERRUPTED_UNKNOWN'
    assert history[0]['actual_posts'] is None


def test_provider_scope_and_chain_tampering_fail_closed(tmp_path):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    owner,token=begin(budget);finish(budget,owner,token)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE events SET digest=? WHERE seq=1", ('f'*64,));db.commit()
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):
        budget.inspect()
    with pytest.raises(ValueError):
        PaperProviderPostBudget(path,'OTHER_PROVIDER').inspect()


def test_write_lock_contention_fails_closed_then_recovers(tmp_path, monkeypatch):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    owner,token=begin(budget);finish(budget,owner,token)
    head=budget.inspect()['head_sha256'];original=path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(path);blocker.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(ValueError,match='PAPER_PROVIDER_POST_BUDGET_DATABASE_BUSY'):
            begin(budget,at=1061,head=head,client='nx_locked')
    finally:
        blocker.rollback();blocker.close()
    assert path.read_bytes()==original
    owner,token=begin(budget,at=1061,head=head,client='nx_recovered')
    finish(budget,owner,token,at=1062)


def test_commit_lock_contention_rolls_back_then_recovers(tmp_path, monkeypatch):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    owner,token=begin(budget);finish(budget,owner,token)
    head=budget.inspect()['head_sha256'];original=path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(path);blocker.execute('BEGIN')
    blocker.execute('SELECT seq FROM events LIMIT 1').fetchone()
    try:
        with pytest.raises(ValueError,match='PAPER_PROVIDER_POST_BUDGET_DATABASE_BUSY'):
            begin(budget,at=1061,head=head,client='nx_commit_locked')
    finally:
        blocker.rollback();blocker.close()
    assert path.read_bytes()==original
    assert budget.inspect()['head_sha256']==head
    owner,token=begin(budget,at=1061,head=head,client='nx_recovered')
    finish(budget,owner,token,at=1062)


def test_public_read_lock_contention_fails_closed_then_recovers(tmp_path, monkeypatch):
    path=tmp_path/'post.sqlite3';budget=PaperProviderPostBudget(path)
    owner,token=begin(budget);finish(budget,owner,token)
    expected=budget.inspect();original=path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(path);blocker.execute('BEGIN EXCLUSIVE')
    try:
        with pytest.raises(ValueError,match='PAPER_PROVIDER_POST_BUDGET_DATABASE_BUSY'):
            budget.inspect()
    finally:
        blocker.rollback();blocker.close()
    assert path.read_bytes()==original
    assert budget.inspect()==expected
