"""Exact-head composition of provider POST and request-journal histories."""
import sqlite3

import pytest

from nexus import paper_provider_post_review as subject
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_provider_post_budget import PaperProviderPostBudget, ZERO
from test_alpaca_paper_contract import plan
from test_broker_request_journal import ACCOUNT, OTHER


def retained(tmp_path, *, account=ACCOUNT, identity='nx_fixture', outcome='NOT_SENT',
        post_head=ZERO, at=1000, budget=None):
    prepared = plan(client_order_id=identity)
    journal = BrokerRequestJournal(tmp_path/f'{identity}.sqlite3', account)
    journal.prepare(prepared); selected = journal.lookup_request(identity)
    budget = budget or PaperProviderPostBudget(tmp_path/'post.sqlite3')
    with budget.ownership() as owner:
        token = budget.begin(account_id=account,
            request_before_head_sha256=selected['head_sha256'],
            payload_sha256=prepared['payload_sha256'], client_order_id=identity,
            admission=lambda:at, ownership=owner, expected_head=post_head)
        journal.begin_submission_checked(identity,
            expected_head=selected['head_sha256'],
            expected_payload_sha256=prepared['payload_sha256'], admission=lambda:at)
        head = budget.finish(token, outcome=outcome,
            request_after_head_sha256=journal.recovery()['head_sha256'],
            actual_posts=0 if outcome == 'NOT_SENT' else 1,
            admission=lambda:at + 1, ownership=owner)
    return journal, budget, head


def binding(journal):
    return dict(journal=journal, expected_head=journal.recovery()['head_sha256'])


def test_complete_multi_account_checked_history_is_read_only(tmp_path):
    first,budget,head=retained(tmp_path,at=1000)
    second,budget,head=retained(tmp_path,account=OTHER,identity='nx_other',
        post_head=head,at=1061,budget=budget)
    before={path:path.read_bytes() for path in (budget.path,first.path,second.path)}
    result=subject.review(budget,expected_post_head=head,
        request_bindings=[binding(first),binding(second)])
    assert result['status']=='COMPLETE_LOCAL_HISTORY_CONSISTENT'
    assert result['post_attempts']==result['exact_attempt_bindings']==2
    assert result['retained_checked_request_markers']==2
    assert result['reasons']==[] and result['read_only']
    assert all(result[field] is False for field in subject.FLAGS)
    assert before=={path:path.read_bytes() for path in before}


def test_stale_heads_and_related_paths_fail_before_mutation(tmp_path):
    journal,budget,head=retained(tmp_path);before=(budget.path.read_bytes(),journal.path.read_bytes())
    for post,request in (('f'*64,journal.recovery()['head_sha256']),
                         (head,'e'*64)):
        with pytest.raises(ValueError,match='HEAD_CHANGED'):
            subject.review(budget,expected_post_head=post,
                request_bindings=[dict(journal=journal,expected_head=request)])
        assert before==(budget.path.read_bytes(),journal.path.read_bytes())
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
        subject.review(budget,expected_post_head=head,
            request_bindings=[dict(journal=BrokerRequestJournal(budget.path,ACCOUNT),
                expected_head=journal.recovery()['head_sha256'])])


def test_omitted_account_and_unbound_checked_marker_require_review(tmp_path):
    first,budget,head=retained(tmp_path,at=1000)
    second,budget,head=retained(tmp_path,account=OTHER,identity='nx_other',
        post_head=head,at=1061,budget=budget)
    omitted=subject.review(budget,expected_post_head=head,
        request_bindings=[binding(first)])
    assert not omitted['complete_local_history_consistent']
    assert 'POST_ACCOUNT_JOURNAL_MISSING' in omitted['reasons']
    extra=plan(client_order_id='nx_extra');first.prepare(extra)
    selected=first.lookup_request('nx_extra')
    first.begin_submission_checked('nx_extra',expected_head=selected['head_sha256'],
        expected_payload_sha256=extra['payload_sha256'],admission=lambda:2000)
    unbound=subject.review(budget,expected_post_head=head,
        request_bindings=[binding(first),binding(second)])
    assert not unbound['complete_local_history_consistent']
    assert unbound['unbound_checked_request_markers']==1
    assert 'CHECKED_REQUEST_MARKER_UNBOUND' in unbound['reasons']


def test_interrupted_provider_attempt_remains_explicitly_unbound(tmp_path):
    prepared=plan();journal=BrokerRequestJournal(tmp_path/'request.sqlite3',ACCOUNT)
    journal.prepare(prepared);selected=journal.lookup_request('nx_fixture')
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    owner=budget.ownership();owner.__enter__()
    token=budget.begin(account_id=ACCOUNT,
        request_before_head_sha256=selected['head_sha256'],
        payload_sha256=prepared['payload_sha256'],client_order_id='nx_fixture',
        admission=lambda:1000,ownership=owner,expected_head=ZERO)
    owner.__exit__();head=budget.inspect()['head_sha256']
    budget.interrupt(token,expected_head=head,confirm_interrupted=True,
        admission=lambda:1001)
    result=subject.review(budget,expected_post_head=budget.inspect()['head_sha256'],
        request_bindings=[binding(journal)])
    assert not result['complete_local_history_consistent']
    assert result['interrupted_unknown_attempts']==1
    assert result['reasons']==['INTERRUPTED_POST_ATTEMPT_UNBOUND']


def test_pending_provider_attempt_never_reports_complete(tmp_path):
    prepared=plan();journal=BrokerRequestJournal(tmp_path/'request.sqlite3',ACCOUNT)
    journal.prepare(prepared);selected=journal.lookup_request('nx_fixture')
    budget=PaperProviderPostBudget(tmp_path/'post.sqlite3')
    owner=budget.ownership();owner.__enter__()
    budget.begin(account_id=ACCOUNT,request_before_head_sha256=selected['head_sha256'],
        payload_sha256=prepared['payload_sha256'],client_order_id='nx_fixture',
        admission=lambda:1000,ownership=owner,expected_head=ZERO)
    owner.__exit__()
    result=subject.review(budget,expected_post_head=budget.inspect()['head_sha256'],
        request_bindings=[binding(journal)])
    assert not result['complete_local_history_consistent']
    assert result['reasons']==['POST_ATTEMPT_PENDING']


def test_request_change_during_composition_fails_closed(tmp_path,monkeypatch):
    journal,budget,head=retained(tmp_path);before=budget.path.read_bytes()
    original=journal.observations_at_heads
    def changed(heads):
        result=original(heads)
        with sqlite3.connect(journal.path) as db:
            db.execute("UPDATE events SET digest=? WHERE seq=2",('f'*64,));db.commit()
        return result
    monkeypatch.setattr(journal,'observations_at_heads',changed)
    with pytest.raises(ValueError):
        subject.review(budget,expected_post_head=head,request_bindings=[binding(journal)])
    assert budget.path.read_bytes()==before


def test_fixture_has_no_authority():
    result=subject.fixture_report()
    assert result['passed'] and all(result[field] is False for field in subject.FLAGS)
