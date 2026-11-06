"""Synthetic restart evidence for the installed Activity SSE operator.

The fixture uses a temporary saved-pair seam, injected event-stream bytes and
private temporary journals.  It performs no provider request, persists no
credential, event or account evidence, and grants no external authority.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from nexus import paper_activity_sse_acceptance_probe as operator
from nexus import paper_credentials
from nexus.alpaca_activity_sse_cursor_store import ZERO
from nexus.alpaca_activity_sse_review import _event
from nexus.alpaca_paper_contract import canonical


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
KEY = 'K' * 24
SECRET = 'S' * 32
AUTHORITY_FLAGS = (
    'provider_source_attested', 'activity_coverage_verified',
    'fee_execution_links_verified', 'broker_account_reconciled',
    'spendable_funds_verified', 'ready_for_routing', 'broker_connected',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved')


class _Vault:
    def __init__(self):
        self.raw = None

    def set_password(self, service, account, raw):
        self.raw = raw

    def get_password(self, service, account):
        return self.raw


class _Response:
    status = 200
    headers = {'Content-Type': 'text/event-stream; charset=utf-8'}

    def __init__(self, raw, target):
        self.body = io.BytesIO(raw)
        self.target = target

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def geturl(self):
        return self.target

    def read(self, size):
        return self.body.read(size)


class _Opener:
    def __init__(self, raw):
        self.raw = raw
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.get_method(), request.full_url, timeout))
        return _Response(self.raw, request.full_url)


class _FailingOpener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.get_method(), request.full_url, timeout))
        raise OSError('SYNTHETIC_ACTIVITY_STREAM_UNAVAILABLE')


def _segments():
    first_trade = _event('01K00000000000000000000001',
        '11111111-1111-1111-1111-111111111111', 'TRD')
    first_fee = _event('01K00000000000000000000002',
        '22222222-2222-2222-2222-222222222222', 'FEE')
    later_trade = _event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333', 'TRD',
        details={**first_trade['details'],
            'order_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id': 'synthetic_fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    later_fee = _event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444', 'FEE',
        details={'parent_id': later_trade['ref_id'],
            'system_date': '2026-10-18'},
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    initial = [first_trade, first_fee, later_trade]
    replay = [copy.deepcopy(later_trade), later_fee]
    raw = ''.join('data: ' + canonical(row) + '\n\n' for row in replay).encode()
    return initial, raw


def _snapshot(root):
    result = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result.append((path.name, path.stat().st_size, digest))
    return tuple(result)


def _cold_status(root, clock=1001, order_name='orders.sqlite3'):
    expression = r'''
import hashlib,json,sys
from pathlib import Path
from nexus import paper_credentials
from nexus.paper_activity_sse_acceptance_probe import status
root=Path(sys.argv[1]);account=sys.argv[2];evaluated=float(sys.argv[3])
order_name=sys.argv[4]
attempts={'external_connections':0,'credential_operations':0}
def audit(event,args):
 if event=='socket.connect':
  attempts['external_connections']+=1
  raise RuntimeError('NETWORK_DENIED')
def denied():
 attempts['credential_operations']+=1
 raise RuntimeError('VAULT_DENIED')
def snapshot():
 rows=[]
 for path in sorted(root.iterdir()):
  if path.is_file():
   raw=path.read_bytes();rows.append((path.name,len(raw),hashlib.sha256(raw).hexdigest()))
 return rows
sys.addaudithook(audit);paper_credentials._vault=denied
before=snapshot()
value=status(expected_account_id=account,cursor_path=root/'cursor.sqlite3',
 budget_path=root/'budget.sqlite3',order_journal_path=root/order_name,
 clock=lambda:evaluated)
after=snapshot()
print(json.dumps({'snapshot_consistent':value['snapshot_consistent'],
 'storage_snapshot_consistent':value['storage_snapshot_consistent'],
 'one_attempt_eligible':value['one_attempt_eligible'],
 'cursor_head_sha256':value['cursor_head_sha256'],
 'reconnect_budget_head_sha256':value['reconnect_budget_head_sha256'],
 'reconnect_budget_completed':value['reconnect_completed'],
 'reconnect_attempt_number':value['reconnect_attempts'],
 'reconnect_next_allowed_at':value['reconnect_next_allowed_at'],
 'reconnect_pending':value['reconnect_pending'],
 'reconnect_review_required':value['reconnect_review_required'],
 'emergency_cut_latch_active':value['emergency_cut_latch_active'],
 'emergency_cut_scope_consistent':value['emergency_cut_scope_consistent'],
 'automatic_retry':value['automatic_retry'],
 'automatic_continuation':value['automatic_continuation'],
 'journals_unchanged':before==after,**attempts},sort_keys=True))
'''
    try:
        child = subprocess.run([sys.executable, '-c', expression, str(root),
            ACCOUNT, str(clock), order_name], capture_output=True, text=True,
            timeout=20)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('PAPER_ACTIVITY_SSE_ACCEPTANCE_EVIDENCE_RESTART') from None
    if child.returncode != 0:
        raise ValueError('PAPER_ACTIVITY_SSE_ACCEPTANCE_EVIDENCE_RESTART')
    return json.loads(child.stdout)


def _independent_stop(root):
    expression = r'''
import json,sys
from nexus import paper_credentials
from nexus.emergency_cut_tool import activate
attempts={'external_connections':0,'credential_operations':0}
def audit(event,args):
 if event=='socket.connect':
  attempts['external_connections']+=1
  raise RuntimeError('NETWORK_DENIED')
def denied():
 attempts['credential_operations']+=1
 raise RuntimeError('VAULT_DENIED')
sys.addaudithook(audit);paper_credentials._vault=denied
value=activate(sys.argv[1])
print(json.dumps({'passed':value['passed'],'halted':value['halted'],
 'intent_preceded_durable_halt':value['intent_preceded_durable_halt'],
 'emergency_intent_present':value['emergency_intent_present'],
 'durable_order_halt_confirmed':value['durable_order_halt_confirmed'],
 'desktop_started':value['desktop_started'],
 'network_requested':value['network_requested'],
 'credential_access_requested':value['credential_access_requested'],
 'automatic_restart':value['automatic_restart'],**attempts},sort_keys=True))
'''
    try:
        child = subprocess.run([sys.executable, '-c', expression, str(root)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, 'NEXUS_PORTABLE_DATA': str(root)})
    except (OSError, subprocess.SubprocessError):
        raise ValueError(
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_EVIDENCE_STOP') from None
    if child.returncode != 0:
        raise ValueError('PAPER_ACTIVITY_SSE_ACCEPTANCE_EVIDENCE_STOP')
    return json.loads(child.stdout)


def fixture_report():
    """Exercise one injected attempt and a vault/network-silent cold replay."""
    original_vault = paper_credentials._vault
    original_epoch = paper_credentials._epoch
    original_blocked = paper_credentials._blocked
    try:
        with tempfile.TemporaryDirectory(
                prefix='nexus-activity-sse-acceptance-evidence-') as directory:
            root = Path(directory)
            initial, raw = _segments()
            source = root/'initial.json'
            source.write_text(canonical(initial), encoding='utf-8')
            source_before = source.read_bytes()
            vault = _Vault()
            paper_credentials._vault = lambda: vault
            paper_credentials._epoch = 0
            paper_credentials._blocked = False
            paper_credentials.install(KEY, SECRET, confirm_store=True)
            opener = _Opener(raw)
            result = operator.attempt(expected_account_id=ACCOUNT,
                initial_events_path=source,
                cursor_path=root/'cursor.sqlite3',
                budget_path=root/'budget.sqlite3',
                order_journal_path=root/'orders.sqlite3',
                expected_cursor_head=ZERO, expected_budget_head=ZERO,
                reconnect_reason='CONNECTION_DROPPED',
                confirm_reconnect_attempt=True,
                confirm_read_only_paper=True,
                confirm_cursor_checkpoint=True,
                opener=opener, wall_clock=lambda:1000,
                elapsed_clock=lambda:50, outcome_clock=lambda:1000)
            committed = _snapshot(root)
            cold = _cold_status(root)
            checks = dict(
                one_injected_get_completed=len(opener.requests) == 1,
                installed_result_review_required=(result['mode'] ==
                    'PAPER_ACTIVITY_SSE_ACCEPTANCE_ATTEMPT' and
                    result['status'] == 'REVIEW_REQUIRED'),
                injected_source_remains_unattested=(result['source'] ==
                    'INJECTED_UNATTESTED' and
                    result['actual_provider_access'] is False and
                    result['actual_stream_connection'] is False),
                exact_replay_counts_retained=(
                    result['initial_events_reviewed'] == 3 and
                    result['replay_events_reviewed'] == 2 and
                    result['redelivered_events_deduplicated'] == 1 and
                    result['new_events_processed'] == 1),
                checkpoint_and_restart_claims_retained=(
                    result['local_cursor_checkpoint_committed'] is True and
                    result['process_restart_recoverable'] is True and
                    result['complete_review_counts_bound'] is True),
                cold_restart_replays_completed_state=(
                    cold['snapshot_consistent'] is True and
                    cold['storage_snapshot_consistent'] is True and
                    cold['reconnect_budget_completed'] is True and
                    cold['reconnect_attempt_number'] == 1 and
                    cold['cursor_head_sha256'] == result['cursor_head_sha256'] and
                    cold['reconnect_budget_head_sha256'] ==
                        result['reconnect_budget_head_sha256']),
                completed_state_denies_second_attempt=
                    cold['one_attempt_eligible'] is False,
                fresh_process_journals_unchanged=(
                    cold['journals_unchanged'] is True and
                    _snapshot(root) == committed),
                fresh_process_vault_silent=
                    cold['credential_operations'] == 0,
                fresh_process_network_silent=
                    cold['external_connections'] == 0,
                private_input_unchanged=source.read_bytes() == source_before,
                no_order_journal_created=not (root/'orders.sqlite3').exists(),
                no_automatic_retry=result['automatic_retry'] is False and
                    len(opener.requests) == 1)
            return dict(
                source='SYNTHETIC_INSTALLED_ACTIVITY_SSE_ACCEPTANCE_FIXTURE',
                passed=all(checks.values()), **checks,
                actual_provider_access=False,
                actual_stream_connection=False,
                retained_private_evidence_published=False,
                historical_or_synthetic_performance_predicts_profit=False,
                **{flag: False for flag in AUTHORITY_FLAGS})
    finally:
        paper_credentials._vault = original_vault
        paper_credentials._epoch = original_epoch
        paper_credentials._blocked = original_blocked


def failure_fixture_report():
    """Exercise one failed GET and cold-read its durable retry admission."""
    original_vault = paper_credentials._vault
    original_epoch = paper_credentials._epoch
    original_blocked = paper_credentials._blocked
    try:
        with tempfile.TemporaryDirectory(
                prefix='nexus-activity-sse-failure-evidence-') as directory:
            root = Path(directory)
            initial, _ = _segments()
            source = root/'initial.json'
            source.write_text(canonical(initial), encoding='utf-8')
            source_before = source.read_bytes()
            vault = _Vault()
            paper_credentials._vault = lambda: vault
            paper_credentials._epoch = 0
            paper_credentials._blocked = False
            paper_credentials.install(KEY, SECRET, confirm_store=True)
            opener = _FailingOpener()
            order = root/'order_lifecycle.sqlite3'
            fixed_failure = False
            try:
                operator.attempt(expected_account_id=ACCOUNT,
                    initial_events_path=source,
                    cursor_path=root/'cursor.sqlite3',
                    budget_path=root/'budget.sqlite3',
                    order_journal_path=order,
                    expected_cursor_head=ZERO, expected_budget_head=ZERO,
                    reconnect_reason='CONNECTION_DROPPED',
                    confirm_reconnect_attempt=True,
                    confirm_read_only_paper=True,
                    confirm_cursor_checkpoint=True,
                    opener=opener, wall_clock=lambda:1000,
                    elapsed_clock=lambda:50, outcome_clock=lambda:1000)
            except ValueError as error:
                fixed_failure = (str(error) ==
                    'PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED')
            committed = _snapshot(root)
            before_due = _cold_status(root, 1000,
                'order_lifecycle.sqlite3')
            due = _cold_status(root,
                before_due['reconnect_next_allowed_at'],
                'order_lifecycle.sqlite3')
            no_order_before_stop = not order.exists()
            stop = _independent_stop(root)
            stopped_committed = _snapshot(root)
            stopped = _cold_status(root,
                before_due['reconnect_next_allowed_at'],
                'order_lifecycle.sqlite3')
            checks = dict(
                one_injected_failed_get=len(opener.requests) == 1,
                installed_failure_is_fixed_diagnostic=fixed_failure,
                failed_attempt_retained_for_restart=(
                    before_due['snapshot_consistent'] is True and
                    before_due['storage_snapshot_consistent'] is True and
                    before_due['reconnect_attempt_number'] == 1 and
                    before_due['reconnect_budget_completed'] is False and
                    before_due['reconnect_pending'] is False and
                    before_due['reconnect_review_required'] is False and
                    before_due['reconnect_next_allowed_at'] == 1001 and
                    before_due['cursor_head_sha256'] == ZERO),
                before_due_restart_denies_attempt=
                    before_due['one_attempt_eligible'] is False,
                due_restart_requires_explicit_attempt=(
                    due['snapshot_consistent'] is True and
                    due['storage_snapshot_consistent'] is True and
                    due['one_attempt_eligible'] is True and
                    due['reconnect_attempt_number'] == 1 and
                    due['reconnect_budget_head_sha256'] ==
                        before_due['reconnect_budget_head_sha256'] and
                    due['cursor_head_sha256'] == ZERO),
                independent_stop_process_is_local_only=(
                    stop['passed'] is True and stop['halted'] is True and
                    stop['intent_preceded_durable_halt'] is True and
                    stop['emergency_intent_present'] is True and
                    stop['durable_order_halt_confirmed'] is True and
                    stop['desktop_started'] is False and
                    stop['network_requested'] is False and
                    stop['credential_access_requested'] is False and
                    stop['automatic_restart'] is False and
                    stop['external_connections'] == 0 and
                    stop['credential_operations'] == 0),
                stopped_restart_denies_due_attempt=(
                    stopped['snapshot_consistent'] is True and
                    stopped['storage_snapshot_consistent'] is True and
                    stopped['emergency_cut_latch_active'] is True and
                    stopped['emergency_cut_scope_consistent'] is True and
                    stopped['one_attempt_eligible'] is False and
                    stopped['reconnect_attempt_number'] == 1 and
                    stopped['reconnect_budget_head_sha256'] ==
                        before_due['reconnect_budget_head_sha256'] and
                    stopped['cursor_head_sha256'] == ZERO),
                cold_statuses_are_vault_silent=(
                    before_due['credential_operations'] == 0 and
                    due['credential_operations'] == 0 and
                    stopped['credential_operations'] == 0),
                cold_statuses_are_network_silent=(
                    before_due['external_connections'] == 0 and
                    due['external_connections'] == 0 and
                    stopped['external_connections'] == 0),
                failed_journals_remain_unchanged=(
                    before_due['journals_unchanged'] is True and
                    due['journals_unchanged'] is True and
                    stopped['journals_unchanged'] is True and
                    stopped_committed == _snapshot(root) and
                    committed != stopped_committed),
                private_input_unchanged=source.read_bytes() == source_before,
                no_order_journal_created_before_explicit_stop=
                    no_order_before_stop,
                no_automatic_retry_or_continuation=(
                    before_due['automatic_retry'] is False and
                    before_due['automatic_continuation'] is False and
                    due['automatic_retry'] is False and
                    due['automatic_continuation'] is False and
                    stopped['automatic_retry'] is False and
                    stopped['automatic_continuation'] is False and
                    len(opener.requests) == 1))
            return dict(
                source='SYNTHETIC_INSTALLED_ACTIVITY_SSE_FAILURE_FIXTURE',
                passed=all(checks.values()), **checks,
                actual_provider_access=False,
                actual_stream_connection=False,
                retained_private_evidence_published=False,
                historical_or_synthetic_performance_predicts_profit=False,
                **{flag: False for flag in AUTHORITY_FLAGS})
    finally:
        paper_credentials._vault = original_vault
        paper_credentials._epoch = original_epoch
        paper_credentials._blocked = original_blocked
