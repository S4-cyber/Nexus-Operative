"""Synthetic restart evidence for the installed Activity SSE operator.

The fixture uses a temporary saved-pair seam, injected event-stream bytes and
private temporary journals.  It performs no provider request, persists no
credential, event or account evidence, and grants no external authority.
"""
import copy
import hashlib
import io
import json
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
KEY = 'synthetic_fixture_key_123'
SECRET = 'synthetic_fixture_secret_123'
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


def _cold_status(root):
    expression = r'''
import hashlib,json,sys
from pathlib import Path
from nexus import paper_credentials
from nexus.paper_activity_sse_acceptance_probe import status
root=Path(sys.argv[1]);account=sys.argv[2]
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
 budget_path=root/'budget.sqlite3',order_journal_path=root/'orders.sqlite3',
 clock=lambda:1001)
after=snapshot()
print(json.dumps({'snapshot_consistent':value['snapshot_consistent'],
 'storage_snapshot_consistent':value['storage_snapshot_consistent'],
 'one_attempt_eligible':value['one_attempt_eligible'],
 'cursor_head_sha256':value['cursor_head_sha256'],
 'reconnect_budget_head_sha256':value['reconnect_budget_head_sha256'],
 'reconnect_budget_completed':value['reconnect_completed'],
 'reconnect_attempt_number':value['reconnect_attempts'],
 'journals_unchanged':before==after,**attempts},sort_keys=True))
'''
    try:
        child = subprocess.run([sys.executable, '-c', expression, str(root),
            ACCOUNT], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('PAPER_ACTIVITY_SSE_ACCEPTANCE_EVIDENCE_RESTART') from None
    if child.returncode != 0:
        raise ValueError('PAPER_ACTIVITY_SSE_ACCEPTANCE_EVIDENCE_RESTART')
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
