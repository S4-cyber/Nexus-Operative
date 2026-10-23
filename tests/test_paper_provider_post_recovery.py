"""Exact-head recovery of a resolved ambiguous provider POST budget tail."""
import json
import sys

import pytest

from nexus import paper_provider_post_recovery as subject
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
