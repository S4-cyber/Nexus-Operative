"""Passive preflight, stale-review fences and post-insert lookup-clock rollback."""
import sqlite3
import pytest
from nexus import broker_request_journal as module
from test_broker_request_journal import ACCOUNT
from test_alpaca_paper_contract import plan

FLAGS=('capacity_reserved','admission_checked','sendable','routing_authorized','transport_invoked',
       'automatic_submission','automatic_retry','broker_connected','broker_account_reconciled',
       'ledger_applied','live_orders_enabled','money_movement_enabled','release_approved')

@pytest.fixture
def journal(tmp_path):
    j=module.BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    j.prepare(plan());return j


def review(j,now=1000,identity='nx_fixture'):
    r=j.submission_review(identity,evaluated_at=now)
    assert r['read_only'] and all(r[k] is False for k in FLAGS)
    return r


def test_missing_review_does_not_create_parent_or_confuse_absence_with_readiness(tmp_path):
    j=module.BrokerRequestJournal(tmp_path/'absent'/'requests.sqlite3',ACCOUNT)
    r=review(j)
    assert not (tmp_path/'absent').exists() and not r['journal_present']
    assert r['reasons']==['REQUEST_NOT_PREPARED'] and not r['local_marker_checks_clear']


def test_clear_review_preserves_bytes_and_is_independent_of_returned_mutations(journal):
    before=journal.path.read_bytes();r=review(journal)
    assert r['local_marker_checks_clear'] and r['status']=='LOCAL_MARKER_CHECKS_CLEAR'
    assert journal.path.read_bytes()==before
    r['reasons'].append('MUTATED');r['capacity'].clear()
    assert review(journal)['reasons']==[] and review(journal)['capacity']


def test_unknown_request_in_existing_history_is_blocked(journal):
    before=journal.path.read_bytes();r=review(journal,identity='unknown')
    assert r['reasons']==['REQUEST_NOT_PREPARED'] and r['payload_sha256'] is None
    assert journal.path.read_bytes()==before


def test_stale_clear_review_cannot_mark_after_another_write(journal):
    r=review(journal);journal.prepare(plan(client_order_id='second'));before=journal.path.read_bytes()
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        journal.begin_submission_checked('nx_fixture',expected_head=r['head_sha256'],expected_payload_sha256=r['payload_sha256'],admission=lambda:1000)
    assert journal.path.read_bytes()==before


def test_marked_attempt_always_requires_original_id_recovery(journal):
    journal.mark_submission_uncertain('nx_fixture');r=review(journal)
    assert 'ORIGINAL_ID_RECOVERY_REQUIRED' in r['reasons'] and not r['local_marker_checks_clear']


@pytest.mark.parametrize('code,now,expected',[(429,1000,'CLOCK_REGRESSION'),(429,1020,'ACCOUNT_COOLDOWN_ACTIVE'),(401,2000,'ACCOUNT_REVIEW_REQUIRED')])
def test_retained_account_blockers_apply_to_different_unsent_request(journal,code,now,expected):
    journal.prepare(plan(client_order_id='second'));journal.mark_submission_uncertain('nx_fixture')
    token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=code,retry_after='30' if code==429 else None)
    before=journal.path.read_bytes();cold=module.BrokerRequestJournal(journal.path,ACCOUNT)
    r=review(cold,now,identity='second');assert expected in r['reasons'] and journal.path.read_bytes()==before
    if code==429:
        assert r['retained_rate_limit_until']==1031
        boundary=review(cold,1031,identity='second');assert boundary['local_marker_checks_clear'] and boundary['wait_seconds']==0


def test_pending_lookup_is_explained_without_consuming_its_completion_slot(journal):
    journal.prepare(plan(client_order_id='second'));journal.mark_submission_uncertain('nx_fixture')
    journal.begin_lookup('nx_fixture',evaluated_at=1000);before=journal.path.read_bytes()
    assert 'LOOKUP_PENDING_REVIEW_REQUIRED' in review(journal,identity='second')['reasons']
    assert journal.path.read_bytes()==before and journal.pending_lookup()['pending']


def test_review_uses_same_marker_and_other_request_recovery_headroom(journal,monkeypatch):
    journal.prepare(plan(client_order_id='second'));journal.mark_submission_uncertain('second')
    count=journal.recovery()['retained_events'];before=journal.path.read_bytes()
    monkeypatch.setattr(module,'MAX_EVENTS',count+4)
    assert 'RECOVERY_RESERVE_REQUIRED' in review(journal)['reasons']
    monkeypatch.setattr(module,'MAX_EVENTS',count+5)
    assert review(journal)['local_marker_checks_clear'] and journal.path.read_bytes()==before


@pytest.mark.parametrize('fault',['account','corrupt','wal','hardlink'])
def test_review_refuses_untrusted_history_without_repair(journal,fault,tmp_path):
    if fault=='account':journal=module.BrokerRequestJournal(journal.path,'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
    if fault=='corrupt':journal.path.write_bytes(b'corrupt retained history')
    if fault=='wal':journal.path.with_name(journal.path.name+'-wal').write_bytes(b'retained sidecar')
    if fault=='hardlink':(tmp_path/'linked.sqlite3').hardlink_to(journal.path)
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises((ValueError,sqlite3.DatabaseError)):review(journal)
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before


@pytest.mark.parametrize('finishing',[False,True],ids=['begin','result'])
@pytest.mark.parametrize('final',['regression','revocation'],ids=['clock','permission'])
def test_checked_lookup_rechecks_after_actual_insert_and_rolls_back(journal,finishing,final,monkeypatch):
    journal.mark_submission_uncertain('nx_fixture')
    if finishing:token=journal.begin_lookup('nx_fixture',evaluated_at=999)['lookup_token']
    before=journal.path.read_bytes();inserted=False;insert=journal._insert
    def observed(*args):
        nonlocal inserted
        insert(*args);inserted=True
    monkeypatch.setattr(journal,'_insert',observed)
    def admission():return (999 if final=='regression' else False) if inserted else 1000
    with pytest.raises(ValueError,match='CLOCK'):
        if finishing:journal.finish_lookup_checked('nx_fixture',token,admission=admission,status_code=404)
        else:journal.begin_lookup_checked('nx_fixture',admission=admission)
    assert inserted and journal.path.read_bytes()==before
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT)
    assert cold.pending_lookup()['pending'] is finishing
    # Equal post-insert time still succeeds; rejection has not consumed the ticket/attempt.
    if finishing:result=cold.finish_lookup_checked('nx_fixture',token,admission=lambda:1000,status_code=404)
    else:result=cold.begin_lookup_checked('nx_fixture',admission=lambda:1000)
    assert not result['sendable'] and not result['broker_connected']


@pytest.mark.parametrize('finishing',[False,True],ids=['begin','result'])
def test_lookup_clock_cannot_regress_while_waiting_for_write_admission(journal,finishing):
    journal.mark_submission_uncertain('nx_fixture')
    if finishing:token=journal.begin_lookup('nx_fixture',evaluated_at=998)['lookup_token']
    before=journal.path.read_bytes();samples=iter([1000,999])
    with pytest.raises(ValueError,match='CLOCK_REGRESSION'):
        if finishing:journal.finish_lookup_checked('nx_fixture',token,admission=lambda:next(samples),status_code=404)
        else:journal.begin_lookup_checked('nx_fixture',admission=lambda:next(samples))
    assert journal.path.read_bytes()==before
