"""Exact-head recovery of a resolved ambiguous provider POST budget tail."""
from contextlib import contextmanager
import json
import os
import shutil
import sys

import pytest

from nexus import paper_provider_post_recovery as subject
from nexus import paper_provider_post_budget as budget_subject
from nexus import broker_request_journal as request_journal_module
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_provider_post_budget import PaperProviderPostBudget, ZERO
from test_alpaca_paper_contract import plan, response
from test_broker_request_journal import ACCOUNT, OTHER


def ambiguous(tmp_path):
    journal = BrokerRequestJournal(tmp_path/'requests.sqlite3', ACCOUNT)
    prepared = plan(); journal.prepare(prepared)
    selected = journal.lookup_request('nx_fixture')
    budget = PaperProviderPostBudget(tmp_path/'post-budget.sqlite3')
    with budget.ownership() as owner:
        token = budget.begin(account_id=ACCOUNT,
            request_before_head_sha256=selected['head_sha256'],
            payload_sha256=prepared['payload_sha256'],
            client_order_id='nx_fixture', admission=lambda:1000,
            ownership=owner, expected_head=ZERO)
        journal.mark_submission_uncertain('nx_fixture')
        budget.finish(token, outcome='OUTCOME_UNKNOWN',
            request_after_head_sha256=journal.recovery()['head_sha256'],
            actual_posts=1, admission=lambda:1001, ownership=owner)
    return journal, budget


def resolve(journal):
    journal.observe('nx_fixture', response(status='filled', filled_qty='2',
        filled_avg_price='99'))
    return journal.recovery()['head_sha256']


def recover(journal, budget, request_head, **changes):
    options = dict(expected_account_id=ACCOUNT, client_order_id='nx_fixture',
        expected_post_budget_head=budget.inspect()['head_sha256'],
        expected_request_head=request_head,
        confirm_original_id_resolved=True, confirm_no_resubmission=True,
        admission=lambda:1002)
    options.update(changes)
    return subject.recover(budget.path, journal.path, **options)


def test_terminal_original_id_resolution_clears_only_shared_budget_review(tmp_path):
    journal,budget=ambiguous(tmp_path);saved_history=budget.history()
    request_head=resolve(journal);result=recover(journal,budget,request_head)
    state=budget.inspect();request=journal.recovery()['requests'][0]
    assert result['mode']=='PAPER_PROVIDER_POST_BUDGET_RESOLVED_UNKNOWN_RECOVERY'
    assert result['review_block_cleared'] and result['original_request_rearmed'] is False
    assert result['request_writer_exclusion_held']
    assert result['request_path_identity_rechecked']
    assert result['request_file_fingerprint_rechecked']
    assert result['request_replay_fingerprint_rechecked_after_load']
    assert result['request_proof_final_before_budget_commit']
    assert result['request_proof_rechecked_after_budget_receipts']
    assert result['request_snapshot_rechecked_on_exit']
    assert result['post_budget_ownership_rechecked_on_exit']
    assert result['recovery_acknowledgement_cold_replayed']
    assert result['post_budget_file_identity_rechecked']
    assert result['post_budget_file_fingerprint_rechecked']
    assert result['cooldown_shortened'] is False and result['uncertainty_preserved']
    assert state['recoveries']==1 and state['review_required'] is False
    recovery=budget.recovery_history()
    assert len(recovery)==1
    assert recovery[0]['request_resolution_head_sha256']==request_head
    assert recovery[0]['blocked_attempt_head_sha256']==saved_history[0]['after_head_sha256']
    assert state['next_allowed_at']==87401 and budget.history()==saved_history
    assert request['submission_attempt_marked'] and request['snapshot']['terminal']
    assert request['recovery_state']=='OBSERVED_TERMINAL_RECONCILIATION_REQUIRED'
    assert all(result[key] is False for key in ('admission_reserved','automatic_retry',
        'automatic_continuation','broker_wide_quota_verified','distributed_ownership_verified',
        'broker_account_reconciled','ready_for_routing','live_orders_enabled',
        'money_movement_enabled','release_approved'))


def test_lost_recovery_acknowledgement_cold_replays_exact_commit(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    original=PaperProviderPostBudget.recover_resolved_unknown
    calls=0
    def commit_then_lose_ack(self,*args,**kwargs):
        nonlocal calls
        calls+=1
        original(self,*args,**kwargs)
        raise RuntimeError('private lost recovery acknowledgement')
    monkeypatch.setattr(PaperProviderPostBudget,'recover_resolved_unknown',
                        commit_then_lose_ack)
    result=recover(journal,budget,request_head)
    state=budget.inspect();receipt=budget.recovery_receipt()
    assert calls==1 and result['recovery_acknowledgement_cold_replayed']
    assert result['head_sha256']==receipt['recovery_head_sha256']
    assert state['recoveries']==1 and state['review_required'] is False
    assert len(budget.recovery_history())==1


@pytest.mark.parametrize('replacement',('raise','fabricate'))
def test_uncommitted_or_fabricated_recovery_ack_is_not_trusted(
        tmp_path, monkeypatch, replacement):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    before=budget.path.read_bytes()
    def fail_before_commit(*_args,**_kwargs):
        if replacement=='raise':
            raise RuntimeError('private recovery storage detail')
        return dict(head_sha256='f'*64,review_block_cleared=True)
    monkeypatch.setattr(PaperProviderPostBudget,'recover_resolved_unknown',
                        fail_before_commit)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_RECOVERY_DURABLE_STATE_CHANGED') as error:
        recover(journal,budget,request_head)
    assert 'private recovery storage detail' not in str(error.value)
    assert budget.path.read_bytes()==before
    assert budget.inspect()['review_required'] is True


def test_post_commit_recovery_reader_failure_is_fixed_uncertainty(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    original_recover=PaperProviderPostBudget.recover_resolved_unknown
    original_receipt=PaperProviderPostBudget.recovery_receipt
    committed=False
    def commit(self,*args,**kwargs):
        nonlocal committed
        result=original_recover(self,*args,**kwargs);committed=True
        return result
    def fail_after_commit(self):
        if committed: raise RuntimeError('private recovery reader detail')
        return original_receipt(self)
    monkeypatch.setattr(PaperProviderPostBudget,'recover_resolved_unknown',commit)
    monkeypatch.setattr(PaperProviderPostBudget,'recovery_receipt',
                        fail_after_commit)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_RECOVERY_DURABLE_STATE_CHANGED') as error:
        recover(journal,budget,request_head)
    assert 'private recovery reader detail' not in str(error.value)
    assert budget.inspect()['recoveries']==1
    assert budget.inspect()['review_required'] is False


def test_post_budget_path_replacement_after_commit_is_fixed_uncertainty(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    replacement=tmp_path/'post-budget-replacement.sqlite3'
    shutil.copyfile(budget.path,replacement)
    original=PaperProviderPostBudget.recover_resolved_unknown
    def commit_then_replace(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        os.replace(replacement,self.path)
        return result
    monkeypatch.setattr(PaperProviderPostBudget,'recover_resolved_unknown',
                        commit_then_replace)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_RECOVERY_FILE_IDENTITY_CHANGED'):
        recover(journal,budget,request_head)
    state=budget.inspect()
    assert state['review_required'] is True and state['recoveries']==0
    assert len(budget.history())==1


def test_post_budget_same_file_mutation_after_commit_is_fixed_uncertainty(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    prior=budget.path.read_bytes();original=PaperProviderPostBudget.recovery_receipt
    calls=0
    def read_then_mutate_same_file(self):
        nonlocal calls
        receipt=original(self);calls+=1
        if calls==1:
            with self.path.open('r+b') as stream:
                stream.seek(0);stream.write(prior);stream.truncate()
                stream.flush();os.fsync(stream.fileno())
        return receipt
    monkeypatch.setattr(PaperProviderPostBudget,'recovery_receipt',
                        read_then_mutate_same_file)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_RECOVERY_FILE_SET_CHANGED'):
        recover(journal,budget,request_head)
    state=budget.inspect()
    assert calls==1 and state['review_required'] is True
    assert state['recoveries']==0 and len(budget.history())==1


def test_request_mutation_after_budget_receipts_is_fixed_uncertainty(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_before=journal.path.read_bytes()
    original=PaperProviderPostBudget.recovery_receipt;calls=0
    original_mtime=journal.path.stat().st_mtime_ns
    def read_then_mutate_request(self):
        nonlocal calls
        receipt=original(self);calls+=1
        if calls==2:
            os.utime(journal.path,
                ns=(journal.path.stat().st_atime_ns,
                    original_mtime+1_000_000_000))
        return receipt
    monkeypatch.setattr(PaperProviderPostBudget,'recovery_receipt',
                        read_then_mutate_request)
    with pytest.raises(ValueError,
            match='BROKER_JOURNAL_LOCKED_SNAPSHOT_FILE_SET_CHANGED'):
        recover(journal,budget,request_head)
    state=budget.inspect()
    assert calls==2 and state['review_required'] is False
    assert state['recoveries']==1 and len(budget.recovery_history())==1
    assert journal.path.read_bytes()==request_before
    assert journal.path.stat().st_mtime_ns!=original_mtime


def test_request_mutation_during_return_is_rechecked_on_snapshot_exit(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_before=journal.path.read_bytes();calls=0;original=subject._proof
    original_mtime=journal.path.stat().st_mtime_ns
    def mutate_after_final_proof(*args,**kwargs):
        nonlocal calls
        result=original(*args,**kwargs);calls+=1
        if calls==4:
            os.utime(journal.path,
                ns=(journal.path.stat().st_atime_ns,
                    original_mtime+1_000_000_000))
        return result
    monkeypatch.setattr(subject,'_proof',mutate_after_final_proof)
    with pytest.raises(ValueError,
            match='BROKER_JOURNAL_LOCKED_SNAPSHOT_FILE_SET_CHANGED'):
        recover(journal,budget,request_head)
    state=budget.inspect()
    assert calls==4 and state['review_required'] is False
    assert state['recoveries']==1 and len(budget.recovery_history())==1
    assert journal.path.read_bytes()==request_before
    assert journal.path.stat().st_mtime_ns!=original_mtime


def test_owner_replacement_during_return_is_rechecked_on_ownership_exit(
        tmp_path,monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    original=BrokerRequestJournal.locked_request_snapshot
    original_validate=budget_subject._PostOwnership.validate
    lock_path=budget.path.resolve().with_name(budget.path.name+'.owner.lock')
    displaced=tmp_path/'displaced-post-owner.lock'
    late={'changed':False}
    def validate_owner(self):
        if late['changed']:
            budget_subject._fail('OWNER_FILE_CHANGED')
        return original_validate(self)
    if os.name=='nt':
        monkeypatch.setattr(budget_subject._PostOwnership,'validate',
            validate_owner)
    @contextmanager
    def replace_owner_after_request_exit(self,*args,**kwargs):
        with original(self,*args,**kwargs) as read:
            yield read
        if os.name=='nt':
            late['changed']=True
        else:
            lock_path.rename(displaced)
            fd=os.open(lock_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.write(fd,b'0');os.close(fd)
    monkeypatch.setattr(BrokerRequestJournal,'locked_request_snapshot',
        replace_owner_after_request_exit)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_BUDGET_OWNER_FILE_CHANGED'):
        recover(journal,budget,request_head)
    state=budget.inspect()
    assert state['review_required'] is False and state['recoveries']==1
    assert len(budget.recovery_history())==1
    assert lock_path.is_file()
    assert displaced.is_file() is (os.name!='nt')


def test_recovery_clock_is_retained_without_shortening_existing_cooldown(tmp_path):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    recover(journal,budget,request_head,admission=lambda:90000)
    recovered=budget.inspect();before=budget.path.read_bytes()
    assert recovered['last_evaluated_at']==90000 and recovered['next_allowed_at']==87401
    with pytest.raises(ValueError,match='CLOCK_REGRESSION'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,request_before_head_sha256=request_head,
                payload_sha256='a'*64,client_order_id='next_request',
                admission=lambda:89999,ownership=owner,
                expected_head=recovered['head_sha256'])
    assert budget.path.read_bytes()==before


def test_unresolved_nonterminal_or_wrong_scope_is_byte_preserving(tmp_path):
    journal,budget=ambiguous(tmp_path);request_head=journal.recovery()['head_sha256']
    before=budget.path.read_bytes()
    with pytest.raises(ValueError,match='TERMINAL_PROOF_REQUIRED'):
        recover(journal,budget,request_head)
    assert budget.path.read_bytes()==before
    terminal_head=resolve(journal)
    for change in (dict(expected_account_id=OTHER),
                   dict(client_order_id='other'),
                   dict(expected_request_head='f'*64),
                   dict(expected_post_budget_head='e'*64),
                   dict(confirm_original_id_resolved=False),
                   dict(confirm_no_resubmission=False)):
        with pytest.raises(ValueError): recover(journal,budget,terminal_head,**change)
        assert budget.path.read_bytes()==before


def test_resolution_proof_change_rolls_back_recovery_event(tmp_path,monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    before=budget.path.read_bytes();calls=0;original=subject._proof
    def changed(*args,**kwargs):
        nonlocal calls
        calls+=1;value=original(*args,**kwargs)
        if calls==3:value=dict(value,request_resolution_head_sha256='f'*64)
        return value
    monkeypatch.setattr(subject,'_proof',changed)
    with pytest.raises(ValueError,match='RESOLUTION_PROOF_CHANGED'):
        recover(journal,budget,request_head)
    assert calls==3 and budget.path.read_bytes()==before


def test_request_writer_is_excluded_through_budget_recovery_commit(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_before=journal.path.read_bytes();attempted=False
    original=subject._proof
    monkeypatch.setattr(request_journal_module,'DB_BUSY_TIMEOUT_MS',1)
    def concurrent(snapshot,*args,**kwargs):
        nonlocal attempted
        if not attempted:
            attempted=True
            with pytest.raises(ValueError,match='BROKER_JOURNAL_DATABASE_BUSY'):
                journal.observe('nx_fixture',response(status='filled',filled_qty='2',
                    filled_avg_price='99'))
        return original(snapshot,*args,**kwargs)
    monkeypatch.setattr(subject,'_proof',concurrent)
    result=recover(journal,budget,request_head)
    assert attempted and result['review_block_cleared']
    assert result['request_writer_exclusion_held']
    assert journal.path.read_bytes()==request_before


def test_request_path_retarget_rolls_back_budget_recovery(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_path=journal.path;replacement=tmp_path/'replacement.sqlite3'
    shutil.copyfile(request_path,replacement)
    budget_before=budget.path.read_bytes();original=subject._proof;calls=0
    monkeypatch.setattr(subject,'BrokerRequestJournal',lambda *args:journal)
    def retarget_after_snapshot(snapshot,*args,**kwargs):
        nonlocal calls
        calls+=1
        result=original(snapshot,*args,**kwargs)
        if calls==2:journal.path=replacement
        return result
    monkeypatch.setattr(subject,'_proof',retarget_after_snapshot)
    with pytest.raises(ValueError,match='LOCKED_SNAPSHOT_FILE_IDENTITY_CHANGED'):
        recover(journal,budget,request_head)
    assert calls==2 and budget.path.read_bytes()==budget_before
    assert request_path.read_bytes()==replacement.read_bytes()


def test_same_file_metadata_change_rolls_back_budget_recovery(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_path=journal.path;request_before=request_path.read_bytes()
    budget_before=budget.path.read_bytes();original=subject._proof;calls=0
    original_mtime=request_path.stat().st_mtime_ns
    monkeypatch.setattr(subject,'BrokerRequestJournal',lambda *args:journal)
    def mutate_after_snapshot(snapshot,*args,**kwargs):
        nonlocal calls
        calls+=1
        result=original(snapshot,*args,**kwargs)
        if calls==2:
            os.utime(request_path,
                ns=(request_path.stat().st_atime_ns, original_mtime+1_000_000_000))
        return result
    monkeypatch.setattr(subject,'_proof',mutate_after_snapshot)
    with pytest.raises(ValueError,match='LOCKED_SNAPSHOT_FILE_SET_CHANGED'):
        recover(journal,budget,request_head)
    assert calls==2 and budget.path.read_bytes()==budget_before
    assert request_path.read_bytes()==request_before
    assert request_path.stat().st_mtime_ns!=original_mtime


def test_final_admission_mutation_is_rechecked_before_budget_commit(tmp_path):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_path=journal.path;request_before=request_path.read_bytes()
    budget_before=budget.path.read_bytes();calls=0
    original_mtime=request_path.stat().st_mtime_ns
    def mutate_on_final_admission():
        nonlocal calls
        calls+=1
        if calls==2:
            os.utime(request_path,
                ns=(request_path.stat().st_atime_ns,
                    original_mtime+1_000_000_000))
        return 1002
    with pytest.raises(ValueError,match='LOCKED_SNAPSHOT_FILE_SET_CHANGED'):
        recover(journal,budget,request_head,admission=mutate_on_final_admission)
    assert calls==2 and budget.path.read_bytes()==budget_before
    assert request_path.read_bytes()==request_before
    assert request_path.stat().st_mtime_ns!=original_mtime


def test_final_request_replay_mutation_is_rechecked_before_budget_commit(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_path=journal.path;request_before=request_path.read_bytes()
    budget_before=budget.path.read_bytes();calls=0;original=journal._load
    original_mtime=request_path.stat().st_mtime_ns
    def mutate_after_replay(db):
        nonlocal calls
        calls+=1
        result=original(db)
        if calls==4:
            os.utime(request_path,
                ns=(request_path.stat().st_atime_ns,
                    original_mtime+1_000_000_000))
        return result
    monkeypatch.setattr(journal,'_load',mutate_after_replay)
    monkeypatch.setattr(subject,'BrokerRequestJournal',lambda *args:journal)
    with pytest.raises(ValueError,match='LOCKED_SNAPSHOT_FILE_SET_CHANGED'):
        recover(journal,budget,request_head)
    assert calls==4 and budget.path.read_bytes()==budget_before
    assert request_path.read_bytes()==request_before
    assert request_path.stat().st_mtime_ns!=original_mtime


def test_final_request_replay_failure_cannot_hide_mutation(
        tmp_path, monkeypatch):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    request_path=journal.path;request_before=request_path.read_bytes()
    budget_before=budget.path.read_bytes();calls=0;original=journal._load
    original_mtime=request_path.stat().st_mtime_ns
    def mutate_and_fail_after_replay(db):
        nonlocal calls
        calls+=1
        result=original(db)
        if calls==4:
            os.utime(request_path,
                ns=(request_path.stat().st_atime_ns,
                    original_mtime+1_000_000_000))
            raise RuntimeError('injected replay reader failure')
        return result
    monkeypatch.setattr(journal,'_load',mutate_and_fail_after_replay)
    monkeypatch.setattr(subject,'BrokerRequestJournal',lambda *args:journal)
    with pytest.raises(ValueError,match='LOCKED_SNAPSHOT_FILE_SET_CHANGED'):
        recover(journal,budget,request_head)
    assert calls==4 and budget.path.read_bytes()==budget_before
    assert request_path.read_bytes()==request_before
    assert request_path.stat().st_mtime_ns!=original_mtime


def test_cli_suppresses_private_inputs_and_requires_confirmations(tmp_path,monkeypatch,capsys):
    journal,budget=ambiguous(tmp_path);request_head=resolve(journal)
    common=['fixture','--post-budget',str(budget.path),'--request-journal',str(journal.path),
        '--expected-account',ACCOUNT,'--client-order-id','nx_fixture',
        '--expected-post-budget-head',budget.inspect()['head_sha256'],
        '--expected-request-head',request_head,'--confirm-original-id-resolved']
    monkeypatch.setattr(sys,'argv',common)
    with pytest.raises(SystemExit) as caught:subject.main()
    assert caught.value.code==2
    assert capsys.readouterr().err=='PAPER_PROVIDER_POST_RECOVERY_USAGE_ERROR\n'
    monkeypatch.setattr(sys,'argv',[*common,'--confirm-no-resubmission'])
    assert subject.main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['review_block_cleared'] and not result['original_request_rearmed']
    assert result['request_file_fingerprint_rechecked']
    assert result['request_replay_fingerprint_rechecked_after_load']
    assert result['request_proof_final_before_budget_commit']
    assert result['request_proof_rechecked_after_budget_receipts']
    assert result['request_snapshot_rechecked_on_exit']
    assert result['post_budget_ownership_rechecked_on_exit']
