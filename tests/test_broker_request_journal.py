"""Durable disconnected request recovery; injected broker data and temporary files."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import pytest
from nexus import broker_request_journal as journal
from nexus import alpaca_paper_contract as contract
from test_alpaca_paper_contract import plan,response

ACCOUNT='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
OTHER='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'

@pytest.fixture
def store(tmp_path):return journal.BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)


def armed(store):
    store.prepare(plan());store.mark_submission_uncertain('nx_fixture')
    return store


def test_status_missing_is_network_and_storage_silent(tmp_path):
    store=journal.BrokerRequestJournal(tmp_path/'missing'/'requests.sqlite3',ACCOUNT)
    assert store.recovery()['requests']==[]
    assert not (tmp_path/'missing').exists()


@pytest.mark.parametrize('body_error',(False,True))
def test_locked_snapshot_rechecks_file_on_context_exit(store,body_error):
    store.prepare(plan());selected=store.lookup_request('nx_fixture')
    original_mtime=store.path.stat().st_mtime_ns
    with pytest.raises(ValueError,
            match='BROKER_JOURNAL_LOCKED_SNAPSHOT_FILE_SET_CHANGED'):
        with store.locked_request_snapshot('nx_fixture',
                expected_head=selected['head_sha256']) as read:
            assert read()['head_sha256']==selected['head_sha256']
            os.utime(store.path,
                ns=(store.path.stat().st_atime_ns,
                    original_mtime+1_000_000_000))
            if body_error:
                raise RuntimeError('private lower context failure')
    assert store.path.stat().st_mtime_ns!=original_mtime


def test_uncertain_request_reopens_without_retry_permission_and_same_id_cannot_change_terms(store):
    first=store.prepare(plan());assert first['recovery_state']=='PREPARED_UNSENT_DIAGNOSTIC'
    assert store.prepare(plan())['duplicate']
    store.mark_submission_uncertain('nx_fixture')
    cold=journal.BrokerRequestJournal(store.path,ACCOUNT)
    before=cold.recovery()
    assert before['requests'][0]['lookup_by_original_client_id_required']
    assert not before['requests'][0]['automatic_retry'] and not before['automatic_submission']
    assert cold.mark_submission_uncertain('nx_fixture')['duplicate']
    with pytest.raises(ValueError,match='CLIENT_ID_CONFLICT'):cold.prepare(plan(limit_price='99'))
    assert cold.recovery()==before


def test_partial_pending_cancel_cancelled_survives_reopen_without_ledger_credit(store):
    armed(store)
    for i,status in enumerate(('partially_filled','pending_cancel','canceled')):
        latest=response(i,status=status,filled_qty='1',filled_avg_price='99')
        result=store.observe('nx_fixture',latest)
        assert result['snapshot']['filled_quantity']=='1' and not result['ledger_applied']
        store=journal.BrokerRequestJournal(store.path,ACCOUNT)
        assert store.recovery()['requests'][0]['snapshot']==result['snapshot']
    before=store.recovery();assert store.observe('nx_fixture',latest)['duplicate']
    assert store.recovery()==before
    assert before['requests'][0]['recovery_state']=='OBSERVED_TERMINAL_RECONCILIATION_REQUIRED'
    assert not before['requests'][0]['broker_account_reconciled']


def test_observation_requires_durable_attempt_and_conflicting_transition_rolls_back(store):
    store.prepare(plan());before=store.recovery()
    with pytest.raises(ValueError,match='NO_ATTEMPT_BOUNDARY'):store.observe('nx_fixture',response())
    assert store.recovery()==before
    store.mark_submission_uncertain('nx_fixture')
    store.observe('nx_fixture',response(2,status='partially_filled',filled_qty='1',filled_avg_price='99'))
    before=store.recovery()
    for raw in [response(),response(3,status='partially_filled',filled_qty='.5',filled_avg_price='99'),response(3,id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')]:
        with pytest.raises(ValueError):store.observe('nx_fixture',raw)
        assert store.recovery()==before


def test_extra_payload_and_response_fields_are_never_retained(store):
    p=plan();p['secret']='PRIVATE_PLAN';store.prepare(p)
    store.mark_submission_uncertain('nx_fixture')
    store.observe('nx_fixture',response(secret='PRIVATE_RESPONSE',account_number='PRIVATE_ACCOUNT'))
    with sqlite3.connect(store.path) as db:text=''.join(row[0] for row in db.execute('SELECT body FROM events'))
    assert 'PRIVATE_' not in text and ACCOUNT not in text
    assert 'PRIVATE_' not in json.dumps(store.recovery())


@pytest.mark.parametrize('action',['read','write'])
def test_account_scope_mismatch_cannot_read_or_append(store,action):
    armed(store);before=store.recovery()
    wrong=journal.BrokerRequestJournal(store.path,OTHER)
    with pytest.raises(ValueError,match='ACCOUNT_OR_SCHEMA_DISAGREEMENT'):
        wrong.recovery() if action=='read' else wrong.prepare(plan())
    assert store.recovery()==before


def test_broker_uuid_cannot_be_reused_across_client_ids(store):
    armed(store);store.observe('nx_fixture',response())
    p=plan(client_order_id='second');store.prepare(p);store.mark_submission_uncertain('second')
    before=store.recovery()
    with pytest.raises(ValueError,match='BROKER_ID_REUSED'):
        store.observe('second',response(client_order_id='second'))
    assert store.recovery()==before


def test_concurrent_repeated_attempt_boundary_commits_once(store):
    store.prepare(plan())
    with ThreadPoolExecutor(8) as pool:results=list(pool.map(lambda _:store.mark_submission_uncertain('nx_fixture'),range(16)))
    assert sum(not r['duplicate'] for r in results)==1
    assert store.recovery()['retained_events']==3


def test_retention_caps_preserve_duplicates_and_evidence(store,monkeypatch):
    armed(store);monkeypatch.setattr(journal,'MAX_EVENTS',3)
    assert store.prepare(plan())['duplicate'] and store.mark_submission_uncertain('nx_fixture')['duplicate']
    before=store.recovery()
    with pytest.raises(ValueError,match='EVENT_CAP'):store.observe('nx_fixture',response())
    assert store.recovery()==before
    monkeypatch.setattr(journal,'MAX_EVENTS',4096);monkeypatch.setattr(journal,'MAX_REQUESTS',1)
    before=store.recovery()
    with pytest.raises(ValueError,match='REQUEST_CAP'):store.prepare(plan(client_order_id='second'))
    assert store.recovery()==before


@pytest.mark.parametrize('tamper',["UPDATE events SET body='{}' WHERE seq=2","DELETE FROM events WHERE seq=2"])
def test_changed_prefix_or_missing_middle_event_blocks_recovery_and_writes(store,tamper):
    armed(store)
    with sqlite3.connect(store.path) as db:db.execute(tamper)
    for action in (store.recovery,lambda:store.prepare(plan())):
        with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):action()


def test_insert_failure_rolls_back_without_acknowledgment(store,monkeypatch):
    armed(store);before=store.recovery();original=store._insert
    def fail(*a):original(*a);raise sqlite3.OperationalError('injected write failure')
    monkeypatch.setattr(store,'_insert',fail)
    with pytest.raises(sqlite3.OperationalError):store.observe('nx_fixture',response())
    assert journal.BrokerRequestJournal(store.path,ACCOUNT).recovery()==before


def test_write_lock_contention_fails_closed_then_recovers(store,monkeypatch):
    store.prepare(plan());before=store.recovery();original=store.path.read_bytes()
    monkeypatch.setattr(journal,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(store.path);blocker.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(ValueError,match='BROKER_JOURNAL_DATABASE_BUSY'):
            store.mark_submission_uncertain('nx_fixture')
    finally:
        blocker.rollback();blocker.close()
    assert store.path.read_bytes()==original and store.recovery()==before
    assert not store.mark_submission_uncertain('nx_fixture')['duplicate']


def test_commit_lock_contention_rolls_back_then_recovers(store,monkeypatch):
    store.prepare(plan());before=store.recovery();original=store.path.read_bytes()
    monkeypatch.setattr(journal,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(store.path);blocker.execute('BEGIN')
    blocker.execute('SELECT seq FROM events LIMIT 1').fetchone()
    try:
        with pytest.raises(ValueError,match='BROKER_JOURNAL_DATABASE_BUSY'):
            store.mark_submission_uncertain('nx_fixture')
    finally:
        blocker.rollback();blocker.close()
    assert store.path.read_bytes()==original and store.recovery()==before
    assert not store.mark_submission_uncertain('nx_fixture')['duplicate']


def test_public_read_lock_contention_fails_closed_then_recovers(store,monkeypatch):
    armed(store);expected=store.recovery();original=store.path.read_bytes()
    monkeypatch.setattr(journal,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(store.path);blocker.execute('BEGIN EXCLUSIVE')
    try:
        with pytest.raises(ValueError,match='BROKER_JOURNAL_DATABASE_BUSY'):
            store.recovery()
    finally:
        blocker.rollback();blocker.close()
    assert store.path.read_bytes()==original and store.recovery()==expected


def test_existing_empty_or_foreign_database_is_not_initialized_silently(tmp_path):
    path=tmp_path/'existing.sqlite3';path.touch();store=journal.BrokerRequestJournal(path,ACCOUNT)
    for action in (store.recovery,lambda:store.prepare(plan())):
        with pytest.raises(sqlite3.DatabaseError):action()
    with sqlite3.connect(path) as db:assert db.execute("SELECT name FROM sqlite_master").fetchall()==[]


def test_read_only_report_does_not_write_and_byte_cap_precedes_database_read(store,monkeypatch):
    armed(store);before=store.path.read_bytes();stamp=store.path.stat().st_mtime_ns
    store.recovery();assert store.path.read_bytes()==before and store.path.stat().st_mtime_ns==stamp
    monkeypatch.setattr(journal,'MAX_DATABASE_BYTES',1)
    with pytest.raises(ValueError,match='BYTE_CAP'):store.recovery()
    assert store.path.read_bytes()==before


@pytest.mark.parametrize('change',[{'qty':'-1'},{'qty':'3'},{'qty':'NaN'},{'client_order_id':'bad/id'},{'type':'market'}])
def test_forged_self_hashed_plan_cannot_bypass_request_scope(store,change):
    p=plan();p['payload'].update(change);p['payload_sha256']=contract.sha(p['payload'])
    with pytest.raises(ValueError):store.prepare(p)
    assert not store.path.exists()


SCRIPT=r'''
import sys
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.alpaca_paper_contract import preview
asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
p=preview('nx_crash','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
j=BrokerRequestJournal(sys.argv[1],'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
j.prepare(p);j.mark_submission_uncertain('nx_crash')
if sys.argv[2]=='observation_committed':
    raw=dict(**p['payload'],id='01234567-89ab-cdef-0123-456789abcdef',asset_class='crypto',status='partially_filled',filled_qty='1',filled_avg_price='99',updated_at='2026-10-17T18:00:00+00:00')
    j.observe('nx_crash',raw)
print('boundary',flush=True);sys.stdin.readline()
'''

@pytest.mark.parametrize('boundary',['attempt_committed','observation_committed'])
def test_process_death_after_commit_before_ack_never_grants_resubmit(tmp_path,boundary):
    path=tmp_path/'requests.sqlite3';ready=queue.Queue()
    child=subprocess.Popen([sys.executable,'-c',SCRIPT,str(path),boundary],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    reader=threading.Thread(target=lambda:ready.put(child.stdout.readline()),daemon=True);reader.start()
    try:
        assert ready.get(timeout=20).strip()=='boundary'
        child.kill();child.wait(10)
    finally:
        if child.poll() is None:child.kill();child.wait(10)
        child.stdin.close();reader.join(5);child.stdout.close();child.stderr.close()
    expression="from nexus.broker_request_journal import BrokerRequestJournal; import json,sys; print(json.dumps(BrokerRequestJournal(sys.argv[1], '"+ACCOUNT+"').recovery()))"
    cold=subprocess.run([sys.executable,'-c',expression,str(path)],capture_output=True,text=True,timeout=20)
    assert cold.returncode==0,cold.stderr
    data=json.loads(cold.stdout);request=data['requests'][0]
    assert request['submission_attempt_marked'] and not request['sendable'] and not request['automatic_retry']
    if boundary=='attempt_committed':assert request['recovery_state']=='SUBMISSION_UNKNOWN_LOOKUP_REQUIRED'
    else:assert request['snapshot']['filled_quantity']=='1' and request['snapshot']['average_fill_price']=='99'
    assert not data['automatic_submission'] and not data['ledger_applied']


def test_fixture_cli_is_disposable_and_deterministic(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));before=dict(os.environ)
    report=journal.fixture_report();assert report['passed'] and report['current_replay_duplicate']
    child=subprocess.run([sys.executable,'-m','nexus.broker_request_journal'],capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)==report
    assert not list(tmp_path.iterdir()) and dict(os.environ)==before


def test_lost_commit_ack_reopens_authoritative_observation_and_duplicate_is_safe(store,monkeypatch):
    armed(store);original=store._connect
    class LostAck:
        def __init__(self,db):self.db=db
        def __getattr__(self,key):return getattr(self.db,key)
        def commit(self):
            self.db.commit()
            raise sqlite3.OperationalError('commit returned no acknowledgment')
    def connect(write=False):
        db=original(write)
        return LostAck(db) if write else db
    monkeypatch.setattr(store,'_connect',connect)
    raw=response(status='partially_filled',filled_qty='1',filled_avg_price='99')
    with pytest.raises(sqlite3.OperationalError):store.observe('nx_fixture',raw)
    cold=journal.BrokerRequestJournal(store.path,ACCOUNT)
    assert cold.recovery()['requests'][0]['snapshot']['filled_quantity']=='1'
    assert cold.observe('nx_fixture',raw)['duplicate']
    assert cold.recovery()['retained_events']==4


def test_exact_observation_receipt_replays_prefix_after_later_lookup(store):
    store.prepare(plan())
    prepared=store.recovery();payload=plan()['payload']
    store.begin_submission_checked('nx_fixture',
        expected_head=prepared['head_sha256'],
        expected_payload_sha256=plan()['payload_sha256'],admission=lambda:1000)
    marker=store.recovery()['head_sha256'];raw=response()
    observed=store.observe_checked('nx_fixture',raw,expected_head=marker,
        expected_payload_sha256=plan()['payload_sha256'])
    store.begin_lookup('nx_fixture',evaluated_at=1001)
    receipt=store.observation_receipt('nx_fixture',raw,
        expected_previous_head=marker,
        expected_payload_sha256=plan()['payload_sha256'])
    assert receipt['exact_response_observation_replayed'] is True
    assert receipt['read_only'] is True and receipt['duplicate'] is False
    assert receipt['head_sha256']==observed['head_sha256']
    assert receipt['current_head_sha256']==store.recovery()['head_sha256']
    assert receipt['current_head_sha256']!=receipt['head_sha256']
    assert receipt['snapshot']==contract.normalize(raw,payload)
    with pytest.raises(ValueError,match='OBSERVATION_RECEIPT_'):
        store.observation_receipt('nx_fixture',response(status='rejected'),
            expected_previous_head=marker,
            expected_payload_sha256=plan()['payload_sha256'])


def test_api_fixture_never_creates_application_state_and_errors_are_sanitized(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from nexus import app
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));client=TestClient(app.app)
    reply=client.get('/api/execution/alpaca-paper/request-recovery-fixture')
    assert reply.status_code==200 and reply.json()==journal.fixture_report()
    assert reply.headers['cache-control']=='no-store' and not list(tmp_path.iterdir())
    assert client.post('/api/execution/alpaca-paper/request-recovery-fixture',json={}).status_code==405
    def failure():raise sqlite3.OperationalError('PRIVATE_PATH')
    monkeypatch.setattr(journal,'fixture_report',failure)
    reply=client.get('/api/execution/alpaca-paper/request-recovery-fixture')
    assert reply.status_code==503 and 'PRIVATE' not in reply.text


def test_actual_portal_request_recovery_controller():
    import shutil
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual PORTAL controller')
    root=Path(__file__).resolve().parents[1]
    # This fixture validates controller invariants, not Node startup latency.
    # Aggregate Windows CI may be CPU-constrained while the full suite drains.
    result=subprocess.run([node,str(root/'tools/broker_request_smoke.cjs')],cwd=root,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr


def test_unknown_attempt_does_not_initialize_an_empty_journal(store):
    with pytest.raises(ValueError,match='REQUEST_UNKNOWN'):store.mark_submission_uncertain('not_prepared')
    assert not store.path.exists()
