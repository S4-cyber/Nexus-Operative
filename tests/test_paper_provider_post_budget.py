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
