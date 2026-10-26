"""Temporary local evidence and injected GETs only; no actual account access."""
import copy
import json
import sqlite3
import subprocess
import sys

import pytest
from nexus import broker_activity_store as storage
from nexus.alpaca_paper_contract import canonical, sha
from test_alpaca_activity_normalizer import inputs, ACCOUNT, OTHER
from test_alpaca_activity_collection import START, END, fee, collect


def store(tmp_path, account=ACCOUNT): return storage.BrokerActivityStore(tmp_path / 'activities.db', account)


def retain(target, rows=None, orders=None, **changes):
    original_rows, original_orders = inputs()
    return target.retain(original_rows if rows is None else rows, original_orders if orders is None else orders,
                         after=START, until=END, **changes)


def test_missing_inspection_no_initialization_and_all_authority_false(tmp_path):
    target = store(tmp_path); result = target.inspect()
    assert not target.path.exists() and result['retained_batches'] == result['retained_activities'] == 0
    for key, value in result.items():
        if key.endswith(('verified', 'complete', 'reconciled', 'applied', 'connected', 'routing', 'enabled', 'continuation')):
            assert value is False


def test_restart_canonical_dedup_and_private_fields_not_retained(tmp_path):
    target = store(tmp_path); first = retain(target); before = target.path.read_bytes()
    rows, orders = inputs(); rows.reverse(); rows.append(copy.deepcopy(rows[-1]))
    rows[-1]['qty'] = '1.000'; rows[-1]['description'] = 'ANOTHER_PRIVATE'
    replay = retain(target, rows, orders)
    assert replay['duplicate'] and replay['receipt_id'] == first['receipt_id']
    assert target.path.read_bytes() == before
    result = store(tmp_path).inspect()
    assert result['retained_batches'] == 1 and result['retained_activities'] == 4
    assert 'PRIVATE' not in json.dumps(result) and b'PRIVATE' not in target.path.read_bytes()
    fees = [r for r in result['normalized_activities'] if r['kind'] != 'FILL']
    assert all('occurred_at' not in f and 'execution_id' not in f for f in fees)
    script = 'import json,sys; from nexus.broker_activity_store import BrokerActivityStore; print(json.dumps(BrokerActivityStore(sys.argv[1],sys.argv[2]).inspect()))'
    recovered = json.loads(subprocess.check_output([sys.executable, '-c', script, str(target.path), ACCOUNT], text=True))
    assert recovered == result


def test_overlapping_windows_reference_existing_records_without_copying(tmp_path):
    target = store(tmp_path); retain(target)
    rows, orders = inputs()
    target.retain(rows, orders, after='2026-10-16T00:00:00Z', until=START)
    result = target.inspect()
    assert result['retained_batches'] == 2 and result['retained_activities'] == 4
    with sqlite3.connect(target.path) as db:
        body = json.loads(db.execute('SELECT body FROM receipts WHERE seq=2').fetchone()[0])
    assert body['new_activities'] == [] and len(body['activity_ids']) == 4


def test_conflicting_activity_keeps_history_unchanged(tmp_path):
    target = store(tmp_path); retain(target); before = target.inspect()
    rows, orders = inputs(); rows[3]['net_amount'] = '-0.2'
    with pytest.raises(ValueError, match='ACTIVITY_CONFLICT'): retain(target, rows, orders)
    assert target.inspect() == before


@pytest.mark.parametrize('stage', [1, 2])
def test_admission_veto_rolls_back_entire_batch(tmp_path, stage):
    target = store(tmp_path); retain(target); before = target.inspect(); calls = []
    def guard():
        calls.append(1); return len(calls) != stage
    with pytest.raises(ValueError, match='ADMISSION_REVOKED'):
        retain(target, [fee(90)], [], admission=guard)
    assert len(calls) == stage and target.inspect() == before


def test_lost_acknowledgment_replays_original_receipt(tmp_path, monkeypatch):
    target = store(tmp_path); original = storage.BrokerActivityStore._connect
    class LostReturn:
        def __init__(self, db): self.db = db
        def __getattr__(self, name): return getattr(self.db, name)
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self, *args):
            self.db.__exit__(*args)
            if args[0] is None: raise RuntimeError('lost acknowledgment')
    monkeypatch.setattr(target, '_connect', lambda write=False: LostReturn(original(target, write)))
    with pytest.raises(RuntimeError, match='lost acknowledgment'): retain(target)
    recovered = store(tmp_path); assert recovered.inspect()['retained_batches'] == 1
    assert retain(recovered)['duplicate']


@pytest.mark.parametrize('cap,value,match', [('MAX_BATCHES',1,'BATCH_CAP'),('MAX_ACTIVITIES',4,'ACTIVITY_CAP'),
    ('MAX_RECEIPT_BYTES',200,'RECEIPT_CAP'),('MAX_RETAINED_BYTES',200,'RETAINED_BYTE_CAP'),
    ('MAX_DATABASE_BYTES',1,'DATABASE_CAP')])
def test_caps_never_evict_history(tmp_path, monkeypatch, cap, value, match):
    target = store(tmp_path); retain(target); before = target.path.read_bytes()
    monkeypatch.setattr(storage, cap, value)
    with pytest.raises(ValueError, match=match): retain(target, [fee(90)], [])
    assert target.path.read_bytes() == before


def test_wrong_account_empty_foreign_and_symlink_rejected(tmp_path):
    target = store(tmp_path); retain(target)
    with pytest.raises(ValueError, match='ACCOUNT_OR_SCHEMA'): store(tmp_path, OTHER).inspect()
    empty = tmp_path / 'empty.db'; empty.touch()
    with pytest.raises(sqlite3.DatabaseError): storage.BrokerActivityStore(empty, ACCOUNT).inspect()
    link = tmp_path / 'link.db'
    try: link.symlink_to(target.path)
    except OSError: return  # Windows symlink privilege is independently accepted.
    with pytest.raises(ValueError, match='SYMLINK'): storage.BrokerActivityStore(link, ACCOUNT).inspect()


@pytest.mark.parametrize('column,value', [('previous','broken'),('digest','broken'),('body','{}'),('seq',9)])
def test_corrupt_chain_rejects_read_and_write(tmp_path, column, value):
    target = store(tmp_path); retain(target)
    with sqlite3.connect(target.path) as db: db.execute(f'UPDATE receipts SET {column}=? WHERE seq=1', (value,))
    for operation in (target.inspect, lambda: retain(target)):
        with pytest.raises(ValueError): operation()


def test_rehashed_semantically_invalid_record_still_rejected(tmp_path):
    target = store(tmp_path); retain(target)
    with sqlite3.connect(target.path) as db:
        encoded, parent = db.execute('SELECT body,previous FROM receipts WHERE seq=1').fetchone()
        body = json.loads(encoded); body['new_activities'][0]['secret'] = 'not allowed'
        db.execute('UPDATE receipts SET body=?,digest=? WHERE seq=1',
                   (canonical(body), sha(dict(seq=1, body=body, previous=parent))))
    with pytest.raises(ValueError, match='NONCANONICAL_INPUT'): target.inspect()


def test_cross_batch_order_binding_conflict_is_atomic(tmp_path):
    target = store(tmp_path); retain(target); before = target.inspect()
    rows, orders = inputs(); orders[0]['client_order_id'] = 'changed-original'
    with pytest.raises(ValueError, match='ORDER_BINDING_CONFLICT'): retain(target, [], orders)
    assert target.inspect() == before


def test_collector_opt_in_commits_unattested_receipt_and_preflights_account(tmp_path):
    from test_alpaca_activity_collection import ACCOUNT as READ_ACCOUNT
    target = store(tmp_path, READ_ACCOUNT)
    result = collect(activity_store=target)
    assert result['local_retention']['receipt_id'] in target.inspect()['receipts']
    assert target.inspect()['source'] == 'SUPPLIED_UNATTESTED'
    assert collect(activity_store=target)['local_retention']['duplicate']
    with pytest.raises(ValueError, match='RETENTION_ACCOUNT_OR_TYPE'): collect(activity_store=store(tmp_path, OTHER))


@pytest.mark.parametrize('phase,expected_batches', [('before',1),('after',2)])
def test_process_death_at_commit_boundary_preserves_atomic_receipts(tmp_path, phase, expected_batches):
    target = store(tmp_path); retain(target)
    script = '''
import os,sys
from nexus.broker_activity_store import BrokerActivityStore
path,account,phase=sys.argv[1:]
target=BrokerActivityStore(path,account)
connect=target._connect
class Boundary:
 def __init__(self,db): self.db=db
 def __getattr__(self,name): return getattr(self.db,name)
 def __enter__(self): self.db.__enter__(); return self
 def __exit__(self,*args):
  if phase=='before': os._exit(71)
  self.db.__exit__(*args)
  os._exit(72)
target._connect=lambda write=False:Boundary(connect(write))
row=dict(id='20261017000000000::11234567-89ab-cdef-0123-000000000090',activity_type='FEE',net_amount='-0.01',status='executed',date='2026-10-16')
target.retain([row],[],after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
'''
    completed = subprocess.run([sys.executable,'-c',script,str(target.path),ACCOUNT,phase], check=False)
    assert completed.returncode == (71 if phase == 'before' else 72)
    recovered = store(tmp_path); result = recovered.inspect()
    assert result['retained_batches'] == expected_batches
    assert retain(recovered, [fee(90)], [])['duplicate'] is (phase == 'after')
    assert recovered.inspect()['retained_batches'] == 2


def test_actual_sqlite_contention_rechecks_admission_after_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    target = store(tmp_path); retain(target); before = target.inspect()
    started = threading.Event(); allowed = threading.Event(); allowed.set()
    with sqlite3.connect(target.path) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute('BEGIN IMMEDIATE')
        def operation():
            started.set()
            return retain(target, [fee(90)], [], admission=allowed.is_set)
        future = pool.submit(operation)
        assert started.wait(2)
        allowed.clear(); blocker.rollback()
        with pytest.raises(ValueError, match='ADMISSION_REVOKED'): future.result(timeout=8)
    assert target.inspect() == before


def test_writer_contention_is_bounded_redacted_and_recovers(tmp_path, monkeypatch):
    target = store(tmp_path); retain(target); before = target.path.read_bytes()
    monkeypatch.setattr(storage, 'DB_BUSY_TIMEOUT_MS', 50)
    with sqlite3.connect(target.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match=r'^BROKER_ACTIVITY_STORE_DATABASE_BUSY$'):
            retain(target, [fee(90)], [])
    assert target.path.read_bytes() == before
    assert not retain(target, [fee(90)], [])['duplicate']


def test_reader_blocked_commit_rolls_back_and_recovers(tmp_path, monkeypatch):
    target = store(tmp_path); retain(target); before = target.path.read_bytes()
    monkeypatch.setattr(storage, 'DB_BUSY_TIMEOUT_MS', 50)
    with sqlite3.connect(target.path) as blocker:
        blocker.execute('BEGIN')
        blocker.execute('SELECT count(*) FROM receipts').fetchone()
        with pytest.raises(ValueError, match=r'^BROKER_ACTIVITY_STORE_DATABASE_BUSY$'):
            retain(target, [fee(90)], [])
    assert target.path.read_bytes() == before
    assert not retain(target, [fee(90)], [])['duplicate']


def test_read_contention_is_bounded_redacted_and_recovers(tmp_path, monkeypatch):
    target = store(tmp_path); retain(target); expected = target.inspect()
    monkeypatch.setattr(storage, 'DB_BUSY_TIMEOUT_MS', 50)
    with sqlite3.connect(target.path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError, match=r'^BROKER_ACTIVITY_STORE_DATABASE_BUSY$'):
            target.inspect()
    assert target.inspect() == expected


def test_concurrent_duplicates_commit_one_receipt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    target = store(tmp_path); retain(target)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: retain(target, [fee(90)], []), range(4)))
    assert sum(not result['duplicate'] for result in results) == 1
    assert len({result['receipt_id'] for result in results}) == 1
    assert target.inspect()['retained_batches'] == 2


@pytest.mark.parametrize('stage', [1,2,3])
def test_collection_late_credential_veto_does_not_claim_retention_success(tmp_path, stage):
    from test_alpaca_activity_collection import ACCOUNT as READ_ACCOUNT
    target = store(tmp_path, READ_ACCOUNT); calls = []
    def credential_check():
        calls.append(1); return len(calls) != stage
    with pytest.raises(ValueError): collect(activity_store=target, credential_check=credential_check)
    # First check precedes storage; the next two guard the write and pre-commit.
    if target.path.exists():
        with sqlite3.connect(target.path) as db:
            assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    else: assert stage == 1


def test_collection_post_commit_revocation_retains_reviewable_receipt(tmp_path):
    from test_alpaca_activity_collection import ACCOUNT as READ_ACCOUNT
    target = store(tmp_path, READ_ACCOUNT); calls = []
    def credential_check():
        calls.append(1); return len(calls) != 4
    with pytest.raises(ValueError): collect(activity_store=target, credential_check=credential_check)
    result = target.inspect()
    assert result['retained_batches'] == 1 and not result['automatic_continuation']


@pytest.mark.parametrize('change', [{'activity_type':'CSD'}, {'correction':True}, {'account_id':OTHER},
    {'description':{'nested':'private'}}, {'net_amount':'NaN'}])
def test_invalid_source_batch_never_initializes_store(tmp_path, change):
    target = store(tmp_path); row = fee(90, **change)
    with pytest.raises(ValueError): retain(target, [row], [])
    assert not target.path.exists()


def test_corrupt_existing_store_prevents_collection_network_io(tmp_path):
    from test_alpaca_activity_collection import ACCOUNT as READ_ACCOUNT
    from test_alpaca_paper_inspection import Opener, account
    target = store(tmp_path, READ_ACCOUNT); target.path.write_bytes(b'foreign')
    opener = Opener([account(), [fee()], account()])
    with pytest.raises(sqlite3.DatabaseError): collect(opener=opener, activity_store=target)
    assert opener.requests == [] and target.path.read_bytes() == b'foreign'


def test_same_client_cannot_be_rebound_to_another_order(tmp_path):
    target = store(tmp_path); retain(target); before = target.inspect()
    _, orders = inputs(); orders = copy.deepcopy(orders[:1])
    orders[0]['broker_order_id'] = OTHER
    with pytest.raises(ValueError, match='CLIENT_BINDING_CONFLICT'): retain(target, [], orders)
    assert target.inspect() == before
