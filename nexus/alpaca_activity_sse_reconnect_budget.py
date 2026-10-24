"""Durable, caller-driven Activity SSE reconnect attempts.

The budget records one explicit attempt before network I/O and enforces a
small fixed backoff schedule across process restarts.  It never sleeps, loops,
retries automatically, stores credentials, or grants order/funds authority.
"""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3

from nexus import alpaca_activity_sse_checkpoint as checkpoint
from nexus.alpaca_activity_sse_cursor_store import ActivitySSECursorStore
from nexus.alpaca_activity_sse_transport import ActivitySSETransportError
from nexus.alpaca_paper_contract import canonical, sha
from nexus.sqlite_journal_guard import check_existing


SCHEMA = 'nexus.private-alpaca-activity-sse-reconnect-budget.v1'
POLICY = 'nexus.explicit-alpaca-activity-sse-reconnect-budget.v1'
ZERO = '0' * 64
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (1, 2, 4, 8)
MAX_RECORD_BYTES = 4096
MAX_DATABASE_BYTES = 1024 * 1024
REASONS = frozenset(('CONNECTION_DROPPED', 'SLOW_CONSUMER',
    'INTERNAL_SERVER_ERROR'))
OUTCOMES = frozenset(('CHECKPOINT_COMMITTED', 'RETRY_REQUIRED',
    'REVIEW_REQUIRED', 'CHECKPOINT_UNCERTAIN'))
RETRYABLE_ERRORS = frozenset((
    'ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
    'ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_FAILED',
    'ALPACA_ACTIVITY_SSE_TRANSPORT_TIME_BUDGET',
    'ALPACA_ACTIVITY_SSE_TRANSPORT_SEGMENT_INCOMPLETE',
    'ALPACA_ACTIVITY_SSE_CHECKPOINT_INTERRUPTED_SEGMENT_INELIGIBLE'))
FLAGS = ('provider_source_attested', 'stream_delivery_verified',
    'historical_replay_complete', 'native_power_loss_durability_verified',
    'fee_coverage_complete', 'broker_account_reconciled',
    'ready_for_routing', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved')


class ActivitySSEReconnectBudgetError(ValueError):
    """Fixed diagnostics only; never echo credentials, cursors, or paths."""


def _fail(code):
    raise ActivitySSEReconnectBudgetError(
        'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_' + code) from None


def _digest(value, code='DIGEST_INVALID'):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        _fail(code)
    return value


def _clock(value):
    if (type(value) not in (int, float) or not math.isfinite(value) or
            not 0 <= value <= 1e12):
        _fail('CLOCK_INVALID')
    return value


class ActivitySSEReconnectBudget:
    """Account-bound hash-chain for one bounded reconnect episode."""

    def __init__(self, path, paper_account_id):
        from nexus.alpaca_activity_normalizer import _uuid
        try:
            account = _uuid(paper_account_id)
        except (ValueError, TypeError, AttributeError):
            _fail('ACCOUNT_INVALID')
        self.path = Path(path)
        self.account_sha256 = sha(account)

    def _check(self):
        check_existing(self.path, MAX_DATABASE_BYTES,
            'ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET_')
        try:
            self.path.with_name(self.path.name + '-journal').lstat()
        except FileNotFoundError:
            pass
        except OSError:
            _fail('FILE_UNAVAILABLE')
        else:
            _fail('ROLLBACK_REVIEW_REQUIRED')

    def _connect(self, write=False):
        self._check()
        new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                new = True
            except FileExistsError:
                pass
            self._check()
        db = sqlite3.connect(self.path.resolve().as_uri() +
            ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=5)
        try:
            if write:
                db.execute('PRAGMA journal_mode=DELETE')
                db.execute('PRAGMA synchronous=FULL')
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._insert(db, 0, dict(schema=SCHEMA,
                        account_sha256=self.account_sha256), ZERO)
            else:
                db.execute('BEGIN')
            return db, new
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _insert(db, seq, body, previous):
        encoded = canonical(body)
        if len(encoded.encode()) > MAX_RECORD_BYTES:
            _fail('RECORD_CAP')
        digest = sha(dict(seq=seq, body=body, previous=previous))
        db.execute('INSERT INTO events VALUES (?,?,?,?)',
            (seq, encoded, previous, digest))
        return digest

    def _load(self, db):
        rows = db.execute('SELECT seq,body,previous,digest FROM events ORDER BY seq LIMIT ?',
            (2 * MAX_ATTEMPTS + 2,)).fetchall()
        if not rows:
            _fail('BINDING_MISSING')
        if len(rows) > 2 * MAX_ATTEMPTS + 1:
            _fail('EVENT_CAP')
        state = dict(head_sha256=ZERO, attempts=0, pending_token=None,
            expected_cursor_head_sha256=None, next_allowed_at=0,
            last_evaluated_at=0, last_outcome=None, review_required=False,
            completed=False, checkpoint_head_sha256=None,
            checkpoint_receipt_sha256=None)
        previous = ZERO
        for index, (seq, encoded, parent, digest) in enumerate(rows):
            if type(encoded) is not str or len(encoded.encode()) > MAX_RECORD_BYTES:
                _fail('RECORD_CAP')
            try:
                body = json.loads(encoded)
            except (ValueError, TypeError):
                _fail('PAYLOAD_INVALID')
            if (seq != index or parent != previous or canonical(body) != encoded or
                    digest != sha(dict(seq=seq, body=body, previous=parent))):
                _fail('CHAIN_DISAGREEMENT')
            if index == 0:
                if body != dict(schema=SCHEMA,
                        account_sha256=self.account_sha256):
                    _fail('ACCOUNT_OR_SCHEMA')
            elif type(body) is dict and body.get('kind') == 'BEGIN' and set(body) == {
                    'kind', 'policy', 'account_sha256', 'attempt',
                    'evaluated_at', 'expected_cursor_head_sha256',
                    'reconnect_reason', 'confirm_reconnect_attempt'}:
                if (state['pending_token'] is not None or state['completed'] or
                        state['review_required']):
                    _fail('BEGIN_STATE_INVALID')
                now = _clock(body['evaluated_at'])
                if (body['policy'] != POLICY or
                        body['account_sha256'] != self.account_sha256 or
                        body['attempt'] != state['attempts'] + 1 or
                        body['attempt'] > MAX_ATTEMPTS or
                        body['reconnect_reason'] not in REASONS or
                        body['confirm_reconnect_attempt'] is not True):
                    _fail('BEGIN_SCOPE_INVALID')
                cursor_head = _digest(body['expected_cursor_head_sha256'],
                    'CURSOR_HEAD_INVALID')
                if (state['expected_cursor_head_sha256'] is not None and
                        cursor_head != state['expected_cursor_head_sha256']):
                    _fail('CURSOR_HEAD_DISAGREEMENT')
                if (now < state['last_evaluated_at'] or
                        now < state['next_allowed_at']):
                    _fail('CLOCK_OR_BACKOFF_DISAGREEMENT')
                state['attempts'] += 1
                state['pending_token'] = digest
                state['expected_cursor_head_sha256'] = cursor_head
                state['last_evaluated_at'] = now
            elif type(body) is dict and body.get('kind') == 'RESULT' and set(body) == {
                    'kind', 'policy', 'token', 'evaluated_at', 'outcome',
                    'detail_code', 'next_allowed_at',
                    'checkpoint_head_sha256', 'checkpoint_receipt_sha256'}:
                now = _clock(body['evaluated_at'])
                if (body['policy'] != POLICY or
                        state['pending_token'] is None or
                        body['token'] != state['pending_token'] or
                        body['outcome'] not in OUTCOMES or
                        now < state['last_evaluated_at'] or
                        type(body['detail_code']) is not str or
                        re.fullmatch(r'[A-Z0-9_]{1,96}', body['detail_code']) is None):
                    _fail('RESULT_SCOPE_INVALID')
                outcome = body['outcome']
                next_at = _clock(body['next_allowed_at'])
                checkpoint_head = body['checkpoint_head_sha256']
                checkpoint_receipt = body['checkpoint_receipt_sha256']
                if outcome == 'CHECKPOINT_COMMITTED':
                    _digest(checkpoint_head, 'CHECKPOINT_HEAD_INVALID')
                    _digest(checkpoint_receipt, 'CHECKPOINT_RECEIPT_INVALID')
                    if (checkpoint_head == state['expected_cursor_head_sha256'] or
                            next_at != 0 or body['detail_code'] != 'SUCCESS'):
                        _fail('RESULT_SCOPE_INVALID')
                    state['completed'] = True
                    state['next_allowed_at'] = 0
                    state['checkpoint_head_sha256'] = checkpoint_head
                    state['checkpoint_receipt_sha256'] = checkpoint_receipt
                else:
                    if checkpoint_head is not None or checkpoint_receipt is not None:
                        _fail('RESULT_SCOPE_INVALID')
                    if outcome == 'RETRY_REQUIRED':
                        if (body['detail_code'] not in RETRYABLE_ERRORS or
                                state['attempts'] >= MAX_ATTEMPTS or
                                next_at != now + BACKOFF_SECONDS[state['attempts'] - 1]):
                            _fail('RESULT_SCOPE_INVALID')
                        state['next_allowed_at'] = next_at
                    else:
                        if next_at != 0:
                            _fail('RESULT_SCOPE_INVALID')
                        state['review_required'] = True
                        state['next_allowed_at'] = 0
                state['pending_token'] = None
                state['last_evaluated_at'] = now
                state['last_outcome'] = outcome
            else:
                _fail('PAYLOAD_INVALID')
            state['head_sha256'] = digest
            previous = digest
        return state

    def inspect(self):
        """Replay the full private budget; a missing path remains absent."""
        self._check()
        if not self.path.exists():
            state = dict(head_sha256=ZERO, attempts=0, pending_token=None,
                expected_cursor_head_sha256=None, next_allowed_at=0,
                last_evaluated_at=0, last_outcome=None,
                review_required=False, completed=False,
                checkpoint_head_sha256=None,
                checkpoint_receipt_sha256=None)
        else:
            with closing(self._connect()[0]) as db:
                state = self._load(db)
        return dict(mode='PRIVATE_ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET',
            status='REVIEW_REQUIRED', account_sha256=self.account_sha256,
            max_attempts=MAX_ATTEMPTS, backoff_seconds=BACKOFF_SECONDS,
            automatic_retry=False, automatic_sleep=False,
            **state, **{flag: False for flag in FLAGS})

    def begin(self, *, evaluated_at, expected_cursor_head,
            reconnect_reason, expected_head, confirm_reconnect_attempt=False):
        """Reserve one explicit attempt. The caller, never this store, runs I/O."""
        now = _clock(evaluated_at)
        cursor_head = _digest(expected_cursor_head, 'CURSOR_HEAD_INVALID')
        expected = _digest(expected_head, 'EXPECTED_HEAD_INVALID')
        if confirm_reconnect_attempt is not True:
            _fail('CONFIRMATION_REQUIRED')
        if reconnect_reason not in REASONS:
            _fail('RECONNECT_REASON_INVALID')
        current = self.inspect()
        if current['head_sha256'] != expected:
            _fail('HEAD_CHANGED')
        if current['pending_token'] is not None:
            _fail('PENDING_REVIEW')
        if current['review_required'] or current['completed']:
            _fail('EPISODE_CLOSED')
        if current['attempts'] >= MAX_ATTEMPTS:
            _fail('ATTEMPT_CAP')
        if now < current['last_evaluated_at']:
            _fail('CLOCK_REGRESSION')
        if now < current['next_allowed_at']:
            _fail('BACKOFF_ACTIVE')
        if (current['expected_cursor_head_sha256'] is not None and
                cursor_head != current['expected_cursor_head_sha256']):
            _fail('CURSOR_HEAD_CHANGED')
        body = dict(kind='BEGIN', policy=POLICY,
            account_sha256=self.account_sha256,
            attempt=current['attempts'] + 1, evaluated_at=now,
            expected_cursor_head_sha256=cursor_head,
            reconnect_reason=reconnect_reason,
            confirm_reconnect_attempt=True)
        db, new = self._connect(write=True)
        with closing(db):
            with db:
                state = self._load(db)
                comparable = ZERO if new and state['attempts'] == 0 else state['head_sha256']
                if comparable != expected:
                    _fail('HEAD_CHANGED')
                if (state['pending_token'] is not None or state['review_required'] or
                        state['completed'] or state['attempts'] >= MAX_ATTEMPTS or
                        now < state['next_allowed_at']):
                    _fail('ADMISSION_CHANGED')
                token = self._insert(db, state['attempts'] + 1 + state['attempts'],
                    body, state['head_sha256'])
                loaded = self._load(db)
                if loaded['pending_token'] != token:
                    _fail('COMMIT_REPLAY_DISAGREEMENT')
        return dict(token=token, attempt=body['attempt'],
            head_sha256=token, expected_cursor_head_sha256=cursor_head)

    def finish(self, token, *, evaluated_at, outcome, detail_code,
            checkpoint_head_sha256=None, checkpoint_receipt_sha256=None):
        """Record the exact outcome of the reserved attempt."""
        now = _clock(evaluated_at)
        current = self.inspect()
        if current['pending_token'] != token:
            _fail('TOKEN_CHANGED')
        if outcome not in OUTCOMES:
            _fail('OUTCOME_INVALID')
        next_at = (now + BACKOFF_SECONDS[current['attempts'] - 1]
            if outcome == 'RETRY_REQUIRED' else 0)
        body = dict(kind='RESULT', policy=POLICY, token=token,
            evaluated_at=now, outcome=outcome, detail_code=detail_code,
            next_allowed_at=next_at,
            checkpoint_head_sha256=checkpoint_head_sha256,
            checkpoint_receipt_sha256=checkpoint_receipt_sha256)
        db, _ = self._connect(write=True)
        with closing(db):
            with db:
                state = self._load(db)
                if state['pending_token'] != token:
                    _fail('TOKEN_CHANGED')
                head = self._insert(db, 2 * state['attempts'], body,
                    state['head_sha256'])
                loaded = self._load(db)
                if loaded['head_sha256'] != head or loaded['last_outcome'] != outcome:
                    _fail('COMMIT_REPLAY_DISAGREEMENT')
        return self.inspect()


def collect_and_checkpoint_due(api_key, api_secret, *, expected_account_id,
        initial_events, reconnect_reason, cursor_store, expected_cursor_head,
        reconnect_budget, expected_budget_head, evaluated_at,
        confirm_reconnect_attempt=False, confirm_read_only_paper=False,
        confirm_cursor_checkpoint=False, opener=None, wall_clock=None,
        elapsed_clock=None, permission_check=None, emergency_stop_check=None,
        credential_check=None):
    """Run one explicitly requested due attempt; never loop or sleep."""
    if type(cursor_store) is not ActivitySSECursorStore:
        _fail('CURSOR_STORE_INVALID')
    if type(reconnect_budget) is not ActivitySSEReconnectBudget:
        _fail('BUDGET_INVALID')
    if confirm_reconnect_attempt is not True:
        _fail('CONFIRMATION_REQUIRED')
    if confirm_read_only_paper is not True:
        _fail('READ_AUTHORIZATION_REQUIRED')
    if confirm_cursor_checkpoint is not True:
        _fail('CHECKPOINT_AUTHORIZATION_REQUIRED')
    if reconnect_budget.account_sha256 != cursor_store.account_sha256:
        _fail('ACCOUNT_DISAGREEMENT')
    before = cursor_store.inspect()
    if before['head_sha256'] != expected_cursor_head:
        _fail('CURSOR_HEAD_CHANGED')
    reservation = reconnect_budget.begin(evaluated_at=evaluated_at,
        expected_cursor_head=expected_cursor_head,
        reconnect_reason=reconnect_reason, expected_head=expected_budget_head,
        confirm_reconnect_attempt=confirm_reconnect_attempt)
    options = dict(expected_account_id=expected_account_id,
        initial_events=initial_events, reconnect_reason=reconnect_reason,
        cursor_store=cursor_store, expected_cursor_head=expected_cursor_head,
        confirm_read_only_paper=confirm_read_only_paper,
        confirm_cursor_checkpoint=confirm_cursor_checkpoint, opener=opener,
        permission_check=permission_check,
        emergency_stop_check=emergency_stop_check,
        credential_check=credential_check)
    if wall_clock is not None:
        options['wall_clock'] = wall_clock
    if elapsed_clock is not None:
        options['elapsed_clock'] = elapsed_clock
    try:
        result = checkpoint.collect_and_checkpoint_once(api_key, api_secret,
            **options)
    except Exception as error:
        after = cursor_store.inspect()
        code = str(error)
        if after['head_sha256'] != expected_cursor_head:
            outcome, detail = 'CHECKPOINT_UNCERTAIN', 'CURSOR_ADVANCED_WITHOUT_BUDGET_ACK'
        elif code in RETRYABLE_ERRORS and reservation['attempt'] < MAX_ATTEMPTS:
            outcome, detail = 'RETRY_REQUIRED', code
        else:
            outcome, detail = 'REVIEW_REQUIRED', 'NONRETRYABLE_FAILURE'
        try:
            reconnect_budget.finish(reservation['token'],
                evaluated_at=evaluated_at, outcome=outcome,
                detail_code=detail)
        except Exception:
            _fail('RESULT_RECORD_UNCERTAIN')
        raise
    try:
        retained = reconnect_budget.finish(reservation['token'],
            evaluated_at=evaluated_at, outcome='CHECKPOINT_COMMITTED',
            detail_code='SUCCESS',
            checkpoint_head_sha256=result['checkpoint_head_sha256'],
            checkpoint_receipt_sha256=result['checkpoint_receipt_sha256'])
    except Exception:
        retained = reconnect_budget.inspect()
        if (not retained['completed'] or
                retained['checkpoint_head_sha256'] != result['checkpoint_head_sha256'] or
                retained['checkpoint_receipt_sha256'] != result['checkpoint_receipt_sha256']):
            _fail('RESULT_RECORD_UNCERTAIN')
    return dict(result, mode='EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_ATTEMPT',
        reconnect_budget_policy=POLICY,
        reconnect_attempt_number=reservation['attempt'],
        reconnect_attempt_cap=MAX_ATTEMPTS,
        bounded_backoff_enforced=True, caller_driven_attempt=True,
        automatic_retry=False, automatic_reconnect_performed=False,
        automatic_backoff_verified=False,
        reconnect_budget_head_sha256=retained['head_sha256'],
        reconnect_budget_completed=True)


def fixture_report():
    """Disposable timeout/backoff/success fixture; injected network only."""
    import copy
    import io
    import tempfile
    from nexus.alpaca_activity_sse_cursor_store import ZERO
    from nexus.alpaca_activity_sse_review import _event

    account = '01234567-89ab-cdef-0123-456789abcdef'
    first_trade = _event('01K00000000000000000000001',
        '11111111-1111-1111-1111-111111111111', 'TRD')
    first_fee = _event('01K00000000000000000000002',
        '22222222-2222-2222-2222-222222222222', 'FEE')
    later_trade = _event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333', 'TRD',
        details={**first_trade['details'],
            'order_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id': 'fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    later_fee = _event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444', 'FEE',
        details={'parent_id': later_trade['ref_id'],
            'system_date': '2026-10-18'},
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    initial = [first_trade, first_fee, later_trade]
    raw = ''.join('data: ' + canonical(row) + '\n\n'
        for row in (copy.deepcopy(later_trade), later_fee)).encode()

    class Response:
        status = 200
        headers = {'Content-Type': 'text/event-stream'}
        def __init__(self, target):
            self.target = target
            self.body = io.BytesIO(raw)
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def geturl(self): return self.target
        def read(self, size): return self.body.read(size)

    class Opener:
        def __init__(self, value):
            self.value = value
            self.requests = []
        def open(self, request, timeout):
            self.requests.append((request, timeout))
            if isinstance(self.value, BaseException):
                raise self.value
            return Response(request.full_url)

    with tempfile.TemporaryDirectory(prefix='nexus-sse-reconnect-budget-') as folder:
        root = Path(folder)
        cursor = ActivitySSECursorStore(root / 'cursor.sqlite3', account)
        budget = ActivitySSEReconnectBudget(root / 'budget.sqlite3', account)
        first = Opener(TimeoutError('fixture timeout'))
        common = dict(expected_account_id=account, initial_events=initial,
            reconnect_reason='CONNECTION_DROPPED', cursor_store=cursor,
            expected_cursor_head=ZERO, reconnect_budget=budget,
            confirm_reconnect_attempt=True, confirm_read_only_paper=True,
            confirm_cursor_checkpoint=True, wall_clock=lambda: 1000,
            elapsed_clock=lambda: 50, permission_check=lambda: True,
            emergency_stop_check=lambda: False,
            credential_check=lambda: True)
        try:
            collect_and_checkpoint_due('fixture_key_only_123',
                'fixture_secret_only_123', expected_budget_head=ZERO,
                evaluated_at=1000, opener=first, **common)
        except ActivitySSETransportError:
            pass
        after_timeout = budget.inspect()
        second = Opener(raw)
        common['wall_clock'] = lambda: 1001
        result = collect_and_checkpoint_due('fixture_key_only_123',
            'fixture_secret_only_123',
            expected_budget_head=after_timeout['head_sha256'],
            evaluated_at=1001, opener=second, **common)
        cold_budget = ActivitySSEReconnectBudget(
            budget.path, account).inspect()
        cold_cursor = ActivitySSECursorStore(cursor.path, account).inspect()
        encoded = json.dumps([result, cold_budget], sort_keys=True)
        checks = dict(first_failure_retained_without_retry=
                after_timeout['last_outcome'] == 'RETRY_REQUIRED' and
                after_timeout['next_allowed_at'] == 1001 and
                len(first.requests) == 1,
            due_second_attempt_explicit_and_single=
                result['reconnect_attempt_number'] == 2 and
                len(second.requests) == 1,
            checkpoint_and_budget_recover_together=
                cold_budget['completed'] and cold_cursor['initialized'] and
                cold_budget['checkpoint_head_sha256'] ==
                    cold_cursor['head_sha256'],
            no_sleep_loop_or_financial_authority=
                not result['automatic_retry'] and
                not result['automatic_reconnect_performed'] and
                all(result[flag] is False for flag in FLAGS),
            no_private_identifier_or_credential_echo=all(token not in encoded
                for token in (account, '01K0000', '11111111',
                    'fixture_key_only_123', 'fixture_secret_only_123')))
    return dict(source='TEMPORARY_INJECTED_ACTIVITY_SSE_RECONNECT_BUDGET_FIXTURE',
        passed=all(checks.values()), **checks,
        actual_storage_retained=False, actual_provider_access=False,
        actual_stream_connection=False, actual_vault_access=False,
        **{flag: False for flag in FLAGS})
