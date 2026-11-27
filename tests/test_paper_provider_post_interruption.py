"""Bounded reconciliation for a paper POST owner-path replacement."""
import json
import os
import shutil
import sys

import pytest

from nexus import paper_provider_post_interruption as subject
from nexus.paper_provider_post_budget import PaperProviderPostBudget, ZERO


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'


def replaced_pending(tmp_path):
    budget = PaperProviderPostBudget(tmp_path/'post-budget.sqlite3')
    owner = budget.ownership().__enter__()
    token = budget.begin(account_id=ACCOUNT,
        request_before_head_sha256='a'*64, payload_sha256='b'*64,
        client_order_id='nx_replaced_owner_fixture', admission=lambda:1000,
        ownership=owner, expected_head=ZERO)
    retained_owner = owner.identity()
    owner.__exit__()
    replacement = tmp_path/'replacement-owner.lock'
    owner_path = budget.path.with_name(budget.path.name+'.owner.lock')
    replacement.write_bytes(owner_path.read_bytes())
    os.replace(replacement, owner_path)
    return budget, token, retained_owner


def interrupt(budget, token, **changes):
    options = dict(expected_head=budget.inspect()['head_sha256'],
        expected_token=token, confirm_owner_replaced=True,
        confirm_uncertainty_preserved=True, admission=lambda:1002)
    options.update(changes)
    return subject.interrupt(budget.path, **options)


def test_exact_replaced_owner_interruption_is_review_only(tmp_path):
    budget,token,retained_owner=replaced_pending(tmp_path)
    before=budget.inspect();result=interrupt(budget,token);after=budget.inspect()
    receipt=budget.replaced_owner_interruption_receipt()
    assert result['mode']=='PAPER_PROVIDER_POST_REPLACED_OWNER_INTERRUPTION'
    assert result['head_sha256']==receipt['interruption_head_sha256']
    assert result['previous_head_sha256']==before['head_sha256']
    assert result['retained_owner_sha256']==retained_owner
    assert result['replacement_owner_sha256']!=retained_owner
    assert result['interruption_acknowledgement_cold_replayed'] is True
    assert result['post_budget_ownership_rechecked_on_exit'] is True
    assert result['post_budget_file_identity_rechecked'] is True
    assert result['post_budget_file_fingerprint_rechecked'] is True
    assert result['post_budget_file_set_rechecked_on_return'] is True
    assert after['events']==before['events']+1
    assert after['completed_attempts']==before['completed_attempts']+1
    assert after['pending_token'] is None and after['review_required'] is True
    assert after['next_allowed_at']==87402
    assert budget.history()[-1]['actual_posts'] is None
    assert budget.history()[-1]['request_after_head_sha256'] is None
    assert all(result[key] is False for key in ('provider_accessed',
        'vault_accessed','automatic_retry','automatic_recovery',
        'owner_repaired','attempt_rearmed','ready_for_routing',
        'live_orders_enabled','money_movement_enabled','release_approved'))


def test_lost_append_acknowledgement_cold_replays_once(tmp_path,monkeypatch):
    budget,token,_=replaced_pending(tmp_path)
    original=PaperProviderPostBudget.interrupt_replaced_owner;calls=0
    def commit_then_lose_ack(self,*args,**kwargs):
        nonlocal calls
        calls+=1;original(self,*args,**kwargs)
        raise RuntimeError('private lost interruption acknowledgement')
    monkeypatch.setattr(PaperProviderPostBudget,
        'interrupt_replaced_owner',commit_then_lose_ack)
    result=interrupt(budget,token)
    assert calls==1 and result['interruption_acknowledgement_cold_replayed']
    assert result['interruption_already_recorded'] is False
    assert budget.inspect()['events']==2 and len(budget.history())==1


def test_exact_retry_cold_replays_committed_interruption_without_append(tmp_path):
    budget,token,_=replaced_pending(tmp_path)
    expected_head=budget.inspect()['head_sha256']
    first=subject.interrupt(budget.path,expected_head=expected_head,
        expected_token=token,confirm_owner_replaced=True,
        confirm_uncertainty_preserved=True,admission=lambda:1002)
    before=budget.inspect()
    second=subject.interrupt(budget.path,expected_head=expected_head,
        expected_token=token,confirm_owner_replaced=True,
        confirm_uncertainty_preserved=True,admission=lambda:9999)
    assert second['interruption_already_recorded'] is True
    assert second['interruption_acknowledgement_cold_replayed'] is True
    assert second['head_sha256']==first['head_sha256']
    assert budget.inspect()==before and len(budget.history())==1


@pytest.mark.parametrize('missing',(
    'confirm_owner_replaced','confirm_uncertainty_preserved'))
def test_exact_retry_still_requires_both_operator_confirmations(
        tmp_path,missing):
    budget,token,_=replaced_pending(tmp_path)
    expected_head=budget.inspect()['head_sha256']
    subject.interrupt(budget.path,expected_head=expected_head,
        expected_token=token,confirm_owner_replaced=True,
        confirm_uncertainty_preserved=True,admission=lambda:1002)
    before=budget.inspect();options=dict(expected_head=expected_head,
        expected_token=token,confirm_owner_replaced=True,
        confirm_uncertainty_preserved=True,admission=lambda:9999)
    options[missing]=False
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_INTERRUPTION_CONFIRMATIONS_REQUIRED'):
        subject.interrupt(budget.path,**options)
    assert budget.inspect()==before and len(budget.history())==1


@pytest.mark.parametrize('replacement',('raise','fabricate'))
def test_uncommitted_or_fabricated_append_is_not_trusted(
        tmp_path,monkeypatch,replacement):
    budget,token,_=replaced_pending(tmp_path);before=budget.path.read_bytes()
    def fail_before_commit(*_args,**_kwargs):
        if replacement=='raise': raise RuntimeError('private storage detail')
        return dict(head_sha256='f'*64)
    monkeypatch.setattr(PaperProviderPostBudget,
        'interrupt_replaced_owner',fail_before_commit)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_INTERRUPTION_DURABLE_STATE_CHANGED') as error:
        interrupt(budget,token)
    assert 'private storage detail' not in str(error.value)
    assert budget.path.read_bytes()==before
    assert budget.inspect()['pending_token']==token


def test_post_commit_journal_replacement_is_fixed_uncertainty(
        tmp_path,monkeypatch):
    budget,token,_=replaced_pending(tmp_path)
    replacement=tmp_path/'post-budget-replacement.sqlite3'
    original=PaperProviderPostBudget.interrupt_replaced_owner
    def commit_then_replace(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        shutil.copyfile(self.path,replacement);os.replace(replacement,self.path)
        return result
    monkeypatch.setattr(PaperProviderPostBudget,
        'interrupt_replaced_owner',commit_then_replace)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_INTERRUPTION_FILE_IDENTITY_CHANGED'):
        interrupt(budget,token)
    assert budget.inspect()['review_required'] is True


def test_same_file_mutation_during_receipt_is_fixed_uncertainty(
        tmp_path,monkeypatch):
    budget,token,_=replaced_pending(tmp_path)
    original=PaperProviderPostBudget.replaced_owner_interruption_receipt
    calls=0
    def receipt_then_touch(self):
        nonlocal calls
        result=original(self);calls+=1
        if calls==1:
            observed=self.path.stat()
            os.utime(self.path,ns=(observed.st_atime_ns,
                observed.st_mtime_ns+1_000_000_000))
        return result
    monkeypatch.setattr(PaperProviderPostBudget,
        'replaced_owner_interruption_receipt',receipt_then_touch)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_INTERRUPTION_FILE_SET_CHANGED'):
        interrupt(budget,token)
    assert calls==1 and budget.inspect()['review_required'] is True


def test_cli_failure_is_fixed_and_does_not_echo_private_detail(
        tmp_path,monkeypatch,capsys):
    private='private-token-do-not-echo'
    def fail(*_args,**_kwargs): raise RuntimeError(private)
    monkeypatch.setattr(subject,'interrupt',fail)
    output=tmp_path/'private-output.json'
    monkeypatch.setattr(sys,'argv',['post-interruption','--post-budget',
        str(tmp_path/'budget.sqlite3'),'--expected-head','a'*64,
        '--expected-token','b'*64,'--confirm-owner-replaced',
        '--confirm-uncertainty-preserved','--output',str(output)])
    assert subject.main()==2
    captured=capsys.readouterr()
    assert captured.out.strip()=='PAPER_PROVIDER_POST_INTERRUPTION_FAILED'
    assert private not in captured.out+captured.err
    assert not output.exists()


def test_cli_existing_output_is_rejected_before_interruption(
        tmp_path,monkeypatch,capsys):
    output=tmp_path/'private-output.json';output.write_text('retained')
    monkeypatch.setattr(subject,'interrupt',
        lambda *_args,**_kwargs:pytest.fail('journal mutation attempted'))
    monkeypatch.setattr(sys,'argv',['post-interruption','--post-budget',
        str(tmp_path/'budget.sqlite3'),'--expected-head','a'*64,
        '--expected-token','b'*64,'--confirm-owner-replaced',
        '--confirm-uncertainty-preserved','--output',str(output)])
    assert subject.main()==2
    assert capsys.readouterr().out=='PAPER_PROVIDER_POST_INTERRUPTION_FAILED\n'
    assert output.read_text()=='retained'


def test_private_output_retries_short_writes_and_is_mode_0600(
        tmp_path,monkeypatch):
    output=tmp_path/'private-output.json';original=os.write
    monkeypatch.setattr(os,'write',
        lambda descriptor,payload:original(descriptor,payload[:1]))
    subject._write_private_output({'status':'REVIEW_REQUIRED'},output)
    assert json.loads(output.read_text())=={'status':'REVIEW_REQUIRED'}
    assert output.stat().st_mode & 0o777==0o600


def test_cli_success_writes_receipt_and_fixed_ack(tmp_path,monkeypatch,capsys):
    output=tmp_path/'private-output.json'
    monkeypatch.setattr(subject,'interrupt',lambda *_args,**_kwargs:{
        'status':'REVIEW_REQUIRED','provider_accessed':False})
    monkeypatch.setattr(sys,'argv',['post-interruption','--post-budget',
        str(tmp_path/'budget.sqlite3'),'--expected-head','a'*64,
        '--expected-token','b'*64,'--confirm-owner-replaced',
        '--confirm-uncertainty-preserved','--output',str(output)])
    assert subject.main()==0
    assert capsys.readouterr().out=='PAPER_PROVIDER_POST_INTERRUPTION_RECORDED\n'
    assert json.loads(output.read_text())['status']=='REVIEW_REQUIRED'


def test_cli_receipt_failure_can_exactly_replay_committed_interruption(
        tmp_path,monkeypatch,capsys):
    budget,token,_=replaced_pending(tmp_path)
    expected_head=budget.inspect()['head_sha256'];output=tmp_path/'receipt.json'
    argv=['post-interruption','--post-budget',str(budget.path),
        '--expected-head',expected_head,'--expected-token',token,
        '--confirm-owner-replaced','--confirm-uncertainty-preserved',
        '--output',str(output)]
    original=subject._PrivateOutput.write
    monkeypatch.setattr(subject._PrivateOutput,'write',
        lambda self,value:(_ for _ in ()).throw(OSError('private failure')))
    monkeypatch.setattr(sys,'argv',argv)
    assert subject.main()==2 and not output.exists()
    committed=budget.inspect();assert committed['review_required'] is True
    monkeypatch.setattr(subject._PrivateOutput,'write',original)
    assert subject.main()==0
    result=json.loads(output.read_text())
    assert result['interruption_already_recorded'] is True
    assert budget.inspect()==committed and len(budget.history())==1
    assert capsys.readouterr().out==(
        'PAPER_PROVIDER_POST_INTERRUPTION_FAILED\n'
        'PAPER_PROVIDER_POST_INTERRUPTION_RECORDED\n')


def test_private_output_path_replacement_is_rejected(tmp_path,monkeypatch):
    output=tmp_path/'receipt.json';replacement=tmp_path/'replacement.json'
    replacement.write_text('retained replacement')
    original=os.write;changed=False
    def write_then_replace(descriptor,payload):
        nonlocal changed
        count=original(descriptor,payload)
        if not changed:
            changed=True;os.replace(replacement,output)
        return count
    monkeypatch.setattr(os,'write',write_then_replace)
    with pytest.raises(OSError,
            match='PAPER_PROVIDER_POST_INTERRUPTION_OUTPUT_CHANGED'):
        subject._write_private_output({'status':'REVIEW_REQUIRED'},output)
    assert output.read_text()=='retained replacement'


def test_fixture_report_is_content_free_and_conservative():
    report=subject.fixture_report()
    assert report['passed'] is True
    assert report['actual_provider_access'] is False
    assert report['actual_vault_access'] is False
    assert report['ordinary_interrupt_refused_replaced_owner'] is True
    assert report['replaced_owner_interrupted_exactly_once'] is True
    assert all(report[key] is False for key in ('owner_repaired',
        'attempt_rearmed','automatic_retry','ready_for_routing',
        'live_orders_enabled','money_movement_enabled','release_approved'))
    assert not any('account' in key or 'client_order' in key for key in report)
