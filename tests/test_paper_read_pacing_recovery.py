"""Reviewed account pacing recovery uses disposable local journals only."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from nexus import paper_read_pacing_recovery as subject
from nexus.paper_read_pacing import PaperReadPacing
from nexus.paper_read_pacing_recovery import recover
from test_alpaca_paper_inspection import ACCOUNT,OTHER


def blocked(tmp_path,status=401):
    gate=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    token=gate.begin(admission=lambda:1000)
    gate.finish(token,admission=lambda:1001,status_code=status)
    return gate


@pytest.mark.parametrize('status,outcome',[(401,'AUTH_DENIED'),(302,'REVIEW_REQUIRED')])
def test_exact_reviewed_recovery_preserves_history_cooldown_and_capacity(tmp_path,status,outcome):
    gate=blocked(tmp_path,status);before=gate.inspect();history=gate.operation_history()
    with gate.ownership() as owner:
        result=gate.recover_review_block(expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002,ownership=owner)
    after=gate.inspect()
    assert result['mode']=='PAPER_READ_PACING_REVIEWED_RECOVERY'
    assert result['blocked_outcome']==outcome and result['review_block_cleared']
    assert result['uncertainty_preserved'] and not result['cooldown_shortened']
    assert not result['history_rewritten'] and not result['session_capacity_reclaimed']
    assert after['recoveries']==1 and not after['review_required']
    assert after['sessions']==before['sessions']==1 and after['next_allowed_at']==before['next_allowed_at']
    assert gate.operation_history()==history
    with pytest.raises(ValueError,match='COOLDOWN'):
        gate.begin(admission=lambda:before['next_allowed_at']-1)
    assert gate.begin(admission=lambda:before['next_allowed_at'])


def test_interrupted_unknown_stays_blocked_until_explicit_recovery(tmp_path):
    gate=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1000,ownership=owner,operation='BTC_QUOTE_OBSERVATION')
    pending=gate.inspect()
    gate.interrupt(token,expected_head=pending['head_sha256'],expected_account_id=ACCOUNT,
        confirm_interrupted=True,admission=lambda:1001)
    interrupted=gate.inspect();history=gate.operation_history()
    assert interrupted['review_required'] and interrupted['next_allowed_at']==87401
    with pytest.raises(ValueError,match='BLOCKED_REVIEW'):
        gate.begin(admission=lambda:87401)
    with gate.ownership() as owner:
        result=gate.recover_review_block(expected_head=interrupted['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002,ownership=owner)
    assert result['next_allowed_at']==87401
    assert gate.inspect()['last_outcome']=='INTERRUPTED_UNKNOWN'
    assert gate.operation_history()==history
    with pytest.raises(ValueError,match='COOLDOWN'):
        gate.begin(admission=lambda:87400)
    assert gate.begin(admission=lambda:87401)


@pytest.mark.parametrize('change,code',[
    ('head','HEAD_DISAGREEMENT'),('account','ACCOUNT_DISAGREEMENT'),
    ('review','CONFIRMATION_REQUIRED'),('uncertainty','CONFIRMATION_REQUIRED')])
def test_scope_and_confirmation_failures_preserve_exact_bytes(tmp_path,change,code):
    gate=blocked(tmp_path);state=gate.inspect();before=gate.path.read_bytes()
    options=dict(expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
        confirm_block_reviewed=True,confirm_uncertainty_preserved=True,admission=lambda:1002)
    if change=='head':options['expected_head']='f'*64
    if change=='account':options['expected_account_id']=OTHER
    if change=='review':options['confirm_block_reviewed']=False
    if change=='uncertainty':options['confirm_uncertainty_preserved']=False
    with gate.ownership() as owner:
        with pytest.raises(ValueError,match=code):
            gate.recover_review_block(**options,ownership=owner)
    assert gate.path.read_bytes()==before and gate.inspect()==state


def test_recovery_is_not_a_cap_reset_or_second_unblock(tmp_path,monkeypatch):
    import nexus.paper_read_pacing as subject
    gate=blocked(tmp_path);state=gate.inspect();monkeypatch.setattr(subject,'MAX_SESSIONS',1)
    with gate.ownership() as owner:
        gate.recover_review_block(expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
            confirm_block_reviewed=True,confirm_uncertainty_preserved=True,
            admission=lambda:1002,ownership=owner)
    after=gate.inspect();before=gate.path.read_bytes()
    with gate.ownership() as owner:
        with pytest.raises(ValueError,match='RECOVERY_NOT_REQUIRED'):
            gate.recover_review_block(expected_head=after['head_sha256'],expected_account_id=ACCOUNT,
                confirm_block_reviewed=True,confirm_uncertainty_preserved=True,
                admission=lambda:1061,ownership=owner)
    with pytest.raises(ValueError,match='SESSION_CAP'):gate.begin(admission=lambda:1061)
    assert gate.path.read_bytes()==before


def test_cli_missing_and_private_failures_are_sanitized_without_creation(tmp_path):
    missing=tmp_path/'missing'/'pacing.sqlite3'
    result=subprocess.run([sys.executable,'-m','nexus.paper_read_pacing_recovery',
        '--pacing',str(missing),'--expected-head','0'*64,'--expected-account',ACCOUNT,
        '--confirm-reviewed-block','--confirm-preserve-uncertainty'],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==2 and result.stdout.strip()=='PAPER_READ_PACING_RECOVERY_FAILED'
    assert not result.stderr and not missing.parent.exists()


def test_recovery_wrapper_requires_existing_exact_source(tmp_path):
    gate=blocked(tmp_path);state=gate.inspect()
    result=recover(gate.path,expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
        confirm_block_reviewed=True,confirm_uncertainty_preserved=True,admission=lambda:1002)
    assert result['review_block_cleared'] and gate.inspect()['recoveries']==1
    assert result['recovery_acknowledgement_cold_replayed']
    assert result['pacing_file_identity_rechecked']
    assert result['pacing_file_fingerprint_rechecked']
    assert result['pacing_file_set_rechecked_on_return']
    assert result['pacing_ownership_rechecked_on_exit']
    assert all(result[key] is False for key in ('admission_reserved',
        'automatic_retry','automatic_continuation','broker_wide_quota_verified',
        'distributed_ownership_verified','ready_for_routing',
        'live_orders_enabled','money_movement_enabled','release_approved'))


def test_lost_recovery_acknowledgement_cold_replays_exact_commit(
        tmp_path,monkeypatch):
    gate=blocked(tmp_path);before=gate.inspect()
    original=PaperReadPacing.recover_review_block;calls=0
    def commit_then_lose_ack(self,*args,**kwargs):
        nonlocal calls
        calls+=1;original(self,*args,**kwargs)
        raise RuntimeError('private lost recovery acknowledgement')
    monkeypatch.setattr(PaperReadPacing,'recover_review_block',
                        commit_then_lose_ack)
    result=recover(gate.path,expected_head=before['head_sha256'],
        expected_account_id=ACCOUNT,confirm_block_reviewed=True,
        confirm_uncertainty_preserved=True,admission=lambda:1002)
    receipt=gate.recovery_receipt();after=gate.inspect()
    assert calls==1 and result['recovery_acknowledgement_cold_replayed']
    assert result['head_sha256']==receipt['recovery_head_sha256']
    assert after['recoveries']==1 and after['review_required'] is False


@pytest.mark.parametrize('replacement',('raise','fabricate'))
def test_uncommitted_or_fabricated_recovery_ack_is_not_trusted(
        tmp_path,monkeypatch,replacement):
    gate=blocked(tmp_path);before=gate.inspect();original=gate.path.read_bytes()
    def fail_before_commit(*_args,**_kwargs):
        if replacement=='raise':raise RuntimeError('private storage detail')
        return dict(head_sha256='f'*64,review_block_cleared=True)
    monkeypatch.setattr(PaperReadPacing,'recover_review_block',fail_before_commit)
    with pytest.raises(ValueError,
            match='PAPER_READ_PACING_RECOVERY_DURABLE_STATE_CHANGED') as error:
        recover(gate.path,expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002)
    assert 'private storage detail' not in str(error.value)
    assert gate.path.read_bytes()==original and gate.inspect()['review_required']


def test_post_commit_recovery_reader_failure_is_fixed_uncertainty(
        tmp_path,monkeypatch):
    gate=blocked(tmp_path);before=gate.inspect()
    original=PaperReadPacing.recover_review_block;committed=False
    def commit(self,*args,**kwargs):
        nonlocal committed
        result=original(self,*args,**kwargs);committed=True;return result
    def fail_after_commit(self):
        if committed:raise RuntimeError('private reader detail')
        raise AssertionError('receipt called before commit')
    monkeypatch.setattr(PaperReadPacing,'recover_review_block',commit)
    monkeypatch.setattr(PaperReadPacing,'recovery_receipt',fail_after_commit)
    with pytest.raises(ValueError,
            match='PAPER_READ_PACING_RECOVERY_DURABLE_STATE_CHANGED') as error:
        recover(gate.path,expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002)
    assert 'private reader detail' not in str(error.value)
    assert gate.inspect()['recoveries']==1
    assert gate.inspect()['review_required'] is False


def test_pacing_path_replacement_after_commit_is_fixed_uncertainty(
        tmp_path,monkeypatch):
    gate=blocked(tmp_path);before=gate.inspect()
    replacement=tmp_path/'pacing-replacement.sqlite3'
    shutil.copyfile(gate.path,replacement)
    original=PaperReadPacing.recover_review_block
    def commit_then_replace(self,*args,**kwargs):
        result=original(self,*args,**kwargs);os.replace(replacement,self.path)
        return result
    monkeypatch.setattr(PaperReadPacing,'recover_review_block',
                        commit_then_replace)
    with pytest.raises(ValueError,
            match='PAPER_READ_PACING_RECOVERY_FILE_IDENTITY_CHANGED'):
        recover(gate.path,expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002)
    state=gate.inspect()
    assert state['review_required'] and state['recoveries']==0
    assert len(gate.operation_history())==1


def test_pacing_same_file_mutation_after_commit_is_fixed_uncertainty(
        tmp_path,monkeypatch):
    gate=blocked(tmp_path);before=gate.inspect();prior=gate.path.read_bytes()
    original=PaperReadPacing.recovery_receipt;calls=0
    def read_then_mutate_same_file(self):
        nonlocal calls
        receipt=original(self);calls+=1
        if calls==1:
            with self.path.open('r+b') as stream:
                stream.seek(0);stream.write(prior);stream.truncate()
                stream.flush();os.fsync(stream.fileno())
        return receipt
    monkeypatch.setattr(PaperReadPacing,'recovery_receipt',
                        read_then_mutate_same_file)
    with pytest.raises(ValueError,
            match='PAPER_READ_PACING_RECOVERY_FILE_SET_CHANGED'):
        recover(gate.path,expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002)
    state=gate.inspect()
    assert calls==1 and state['review_required']
    assert state['recoveries']==0 and len(gate.operation_history())==1


def test_pacing_mutation_during_return_is_rechecked_on_exit(
        tmp_path,monkeypatch):
    gate=blocked(tmp_path);before=gate.inspect();prior=gate.path.read_bytes()
    original=PaperReadPacing.recovery_receipt;reads=0;head_reads=0
    class LateMutationReceipt(dict):
        def __getitem__(self,key):
            nonlocal head_reads
            value=super().__getitem__(key)
            if key=='recovery_head_sha256':
                head_reads+=1
                if head_reads==2:
                    with gate.path.open('r+b') as stream:
                        stream.seek(0);stream.write(prior);stream.truncate()
                        stream.flush();os.fsync(stream.fileno())
            return value
    def receipt_then_late_mutation(self):
        nonlocal reads
        reads+=1;value=original(self)
        return LateMutationReceipt(value) if reads==1 else value
    monkeypatch.setattr(PaperReadPacing,'recovery_receipt',
                        receipt_then_late_mutation)
    with pytest.raises(ValueError,
            match='PAPER_READ_PACING_RECOVERY_FILE_SET_CHANGED'):
        recover(gate.path,expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002)
    state=gate.inspect()
    assert reads==2 and head_reads==2
    assert state['review_required'] and state['recoveries']==0
    assert len(gate.operation_history())==1


def test_fixture_proves_lost_ack_without_authority():
    result=subject.fixture_report()
    assert result['passed']
    assert all(result[key] is True for key in subject.RECOVERY_CHECKS)
    assert all(result[key] is False for key in subject.RECOVERY_FLAGS)


def test_success_cli_emits_only_allowlisted_recovery_result(
        tmp_path,monkeypatch,capsys):
    gate=blocked(tmp_path);state=gate.inspect()
    monkeypatch.setattr(sys,'argv',['fixture','--pacing',str(gate.path),
        '--expected-head',state['head_sha256'],'--expected-account',ACCOUNT,
        '--confirm-reviewed-block','--confirm-preserve-uncertainty'])
    assert subject.main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['recovery_acknowledgement_cold_replayed']
    assert result['uncertainty_preserved'] and not result['release_approved']
