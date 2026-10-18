"""Disconnected admission: persist uncertainty before any future transport seam."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import pytest
from nexus import broker_request_journal as module
from test_broker_request_journal import ACCOUNT
from test_alpaca_paper_contract import plan,response

@pytest.fixture
def store(tmp_path):
    journal=module.BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT);journal.prepare(plan());return journal


def begin(store,**overrides):
    retained=store.lookup_request('nx_fixture')
    return store.begin_submission_checked('nx_fixture',expected_head=overrides.get('head',retained['head_sha256']),
        expected_payload_sha256=overrides.get('payload',retained['plan']['payload_sha256']),admission=overrides.get('admission',lambda:1000))


def test_checked_marker_commits_once_and_reopens_uncertain_without_send_permission(store):
    result=begin(store)
    assert result['attempt_marker_commit_observed'] and result['submission_attempt_marked'] and not result['duplicate']
    assert all(result[k] is False for k in ('sendable','routing_authorized','transport_invoked','live_orders_enabled','broker_connected','ledger_applied'))
    fresh=module.BrokerRequestJournal(store.path,ACCOUNT);before=store.path.read_bytes()
    with pytest.raises(ValueError,match='ALREADY_MARKED'):begin(fresh)
    assert fresh.path.read_bytes()==before and fresh.recovery()['capacity']['recovery_slots_reserved']==2


@pytest.mark.parametrize('fault',['head','terms','veto','late-veto','clock'])
def test_bad_or_revoked_admission_cannot_mark_attempt(store,fault):
    before=store.path.read_bytes();args={}
    if fault=='head':args['head']='0'*64
    elif fault=='terms':args['payload']='0'*64
    else:
        samples=iter([1000,False] if fault=='late-veto' else [1000,999] if fault=='clock' else [False])
        args['admission']=lambda:next(samples)
    with pytest.raises(ValueError):begin(store,**args)
    assert store.path.read_bytes()==before and not store.recovery()['requests'][0]['submission_attempt_marked']


def test_marker_requires_recovery_pair_headroom_and_other_writes_cannot_spend_it(store,monkeypatch):
    before=store.path.read_bytes();monkeypatch.setattr(module,'MAX_EVENTS',4)
    with pytest.raises(ValueError,match='RECOVERY_RESERVE'):begin(store)
    assert store.path.read_bytes()==before
    monkeypatch.setattr(module,'MAX_EVENTS',5);begin(store);before=store.path.read_bytes()
    with pytest.raises(ValueError,match='RECOVERY_RESERVE'):store.prepare(plan(client_order_id='second'))
    assert store.path.read_bytes()==before
    ticket=store.begin_lookup('nx_fixture',evaluated_at=1000)
    assert store.recovery()['capacity']['recovery_slots_reserved']==1
    result=store.finish_lookup('nx_fixture',ticket['lookup_token'],evaluated_at=1001,status_code=200,response=response(status='filled',filled_qty='2',filled_avg_price='99'))
    assert result['snapshot']['terminal'] and store.recovery()['capacity']['recovery_slots_reserved']==0


def test_multiple_unresolved_requests_keep_separate_recovery_reserves(store,monkeypatch):
    store.prepare(plan(client_order_id='second'));begin(store);store.mark_submission_uncertain('second')
    assert store.recovery()['capacity']['recovery_slots_reserved']==4
    count=store.recovery()['retained_events'];monkeypatch.setattr(module,'MAX_EVENTS',count+3)
    before=store.path.read_bytes()
    with pytest.raises(ValueError,match='COMPLETION_RESERVE'):store.begin_lookup('nx_fixture',evaluated_at=1000)
    assert store.path.read_bytes()==before
    # Historical headroom deficits are diagnostics, never a reason to erase history.
    monkeypatch.setattr(module,'MAX_EVENTS',count+1)
    assert store.recovery()['capacity']['recovery_reserve_deficit']==3 and store.path.read_bytes()==before


def test_post_lock_admission_rechecks_revocation_before_any_marker(store):
    held=sqlite3.connect(store.path);held.execute('BEGIN IMMEDIATE');entered=threading.Event();revoked=threading.Event()
    retained=store.lookup_request('nx_fixture');before=store.path.read_bytes()
    def run():
        entered.set()
        return store.begin_submission_checked('nx_fixture',expected_head=retained['head_sha256'],expected_payload_sha256=retained['plan']['payload_sha256'],admission=lambda:False if revoked.is_set() else 1000)
    try:
        with ThreadPoolExecutor(1) as pool:
            pending=pool.submit(run);assert entered.wait(2);revoked.set();held.rollback()
            with pytest.raises(ValueError):pending.result(timeout=5)
    finally:held.close()
    assert store.path.read_bytes()==before


def test_concurrent_same_head_begins_commit_only_once(store):
    retained=store.lookup_request('nx_fixture')
    def run(_):
        try:return store.begin_submission_checked('nx_fixture',expected_head=retained['head_sha256'],expected_payload_sha256=retained['plan']['payload_sha256'],admission=lambda:1000)
        except ValueError:return None
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(run,range(8)))
    assert sum(r is not None for r in results)==1 and store.recovery()['retained_events']==3


def test_lost_return_after_commit_keeps_unknown_and_does_not_rearm(store,monkeypatch):
    def lose(*_):raise RuntimeError('LOST_RETURN_FIXTURE')
    with monkeypatch.context() as m:
        m.setattr(store,'_summary',lose)
        with pytest.raises(RuntimeError):begin(store)
    fresh=module.BrokerRequestJournal(store.path,ACCOUNT)
    assert fresh.recovery()['requests'][0]['lookup_by_original_client_id_required']
    with pytest.raises(ValueError,match='ALREADY_MARKED'):begin(fresh)


def test_terminal_observations_can_reduce_historical_deficits_without_rewriting_history(store,monkeypatch):
    store.prepare(plan(client_order_id='second'));begin(store);store.mark_submission_uncertain('second')
    count=store.recovery()['retained_events'];monkeypatch.setattr(module,'MAX_EVENTS',count+2)
    assert store.recovery()['capacity']['recovery_reserve_deficit']==2
    store.observe('nx_fixture',response(status='filled',filled_qty='2',filled_avg_price='99'))
    assert store.recovery()['capacity']['recovery_reserve_deficit']==1
    store.observe('second',response(client_order_id='second',id='11234567-89ab-cdef-0123-456789abcdef',status='filled',filled_qty='2',filled_avg_price='99'))
    assert store.recovery()['capacity']['recovery_slots_reserved']==0 and store.recovery()['retained_events']==count+2


def test_retained_account_review_blocks_another_checked_submission(store):
    store.prepare(plan(client_order_id='second'));begin(store)
    token=store.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    store.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=401)
    retained=store.lookup_request('second');before=store.path.read_bytes()
    with pytest.raises(ValueError,match='ACCOUNT_REVIEW_REQUIRED'):
        store.begin_submission_checked('second',expected_head=retained['head_sha256'],expected_payload_sha256=retained['plan']['payload_sha256'],admission=lambda:pytest.fail('Blocked account invoked admission'))
    assert store.path.read_bytes()==before


@pytest.mark.parametrize('now,reason',[(1000,'CLOCK_REGRESSION'),(1020,'ACCOUNT_COOLDOWN_ACTIVE')])
def test_retained_lookup_clock_and_rate_limit_fence_another_checked_marker(store,now,reason):
    store.prepare(plan(client_order_id='second'));begin(store)
    token=store.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    store.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=429,retry_after='30')
    retained=store.lookup_request('second');before=store.path.read_bytes()
    with pytest.raises(ValueError,match=reason):
        store.begin_submission_checked('second',expected_head=retained['head_sha256'],expected_payload_sha256=retained['plan']['payload_sha256'],admission=lambda:now)
    assert store.path.read_bytes()==before


def test_checked_marker_can_resume_at_rate_deadline_but_never_becomes_send_permission(store):
    store.prepare(plan(client_order_id='second'));begin(store)
    token=store.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    store.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=429,retry_after='30')
    retained=store.lookup_request('second')
    result=store.begin_submission_checked('second',expected_head=retained['head_sha256'],expected_payload_sha256=retained['plan']['payload_sha256'],admission=lambda:1031)
    assert result['attempt_marker_commit_observed'] and not result['routing_authorized'] and not result['sendable']


def test_post_insert_clock_cannot_cross_back_into_retained_rate_block(store):
    store.prepare(plan(client_order_id='second'));begin(store)
    token=store.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
    store.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=429,retry_after='30')
    retained=store.lookup_request('second');before=store.path.read_bytes();samples=iter([1031,1030])
    with pytest.raises(ValueError):
        store.begin_submission_checked('second',expected_head=retained['head_sha256'],expected_payload_sha256=retained['plan']['payload_sha256'],admission=lambda:next(samples))
    assert store.path.read_bytes()==before
