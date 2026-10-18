"""Explicit interrupted-lookup review: current-head lease, retained budgets, no order retry."""
from concurrent.futures import ThreadPoolExecutor
import json
import queue
import sqlite3
import subprocess
import sys
import threading
import pytest
from nexus import broker_request_journal as module
from nexus import recovery_bundle
from test_broker_lookup import journal,request
from test_broker_request_journal import ACCOUNT,OTHER,SCRIPT
from test_alpaca_paper_contract import plan,response


def pending(journal):
    journal.begin_lookup('nx_fixture',evaluated_at=1000)
    return journal.pending_lookup()


def interrupt(journal,review,**changes):
    params=dict(client_order_id=review['client_order_id'],token=review['lookup_token'],expected_head=review['head_sha256'],evaluated_at=1001,confirm_interrupted=True)
    params.update(changes)
    return journal.interrupt_lookup(**params)


def test_review_missing_is_read_only_and_does_not_initialize_storage(tmp_path):
    path=tmp_path/'missing'/'requests.sqlite3'
    result=module.BrokerRequestJournal(path,ACCOUNT).pending_lookup()
    assert not result['pending'] and result['read_only'] and not path.parent.exists()
    assert not result['automatic_submission'] and not result['automatic_resume']


def test_review_is_a_copy_with_no_write_or_budget_charge(journal):
    pending(journal);before=journal.recovery();raw=journal.path.read_bytes();stamp=journal.path.stat().st_mtime_ns
    review=journal.pending_lookup();review['lookup_token']='changed'
    assert journal.pending_lookup()['lookup_token']!='changed'
    assert journal.recovery()==before and journal.path.read_bytes()==raw and journal.path.stat().st_mtime_ns==stamp


@pytest.mark.parametrize('change',[
    {'confirm_interrupted':False},{'confirm_interrupted':1},{'token':'0'*64},{'expected_head':'0'*64},
    {'token':'PRIVATE'},{'evaluated_at':999},{'client_order_id':'second'}
],ids=['not_confirmed','integer_confirmation','wrong_ticket','stale_head','bad_ticket_format','old_clock','wrong_request'])
def test_unreviewed_stale_or_mismatched_resolution_cannot_change_state(journal,change):
    review=pending(journal);before=journal.recovery()
    with pytest.raises(ValueError):interrupt(journal,review,**change)
    assert journal.recovery()==before


def test_interruption_preserves_original_uncertainty_and_consumed_attempt_and_adds_cooldown(journal):
    review=pending(journal);result=interrupt(journal,review)
    assert result['interruption_recorded'] and not result['lookup']['pending']
    assert result['lookup']['attempts']==1 and result['lookup']['interruptions']==1
    assert result['lookup']['last_outcome']=='INTERRUPTED' and result['lookup']['next_allowed_at']==1006
    assert result['recovery_state']=='SUBMISSION_UNKNOWN_LOOKUP_REQUIRED' and result['lookup_by_original_client_id_required']
    assert not result['automatic_retry'] and not result['sendable'] and not result['broker_account_reconciled']
    with pytest.raises(ValueError,match='COOLDOWN'):journal.begin_lookup('second',evaluated_at=1005)
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT)
    assert not cold.pending_lookup()['pending'] and request(cold)['lookup']['attempts']==1
    with pytest.raises(ValueError,match='PENDING_TOKEN_REQUIRED'):
        cold.finish_lookup('nx_fixture',review['lookup_token'],evaluated_at=1002,status_code=404)
    next_ticket=cold.begin_lookup('nx_fixture',evaluated_at=1006)['lookup_token']
    assert next_ticket!=review['lookup_token'] and request(cold)['lookup']['attempts']==2


def test_interruption_retains_previously_observed_partial_quantity_and_rejects_late_fill(journal):
    journal.observe('nx_fixture',response(status='partially_filled',filled_qty='1',filled_avg_price='99'))
    review=pending(journal);interrupt(journal,review)
    before=journal.recovery()
    with pytest.raises(ValueError,match='PENDING_TOKEN_REQUIRED'):
        journal.finish_lookup('nx_fixture',review['lookup_token'],evaluated_at=1002,status_code=200,response=response(1,status='filled',filled_qty='2',filled_avg_price='99'))
    assert journal.recovery()==before and request(journal)['snapshot']['filled_quantity']=='1'
    assert request(journal)['recovery_state']=='OBSERVED_NONTERMINAL_REVIEW_REQUIRED'


def test_current_head_lease_prevents_interrupting_a_lookup_that_completed_since_inspection(journal):
    review=pending(journal)
    journal.finish_lookup('nx_fixture',review['lookup_token'],evaluated_at=1001,status_code=404)
    before=journal.recovery()
    with pytest.raises(ValueError,match='REVIEW_HEAD_DISAGREEMENT'):interrupt(journal,review,evaluated_at=1002)
    assert journal.recovery()==before


def test_exact_interruption_replay_never_clears_a_newer_pending_ticket(journal):
    review=pending(journal);interrupt(journal,review);before=journal.recovery()
    assert interrupt(journal,review)['duplicate'] and journal.recovery()==before
    journal.begin_lookup('nx_fixture',evaluated_at=1006);new=journal.pending_lookup();before=journal.recovery()
    replay=interrupt(journal,review)
    assert replay['duplicate'] and replay['lookup']['pending']
    assert journal.recovery()==before and journal.pending_lookup()==new


def test_completion_and_interruption_race_has_only_one_committed_winner(journal):
    review=pending(journal);barrier=threading.Barrier(2)
    def complete():
        barrier.wait(5)
        try:return journal.finish_lookup('nx_fixture',review['lookup_token'],evaluated_at=1001,status_code=200,response=response(status='partially_filled',filled_qty='1',filled_avg_price='99'))
        except ValueError as exc:return str(exc)
    def close():
        barrier.wait(5)
        try:return interrupt(journal,review)
        except ValueError as exc:return str(exc)
    count=journal.recovery()['retained_events']
    with ThreadPoolExecutor(2) as pool:
        completed=pool.submit(complete);closed=pool.submit(close);results=[completed.result(10),closed.result(10)]
    assert sum(isinstance(r,dict) for r in results)==1
    assert journal.recovery()['retained_events']==count+1 and not journal.pending_lookup()['pending']
    current=request(journal)
    if current['lookup']['last_outcome']=='FOUND':assert current['snapshot']['filled_quantity']=='1'
    else:assert current['lookup']['last_outcome']=='INTERRUPTED' and current['snapshot'] is None


def test_completion_reserve_also_admits_explicit_interruption_at_capacity(journal,monkeypatch):
    count=journal.recovery()['retained_events'];monkeypatch.setattr(module,'MAX_EVENTS',count+4)
    review=pending(journal);interrupt(journal,review)
    assert journal.recovery()['retained_events']==count+2
    assert interrupt(journal,review)['duplicate']


def test_interruption_does_not_rearm_lookup_budget(journal):
    now=1000
    for _ in range(4):
        journal.begin_lookup('nx_fixture',evaluated_at=now);review=journal.pending_lookup()
        result=interrupt(journal,review,evaluated_at=now+1);now=result['lookup']['next_allowed_at']
    with pytest.raises(ValueError,match='ATTEMPT_CAP'):journal.begin_lookup('nx_fixture',evaluated_at=now)
    assert request(journal)['lookup']['attempts']==request(journal)['lookup']['interruptions']==4


def test_failed_interruption_insert_rolls_back_without_releasing_pending(journal,monkeypatch):
    review=pending(journal);before=journal.recovery();original=journal._insert
    def failure(*args):original(*args);raise sqlite3.OperationalError('PRIVATE')
    monkeypatch.setattr(journal,'_insert',failure)
    with pytest.raises(sqlite3.OperationalError):interrupt(journal,review)
    assert journal.recovery()==before and journal.pending_lookup()['pending']


def test_committed_interruption_lost_ack_is_recovered_idempotently(journal,monkeypatch):
    review=pending(journal);original=journal._connect
    class LostAck:
        def __init__(self,db):self.db=db
        def __getattr__(self,key):return getattr(self.db,key)
        def commit(self):self.db.commit();raise sqlite3.OperationalError('PRIVATE_ACK')
    def connect(write=False):
        db=original(write)
        return LostAck(db) if write else db
    monkeypatch.setattr(journal,'_connect',connect)
    with pytest.raises(sqlite3.OperationalError):interrupt(journal,review)
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT);before=cold.recovery()
    assert not cold.pending_lookup()['pending'] and request(cold)['lookup']['attempts']==1
    assert interrupt(cold,review)['duplicate'] and cold.recovery()==before


def test_account_mismatch_or_changed_chain_prevents_review_and_interruption(journal):
    review=pending(journal);wrong=module.BrokerRequestJournal(journal.path,OTHER)
    with pytest.raises(ValueError,match='ACCOUNT_OR_SCHEMA'):wrong.pending_lookup()
    with pytest.raises(ValueError,match='ACCOUNT_OR_SCHEMA'):interrupt(wrong,review)
    with sqlite3.connect(journal.path) as db:db.execute("UPDATE events SET body='{}' WHERE seq=2")
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):journal.pending_lookup()
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):interrupt(journal,review)


@pytest.mark.parametrize('after_interruption',[False,True],ids=['pending_crash','interruption_committed_crash'])
def test_process_death_preserves_review_and_explicit_resolution_boundaries(tmp_path,after_interruption):
    path=tmp_path/'requests.sqlite3';ready=queue.Queue()
    extra="j.begin_lookup('nx_crash',evaluated_at=1000)\n"
    if after_interruption:
        extra+="v=j.pending_lookup();j.interrupt_lookup('nx_crash',v['lookup_token'],expected_head=v['head_sha256'],evaluated_at=1001,confirm_interrupted=True)\n"
    script=SCRIPT.replace("print('boundary',flush=True);sys.stdin.readline()",extra+"print('boundary',flush=True);sys.stdin.readline()")
    child=subprocess.Popen([sys.executable,'-c',script,str(path),'attempt_committed'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    reader=threading.Thread(target=lambda:ready.put(child.stdout.readline()),daemon=True);reader.start()
    try:
        assert ready.get(timeout=20).strip()=='boundary'
        child.kill();child.wait(10)
    finally:
        if child.poll() is None:child.kill();child.wait(10)
        child.stdin.close();reader.join(5);child.stdout.close();child.stderr.close()
    expression="import json,sys;from nexus.broker_request_journal import BrokerRequestJournal;j=BrokerRequestJournal(sys.argv[1],'"+ACCOUNT+"');print(json.dumps(dict(review=j.pending_lookup(),state=j.recovery())))"
    cold=subprocess.run([sys.executable,'-c',expression,str(path)],capture_output=True,text=True,timeout=20)
    assert cold.returncode==0,cold.stderr
    result=json.loads(cold.stdout);assert result['review']['pending'] is not after_interruption
    state=result['state']['requests'][0];assert state['lookup']['attempts']==1
    assert state['lookup_by_original_client_id_required'] and not state['automatic_retry']
    recovered=module.BrokerRequestJournal(path,ACCOUNT)
    if not after_interruption:
        view=recovered.pending_lookup();recovered.interrupt_lookup('nx_crash',view['lookup_token'],expected_head=view['head_sha256'],evaluated_at=1001,confirm_interrupted=True)
    assert not recovered.pending_lookup()['pending']


def test_offline_backup_restore_preserves_pending_ticket_without_resuming(tmp_path,journal):
    review=pending(journal);source=tmp_path/'source';source.mkdir()
    # Copy the closed SQLite file only; no external request exists in this fixture.
    import shutil
    shutil.copy2(journal.path,source/'request.sqlite3')
    recovery_bundle.backup(source,tmp_path/'backup',application_closed=True)
    recovery_bundle.restore(tmp_path/'backup',tmp_path/'restored',application_closed=True)
    restored=module.BrokerRequestJournal(tmp_path/'restored'/'request.sqlite3',ACCOUNT)
    assert restored.pending_lookup()==review
    assert not restored.recovery()['automatic_resume']


def test_self_consistent_hashes_cannot_hide_a_wrong_review_predecessor(journal):
    from nexus.alpaca_paper_contract import canonical,sha
    review=pending(journal);interrupt(journal,review)
    with sqlite3.connect(journal.path) as db:
        seq,kind,encoded,parent=db.execute('SELECT seq,kind,body,previous FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        body=json.loads(encoded);body['expected_head']='0'*64
        digest=sha(dict(seq=seq,kind=kind,body=body,previous=parent))
        db.execute('UPDATE events SET body=?,digest=? WHERE seq=?',(canonical(body),digest,seq))
    # Byte hashes agree; the retained review still has to refer to its actual predecessor.
    with pytest.raises(ValueError,match='REVIEW_HEAD_DISAGREEMENT'):journal.recovery()
    with pytest.raises(ValueError,match='REVIEW_HEAD_DISAGREEMENT'):journal.pending_lookup()
