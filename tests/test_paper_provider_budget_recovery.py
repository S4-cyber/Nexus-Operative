"""Operator entry point for reviewed provider-budget recovery."""
import json
import sys

import pytest

from nexus import paper_provider_budget_recovery as subject
from nexus.paper_provider_read_budget import PaperProviderReadBudget, ZERO
from test_alpaca_paper_inspection import ACCOUNT


def blocked(path):
    budget=PaperProviderReadBudget(path)
    with budget.ownership() as owner:
        token=budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
            operation='ACCOUNT_INVENTORY',admission=lambda:1000,ownership=owner)
        budget.finish(token,account_after_head_sha256='a'*64,actual_gets=4,
            admission=lambda:1001,ownership=owner,status_code=401)
    return budget


def test_recover_entry_point_is_exact_and_retains_false_authority(tmp_path):
    budget=blocked(tmp_path/'provider.sqlite3');before=budget.inspect()
    result=subject.recover(budget.path,expected_head=before['head_sha256'],
        expected_account_id=ACCOUNT,confirm_block_reviewed=True,
        confirm_uncertainty_preserved=True,admission=lambda:1002)
    after=budget.inspect()
    assert result['mode']=='PAPER_PROVIDER_READ_BUDGET_REVIEWED_RECOVERY'
    assert result['status']=='REVIEW_REQUIRED' and result['review_block_cleared']
    assert result['next_allowed_at']==before['next_allowed_at']==after['next_allowed_at']
    assert after['recoveries']==1 and not after['review_required']
    assert result['recovery_acknowledgement_cold_replayed']
    assert result['provider_budget_ownership_rechecked_on_exit']
    assert all(result[key] is False for key in ('admission_reserved','automatic_retry',
        'automatic_continuation','broker_wide_quota_verified','distributed_ownership_verified',
        'ready_for_routing','live_orders_enabled','money_movement_enabled','release_approved'))


def test_lost_recovery_acknowledgement_cold_replays_exact_commit(
        tmp_path,monkeypatch):
    budget=blocked(tmp_path/'provider.sqlite3');before=budget.inspect()
    original=PaperProviderReadBudget.recover_review_block
    calls=0
    def commit_then_lose_ack(self,*args,**kwargs):
        nonlocal calls
        calls+=1
        original(self,*args,**kwargs)
        raise RuntimeError('private lost recovery acknowledgement')
    monkeypatch.setattr(PaperProviderReadBudget,'recover_review_block',
                        commit_then_lose_ack)
    result=subject.recover(budget.path,expected_head=before['head_sha256'],
        expected_account_id=ACCOUNT,confirm_block_reviewed=True,
        confirm_uncertainty_preserved=True,admission=lambda:1002)
    receipt=budget.recovery_receipt();after=budget.inspect()
    assert calls==1 and result['recovery_acknowledgement_cold_replayed']
    assert result['head_sha256']==receipt['recovery_head_sha256']
    assert after['recoveries']==1 and after['review_required'] is False


@pytest.mark.parametrize('replacement',('raise','fabricate'))
def test_uncommitted_or_fabricated_recovery_ack_is_not_trusted(
        tmp_path,monkeypatch,replacement):
    budget=blocked(tmp_path/'provider.sqlite3');before=budget.inspect()
    original=budget.path.read_bytes()
    def fail_before_commit(*_args,**_kwargs):
        if replacement=='raise':
            raise RuntimeError('private recovery storage detail')
        return dict(head_sha256='f'*64,review_block_cleared=True)
    monkeypatch.setattr(PaperProviderReadBudget,'recover_review_block',
                        fail_before_commit)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_BUDGET_RECOVERY_DURABLE_STATE_CHANGED') as error:
        subject.recover(budget.path,expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002)
    assert 'private recovery storage detail' not in str(error.value)
    assert budget.path.read_bytes()==original
    assert budget.inspect()['review_required'] is True


def test_post_commit_recovery_reader_failure_is_fixed_uncertainty(
        tmp_path,monkeypatch):
    budget=blocked(tmp_path/'provider.sqlite3');before=budget.inspect()
    original_recover=PaperProviderReadBudget.recover_review_block
    committed=False
    def commit(self,*args,**kwargs):
        nonlocal committed
        result=original_recover(self,*args,**kwargs);committed=True
        return result
    def fail_after_commit(self):
        if committed:raise RuntimeError('private recovery reader detail')
        raise AssertionError('receipt called before commit')
    monkeypatch.setattr(PaperProviderReadBudget,'recover_review_block',commit)
    monkeypatch.setattr(PaperProviderReadBudget,'recovery_receipt',fail_after_commit)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_BUDGET_RECOVERY_DURABLE_STATE_CHANGED') as error:
        subject.recover(budget.path,expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002)
    assert 'private recovery reader detail' not in str(error.value)
    assert budget.inspect()['recoveries']==1
    assert budget.inspect()['review_required'] is False


def test_fixture_proves_lost_ack_without_authority():
    result=subject.fixture_report()
    assert result['passed']
    assert all(result[key] is True for key in (
        'exact_review_block_bound','recovery_acknowledgement_cold_replayed',
        'provider_budget_ownership_rechecked_on_exit',
        'blocked_history_preserved','cooldown_preserved',
        'session_capacity_not_reclaimed'))
    assert all(result[key] is False for key in ('actual_provider_access',
        'actual_vault_access','broker_account_reconciled',
        'spendable_funds_verified','ready_for_routing','live_orders_enabled',
        'money_movement_enabled','release_approved'))


def test_cli_requires_both_confirmations_and_suppresses_private_failure(
        tmp_path,monkeypatch,capsys):
    budget=blocked(tmp_path/'private-account-provider.sqlite3');state=budget.inspect()
    common=['fixture','--provider-budget',str(budget.path),'--expected-head',state['head_sha256'],
        '--expected-account',ACCOUNT,'--confirm-reviewed-block']
    monkeypatch.setattr(sys,'argv',common)
    with pytest.raises(SystemExit) as caught:subject.main()
    assert caught.value.code==2
    output=capsys.readouterr();assert output.err=='PAPER_PROVIDER_BUDGET_RECOVERY_USAGE_ERROR\n'
    assert ACCOUNT not in output.err and str(budget.path) not in output.err
    monkeypatch.setattr(sys,'argv',[*common,'--confirm-preserve-uncertainty'])
    assert subject.main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['uncertainty_preserved'] and result['history_rewritten'] is False


def test_failed_cli_never_echoes_private_inputs(tmp_path,monkeypatch,capsys):
    private=tmp_path/'private-provider.sqlite3'
    monkeypatch.setattr(sys,'argv',['fixture','--provider-budget',str(private),
        '--expected-head','f'*64,'--expected-account',ACCOUNT,
        '--confirm-reviewed-block','--confirm-preserve-uncertainty'])
    assert subject.main()==2
    output=capsys.readouterr().out
    assert output=='PAPER_PROVIDER_BUDGET_RECOVERY_FAILED\n'
    assert ACCOUNT not in output and str(private) not in output and not private.exists()
