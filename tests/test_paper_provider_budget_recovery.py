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
    assert all(result[key] is False for key in ('admission_reserved','automatic_retry',
        'automatic_continuation','broker_wide_quota_verified','distributed_ownership_verified',
        'ready_for_routing','live_orders_enabled','money_movement_enabled','release_approved'))


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
