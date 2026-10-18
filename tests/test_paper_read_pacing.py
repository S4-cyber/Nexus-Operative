"""Disposable local journals and injected HTTP/OS only; no provider access."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest
from nexus import paper_read_pacing as policy
from nexus import alpaca_paper_inspection as transport
from nexus import paper_credentials as credentials
from nexus.broker_request_journal import BrokerRequestJournal
from test_alpaca_paper_contract import plan, response
from test_alpaca_paper_inspection import ACCOUNT, OTHER, KEY, SECRET, Opener, Response, account
from test_alpaca_activity_collection import collect, NOW
from test_broker_paper_recovery import recover
from test_paper_credentials import vault  # Injected OS-store fixture.


@pytest.fixture
def gate(tmp_path):
    return policy.PaperReadPacing(tmp_path / 'pacing.sqlite3', ACCOUNT)


def begin(gate, at=1000): return gate.begin(admission=lambda: at)
def finish(gate, token, at=1000, **result):
    return gate.finish(token, admission=lambda: at, **(result or dict(status_code=200)))


def inspect(gate, opener=None, at=1000, **changes):
    args = dict(confirm_read_only_paper=True, expected_account_id=ACCOUNT, pacing=gate,
                opener=opener or Opener(), wall_clock=lambda: at, elapsed_clock=lambda: 50)
    args.update(changes)
    return transport.inspect_account(KEY, SECRET, **args)


def test_missing_inspection_never_creates_state_or_parent(tmp_path):
    gate = policy.PaperReadPacing(tmp_path / 'absent' / 'pacing.sqlite3', ACCOUNT)
    state = gate.inspect()
    assert state['sessions'] == 0 and state['pending_token'] is None
    assert not gate.path.parent.exists()
    with pytest.raises(ValueError, match='BINDING_MISSING'):
        finish(gate, 'unknown')
    assert not gate.path.parent.exists()


def test_reservation_precedes_every_get_and_fresh_instance_retains_cooldown(gate):
    opener = Opener(); original = opener.open
    def guarded(request, timeout):
        state = policy.PaperReadPacing(gate.path, ACCOUNT).inspect()
        assert state['pending_token'] is not None and state['sessions'] == 1
        return original(request, timeout)
    opener.open = guarded
    result = inspect(gate, opener)
    assert len(opener.requests) == 4 and result['ready_for_routing'] is False
    state = policy.PaperReadPacing(gate.path, ACCOUNT).inspect()
    assert state['pending_token'] is None and state['next_allowed_at'] == 1060
    assert state['last_outcome'] == 'SUCCESS' and state['sessions'] == 1
    denied = Opener()
    with pytest.raises(transport.PaperInspectionError): inspect(gate, denied, at=1059.9)
    assert not denied.requests and gate.inspect() == state
    inspect(gate, at=1060)
    assert gate.inspect()['sessions'] == 2
    assert not state['broker_wide_quota_verified'] and not state['automatic_retry']


@pytest.mark.parametrize('status,delay,outcome,next_at,blocked', [
    (429, '120', 'RATE_LIMITED', 1120, False),
    (429, None, 'RATE_LIMITED', 1060, False),
    (429, '0', 'RATE_LIMITED', 1060, False),
    (429, 'Thu, 01 Jan 1970 00:20:00 GMT', 'RATE_LIMITED', 1200, False),
    (429, '999999', 'REVIEW_REQUIRED', 1060, True),
    (429, 'INVALID_PRIVATE_HEADER', 'REVIEW_REQUIRED', 1060, True),
    (401, None, 'AUTH_DENIED', 1060, True),
    (403, None, 'AUTH_DENIED', 1060, True),
    (500, None, 'SERVER_ERROR', 1060, False),
    (404, None, 'NOT_FOUND', 1060, False),
])
def test_error_outcomes_shared_across_sessions_without_private_bodies(gate, status, delay, outcome, next_at, blocked):
    headers = Message()
    if delay is not None: headers['Retry-After'] = delay
    error = HTTPError(transport.URLS[0], status, SECRET, headers, __import__('io').BytesIO(SECRET.encode()))
    opener = Opener([error])
    with pytest.raises(transport.PaperInspectionError): inspect(gate, opener)
    state = gate.inspect()
    assert len(opener.requests) == 1 and error.fp.closed
    assert state['last_outcome'] == outcome and state['next_allowed_at'] == next_at
    assert state['review_required'] is blocked and state['pending_token'] is None
    with sqlite3.connect(gate.path) as db:
        data = str(db.execute('SELECT body FROM events').fetchall())
    assert KEY not in data and SECRET not in data and 'INVALID_PRIVATE_HEADER' not in data
    if blocked:
        with pytest.raises(ValueError, match='BLOCKED_REVIEW'): begin(gate, 100000)
    else:
        with pytest.raises(ValueError, match='COOLDOWN'): begin(gate, next_at - .1)
        assert begin(gate, next_at)


@pytest.mark.parametrize('exc,outcome', [(TimeoutError(SECRET), 'TIMEOUT'), (URLError(SECRET), 'NETWORK_ERROR')])
def test_transport_failure_is_recorded_once_without_retry(gate, exc, outcome):
    opener = Opener([exc])
    with pytest.raises(transport.PaperInspectionError): inspect(gate, opener)
    assert len(opener.requests) == 1
    assert gate.inspect()['last_outcome'] == outcome and gate.inspect()['events'] == 2


def test_duplicate_retry_after_requires_review(gate):
    headers = Message(); headers['Retry-After'] = '1'; headers['Retry-After'] = '2'
    opener = Opener([Response(None, status=429, headers=headers)])
    with pytest.raises(transport.PaperInspectionError): inspect(gate, opener)
    assert gate.inspect()['last_outcome'] == 'REVIEW_REQUIRED'
    assert gate.inspect()['review_required']


@pytest.mark.parametrize('value', [Response(None, raw=b'{invalid'), Response(None, url='https://other.invalid'),
                                  dict(id=OTHER)])
def test_malformed_or_changed_account_read_leaves_pending_without_auto_release(gate, value):
    with pytest.raises(transport.PaperInspectionError): inspect(gate, Opener([value]))
    state = gate.inspect()
    assert state['pending_token'] and state['events'] == 1
    with pytest.raises(ValueError, match='PENDING_REVIEW'): begin(gate, 99999999)
    assert gate.inspect() == state


@pytest.mark.parametrize('scope', [None, OTHER])
def test_gate_requires_exact_expected_account_before_io_or_write(gate, scope):
    opener = Opener()
    with pytest.raises(transport.PaperInspectionError, match='PACING_ACCOUNT_OR_TYPE'):
        inspect(gate, opener, expected_account_id=scope)
    assert not opener.requests and not gate.path.exists()


def test_revocation_after_reservation_retains_pending_and_releases_local_lock(gate):
    allowed = [True]; opener = Opener(); original = opener.open
    def revoke(request, timeout):
        allowed[0] = False
        return original(request, timeout)
    opener.open = revoke
    with pytest.raises(transport.PaperInspectionError, match='PERMISSION_REVOKED'):
        inspect(gate, opener, permission_check=lambda: allowed[0])
    assert len(opener.requests) == 1 and gate.inspect()['pending_token']
    assert transport._lock.acquire(blocking=False)
    transport._lock.release()


@pytest.mark.parametrize('operation', ['begin', 'finish'])
def test_precommit_admission_failure_rolls_back_new_event(gate, operation):
    token = begin(gate) if operation == 'finish' else None
    calls = []
    def admission():
        calls.append(1)
        if len(calls) == 2: raise ValueError('REVOKED')
        return 1000
    with pytest.raises(ValueError, match='REVOKED'):
        if token: gate.finish(token, admission=admission, status_code=200)
        else: gate.begin(admission=admission)
    if token:
        assert gate.inspect()['pending_token'] == token and gate.inspect()['events'] == 1
    else:
        # A failed initial transaction can leave an empty file; never auto-repair it.
        with pytest.raises(sqlite3.DatabaseError): gate.inspect()


def test_finish_token_and_clock_fences_preserve_original_pending(gate):
    token = begin(gate); state = gate.inspect()
    with pytest.raises(ValueError, match='TOKEN_DISAGREEMENT'): finish(gate, 'wrong')
    with pytest.raises(ValueError, match='CLOCK_REGRESSION'): finish(gate, token, at=999)
    assert gate.inspect() == state
    finish(gate, token)
    with pytest.raises(ValueError, match='TOKEN_DISAGREEMENT'): finish(gate, token)


@pytest.mark.parametrize('bad', [True, None, float('nan'), float('inf'), -1])
def test_invalid_clock_has_no_admitted_reservation(gate, bad):
    with pytest.raises(ValueError): begin(gate, bad)
    with pytest.raises(sqlite3.DatabaseError): gate.inspect()


def test_cap_reserves_last_completion_slot_without_history_eviction(gate, monkeypatch):
    monkeypatch.setattr(policy, 'MAX_SESSIONS', 2)
    for at in (1000, 1060): finish(gate, begin(gate, at), at=at)
    state = gate.inspect()
    assert state['sessions'] == 2 and state['events'] == 4
    with pytest.raises(ValueError, match='SESSION_CAP'): begin(gate, 1120)
    assert gate.inspect() == state


def test_corrupt_foreign_empty_and_symlink_history_never_admit(gate, tmp_path):
    token = begin(gate)
    with pytest.raises(ValueError, match='ACCOUNT_OR_SCHEMA'):
        policy.PaperReadPacing(gate.path, OTHER).inspect()
    with sqlite3.connect(gate.path) as db: db.execute("UPDATE events SET digest='bad' WHERE seq=1")
    opener = Opener()
    with pytest.raises(ValueError, match='CHAIN_DISAGREEMENT'): inspect(gate, opener)
    assert not opener.requests
    empty = policy.PaperReadPacing(tmp_path / 'empty.db', ACCOUNT); empty.path.touch()
    with pytest.raises(sqlite3.DatabaseError): begin(empty)
    if os.name != 'nt':
        link = tmp_path / 'link'; link.symlink_to(gate.path)
        with pytest.raises(ValueError, match='SYMLINK'): policy.PaperReadPacing(link, ACCOUNT).inspect()


def test_lost_result_ack_retains_commit_and_never_retries_storage(gate, monkeypatch):
    original = gate.finish; calls = []
    def lose(*args, **kwargs):
        calls.append(1); original(*args, **kwargs); raise OSError(SECRET)
    monkeypatch.setattr(gate, 'finish', lose)
    opener = Opener()
    with pytest.raises(transport.PaperInspectionError) as error: inspect(gate, opener)
    assert SECRET not in str(error.value) and calls == [1] and len(opener.requests) == 4
    assert gate.inspect()['last_outcome'] == 'SUCCESS' and gate.inspect()['events'] == 2


def test_activity_and_original_id_recovery_use_same_retained_reservation(gate, tmp_path):
    collect(pacing=gate)
    journal = BrokerRequestJournal(tmp_path / 'requests.db', ACCOUNT)
    journal.prepare(plan()); journal.mark_submission_uncertain('nx_fixture')
    opener = Opener([account(), response(), account()])
    with pytest.raises(transport.PaperInspectionError):
        recover(journal, opener, pacing=gate, wall_clock=lambda: NOW + 59)
    assert not opener.requests and journal.pending_lookup()['pending'] is False
    recover(journal, opener, pacing=gate, wall_clock=lambda: NOW + 60)
    assert len(opener.requests) == 3 and gate.inspect()['sessions'] == 2


def test_lookup_rate_limit_retains_both_journal_and_shared_cooldown(gate, tmp_path):
    journal = BrokerRequestJournal(tmp_path / 'requests.db', ACCOUNT)
    journal.prepare(plan()); journal.mark_submission_uncertain('nx_fixture')
    opener = Opener([account(), Response(None, status=429, headers={'Retry-After': '120'})])
    result = recover(journal, opener, pacing=gate)
    assert not result['ready_for_routing'] and len(opener.requests) == 2
    assert gate.inspect()['next_allowed_at'] == 1120 and not journal.pending_lookup()['pending']


def test_saved_wrappers_forward_explicit_gate_with_injected_vault(gate, vault, tmp_path):
    credentials.install(KEY, SECRET, confirm_store=True)
    kwargs = dict(confirm_read_only_paper=True, expected_account_id=ACCOUNT, pacing=gate,
                  opener=Opener(), wall_clock=lambda: NOW, elapsed_clock=lambda: 50)
    credentials.inspect_saved(**kwargs)
    with pytest.raises(transport.PaperInspectionError):
        credentials.collect_saved([], after='2026-10-17T00:00:00Z', until='2026-10-18T00:00:00Z', **kwargs)
    journal = BrokerRequestJournal(tmp_path / 'requests.db', ACCOUNT)
    journal.prepare(plan()); journal.mark_submission_uncertain('nx_fixture')
    with pytest.raises(transport.PaperInspectionError): credentials.recover_saved(journal, 'nx_fixture', **kwargs)
    assert gate.inspect()['sessions'] == 1


def test_process_death_after_committed_reservation_never_auto_expires(gate):
    code = "from nexus.paper_read_pacing import PaperReadPacing; import os,sys; g=PaperReadPacing(sys.argv[1],sys.argv[2]); g.begin(admission=lambda:1000); os._exit(37)"
    result = subprocess.run([sys.executable, '-c', code, str(gate.path), ACCOUNT], capture_output=True, timeout=20)
    assert result.returncode == 37
    assert gate.inspect()['pending_token']
    with pytest.raises(ValueError, match='PENDING_REVIEW'): begin(gate, 1000000)


def test_cross_process_pending_reservation_blocks_competing_session(gate):
    begin(gate)
    code = "from nexus.paper_read_pacing import PaperReadPacing; import sys; g=PaperReadPacing(sys.argv[1],sys.argv[2]); g.begin(admission=lambda:1000)"
    result = subprocess.run([sys.executable, '-c', code, str(gate.path), ACCOUNT], capture_output=True, timeout=20)
    assert result.returncode != 0 and b'PENDING_REVIEW' in result.stderr
    assert gate.inspect()['events'] == 1


def test_competing_processes_cannot_both_reserve_same_ready_journal(gate):
    finish(gate, begin(gate))
    code = "from nexus.paper_read_pacing import PaperReadPacing; import sys; g=PaperReadPacing(sys.argv[1],sys.argv[2]); g.begin(admission=lambda:1060); print('ADMITTED')"
    processes = [subprocess.Popen([sys.executable, '-c', code, str(gate.path), ACCOUNT], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    results = [p.communicate(timeout=20) for p in processes]
    assert sorted(p.returncode for p in processes) == [0, 1]
    assert sum(b'ADMITTED' in out for out, err in results) == 1
    assert sum(b'PENDING_REVIEW' in err for out, err in results) == 1
    assert gate.inspect()['sessions'] == 2 and gate.inspect()['events'] == 3


def test_admission_samples_clock_after_real_sqlite_contention(gate, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    finish(gate, begin(gate))
    at = [1059]; attempted = threading.Event(); original = gate._connect
    def connect(*args, **kwargs):
        attempted.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(gate, '_connect', connect)
    with sqlite3.connect(gate.path) as db:
        db.execute('BEGIN IMMEDIATE')
        with ThreadPoolExecutor() as pool:
            future = pool.submit(gate.begin, admission=lambda: at[0])
            assert attempted.wait(timeout=2)
            at[0] = 1060
            db.commit()
            token = future.result(timeout=10)
    assert gate.inspect()['pending_token'] == token
    assert gate.inspect()['last_evaluated_at'] == 1060


def test_lost_begin_ack_retains_reservation_without_network(gate, monkeypatch):
    original = gate.begin
    def lose(**kwargs):
        original(**kwargs)
        raise OSError(SECRET)
    monkeypatch.setattr(gate, 'begin', lose)
    opener = Opener()
    with pytest.raises(transport.PaperInspectionError): inspect(gate, opener)
    assert not opener.requests and gate.inspect()['pending_token']


def test_no_extra_get_after_recorded_negative_lookup(gate):
    opener = Opener([Response(None, status=429, headers={'Retry-After': '120'})])
    from nexus.broker_lookup_policy import LOOKUP_URL
    with pytest.raises(transport.PaperInspectionError, match='PACING_SESSION_FINISHED'):
        with transport._ReadSession(KEY, SECRET, confirm_read_only_paper=True, pacing=gate,
                expected_account_id=ACCOUNT, opener=opener, wall_clock=lambda:1000, elapsed_clock=lambda:50) as session:
            result = session.get(LOOKUP_URL + '?client_order_id=nx_fixture', lookup=True)
            assert result['status_code'] == 429
            session.get(transport.URLS[0])
    assert len(opener.requests) == 1 and gate.inspect()['next_allowed_at'] == 1120


@pytest.mark.parametrize('kind', ['BEGIN', 'RESULT'])
def test_process_death_before_commit_rolls_back_only_new_event(gate, kind):
    if kind == 'BEGIN': finish(gate, begin(gate))
    else: begin(gate)
    before = gate.inspect()
    code = '''from nexus.paper_read_pacing import PaperReadPacing
import os,sys
g=PaperReadPacing(sys.argv[1],sys.argv[2])
original=g._append
def die(db,seq,body,previous):
    result=original(db,seq,body,previous)
    if body.get('kind')==sys.argv[3]: os._exit(38)
    return result
g._append=die
if sys.argv[3]=='BEGIN': g.begin(admission=lambda:1060)
else: g.finish(g.inspect()['pending_token'],admission=lambda:1000,status_code=200)
'''
    result = subprocess.run([sys.executable, '-c', code, str(gate.path), ACCOUNT, kind], capture_output=True, timeout=20)
    assert result.returncode == 38
    assert gate.inspect() == before


def test_existing_database_file_cap_blocks_before_http(gate, monkeypatch):
    finish(gate, begin(gate))
    monkeypatch.setattr(policy, 'MAX_DATABASE_BYTES', 1)
    opener = Opener()
    with pytest.raises(ValueError, match='DATABASE_CAP'): inspect(gate, opener)
    assert not opener.requests
