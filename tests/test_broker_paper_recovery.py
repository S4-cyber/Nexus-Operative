"""Injected HTTPS and temporary journal integration; never an actual broker."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest
from nexus import alpaca_paper_inspection as transport
from nexus import broker_request_journal as storage
from nexus import broker_paper_recovery as recovery
from nexus import paper_credentials as credentials
from nexus.broker_lookup_policy import LOOKUP_URL
from test_alpaca_paper_contract import plan, response
from test_alpaca_paper_inspection import Opener, Response, account, ACCOUNT, OTHER, KEY, SECRET
from test_paper_credentials import Vault


@pytest.fixture
def journal(tmp_path):
    j = storage.BrokerRequestJournal(tmp_path / 'requests.sqlite3', ACCOUNT)
    j.prepare(plan()); j.mark_submission_uncertain('nx_fixture')
    return j


def recover(journal, opener=None, **changes):
    args = dict(expected_account_id=ACCOUNT, confirm_read_only_paper=True,
                opener=opener or Opener([account(), response(), account()]),
                wall_clock=lambda: 1000, elapsed_clock=lambda: 50)
    args.update(changes)
    return recovery.recover_original(journal, 'nx_fixture', KEY, SECRET, **args)


def current(journal):
    return journal.recovery()['requests'][0]


def test_found_original_id_commits_only_normalized_observation(journal):
    opener = Opener([account(), response(status='partially_filled', filled_qty='1', filled_avg_price='99', private=SECRET), account()])
    report = recover(journal, opener)
    assert [r.full_url for r, _ in opener.requests] == [transport.URLS[0], LOOKUP_URL + '?client_order_id=nx_fixture', transport.URLS[0]]
    assert all(r.get_method() == 'GET' and r.data is None for r, _ in opener.requests)
    assert report['account_fields_double_checked'] is True and report['source'] == 'INJECTED_UNATTESTED'
    for field in ('automatic_retry', 'sendable', 'ready_for_routing', 'spendable_funds_verified',
                  'broker_account_reconciled', 'broker_connected', 'live_orders_enabled', 'ledger_applied'):
        assert report[field] is False
    retained = current(storage.BrokerRequestJournal(journal.path, ACCOUNT))
    assert retained['snapshot']['filled_quantity'] == '1'
    assert retained['lookup']['attempts'] == 1 and retained['lookup']['pending'] is False
    assert retained['submission_attempt_marked'] is True
    assert KEY not in json.dumps(report) and SECRET not in json.dumps(report)
    with sqlite3.connect(journal.path) as db:
        assert SECRET not in ''.join(r[0] for r in db.execute('SELECT body FROM events'))
    assert all(r.closed for r in opener.responses)


def test_journal_admission_precedes_every_network_request(journal):
    opener = Opener([account(), response(), account()]); original = opener.open
    def admitted(request, timeout):
        state = current(storage.BrokerRequestJournal(journal.path, ACCOUNT))
        assert state['lookup']['pending'] and state['lookup']['attempts'] == 1
        return original(request, timeout)
    opener.open = admitted
    recover(journal, opener)


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_consent_failure_has_no_network_or_journal_write(journal, confirmation):
    before = journal.recovery(); opener = Opener()
    with pytest.raises(transport.PaperInspectionError, match='EXPLICIT_AUTHORIZATION_REQUIRED'):
        recover(journal, opener, confirm_read_only_paper=confirmation)
    assert not opener.requests and journal.recovery() == before


def test_missing_unknown_foreign_or_unsent_requests_never_contact_provider(journal, tmp_path):
    missing = storage.BrokerRequestJournal(tmp_path / 'absent' / 'none.sqlite3', ACCOUNT)
    unsent = storage.BrokerRequestJournal(tmp_path / 'unsent.sqlite3', ACCOUNT); unsent.prepare(plan())
    foreign = storage.BrokerRequestJournal(journal.path, OTHER)
    for j, expected in ((missing, ACCOUNT), (foreign, OTHER), (journal, OTHER), (unsent, ACCOUNT)):
        opener = Opener()
        with pytest.raises(ValueError): recover(j, opener, expected_account_id=expected)
        assert not opener.requests
    assert not missing.path.parent.exists()
    opener = Opener(); before = journal.recovery()
    with pytest.raises(ValueError):
        recovery.recover_original(journal, 'bad?client_order_id=second', KEY, SECRET,
            expected_account_id=ACCOUNT, confirm_read_only_paper=True, opener=opener)
    assert not opener.requests and journal.recovery() == before


def test_lookup_request_is_copied_read_only_original_terms(journal):
    before = journal.recovery(); copied = journal.lookup_request('nx_fixture')
    copied['plan']['payload']['qty'] = '999'
    assert journal.lookup_request('nx_fixture')['plan']['payload']['qty'] == '2'
    assert journal.recovery() == before


@pytest.mark.parametrize('code,outcome', [(404, 'NOT_FOUND'), (401, 'AUTH_DENIED'), (403, 'AUTH_DENIED'),
                                         (429, 'RATE_LIMITED'), (500, 'SERVER_ERROR'), (201, 'REVIEW_REQUIRED')])
def test_http_outcomes_preserve_uncertainty_and_do_not_read_error_body_or_retry(journal, code, outcome):
    error = Response(None, status=code, headers={'Retry-After': '30'}, raw=SECRET.encode())
    opener = Opener([account(), error])
    report = recover(journal, opener)
    state = current(journal)
    assert state['lookup']['last_outcome'] == outcome and state['snapshot'] is None
    assert state['lookup_by_original_client_id_required'] is True
    assert report['account_fields_double_checked'] is False
    assert len(opener.requests) == 2 and error.reads == 0 and error.closed
    if code == 429: assert state['lookup']['next_allowed_at'] == 1030
    if code in (401, 403, 201): assert state['lookup']['review_blocked'] is True
    assert SECRET not in json.dumps(report)


def test_actual_http_error_is_closed_and_retry_header_is_never_retained(journal):
    import io
    stream = io.BytesIO(SECRET.encode())
    headers = Message(); headers['Retry-After'] = '30'
    error = HTTPError(LOOKUP_URL + '?client_order_id=nx_fixture', 429, SECRET, headers, stream)
    report = recover(journal, Opener([account(), error]))
    assert report['journal']['lookup']['next_allowed_at'] == 1030 and stream.closed
    assert SECRET not in json.dumps(report)


@pytest.mark.parametrize('header', ['bad', '86401', '30\r\nprivate', 'x' * 129], ids=['bad', 'long-wait', 'crlf', 'large'])
def test_invalid_retry_after_blocks_review_without_shortened_wait(journal, header):
    recover(journal, Opener([account(), Response(None, status=429, headers={'Retry-After': header})]))
    assert current(journal)['lookup']['review_blocked'] is True
    assert current(journal)['lookup']['last_outcome'] == 'REVIEW_REQUIRED'


def test_duplicate_retry_headers_require_review(journal):
    headers = Message(); headers['Retry-After'] = '3'; headers['Retry-After'] = '30'
    recover(journal, Opener([account(), Response(None, status=429, headers=headers)]))
    assert current(journal)['lookup']['last_outcome'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('error,outcome', [(TimeoutError(SECRET), 'TIMEOUT'), (URLError(TimeoutError(SECRET)), 'TIMEOUT'),
                                         (URLError(SECRET), 'NETWORK_ERROR'), (OSError(SECRET), 'NETWORK_ERROR')])
def test_network_outcomes_commit_uncertainty_without_retry(journal, error, outcome):
    opener = Opener([account(), error]); report = recover(journal, opener)
    assert report['journal']['lookup']['last_outcome'] == outcome
    assert current(journal)['lookup_by_original_client_id_required'] is True
    assert len(opener.requests) == 2 and not current(journal)['lookup']['pending']


@pytest.mark.parametrize('raw', [b'{"id":1,"id":2}', b'{"x":NaN}', b' ' * (transport.MAX_BODY + 1), b'{'],
                         ids=['duplicate', 'nonfinite', 'cap', 'json'])
def test_malformed_success_retains_pending_ticket_for_review(journal, raw):
    opener = Opener([account(), Response(None, raw=raw)])
    with pytest.raises(transport.PaperInspectionError): recover(journal, opener)
    assert current(journal)['lookup']['pending'] and current(journal)['snapshot'] is None
    assert len(opener.requests) == 2 and opener.responses[-1].closed


@pytest.mark.parametrize('code', [301, 302, 307, 308])
def test_redirect_is_not_followed_or_converted_to_not_found(journal, code):
    denied = Response(None, status=code, raw=SECRET.encode()); opener = Opener([account(), denied])
    with pytest.raises(transport.PaperInspectionError): recover(journal, opener)
    assert len(opener.requests) == 2 and denied.reads == 0 and denied.closed
    assert current(journal)['lookup']['pending']


@pytest.mark.parametrize('changes', [{'client_order_id': 'second'}, {'qty': '3'}, {'symbol': 'ETH/USD'},
                                    {'status': 'unknown'}, {'id': 'invalid'}])
def test_found_identity_or_terms_disagreement_never_changes_observation(journal, changes):
    with pytest.raises(transport.PaperInspectionError):
        recover(journal, Opener([account(), response(**changes), account()]))
    assert current(journal)['snapshot'] is None and current(journal)['lookup']['pending']


def test_cumulative_regression_preserves_retained_partial_snapshot(journal):
    journal.observe('nx_fixture', response(status='partially_filled', filled_qty='1', filled_avg_price='99'))
    with pytest.raises(transport.PaperInspectionError): recover(journal)
    assert current(journal)['snapshot']['filled_quantity'] == '1' and current(journal)['lookup']['pending']


@pytest.mark.parametrize('phase', ['first-account', 'last-account', 'last-cash'])
def test_account_mismatch_does_not_credit_response(journal, phase):
    first, last = account(), account()
    if phase == 'first-account': first['id'] = OTHER
    elif phase == 'last-account': last['id'] = OTHER
    else: last['cash'] = '999'
    opener = Opener([first, response(), last])
    with pytest.raises(transport.PaperInspectionError): recover(journal, opener)
    assert current(journal)['lookup']['pending'] and current(journal)['snapshot'] is None
    assert len(opener.requests) == (1 if phase == 'first-account' else 3)


def test_rate_limit_cooldown_is_retained_and_blocks_another_transport(journal):
    recover(journal, Opener([account(), Response(None, status=429, headers={'Retry-After': '30'})]))
    before = journal.recovery(); opener = Opener()
    with pytest.raises(transport.PaperInspectionError): recover(journal, opener)
    assert not opener.requests and journal.recovery() == before


def test_clock_fault_after_response_does_not_commit_outcome(journal):
    opener = Opener([account(), response(), account()]); original = opener.open; now = [1000]
    def jumping(request, timeout):
        result = original(request, timeout)
        if len(opener.requests) == 2: now[0] += 10
        return result
    opener.open = jumping
    with pytest.raises(transport.PaperInspectionError, match='CLOCK_DISCONTINUITY'):
        recover(journal, opener, wall_clock=lambda: now[0])
    assert current(journal)['lookup']['pending'] and current(journal)['snapshot'] is None


def test_credential_check_failure_preserves_pending_and_no_snapshot(journal):
    with pytest.raises(transport.PaperInspectionError, match='CREDENTIALS_CHANGED'):
        recover(journal, credential_check=lambda: False)
    assert current(journal)['lookup']['pending'] and current(journal)['snapshot'] is None


@pytest.mark.parametrize('kind', ['LOOKUP_BEGIN', 'LOOKUP_RESULT'])
def test_permission_revoked_after_insert_rolls_back_before_commit(journal, monkeypatch, kind):
    allowed = [True]; original = journal._insert; opener = Opener([account(), response(), account()])
    def revoke(db, seq, event, body, previous):
        original(db, seq, event, body, previous)
        if event == kind: allowed[0] = False
    monkeypatch.setattr(journal, '_insert', revoke)
    with pytest.raises(transport.PaperInspectionError, match='PERMISSION_REVOKED'):
        recover(journal, opener, permission_check=lambda: allowed[0])
    state = current(journal)
    assert state['snapshot'] is None
    assert state['lookup']['pending'] is (kind == 'LOOKUP_RESULT')
    if kind == 'LOOKUP_BEGIN': assert not opener.requests and state['lookup']['attempts'] == 0


def test_checked_result_samples_time_after_write_admission(journal, monkeypatch):
    token = journal.begin_lookup('nx_fixture', evaluated_at=1000)['lookup_token']
    original = journal._connect; now = [1001]
    def after_lock(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get('write'): now[0] = 1010
        return result
    monkeypatch.setattr(journal, '_connect', after_lock)
    result = journal.finish_lookup_checked('nx_fixture', token, admission=lambda: now[0], status_code=429, retry_after='30')
    assert result['lookup']['next_allowed_at'] == 1040


def test_checked_admission_denial_after_write_lock_does_not_charge_attempt(journal, monkeypatch):
    original = journal._connect; allowed = [True]; before = journal.recovery()
    def after_lock(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get('write'): allowed[0] = False
        return result
    monkeypatch.setattr(journal, '_connect', after_lock)
    opener = Opener()
    with pytest.raises(transport.PaperInspectionError, match='PERMISSION_REVOKED'):
        recover(journal, opener, permission_check=lambda: allowed[0])
    assert not opener.requests and journal.recovery() == before


def test_pending_transport_crash_can_only_be_reviewed_explicitly(journal):
    with pytest.raises(transport.PaperInspectionError):
        recover(journal, Opener([account(), Response(None, raw=b'{')]))
    cold = storage.BrokerRequestJournal(journal.path, ACCOUNT); pending = cold.pending_lookup()
    assert pending is not None
    cold.interrupt_lookup('nx_fixture', pending['lookup_token'], expected_head=pending['head_sha256'],
                          evaluated_at=1001, confirm_interrupted=True)
    with pytest.raises(ValueError): cold.begin_lookup('nx_fixture', evaluated_at=1002)
    assert current(cold)['lookup_by_original_client_id_required']


def test_saved_credential_removal_during_lookup_fences_response(journal, monkeypatch):
    vault = Vault(); monkeypatch.setattr(credentials, '_vault', lambda: vault)
    monkeypatch.setattr(credentials, '_epoch', 0); monkeypatch.setattr(credentials, '_blocked', False)
    credentials.install(KEY, SECRET, confirm_store=True)
    opener = Opener([account(), response(), account()]); original = opener.open
    def remove(request, timeout):
        result = original(request, timeout)
        if len(opener.requests) == 2: credentials.remove(confirm_remove=True)
        return result
    opener.open = remove
    with pytest.raises(transport.PaperInspectionError, match='PERMISSION_REVOKED'):
        credentials.recover_saved(journal, 'nx_fixture', expected_account_id=ACCOUNT,
            confirm_read_only_paper=True, opener=opener, wall_clock=lambda: 1000, elapsed_clock=lambda: 50)
    assert current(journal)['lookup']['pending'] and current(journal)['snapshot'] is None


def test_session_path_caps_lifetime_and_default_opener_errors_are_closed(monkeypatch):
    opener = Opener([account()] * 4)
    session = transport._ReadSession(KEY, SECRET, confirm_read_only_paper=True, opener=opener)
    with session:
        for path, lookup in [('https://api.alpaca.markets/v2/account', False),
                             (LOOKUP_URL + '?client_order_id=nx&other=1', True)]:
            with pytest.raises(transport.PaperInspectionError, match='PATH_DENIED'): session.get(path, lookup=lookup)
        for _ in range(4): session.get(transport.URLS[0])
        with pytest.raises(transport.PaperInspectionError, match='REQUEST_CAP'): session.get(transport.URLS[0])
    with pytest.raises(transport.PaperInspectionError, match='NOT_ACTIVE'): session.get(transport.URLS[0])
    with pytest.raises(transport.PaperInspectionError, match='REUSE_DENIED'):
        with session: pass
    def broken(*args): raise RuntimeError(SECRET)
    monkeypatch.setattr(transport, 'build_opener', broken)
    with pytest.raises(transport.PaperInspectionError, match='RESPONSE_INVALID') as caught:
        with transport._ReadSession(KEY, SECRET, confirm_read_only_paper=True): pass
    assert SECRET not in str(caught.value)
    assert transport._lock.acquire(False); transport._lock.release()


@pytest.mark.parametrize('phase', ['before-account', 'after-found'])
def test_identity_read_failure_retains_pending_instead_of_applying_order(journal, phase):
    values = [TimeoutError(SECRET)] if phase == 'before-account' else [account(), response(), TimeoutError(SECRET)]
    with pytest.raises(transport.PaperInspectionError, match='TRANSPORT_UNAVAILABLE'):
        recover(journal, Opener(values))
    assert current(journal)['lookup']['pending'] and current(journal)['snapshot'] is None


def test_not_found_does_not_erase_a_previously_observed_partial_fill(journal):
    journal.observe('nx_fixture', response(status='partially_filled', filled_qty='1', filled_avg_price='99'))
    recover(journal, Opener([account(), Response(None, status=404)]))
    state = current(journal)
    assert state['snapshot']['filled_quantity'] == '1' and state['submission_attempt_marked']
    assert state['lookup']['last_outcome'] == 'NOT_FOUND' and not state['automatic_retry']


def test_saved_entry_change_is_checked_before_outcome_commit(journal, monkeypatch):
    vault = Vault(); monkeypatch.setattr(credentials, '_vault', lambda: vault)
    monkeypatch.setattr(credentials, '_epoch', 0); monkeypatch.setattr(credentials, '_blocked', False)
    credentials.install(KEY, SECRET, confirm_store=True)
    opener = Opener([account(), response(), account()]); original = opener.open
    def changed(request, timeout):
        result = original(request, timeout)
        if len(opener.requests) == 3:
            key = (credentials.SERVICE, credentials.ACCOUNT)
            record = json.loads(vault.data[key]); record['api_key'] = 'other_fixture_key_1234'
            vault.data[key] = json.dumps(record)
        return result
    opener.open = changed
    with pytest.raises(transport.PaperInspectionError, match='CREDENTIALS_CHANGED'):
        credentials.recover_saved(journal, 'nx_fixture', expected_account_id=ACCOUNT,
            confirm_read_only_paper=True, opener=opener, wall_clock=lambda: 1000, elapsed_clock=lambda: 50)
    assert current(journal)['lookup']['pending'] and current(journal)['snapshot'] is None


SCRIPT = r'''
import os,sys
sys.path.insert(0,sys.argv[3])
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.broker_paper_recovery import recover_original
from test_alpaca_paper_inspection import ACCOUNT,KEY,SECRET,Opener,account
from test_alpaca_paper_contract import response
j=BrokerRequestJournal(sys.argv[1],ACCOUNT)
phase=sys.argv[2]
insert=j._insert
write=j._lookup_write
def interrupted_insert(db,seq,kind,body,previous):
    insert(db,seq,kind,body,previous)
    if kind=='LOOKUP_RESULT' and phase=='before_commit':os._exit(61)
def interrupted_write(identity,now,result=None,admission=None):
    value=write(identity,now,result,admission=admission)
    if result is not None and phase=='after_commit':os._exit(62)
    return value
j._insert=interrupted_insert
j._lookup_write=interrupted_write
recover_original(j,'nx_fixture',KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
    opener=Opener([account(),response(status='partially_filled',filled_qty='1',filled_avg_price='99'),account()]),
    wall_clock=lambda:1000,elapsed_clock=lambda:50)
'''


@pytest.mark.parametrize('phase,exit_code,pending,filled', [('before_commit', 61, True, None),
                                                          ('after_commit', 62, False, '1')])
def test_actual_process_death_before_and_after_result_commit_recovers_retained_prefix(journal, phase, exit_code, pending, filled):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-c', SCRIPT, str(journal.path), phase, str(root / 'tests')],
                            cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == exit_code, result.stderr
    cold = storage.BrokerRequestJournal(journal.path, ACCOUNT)
    state = current(cold)
    assert state['lookup']['pending'] is pending and state['lookup']['attempts'] == 1
    assert (state['snapshot']['filled_quantity'] if state['snapshot'] else None) == filled
    assert state['submission_attempt_marked'] and state['automatic_retry'] is False
    reopened = subprocess.run([sys.executable, '-c',
        'import json,sys;from nexus.broker_request_journal import BrokerRequestJournal;print(json.dumps(BrokerRequestJournal(sys.argv[1],sys.argv[2]).recovery()))',
        str(journal.path), ACCOUNT], cwd=root, capture_output=True, text=True, timeout=20)
    assert reopened.returncode == 0, reopened.stderr
    assert json.loads(reopened.stdout) == cold.recovery()


def test_four_retained_attempts_cannot_be_rearmed_by_restart(journal):
    now = 1000
    for _ in range(4):
        result = recover(journal, Opener([account(), Response(None, status=404)]), wall_clock=lambda: now)
        now = result['journal']['lookup']['next_allowed_at']
        journal = storage.BrokerRequestJournal(journal.path, ACCOUNT)
    opener = Opener()
    with pytest.raises(transport.PaperInspectionError): recover(journal, opener, wall_clock=lambda: now)
    assert not opener.requests and current(journal)['lookup']['attempts'] == 4


def test_session_lock_is_shared_with_manual_account_inspection(journal):
    opener = Opener()
    with transport._ReadSession(KEY, SECRET, confirm_read_only_paper=True, opener=Opener()):
        before = journal.recovery()
        with pytest.raises(transport.PaperInspectionError, match='BUSY'): recover(journal, opener)
        assert not opener.requests and journal.recovery() == before


@pytest.mark.parametrize('phase', ['begin', 'finish'])
def test_real_sqlite_contention_rechecks_revocation_after_lock_release(journal, phase):
    import threading
    blocker = sqlite3.connect(journal.path, check_same_thread=False)
    allowed = [True]; waiting = threading.Event(); errors = []
    opener = Opener([account(), response(), account()]); open_original = opener.open
    connect_original = journal._connect
    if phase == 'begin': blocker.execute('BEGIN IMMEDIATE')
    def connect(*args, **kwargs):
        if kwargs.get('write') and phase == 'begin': waiting.set()
        return connect_original(*args, **kwargs)
    journal._connect = connect
    def open_response(request, timeout):
        value = open_original(request, timeout)
        if phase == 'finish' and len(opener.requests) == 3:
            blocker.execute('BEGIN IMMEDIATE'); waiting.set()
        return value
    opener.open = open_response
    def worker():
        try: recover(journal, opener, permission_check=lambda: allowed[0])
        except transport.PaperInspectionError as exc: errors.append(str(exc))
    thread = threading.Thread(target=worker); thread.start()
    try:
        assert waiting.wait(10)
        threading.Event().wait(.05)
        assert thread.is_alive()
        allowed[0] = False
        blocker.commit()
        thread.join(10)
        assert not thread.is_alive()
    finally:
        blocker.rollback(); blocker.close()
    assert errors == ['PAPER_INSPECTION_PERMISSION_REVOKED']
    state = current(journal)
    assert state['snapshot'] is None and state['lookup']['pending'] is (phase == 'finish')
    if phase == 'begin': assert not opener.requests and state['lookup']['attempts'] == 0


def test_revocation_after_result_commit_loses_ack_but_preserves_committed_observation(journal, monkeypatch):
    original = journal._lookup_write; allowed = [True]
    def committed(identity, now, result=None, admission=None):
        value = original(identity, now, result, admission=admission)
        if result is not None: allowed[0] = False
        return value
    monkeypatch.setattr(journal, '_lookup_write', committed)
    with pytest.raises(transport.PaperInspectionError, match='PERMISSION_REVOKED'):
        recover(journal, permission_check=lambda: allowed[0])
    state = current(journal)
    assert state['snapshot'] is not None and state['lookup']['pending'] is False
    assert state['lookup']['attempts'] == 1 and state['automatic_retry'] is False
