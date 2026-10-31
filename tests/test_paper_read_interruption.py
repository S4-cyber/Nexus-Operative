"""Actual local ownership/process/SQLite fixtures; HTTP/credentials stay injected."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from nexus import paper_read_pacing as policy
from nexus import alpaca_paper_inspection as transport
from test_alpaca_paper_inspection import ACCOUNT, OTHER, KEY, SECRET, Opener, Response
from test_paper_read_pacing import gate, begin, finish, inspect


def pending(gate):
    with gate.ownership() as owner:
        return gate.begin(admission=lambda:1000, ownership=owner)


def interrupt(gate, **changes):
    state = gate.inspect()
    args = dict(token=state['pending_token'], expected_head=state['head_sha256'],
                expected_account_id=ACCOUNT, confirm_interrupted=True, admission=lambda:1001)
    args.update(changes)
    return gate.interrupt(**args)


def test_owned_interruption_preserves_unknown_history_and_full_day_cooldown(gate):
    token = pending(gate); before = gate.inspect()
    assert before['pending_owner_fenced']
    result = interrupt(gate)
    state = policy.PaperReadPacing(gate.path, ACCOUNT).inspect()
    assert result['interruption_recorded'] and not result['duplicate']
    assert state['pending_token'] is None and not state['pending_owner_fenced']
    assert state['sessions'] == 1 and state['events'] == 2 and state['interruptions'] == 1
    assert state['last_outcome'] == 'INTERRUPTED_UNKNOWN' and state['next_allowed_at'] == 87401
    assert state['last_interruption']['token'] == token
    assert state['last_interruption']['expected_head'] == before['head_sha256']
    assert not result['automatic_retry'] and not result['ready_for_routing']
    with pytest.raises(ValueError, match='BLOCKED_REVIEW'): begin(gate, 87400.99)
    with pytest.raises(ValueError, match='BLOCKED_REVIEW'): begin(gate, 87401)


@pytest.mark.parametrize('consent', [False, None, 1, 'true'])
def test_confirmation_required_before_any_new_file(gate, consent):
    with pytest.raises(ValueError, match='CONFIRMATION_REQUIRED'):
        interrupt(gate, confirm_interrupted=consent)
    assert not gate.path.exists() and not list(gate.path.parent.glob('*.lock'))


@pytest.mark.parametrize('field,value,error', [
    ('expected_account_id', OTHER, 'ACCOUNT_DISAGREEMENT'),
    ('expected_head', '0'*64, 'HEAD_DISAGREEMENT'),
    ('token', '0'*64, 'TOKEN_DISAGREEMENT'),
    ('token', 'bad', 'REVIEW_REFERENCE_INVALID'),
    ('expected_head', None, 'REVIEW_REFERENCE_INVALID'),
])
def test_scope_and_reference_fences_preserve_original_history(gate, field, value, error):
    pending(gate); before = gate.inspect()
    with pytest.raises(ValueError, match=error): interrupt(gate, **{field:value})
    assert gate.inspect() == before


def test_missing_history_not_initialized(gate):
    with pytest.raises(ValueError, match='BINDING_MISSING'):
        interrupt(gate, token='1'*64)
    assert not gate.path.exists() and not list(gate.path.parent.glob('*.lock'))


def test_legacy_pending_read_cannot_be_released_by_unproven_ownership(gate):
    begin(gate); before = gate.inspect()
    assert not before['pending_owner_fenced']
    with pytest.raises(ValueError, match='LEGACY_OWNER_UNVERIFIED'): interrupt(gate)
    assert gate.inspect() == before


@pytest.mark.parametrize('status', [401, 403, 429, 302])
def test_completed_auth_review_and_rate_blocks_cannot_be_cleared(gate, status):
    token = begin(gate)
    finish(gate, token, status_code=status, retry_after='120' if status == 429 else None)
    before = gate.inspect()
    with pytest.raises(ValueError, match='TOKEN_DISAGREEMENT'): interrupt(gate, token=token)
    assert gate.inspect() == before


def test_late_finish_after_interruption_cannot_overwrite_unknown_outcome(gate):
    token = pending(gate); interrupt(gate); before = gate.inspect()
    with pytest.raises(ValueError, match='TOKEN_DISAGREEMENT'): finish(gate, token, at=1002)
    assert gate.inspect() == before


def test_latest_interruption_replay_is_idempotent_without_extending_or_shortening_wait(gate):
    token = pending(gate); head = gate.inspect()['head_sha256']; interrupt(gate)
    before = gate.inspect()
    result = interrupt(gate, token=token, expected_head=head, admission=lambda:2000)
    assert result['duplicate'] and gate.inspect() == before
    with pytest.raises(ValueError,match='BLOCKED_REVIEW'):begin(gate,87401)
    with gate.ownership() as owner:
        gate.recover_review_block(expected_head=before['head_sha256'],expected_account_id=ACCOUNT,
            confirm_block_reviewed=True,confirm_uncertainty_preserved=True,
            admission=lambda:2001,ownership=owner)
    begin(gate,87401)
    later = gate.inspect()
    with pytest.raises(ValueError, match='HEAD_DISAGREEMENT'):
        interrupt(gate, token=token, expected_head=head, admission=lambda:87402)
    assert gate.inspect() == later


def test_active_request_retains_owner_through_fault_cleanup(gate):
    entered, release = threading.Event(), threading.Event()
    opener = Opener([Response(None, raw=b'{invalid')]); original = opener.open
    def blocked(request, timeout):
        entered.set()
        assert release.wait(timeout=10)
        return original(request, timeout)
    opener.open = blocked
    with ThreadPoolExecutor() as pool:
        future = pool.submit(inspect, gate, opener)
        try:
            assert entered.wait(timeout=5)
            before = gate.inspect()
            assert before['pending_owner_fenced']
            with pytest.raises(ValueError, match='OWNER_BUSY_OR_UNAVAILABLE'): interrupt(gate)
            assert gate.inspect() == before
        finally: release.set()
        with pytest.raises(transport.PaperInspectionError): future.result(timeout=10)
    assert interrupt(gate)['interruption_recorded']


def test_actual_process_death_releases_owner_but_preserves_pending_review(gate):
    code = '''from nexus.paper_read_pacing import PaperReadPacing
import sys
g=PaperReadPacing(sys.argv[1],sys.argv[2])
with g.ownership() as owner:
    g.begin(admission=lambda:1000,ownership=owner)
    print('ready',flush=True)
    sys.stdin.read()
'''
    child = subprocess.Popen([sys.executable, '-c', code, str(gate.path), ACCOUNT], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        before = gate.inspect()
        with pytest.raises(ValueError, match='OWNER_BUSY_OR_UNAVAILABLE'): interrupt(gate)
        child.kill(); child.wait(timeout=10)
        assert gate.inspect() == before
        assert interrupt(gate)['interruption_recorded']
    finally:
        if child.poll() is None: child.kill(); child.wait(timeout=10)
        child.stdin.close(); child.stdout.close(); child.stderr.close()


def test_sqlite_wait_rechecks_clock_and_review_head(gate, monkeypatch):
    pending(gate); head = gate.inspect()['head_sha256']; token = gate.inspect()['pending_token']
    entered = threading.Event(); at = [1000]; original = gate._connect
    def connect(*args, **kwargs):
        entered.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(gate, '_connect', connect)
    with sqlite3.connect(gate.path) as db:
        db.execute('BEGIN IMMEDIATE')
        with ThreadPoolExecutor() as pool:
            future = pool.submit(gate.interrupt, token, expected_head=head,
                expected_account_id=ACCOUNT, confirm_interrupted=True, admission=lambda:at[0])
            assert entered.wait(timeout=5)
            at[0] = 1005
            db.commit()
            assert future.result(timeout=10)['interruption_recorded']
    assert gate.inspect()['next_allowed_at'] == 87405


@pytest.mark.parametrize('fail_on', [1, 2])
def test_revocation_or_clock_fault_rolls_back_review(gate, fail_on):
    pending(gate); before = gate.inspect(); calls = []
    def admission():
        calls.append(1)
        if len(calls) == fail_on: raise ValueError('REVOKED')
        return 1001
    with pytest.raises(ValueError, match='REVOKED'): interrupt(gate, admission=admission)
    assert gate.inspect() == before
    # The failed review must also release its OS ownership.
    with gate.ownership(): pass


def test_backward_clock_blocks_review(gate):
    pending(gate); before = gate.inspect()
    with pytest.raises(ValueError, match='CLOCK_REGRESSION'): interrupt(gate, admission=lambda:999)
    assert gate.inspect() == before


def test_process_death_before_interruption_commit_preserves_pending(gate):
    pending(gate); before = gate.inspect()
    code = '''from nexus.paper_read_pacing import PaperReadPacing
import os,sys
g=PaperReadPacing(sys.argv[1],sys.argv[2]); state=g.inspect(); original=g._append
def die(db,seq,body,previous):
    result=original(db,seq,body,previous)
    if body.get('kind')=='INTERRUPTED': os._exit(41)
    return result
g._append=die
g.interrupt(state['pending_token'],expected_head=state['head_sha256'],expected_account_id=sys.argv[2],confirm_interrupted=True,admission=lambda:1001)
'''
    child = subprocess.run([sys.executable,'-c',code,str(gate.path),ACCOUNT],capture_output=True,timeout=20)
    assert child.returncode == 41
    assert gate.inspect() == before
    assert interrupt(gate)['interruption_recorded']


def test_lost_acknowledgment_retains_interruption_and_cooldown(gate, monkeypatch):
    token = pending(gate); head = gate.inspect()['head_sha256']; original = gate.interrupt
    def lose(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError(SECRET)
    monkeypatch.setattr(gate, 'interrupt', lose)
    with pytest.raises(OSError): interrupt(gate)
    state = policy.PaperReadPacing(gate.path, ACCOUNT).inspect()
    assert state['pending_token'] is None and state['interruptions'] == 1 and state['next_allowed_at'] == 87401
    result = policy.PaperReadPacing(gate.path, ACCOUNT).interrupt(token,expected_head=head,
        expected_account_id=ACCOUNT,confirm_interrupted=True,admission=lambda:1002)
    assert result['duplicate']


def test_interrupt_last_session_uses_reserved_completion_space(gate, monkeypatch):
    monkeypatch.setattr(policy,'MAX_SESSIONS',1)
    pending(gate); interrupt(gate); before = gate.inspect()
    assert before['events'] == 2 and before['sessions'] == 1
    with gate.ownership() as owner:
        gate.recover_review_block(expected_head=before['head_sha256'],expected_account_id=ACCOUNT,
            confirm_block_reviewed=True,confirm_uncertainty_preserved=True,
            admission=lambda:1002,ownership=owner)
    with pytest.raises(ValueError, match='SESSION_CAP'): begin(gate, 87401)
    assert gate.inspect()['sessions']==before['sessions']==1


def test_owner_rejects_wrong_instance_closed_lease_and_replaced_file(gate, tmp_path):
    other = policy.PaperReadPacing(tmp_path/'other.db', ACCOUNT)
    with other.ownership() as owner:
        with pytest.raises(ValueError, match='OWNER_REQUIRED'): gate.begin(admission=lambda:1000,ownership=owner)
    with pytest.raises(ValueError, match='OWNER_REQUIRED'): other.begin(admission=lambda:1000,ownership=owner)
    if os.name != 'nt':
        # Windows denies unlinking the held file; POSIX replacement must be detected.
        with pytest.raises(ValueError, match='OWNER_FILE_CHANGED'):
            with gate.ownership() as owner:
                owner.path.unlink(); owner.path.write_bytes(b'0')
                with pytest.raises(ValueError, match='OWNER_FILE_CHANGED'):
                    owner.validate()


@pytest.mark.parametrize('body_error',(False,True))
def test_owner_rechecks_lock_path_on_context_exit(
        gate,tmp_path,body_error,monkeypatch):
    lock_path=gate.path.resolve().with_name(gate.path.name+'.owner.lock')
    displaced=tmp_path/'displaced-read-owner.lock'
    with pytest.raises(ValueError,match='PAPER_READ_PACING_OWNER_FILE_CHANGED'):
        with gate.ownership() as owner:
            assert owner.identity()
            if os.name=='nt':
                monkeypatch.setattr(owner,'validate',
                    lambda:policy._fail('OWNER_FILE_CHANGED'))
            else:
                lock_path.rename(displaced)
                lock_path.write_bytes(b'0')
            if body_error:
                raise RuntimeError('private lower context failure')
    assert lock_path.is_file()
    assert displaced.is_file() is (os.name!='nt')


@pytest.mark.parametrize('body_error',(False,True))
def test_owner_normalizes_missing_path_on_context_exit(
        gate,body_error,monkeypatch):
    original=policy.Path.stat
    with pytest.raises(ValueError,match='PAPER_READ_PACING_OWNER_FILE_CHANGED'):
        with gate.ownership() as owner:
            def missing(path,*args,**kwargs):
                if path==owner.path:
                    raise FileNotFoundError('private lock path')
                return original(path,*args,**kwargs)
            monkeypatch.setattr(policy.Path,'stat',missing)
            if body_error:
                raise RuntimeError('private lower context failure')


def test_owner_symlink_rejected_before_open(gate, tmp_path):
    if os.name == 'nt': pytest.skip('POSIX symlink fixture; native OS locking exercised on Windows')
    target=tmp_path/'target';target.write_bytes(b'0')
    owner=gate.ownership();owner.path.symlink_to(target)
    with pytest.raises(ValueError,match='OWNER_SYMLINK'): owner.__enter__()
    assert target.read_bytes() == b'0'


def test_replaced_lock_file_cannot_release_original_pending_owner(gate):
    pending(gate); before=gate.inspect(); owner=gate.ownership()
    owner.path.rename(owner.path.with_suffix('.previous')); owner.path.write_bytes(b'0')
    with pytest.raises(ValueError,match='OWNER_SCOPE_CHANGED'): interrupt(gate)
    assert gate.inspect()==before


def test_copied_journal_cannot_reuse_original_ownership_marker(gate,tmp_path):
    import shutil
    pending(gate)
    copy=policy.PaperReadPacing(tmp_path/'copied.db',ACCOUNT)
    shutil.copyfile(gate.path,copy.path)
    with pytest.raises(ValueError,match='OWNER_SCOPE_CHANGED'): interrupt(copy)
    assert gate.inspect()['pending_token']==copy.inspect()['pending_token']


def test_hardlinked_database_cannot_bypass_original_local_owner(gate,tmp_path):
    pending(gate); alias=tmp_path/'alias.db'
    try: os.link(gate.path,alias)
    except OSError: pytest.skip('Filesystem does not support hardlinks')
    try:
        with pytest.raises(ValueError,match='HARDLINK'): policy.PaperReadPacing(alias,ACCOUNT).inspect()
        with pytest.raises(ValueError,match='HARDLINK'): gate.inspect()
    finally: alias.unlink()
    assert gate.inspect()['pending_token']


def test_pacing_review_never_clears_original_order_lookup_uncertainty(gate,tmp_path):
    from nexus.broker_request_journal import BrokerRequestJournal
    from test_alpaca_paper_contract import plan
    from test_alpaca_paper_inspection import account
    from test_broker_paper_recovery import recover
    journal=BrokerRequestJournal(tmp_path/'orders.db',ACCOUNT)
    journal.prepare(plan());journal.mark_submission_uncertain('nx_fixture')
    with pytest.raises(transport.PaperInspectionError):
        recover(journal,Opener([account(),Response(None,raw=b'{invalid')]),pacing=gate)
    before=journal.recovery()
    assert journal.pending_lookup()['pending']
    interrupt(gate)
    assert journal.recovery()==before and journal.pending_lookup()['pending']


def test_result_commit_ack_loss_keeps_owner_until_context_cleanup(gate,monkeypatch):
    original=gate.finish; observed=[]
    def lose(*args,**kwargs):
        original(*args,**kwargs)
        with pytest.raises(ValueError,match='OWNER_BUSY_OR_UNAVAILABLE'):
            with gate.ownership(): pass
        observed.append(True)
        raise OSError(SECRET)
    monkeypatch.setattr(gate,'finish',lose)
    with pytest.raises(transport.PaperInspectionError): inspect(gate)
    assert observed==[True] and gate.inspect()['last_outcome']=='SUCCESS'
    with gate.ownership(): pass
