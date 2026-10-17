"""Disconnected lookup pacing/recovery. No network or broker credentials."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import pytest
from nexus import broker_lookup_policy as policy
from nexus import broker_request_journal as module
from test_broker_request_journal import ACCOUNT,SCRIPT
from test_alpaca_paper_contract import plan,response


@pytest.fixture
def journal(tmp_path):
    j=module.BrokerRequestJournal(tmp_path/'lookup.sqlite3',ACCOUNT)
    for identity in ('nx_fixture','second'):
        j.prepare(plan(client_order_id=identity));j.mark_submission_uncertain(identity)
    return j


def request(j,identity='nx_fixture'):
    return next(r for r in j.recovery()['requests'] if r['client_order_id']==identity)


@pytest.mark.parametrize('code,outcome',[(200,'FOUND'),(404,'NOT_FOUND'),(401,'AUTH_DENIED'),(403,'AUTH_DENIED'),(500,'SERVER_ERROR'),(503,'SERVER_ERROR'),(302,'REVIEW_REQUIRED'),(201,'REVIEW_REQUIRED'),(422,'REVIEW_REQUIRED')])
def test_response_classification_is_conservative(code,outcome):
    assert policy.classify(status_code=code,evaluated_at=1000)==dict(outcome=outcome,retry_after_seconds=0)


@pytest.mark.parametrize('header,delay',[('30',30),('0',1),('86400',86400),(None,60),(formatdate(1030,usegmt=True),30)])
def test_retry_after_seconds_or_http_date_is_never_shortened(header,delay):
    assert policy.classify(status_code=429,retry_after=header,evaluated_at=1000)==dict(outcome='RATE_LIMITED',retry_after_seconds=delay)


@pytest.mark.parametrize('header',['-1','1.5','86401','999999999','invalid','30\r\nPRIVATE','x'*129,True],ids=['negative','fraction','long_wait','huge','malformed','crlf','too_long','boolean'])
def test_unsupported_retry_after_requires_review_instead_of_silently_retrying(header):
    assert policy.classify(status_code=429,retry_after=header,evaluated_at=1000)['outcome']=='REVIEW_REQUIRED'


def test_pending_lookup_reopens_and_blocks_duplicate_calls_and_ordinary_mutation(journal):
    ticket=journal.begin_lookup('nx_fixture',evaluated_at=1000)
    assert ticket['hypothetical_lookup']==dict(method='GET',url=policy.LOOKUP_URL,query=dict(client_order_id='nx_fixture'),sendable=False)
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT);before=cold.recovery()
    assert request(cold)['lookup']['pending']
    for action in (lambda:cold.begin_lookup('second',evaluated_at=1001),lambda:cold.prepare(plan()),lambda:cold.observe('nx_fixture',response()),lambda:cold.mark_submission_uncertain('nx_fixture')):
        with pytest.raises(ValueError,match='PENDING_REVIEW_REQUIRED'):action()
        assert cold.recovery()==before


def test_timeout_and_not_found_retain_original_submission_and_cooldown_across_restart(journal):
    ticket=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    result=journal.finish_lookup('nx_fixture',ticket,evaluated_at=1001,transport_error='TIMEOUT')
    assert result['lookup_by_original_client_id_required'] and not result['automatic_retry']
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT)
    with pytest.raises(ValueError,match='COOLDOWN'):cold.begin_lookup('second',evaluated_at=1005)
    ticket=cold.begin_lookup('nx_fixture',evaluated_at=1006)['lookup_token']
    result=cold.finish_lookup('nx_fixture',ticket,evaluated_at=1007,status_code=404,response={'secret':'PRIVATE'})
    assert result['recovery_state']=='SUBMISSION_UNKNOWN_LOOKUP_REQUIRED'
    assert result['lookup']['next_allowed_at']==1017 and result['lookup']['attempts']==2
    with sqlite3.connect(journal.path) as db:assert 'PRIVATE' not in ''.join(r[0] for r in db.execute('SELECT body FROM events'))


def test_rate_limit_blocks_other_request_and_auth_failure_stops_all_lookup_admission(journal):
    ticket=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    journal.finish_lookup('nx_fixture',ticket,evaluated_at=1001,status_code=429,retry_after='30')
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT)
    with pytest.raises(ValueError,match='COOLDOWN'):cold.begin_lookup('second',evaluated_at=1030)
    ticket=cold.begin_lookup('second',evaluated_at=1031)['lookup_token']
    cold.finish_lookup('second',ticket,evaluated_at=1032,status_code=401)
    before=cold.recovery()
    with pytest.raises(ValueError,match='ACCOUNT_REVIEW_REQUIRED'):cold.begin_lookup('nx_fixture',evaluated_at=2000)
    assert cold.recovery()==before


def test_total_attempt_budget_cannot_be_rearmed_by_reopening_or_preparing_again(journal):
    now=1000
    for _ in range(4):
        ticket=journal.begin_lookup('nx_fixture',evaluated_at=now)['lookup_token']
        result=journal.finish_lookup('nx_fixture',ticket,evaluated_at=now+1,status_code=503)
        now=result['lookup']['next_allowed_at']
        journal=module.BrokerRequestJournal(journal.path,ACCOUNT)
        assert journal.prepare(plan())['duplicate']
    before=journal.recovery()
    with pytest.raises(ValueError,match='ATTEMPT_CAP'):journal.begin_lookup('nx_fixture',evaluated_at=now)
    assert journal.recovery()==before and request(journal)['lookup_by_original_client_id_required']


def test_found_snapshot_and_lookup_completion_commit_together_and_duplicates_do_not_add_events(journal):
    ticket=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    raw=response(status='partially_filled',filled_qty='1',filled_avg_price='99',secret='PRIVATE')
    result=journal.finish_lookup('nx_fixture',ticket,evaluated_at=1001,status_code=200,response=raw)
    assert result['snapshot']['filled_quantity']=='1' and not result['ledger_applied']
    before=journal.recovery()
    assert journal.finish_lookup('nx_fixture',ticket,evaluated_at=1001,status_code=200,response=raw)['duplicate']
    assert journal.recovery()==before
    new=journal.begin_lookup('nx_fixture',evaluated_at=1002)['lookup_token']
    with pytest.raises(ValueError,match='PENDING_TOKEN_REQUIRED'):
        journal.finish_lookup('nx_fixture',ticket,evaluated_at=1003,status_code=404)
    assert request(journal)['lookup']['pending']
    # A delayed exact completion replay cannot clear a newer pending ticket.
    assert journal.finish_lookup('nx_fixture',ticket,evaluated_at=1001,status_code=200,response=raw)['duplicate']
    assert request(journal)['lookup']['pending']
    journal.finish_lookup('nx_fixture',new,evaluated_at=1003,status_code=200,response=response(1,status='canceled',filled_qty='1',filled_avg_price='99'))
    assert request(journal)['snapshot']['filled_quantity']=='1'
    with pytest.raises(ValueError,match='TERMINAL_REQUIRES_RECONCILIATION'):journal.begin_lookup('nx_fixture',evaluated_at=1004)


def test_bad_found_identity_rolls_back_and_preserves_pending_ticket(journal):
    token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token'];before=journal.recovery()
    with pytest.raises(ValueError,match='IDENTITY_DISAGREEMENT'):
        journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=200,response=response(client_order_id='other'))
    assert journal.recovery()==before
    journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=302,response={'location':'PRIVATE'})
    assert request(journal)['lookup']['review_blocked']


def test_completion_capacity_is_reserved_and_current_duplicate_survives_a_full_journal(journal,monkeypatch):
    count=journal.recovery()['retained_events'];monkeypatch.setattr(module,'MAX_EVENTS',count+1)
    with pytest.raises(ValueError,match='COMPLETION_RESERVE'):journal.begin_lookup('nx_fixture',evaluated_at=1000)
    monkeypatch.setattr(module,'MAX_EVENTS',count+2)
    token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    with pytest.raises(ValueError,match='PENDING_REVIEW_REQUIRED'):journal.prepare(plan())
    journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=404)
    assert journal.recovery()['retained_events']==count+2
    assert journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=404)['duplicate']


def test_clock_regression_and_invalid_clock_cannot_bypass_account_cooldown(journal):
    token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token'];before=journal.recovery()
    for now in [999,True,float('nan')]:
        with pytest.raises(ValueError):journal.finish_lookup('nx_fixture',token,evaluated_at=now,status_code=404)
        assert journal.recovery()==before
    journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=404)
    with pytest.raises(ValueError,match='CLOCK_REGRESSION'):journal.begin_lookup('second',evaluated_at=999)


def test_concurrent_lookup_admission_charges_only_one_attempt(journal):
    def begin(_):
        try:return journal.begin_lookup('nx_fixture',evaluated_at=1000)
        except ValueError as exc:return str(exc)
    with ThreadPoolExecutor(8) as pool:results=list(pool.map(begin,range(8)))
    assert sum(isinstance(r,dict) for r in results)==1
    assert all(isinstance(r,dict) or r=='BROKER_LOOKUP_PENDING_REVIEW_REQUIRED' for r in results)
    assert request(journal)['lookup']['attempts']==1


def test_failed_result_insert_retains_pending_for_explicit_retry(journal,monkeypatch):
    token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token'];before=journal.recovery();original=journal._insert
    def failure(*args):original(*args);raise sqlite3.OperationalError('PRIVATE')
    monkeypatch.setattr(journal,'_insert',failure)
    with pytest.raises(sqlite3.OperationalError):journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=404)
    assert journal.recovery()==before
    monkeypatch.setattr(journal,'_insert',original)
    journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=404)
    assert not request(journal)['lookup']['pending']


def test_killed_process_retains_charged_pending_lookup_and_no_resume(tmp_path):
    path=tmp_path/'lookup.sqlite3';ready=queue.Queue()
    script=SCRIPT.replace("print('boundary',flush=True);sys.stdin.readline()","j.begin_lookup('nx_crash',evaluated_at=1000);print('boundary',flush=True);sys.stdin.readline()")
    proc=subprocess.Popen([sys.executable,'-c',script,str(path),'attempt_committed'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    reader=threading.Thread(target=lambda:ready.put(proc.stdout.readline()),daemon=True);reader.start()
    try:
        assert ready.get(timeout=20).strip()=='boundary'
        proc.kill();proc.wait(10)
    finally:
        if proc.poll() is None:proc.kill();proc.wait(10)
        proc.stdin.close();reader.join(5);proc.stdout.close();proc.stderr.close()
    expression="import json,sys;from nexus.broker_request_journal import BrokerRequestJournal;print(json.dumps(BrokerRequestJournal(sys.argv[1],'"+ACCOUNT+"').recovery()))"
    cold=subprocess.run([sys.executable,'-c',expression,str(path)],capture_output=True,text=True,timeout=20)
    assert cold.returncode==0,cold.stderr
    data=json.loads(cold.stdout);assert data['requests'][0]['lookup']['pending']
    assert data['requests'][0]['lookup']['attempts']==1 and not data['automatic_submission']
    with pytest.raises(ValueError,match='PENDING_REVIEW_REQUIRED'):
        module.BrokerRequestJournal(path,ACCOUNT).begin_lookup('nx_crash',evaluated_at=2000)


def test_successful_found_commit_with_lost_ack_replays_exact_completion(journal,monkeypatch):
    token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token'];original=journal._connect
    class LostAck:
        def __init__(self,db):self.db=db
        def __getattr__(self,key):return getattr(self.db,key)
        def commit(self):self.db.commit();raise sqlite3.OperationalError('PRIVATE_ACK')
    def connect(write=False):
        db=original(write)
        return LostAck(db) if write else db
    monkeypatch.setattr(journal,'_connect',connect)
    raw=response(status='partially_filled',filled_qty='1',filled_avg_price='99')
    with pytest.raises(sqlite3.OperationalError):journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=200,response=raw)
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT);before=cold.recovery()
    assert request(cold)['snapshot']['filled_quantity']=='1' and not request(cold)['lookup']['pending']
    assert cold.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=200,response=raw)['duplicate']
    assert cold.recovery()==before


def test_unsupported_header_and_invalid_clock_never_create_retry_or_new_state(journal):
    for now in [10**1000,True,float('inf')]:
        before=journal.recovery()
        with pytest.raises(ValueError,match='CLOCK'):journal.begin_lookup('nx_fixture',evaluated_at=now)
        assert journal.recovery()==before
    token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=429,retry_after='PRIVATE_UNSUPPORTED')
    assert request(journal)['lookup']['review_blocked'] and not request(journal)['automatic_retry']
    with sqlite3.connect(journal.path) as db:
        assert 'PRIVATE_UNSUPPORTED' not in ''.join(r[0] for r in db.execute('SELECT body FROM events'))
