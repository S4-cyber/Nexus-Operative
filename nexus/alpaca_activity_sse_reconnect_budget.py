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
import time

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
MAX_PROVIDER_RETRY_AFTER = 86400
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
    'ALPACA_ACTIVITY_SSE_CHECKPOINT_INTERRUPTED_SEGMENT_INELIGIBLE',
    'ALPACA_ACTIVITY_SSE_TRANSPORT_RATE_LIMITED',
    'ALPACA_ACTIVITY_SSE_TRANSPORT_SERVICE_UNAVAILABLE'))
PROVIDER_DELAY_ERRORS = frozenset((
    'ALPACA_ACTIVITY_SSE_TRANSPORT_RATE_LIMITED',
    'ALPACA_ACTIVITY_SSE_TRANSPORT_SERVICE_UNAVAILABLE'))
FLAGS = ('provider_source_attested', 'stream_delivery_verified',
    'historical_replay_complete', 'native_power_loss_durability_verified',
    'fee_coverage_complete', 'broker_account_reconciled',
    'ready_for_routing', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved')
BUDGET_VIEW_KEYS = frozenset((
    'mode', 'status', 'account_sha256', 'max_attempts', 'backoff_seconds',
    'automatic_retry', 'automatic_sleep', 'head_sha256', 'attempts',
    'pending_token', 'expected_cursor_head_sha256', 'next_allowed_at',
    'last_evaluated_at', 'last_outcome', 'last_detail_code',
    'last_requested_outcome', 'last_requested_detail_code',
    'last_requested_retry_after_seconds', 'last_applied_backoff_seconds',
    'provider_directed_backoff_observed',
    'last_begin_expected_head_sha256', 'last_reconnect_reason',
    'review_required', 'completed', 'checkpoint_head_sha256',
    'checkpoint_receipt_sha256', 'last_attempt_head_sha256',
    'last_result_head_sha256', 'emergency_stop_prior_head_sha256', *FLAGS))
CHECKPOINT_BINDING_FALSE = ('actual_provider_access',
    'actual_vault_access', 'actual_stream_connection', *FLAGS)
CHECKPOINT_ACK_TRUE = ('single_get_attempt', 'single_reconnect_attempt',
    'explicit_read_authorization', 'explicit_checkpoint_authorization',
    'cursor_checkpoint_eligible', 'cursor_written',
    'local_cursor_checkpoint_committed', 'process_restart_recoverable',
    'reconnect_reservation_bound')
CHECKPOINT_ACK_FALSE = ('actual_vault_access', 'automatic_retry',
    'automatic_reconnect_performed', 'automatic_backoff_verified',
    'ledger_applied')
CHECKPOINT_BINDING_KEYS = frozenset((
    'mode', 'status', 'account_sha256', 'head_sha256',
    'expected_head_sha256', 'checkpoint_receipt_sha256', 'cursor_sha256',
    'reconnect_reservation_sha256', 'reconnect_reservation_bound',
    'read_only', *CHECKPOINT_BINDING_FALSE))


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


def _outcome_time(clock, not_before):
    """Sample a sanitized result time after the provider attempt returns."""
    try:
        value = clock()
    except Exception:
        _fail('OUTCOME_CLOCK_UNAVAILABLE')
    try:
        value = _clock(value)
    except ActivitySSEReconnectBudgetError:
        _fail('OUTCOME_CLOCK_INVALID')
    if value < not_before:
        _fail('OUTCOME_CLOCK_REGRESSION')
    return value


def _inspect_after_result_error(reconnect_budget, code):
    """Inspect a possibly committed result without leaking recovery faults."""
    try:
        retained = reconnect_budget.inspect()
    except Exception:
        _fail(code)
    return _validated_budget_view(reconnect_budget, retained, code)


def _cursor_head_for_attempt(cursor_store):
    """Read one attempt-bound cursor head with a fixed uncertainty failure."""
    try:
        retained = cursor_store.inspect()
        head = retained['head_sha256']
    except Exception:
        _fail('CURSOR_STATE_UNCERTAIN')
    return _digest(head, 'CURSOR_STATE_UNCERTAIN')


def _store_bindings_match(cursor_store, reconnect_budget, *,
        cursor_binding, budget_binding):
    """Return whether both mutable store objects still name one exact scope."""
    try:
        return ((cursor_store.path, cursor_store.account_sha256) ==
                cursor_binding and
            (reconnect_budget.path, reconnect_budget.account_sha256) ==
                budget_binding)
    except Exception:
        return False


def _budget_binding_matches(reconnect_budget, budget_binding):
    """Return whether one mutable budget object still names one scope."""
    try:
        return ((reconnect_budget.path, reconnect_budget.account_sha256) ==
            budget_binding)
    except Exception:
        return False


def _validated_budget_view(reconnect_budget, value, code, *,
        result_acknowledgement=False):
    """Validate one complete public replay of the private budget journal."""
    try:
        keys = BUDGET_VIEW_KEYS | ({'duplicate_result'}
            if result_acknowledgement else set())
        if type(value) is not dict or set(value) != keys:
            raise TypeError
        if (value['mode'] !=
                'PRIVATE_ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET' or
                value['status'] != 'REVIEW_REQUIRED' or
                value['account_sha256'] != reconnect_budget.account_sha256 or
                value['max_attempts'] != MAX_ATTEMPTS or
                type(value['backoff_seconds']) is not tuple or
                value['backoff_seconds'] != BACKOFF_SECONDS or
                value['automatic_retry'] is not False or
                value['automatic_sleep'] is not False or
                any(value[flag] is not False for flag in FLAGS)):
            raise ValueError
        _digest(value['head_sha256'])
        for field in ('pending_token', 'expected_cursor_head_sha256',
                'last_begin_expected_head_sha256',
                'checkpoint_head_sha256', 'checkpoint_receipt_sha256',
                'last_attempt_head_sha256', 'last_result_head_sha256',
                'emergency_stop_prior_head_sha256'):
            if value[field] is not None:
                _digest(value[field])
        _clock(value['last_evaluated_at'])
        _clock(value['next_allowed_at'])
        attempts = value['attempts']
        if type(attempts) is not int or not 0 <= attempts <= MAX_ATTEMPTS:
            raise TypeError
        for field in ('review_required', 'completed',
                'provider_directed_backoff_observed'):
            if type(value[field]) is not bool:
                raise TypeError
        for field in ('last_outcome', 'last_requested_outcome'):
            if value[field] is not None and value[field] not in OUTCOMES:
                raise ValueError
        for field in ('last_detail_code', 'last_requested_detail_code'):
            if value[field] is not None and type(value[field]) is not str:
                raise TypeError
        for field in ('last_requested_retry_after_seconds',
                'last_applied_backoff_seconds'):
            delay = value[field]
            if (type(delay) is not int or
                    not 0 <= delay <= MAX_PROVIDER_RETRY_AFTER):
                raise TypeError
        reason = value['last_reconnect_reason']
        if reason is not None and reason not in REASONS:
            raise ValueError
        if (result_acknowledgement and
                type(value['duplicate_result']) is not bool):
            raise TypeError
    except Exception:
        _fail(code)
    return value


def _budget_state_for_attempt(reconnect_budget):
    """Read and totally validate admission state without exposing faults."""
    try:
        return _validated_budget_view(reconnect_budget,
            reconnect_budget.inspect(), 'ADMISSION_STATE_UNCERTAIN')
    except ActivitySSEReconnectBudgetError:
        raise
    except Exception:
        _fail('ADMISSION_STATE_UNCERTAIN')


def _validated_checkpoint_binding(cursor_store, value, code):
    """Validate the total public shape of one private cursor binding.

    Callers use the returned exact built-in dictionary for stability
    comparison.  Subclasses, extra private fields, promoted authority and
    custom equality therefore cannot stand in for a replayed journal view.
    """
    try:
        if type(value) is not dict or set(value) != CHECKPOINT_BINDING_KEYS:
            raise TypeError
        _digest(value['head_sha256'])
        _digest(value['expected_head_sha256'])
        _digest(value['checkpoint_receipt_sha256'])
        _digest(value['cursor_sha256'])
        bound = value['reconnect_reservation_bound']
        if type(bound) is not bool:
            raise TypeError
        reservation = value['reconnect_reservation_sha256']
        if bound:
            _digest(reservation)
        elif reservation is not None:
            raise ValueError
        if (value['mode'] !=
                    'PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_BINDING' or
                value['status'] != 'REVIEW_REQUIRED' or
                value['account_sha256'] != cursor_store.account_sha256 or
                value['read_only'] is not True or
                any(value[flag] is not False
                    for flag in CHECKPOINT_BINDING_FALSE)):
            raise ValueError
    except Exception:
        _fail(code)
    return value


def _exact_pending_reservation(state, *, token, attempt, cursor_head,
        expected_budget_head, evaluated_at, reconnect_reason):
    """Prove one request still owns the exact durable pending BEGIN."""
    admission_time = _clock(evaluated_at)
    prior_result_is_due = (
        (attempt == 1 and state['last_outcome'] is None and
            state['next_allowed_at'] == 0) or
        (attempt > 1 and state['last_outcome'] == 'RETRY_REQUIRED' and
            0 < state['next_allowed_at'] <= admission_time))
    return (state['pending_token'] == token and
        state['head_sha256'] == token and
        state['attempts'] == attempt and
        state['expected_cursor_head_sha256'] == cursor_head and
        state['last_begin_expected_head_sha256'] == expected_budget_head and
        state['last_evaluated_at'] == admission_time and
        state['last_reconnect_reason'] == reconnect_reason and
        prior_result_is_due and not state['review_required'] and
        not state['completed'])


def _checkpoint_binding_for_attempt(cursor_store, result, *, token,
        expected_cursor_head):
    """Prove a checkpoint acknowledgement against its durable journal.

    The checkpoint function is an internal boundary, but its return value is
    still not durable evidence.  Only the latest replayed cursor binding may
    authorize closing the reconnect budget.
    """
    try:
        if type(result) is not dict:
            raise TypeError
        acknowledged_head = _digest(result['checkpoint_head_sha256'])
        acknowledged_receipt = _digest(
            result['checkpoint_receipt_sha256'])
        acknowledged_cursor = _digest(result['checkpoint_cursor_sha256'])
        if (result['mode'] != 'EXPLICIT_ALPACA_ACTIVITY_SSE_CHECKPOINT' or
                result['status'] != 'REVIEW_REQUIRED' or
                result['account_sha256'] != cursor_store.account_sha256 or
                any(result[field] is not True
                    for field in CHECKPOINT_ACK_TRUE) or
                any(result[field] is not False
                    for field in CHECKPOINT_ACK_FALSE + FLAGS) or
                type(result['duplicate_checkpoint']) is not bool or
                type(result['actual_provider_access']) is not bool or
                type(result['actual_stream_connection']) is not bool or
                type(result['actual_reconnect_performed']) is not bool or
                result['actual_provider_access'] is not
                    result['actual_stream_connection'] or
                result['actual_reconnect_performed'] is not
                    result['actual_stream_connection'] or
                result['source'] != ('PAPER_HTTPS_READS'
                    if result['actual_provider_access'] else
                    'INJECTED_UNATTESTED')):
            raise ValueError
        retained = _validated_checkpoint_binding(cursor_store,
            cursor_store.latest_checkpoint_binding(),
            'CHECKPOINT_STATE_UNCERTAIN')
        retained_head = _digest(retained['head_sha256'])
        retained_expected = _digest(retained['expected_head_sha256'])
        retained_receipt = _digest(retained['checkpoint_receipt_sha256'])
        retained_cursor = _digest(retained['cursor_sha256'])
        retained_reservation = _digest(
            retained['reconnect_reservation_sha256'])
        if (retained['mode'] !=
                'PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_BINDING' or
                retained['status'] != 'REVIEW_REQUIRED' or
                retained['account_sha256'] != cursor_store.account_sha256 or
                retained['reconnect_reservation_bound'] is not True or
                retained['read_only'] is not True or
                retained_head != acknowledged_head or
                retained_expected != expected_cursor_head or
                retained_receipt != acknowledged_receipt or
                retained_cursor != acknowledged_cursor or
                retained_reservation != sha(token)):
            raise ValueError
    except Exception:
        _fail('CHECKPOINT_STATE_UNCERTAIN')
    return retained


def _close_failed_clock_uncertainty(reconnect_budget, token, evaluated_at):
    """Retain terminal review state from the last trusted admission time.

    This helper is used only after a failed attempt whose cursor did not move.
    It cannot schedule a retry.  Inspecting after an exception recovers a
    committed result whose acknowledgement was lost.
    """
    try:
        retained = reconnect_budget.finish(token,
            evaluated_at=evaluated_at, outcome='REVIEW_REQUIRED',
            detail_code='OUTCOME_CLOCK_UNCERTAIN')
    except Exception:
        retained = _inspect_after_result_error(reconnect_budget,
            'RESULT_RECORD_UNCERTAIN')
    else:
        retained = _validated_budget_view(reconnect_budget, retained,
            'RESULT_RECORD_UNCERTAIN', result_acknowledgement=True)
    if not _exact_failure_result(retained, token, evaluated_at,
            'REVIEW_REQUIRED', 'OUTCOME_CLOCK_UNCERTAIN', 0):
        _fail('RESULT_RECORD_UNCERTAIN')
    return retained


def _exact_checkpoint_result(retained, token, checkpoint_head,
        checkpoint_receipt, evaluated_at=None):
    """Recognize only the exact terminal checkpoint result.

    This is intentionally stricter than checking the two checkpoint hashes:
    acknowledgement recovery must also remain bound to the reservation token,
    requested/final outcome, current result head and (when newly written) the
    supplied result time.
    """
    try:
        return (retained['completed'] and
            not retained['review_required'] and
            retained['pending_token'] is None and
            retained['head_sha256'] == retained['last_result_head_sha256'] and
            retained['last_attempt_head_sha256'] == token and
            (evaluated_at is None or
                retained['last_evaluated_at'] == evaluated_at) and
            retained['last_outcome'] == 'CHECKPOINT_COMMITTED' and
            retained['last_detail_code'] == 'SUCCESS' and
            retained['last_requested_outcome'] == 'CHECKPOINT_COMMITTED' and
            retained['last_requested_detail_code'] == 'SUCCESS' and
            retained['last_requested_retry_after_seconds'] == 0 and
            retained['last_applied_backoff_seconds'] == 0 and
            retained['next_allowed_at'] == 0 and
            retained['checkpoint_head_sha256'] == checkpoint_head and
            retained['checkpoint_receipt_sha256'] == checkpoint_receipt)
    except (AttributeError, IndexError, KeyError, TypeError):
        return False


def _same_result_head(left, right):
    """Compare recovery views without exposing malformed private state."""
    try:
        return left['head_sha256'] == right['head_sha256']
    except (AttributeError, IndexError, KeyError, TypeError):
        return False


def _exact_failure_result(retained, token, evaluated_at,
        requested_outcome, requested_detail, requested_delay):
    """Recognize one exact non-success result after acknowledgement loss."""
    try:
        common = (not retained['completed'] and
            retained['pending_token'] is None and
            retained['head_sha256'] == retained['last_result_head_sha256'] and
            retained['last_attempt_head_sha256'] == token and
            retained['last_evaluated_at'] == evaluated_at and
            retained['last_requested_outcome'] == requested_outcome and
            retained['last_requested_detail_code'] == requested_detail and
            retained['last_requested_retry_after_seconds'] ==
                requested_delay and
            retained['checkpoint_head_sha256'] is None and
            retained['checkpoint_receipt_sha256'] is None and
            retained['provider_directed_backoff_observed'] is
                (requested_detail in PROVIDER_DELAY_ERRORS and
                    requested_delay > 0))
        if not common:
            return False
        if requested_outcome == 'RETRY_REQUIRED':
            converted = (retained['review_required'] and
                retained['last_outcome'] == 'REVIEW_REQUIRED' and
                retained['last_detail_code'] == 'EMERGENCY_STOP_ACTIVE' and
                retained['last_applied_backoff_seconds'] == 0 and
                retained['next_allowed_at'] == 0)
            applied = max(BACKOFF_SECONDS[retained['attempts'] - 1],
                requested_delay)
            scheduled = (not retained['review_required'] and
                retained['last_outcome'] == requested_outcome and
                retained['last_detail_code'] == requested_detail and
                retained['last_applied_backoff_seconds'] == applied and
                retained['next_allowed_at'] == evaluated_at + applied)
            return converted or scheduled
        return (retained['review_required'] and
            retained['last_outcome'] == requested_outcome and
            retained['last_detail_code'] == requested_detail and
            retained['last_applied_backoff_seconds'] == 0 and
            retained['next_allowed_at'] == 0)
    except (AttributeError, IndexError, KeyError, TypeError):
        return False


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
            last_evaluated_at=0, last_outcome=None, last_detail_code=None,
            last_requested_outcome=None, last_requested_detail_code=None,
            last_requested_retry_after_seconds=0,
            last_applied_backoff_seconds=0,
            provider_directed_backoff_observed=False,
            last_begin_expected_head_sha256=None,
            last_reconnect_reason=None,
            review_required=False,
            completed=False, checkpoint_head_sha256=None,
            checkpoint_receipt_sha256=None,
            last_attempt_head_sha256=None, last_result_head_sha256=None,
            emergency_stop_prior_head_sha256=None)
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
            elif (type(body) is dict and body.get('kind') == 'BEGIN' and
                    set(body) in ({'kind', 'policy', 'account_sha256',
                        'attempt', 'evaluated_at',
                        'expected_cursor_head_sha256', 'reconnect_reason',
                        'confirm_reconnect_attempt'},
                    {'kind', 'policy', 'account_sha256', 'attempt',
                        'evaluated_at', 'expected_cursor_head_sha256',
                        'expected_budget_head_sha256', 'reconnect_reason',
                        'confirm_reconnect_attempt'})):
                if (state['pending_token'] is not None or state['completed'] or
                        state['review_required']):
                    _fail('BEGIN_STATE_INVALID')
                now = _clock(body['evaluated_at'])
                requested_head = body.get('expected_budget_head_sha256')
                if (body['policy'] != POLICY or
                        body['account_sha256'] != self.account_sha256 or
                        body['attempt'] != state['attempts'] + 1 or
                        body['attempt'] > MAX_ATTEMPTS or
                        body['reconnect_reason'] not in REASONS or
                        body['confirm_reconnect_attempt'] is not True):
                    _fail('BEGIN_SCOPE_INVALID')
                if requested_head is not None:
                    requested_head = _digest(requested_head,
                        'EXPECTED_HEAD_INVALID')
                    if (requested_head != state['head_sha256'] and not
                            (state['attempts'] == 0 and
                                requested_head == ZERO)):
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
                state['last_begin_expected_head_sha256'] = requested_head
                state['last_reconnect_reason'] = body['reconnect_reason']
            elif (type(body) is dict and body.get('kind') == 'RESULT' and
                    set(body) in ({'kind', 'policy', 'token',
                        'evaluated_at', 'outcome', 'detail_code',
                        'next_allowed_at', 'checkpoint_head_sha256',
                        'checkpoint_receipt_sha256'},
                    {'kind', 'policy', 'token', 'evaluated_at', 'outcome',
                        'detail_code', 'requested_outcome',
                        'requested_detail_code', 'next_allowed_at',
                        'checkpoint_head_sha256',
                        'checkpoint_receipt_sha256'},
                    {'kind', 'policy', 'token', 'evaluated_at', 'outcome',
                        'detail_code', 'requested_outcome',
                        'requested_detail_code',
                        'requested_retry_after_seconds',
                        'applied_backoff_seconds', 'next_allowed_at',
                        'checkpoint_head_sha256',
                        'checkpoint_receipt_sha256'})):
                now = _clock(body['evaluated_at'])
                requested_outcome = body.get('requested_outcome',
                    body['outcome'])
                requested_detail = body.get('requested_detail_code',
                    body['detail_code'])
                requested_delay = body.get('requested_retry_after_seconds', 0)
                applied_backoff = body.get('applied_backoff_seconds', 0)
                if (body['policy'] != POLICY or
                        state['pending_token'] is None or
                        body['token'] != state['pending_token'] or
                        body['outcome'] not in OUTCOMES or
                        requested_outcome not in OUTCOMES or
                        now < state['last_evaluated_at'] or
                        type(body['detail_code']) is not str or
                        re.fullmatch(r'[A-Z0-9_]{1,96}', body['detail_code']) is None or
                        type(requested_detail) is not str or
                        re.fullmatch(r'[A-Z0-9_]{1,96}', requested_detail) is None or
                        type(requested_delay) is not int or
                        not 0 <= requested_delay <= MAX_PROVIDER_RETRY_AFTER or
                        type(applied_backoff) is not int or
                        not 0 <= applied_backoff <= MAX_PROVIDER_RETRY_AFTER):
                    _fail('RESULT_SCOPE_INVALID')
                outcome = body['outcome']
                converted_stop_result = (
                    requested_outcome == 'RETRY_REQUIRED' and
                    requested_detail in RETRYABLE_ERRORS and
                    outcome == 'REVIEW_REQUIRED' and
                    body['detail_code'] == 'EMERGENCY_STOP_ACTIVE')
                if (not converted_stop_result and
                        (requested_outcome != outcome or
                            requested_detail != body['detail_code'])):
                    _fail('RESULT_SCOPE_INVALID')
                state['last_attempt_head_sha256'] = state['pending_token']
                state['last_result_head_sha256'] = digest
                next_at = _clock(body['next_allowed_at'])
                checkpoint_head = body['checkpoint_head_sha256']
                checkpoint_receipt = body['checkpoint_receipt_sha256']
                if outcome == 'CHECKPOINT_COMMITTED':
                    _digest(checkpoint_head, 'CHECKPOINT_HEAD_INVALID')
                    _digest(checkpoint_receipt, 'CHECKPOINT_RECEIPT_INVALID')
                    if (checkpoint_head == state['expected_cursor_head_sha256'] or
                            next_at != 0 or body['detail_code'] != 'SUCCESS' or
                            requested_delay != 0 or applied_backoff != 0):
                        _fail('RESULT_SCOPE_INVALID')
                    state['completed'] = True
                    state['next_allowed_at'] = 0
                    state['checkpoint_head_sha256'] = checkpoint_head
                    state['checkpoint_receipt_sha256'] = checkpoint_receipt
                else:
                    if checkpoint_head is not None or checkpoint_receipt is not None:
                        _fail('RESULT_SCOPE_INVALID')
                    if outcome == 'RETRY_REQUIRED':
                        provider_delay = (requested_delay if
                            body['detail_code'] in PROVIDER_DELAY_ERRORS
                            else 0)
                        if ((body['detail_code'] in PROVIDER_DELAY_ERRORS) !=
                                (requested_delay > 0)):
                            _fail('RESULT_SCOPE_INVALID')
                        expected_backoff = max(
                            BACKOFF_SECONDS[state['attempts'] - 1],
                            provider_delay)
                        # Earlier RESULT shapes had only the fixed schedule.
                        if 'applied_backoff_seconds' not in body:
                            applied_backoff = expected_backoff
                        if (body['detail_code'] not in RETRYABLE_ERRORS or
                                state['attempts'] >= MAX_ATTEMPTS or
                                applied_backoff != expected_backoff or
                                next_at != now + expected_backoff):
                            _fail('RESULT_SCOPE_INVALID')
                        state['next_allowed_at'] = next_at
                    else:
                        if (next_at != 0 or applied_backoff != 0 or
                                (requested_delay > 0 and not
                                    converted_stop_result)):
                            _fail('RESULT_SCOPE_INVALID')
                        state['review_required'] = True
                        state['next_allowed_at'] = 0
                state['pending_token'] = None
                state['last_evaluated_at'] = now
                state['last_outcome'] = outcome
                state['last_detail_code'] = body['detail_code']
                state['last_requested_outcome'] = requested_outcome
                state['last_requested_detail_code'] = requested_detail
                state['last_requested_retry_after_seconds'] = requested_delay
                state['last_applied_backoff_seconds'] = applied_backoff
                state['provider_directed_backoff_observed'] = (
                    requested_detail in PROVIDER_DELAY_ERRORS and
                    requested_delay > 0)
            elif (type(body) is dict and
                    body.get('kind') == 'EMERGENCY_STOP_CLOSE' and
                    set(body) == {'kind', 'policy', 'account_sha256',
                        'evaluated_at', 'confirm_emergency_stop_close'}):
                now = _clock(body['evaluated_at'])
                if (body['policy'] != POLICY or
                        body['account_sha256'] != self.account_sha256 or
                        body['confirm_emergency_stop_close'] is not True or
                        state['pending_token'] is not None or
                        state['completed'] or state['review_required'] or
                        state['attempts'] <= 0 or
                        state['last_outcome'] != 'RETRY_REQUIRED' or
                        state['next_allowed_at'] <= 0 or
                        now < state['last_evaluated_at']):
                    _fail('EMERGENCY_STOP_CLOSE_SCOPE_INVALID')
                state['emergency_stop_prior_head_sha256'] = \
                    state['head_sha256']
                state['review_required'] = True
                state['next_allowed_at'] = 0
                state['last_evaluated_at'] = now
                state['last_outcome'] = 'REVIEW_REQUIRED'
                state['last_detail_code'] = 'EMERGENCY_STOP_ACTIVE'
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
                last_detail_code=None, last_requested_outcome=None,
                last_requested_detail_code=None,
                last_requested_retry_after_seconds=0,
                last_applied_backoff_seconds=0,
                provider_directed_backoff_observed=False,
                last_begin_expected_head_sha256=None,
                last_reconnect_reason=None,
                review_required=False, completed=False,
                checkpoint_head_sha256=None,
                checkpoint_receipt_sha256=None,
                last_attempt_head_sha256=None,
                last_result_head_sha256=None,
                emergency_stop_prior_head_sha256=None)
        else:
            with closing(self._connect()[0]) as db:
                state = self._load(db)
        return dict(mode='PRIVATE_ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET',
            status='REVIEW_REQUIRED', account_sha256=self.account_sha256,
            max_attempts=MAX_ATTEMPTS, backoff_seconds=BACKOFF_SECONDS,
            automatic_retry=False, automatic_sleep=False,
            **state, **{flag: False for flag in FLAGS})

    def begin(self, *, evaluated_at, expected_cursor_head,
            reconnect_reason, expected_head, emergency_stop_check=None,
            confirm_reconnect_attempt=False):
        """Reserve one explicit attempt. The caller, never this store, runs I/O."""
        now = _clock(evaluated_at)
        cursor_head = _digest(expected_cursor_head, 'CURSOR_HEAD_INVALID')
        expected = _digest(expected_head, 'EXPECTED_HEAD_INVALID')
        budget_binding = (self.path, self.account_sha256)

        def require_budget_binding():
            if not _budget_binding_matches(self, budget_binding):
                _fail('STORE_BINDING_CHANGED')

        if confirm_reconnect_attempt is not True:
            _fail('CONFIRMATION_REQUIRED')
        if reconnect_reason not in REASONS:
            _fail('RECONNECT_REASON_INVALID')
        if not callable(emergency_stop_check):
            _fail('EMERGENCY_STOP_CHECK_INVALID')

        def stop_clear():
            try:
                return emergency_stop_check() is False
            except Exception:
                return False

        clear = stop_clear()
        require_budget_binding()
        if not clear:
            _fail('EMERGENCY_STOP_ACTIVE')
        current = _budget_state_for_attempt(self)
        require_budget_binding()
        if current['head_sha256'] != expected:
            exact_reservation = (
                current['pending_token'] is not None and
                current['head_sha256'] == current['pending_token'] and
                current['last_begin_expected_head_sha256'] == expected)
            if not exact_reservation:
                _fail('HEAD_CHANGED')
            if (current['last_evaluated_at'] != now or
                    current['expected_cursor_head_sha256'] != cursor_head or
                    current['last_reconnect_reason'] != reconnect_reason):
                _fail('BEGIN_REPLAY_DISAGREEMENT')
            db, _ = self._connect(write=True)
            with closing(db):
                with db:
                    state = self._load(db)
                    if (state['pending_token'] is None or
                            state['head_sha256'] != state['pending_token'] or
                            state['last_begin_expected_head_sha256'] !=
                                expected):
                        _fail('HEAD_CHANGED')
                    if (state['last_evaluated_at'] != now or
                            state['expected_cursor_head_sha256'] !=
                                cursor_head or
                            state['last_reconnect_reason'] !=
                                reconnect_reason):
                        _fail('BEGIN_REPLAY_DISAGREEMENT')
                    clear = stop_clear()
                    require_budget_binding()
                    if not clear:
                        _fail('EMERGENCY_STOP_ACTIVE')
                    token = state['pending_token']
                    attempt = state['attempts']
            require_budget_binding()
            return dict(token=token, attempt=attempt,
                head_sha256=token,
                expected_cursor_head_sha256=cursor_head,
                duplicate_reservation=True,
                provider_request_authorized=False)
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
            expected_budget_head_sha256=expected,
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
                db.execute('SAVEPOINT reconnect_begin_scope')

                def require_transaction_binding():
                    if _budget_binding_matches(self, budget_binding):
                        return
                    db.execute('ROLLBACK TO reconnect_begin_scope')
                    db.execute('RELEASE reconnect_begin_scope')
                    if new:
                        # Preserve only the account-bound schema for a fresh
                        # path.  Never leave an empty SQLite file that cannot
                        # be replayed after rejecting a retargeted callback.
                        db.commit()
                    _fail('STORE_BINDING_CHANGED')

                # Re-sample under the exact-head write transaction.  A stop
                # raised between review and reservation cannot consume an
                # attempt or create a pending episode.
                clear = stop_clear()
                require_transaction_binding()
                if not clear:
                    # On the first use, retain only the account-bound schema
                    # so the newly created file remains replayable.  No BEGIN
                    # attempt is appended and every existing journal remains
                    # byte-for-byte unchanged.
                    if new:
                        db.commit()
                    _fail('EMERGENCY_STOP_ACTIVE')
                token = self._insert(db, state['attempts'] + 1 + state['attempts'],
                    body, state['head_sha256'])
                require_transaction_binding()
                loaded = self._load(db)
                if loaded['pending_token'] != token:
                    _fail('COMMIT_REPLAY_DISAGREEMENT')
                db.execute('RELEASE reconnect_begin_scope')
        require_budget_binding()
        return dict(token=token, attempt=body['attempt'],
            head_sha256=token, expected_cursor_head_sha256=cursor_head,
            duplicate_reservation=False,
            provider_request_authorized=True)

    def finish(self, token, *, evaluated_at, outcome, detail_code,
            checkpoint_head_sha256=None, checkpoint_receipt_sha256=None,
            emergency_stop_check=None, provider_retry_after_seconds=0):
        """Record the exact outcome of the reserved attempt."""
        now = _clock(evaluated_at)
        token = _digest(token, 'TOKEN_INVALID')
        budget_binding = (self.path, self.account_sha256)

        def require_budget_binding():
            if not _budget_binding_matches(self, budget_binding):
                _fail('STORE_BINDING_CHANGED')

        if outcome not in OUTCOMES:
            _fail('OUTCOME_INVALID')
        if (type(detail_code) is not str or
                re.fullmatch(r'[A-Z0-9_]{1,96}', detail_code) is None):
            _fail('DETAIL_CODE_INVALID')
        if outcome == 'RETRY_REQUIRED' and detail_code not in RETRYABLE_ERRORS:
            _fail('RETRY_DETAIL_INVALID')
        if (type(provider_retry_after_seconds) is not int or
                not 0 <= provider_retry_after_seconds <=
                    MAX_PROVIDER_RETRY_AFTER or
                ((detail_code in PROVIDER_DELAY_ERRORS) !=
                    (provider_retry_after_seconds > 0)) or
                (provider_retry_after_seconds > 0 and
                    outcome != 'RETRY_REQUIRED')):
            _fail('PROVIDER_RETRY_AFTER_INVALID')
        retry_clear_before_commit = None
        if outcome == 'RETRY_REQUIRED':
            if not callable(emergency_stop_check):
                _fail('EMERGENCY_STOP_CHECK_INVALID')

            def stop_clear():
                try:
                    return emergency_stop_check() is False
                except Exception:
                    return False

            retry_clear_before_commit = stop_clear()
            require_budget_binding()
        current = self.inspect()
        require_budget_binding()
        if current['pending_token'] != token:
            exact_result = (
                current['pending_token'] is None and
                current['head_sha256'] == current['last_result_head_sha256'] and
                current['last_attempt_head_sha256'] == token)
            if exact_result:
                if (current['last_evaluated_at'] != now or
                        current['last_requested_outcome'] != outcome or
                        current['last_requested_detail_code'] != detail_code or
                        current['last_requested_retry_after_seconds'] !=
                            provider_retry_after_seconds or
                        current['checkpoint_head_sha256'] !=
                            checkpoint_head_sha256 or
                        current['checkpoint_receipt_sha256'] !=
                            checkpoint_receipt_sha256):
                    _fail('RESULT_REPLAY_DISAGREEMENT')
                return dict(current, duplicate_result=True)
            _fail('TOKEN_CHANGED')
        db, _ = self._connect(write=True)
        with closing(db):
            with db:
                state = self._load(db)
                if state['pending_token'] != token:
                    _fail('TOKEN_CHANGED')
                retained_outcome = outcome
                retained_detail = detail_code
                # A retry can be scheduled only while the independent stop is
                # exactly clear both before review and under the exact-token
                # write transaction.  Active, failing, or indeterminate checks
                # close the episode for review without a due reconnect.
                retry_clear_during_commit = retry_clear_before_commit
                if (outcome == 'RETRY_REQUIRED' and
                        retry_clear_before_commit):
                    retry_clear_during_commit = stop_clear()
                    require_budget_binding()
                if (outcome == 'RETRY_REQUIRED' and
                        (not retry_clear_before_commit or
                            not retry_clear_during_commit)):
                    retained_outcome = 'REVIEW_REQUIRED'
                    retained_detail = 'EMERGENCY_STOP_ACTIVE'
                next_at = (now + BACKOFF_SECONDS[state['attempts'] - 1]
                    if retained_outcome == 'RETRY_REQUIRED' else 0)
                applied_backoff = 0
                if retained_outcome == 'RETRY_REQUIRED':
                    applied_backoff = max(
                        BACKOFF_SECONDS[state['attempts'] - 1],
                        provider_retry_after_seconds)
                    next_at = now + applied_backoff
                body = dict(kind='RESULT', policy=POLICY, token=token,
                    evaluated_at=now, outcome=retained_outcome,
                    detail_code=retained_detail,
                    requested_outcome=outcome,
                    requested_detail_code=detail_code,
                    requested_retry_after_seconds=
                        provider_retry_after_seconds,
                    applied_backoff_seconds=applied_backoff,
                    next_allowed_at=next_at,
                    checkpoint_head_sha256=checkpoint_head_sha256,
                    checkpoint_receipt_sha256=checkpoint_receipt_sha256)
                head = self._insert(db, 2 * state['attempts'], body,
                    state['head_sha256'])
                require_budget_binding()
                loaded = self._load(db)
                if (loaded['head_sha256'] != head or
                        loaded['last_outcome'] != retained_outcome):
                    _fail('COMMIT_REPLAY_DISAGREEMENT')
        require_budget_binding()
        retained = self.inspect()
        require_budget_binding()
        return dict(retained, duplicate_result=False)

    def close_pending_for_outcome_uncertainty(self, *, evaluated_at,
            expected_head, confirm_outcome_uncertain=False,
            confirm_no_retry=False):
        """Permanently close one pending attempt after clock uncertainty.

        This operator-reviewed path receives no credentials, cursor store,
        provider opener, or authority callback. It records only a terminal
        review-required result and cannot schedule or reserve another attempt.
        Exact replay supports a lost acknowledgement without another write.
        """
        now = _clock(evaluated_at)
        expected = _digest(expected_head, 'EXPECTED_HEAD_INVALID')
        budget_binding = (self.path, self.account_sha256)

        def require_budget_binding():
            if not _budget_binding_matches(self, budget_binding):
                _fail('STORE_BINDING_CHANGED')

        if confirm_outcome_uncertain is not True:
            _fail('OUTCOME_UNCERTAIN_CONFIRMATION_REQUIRED')
        if confirm_no_retry is not True:
            _fail('OUTCOME_UNCERTAIN_NO_RETRY_CONFIRMATION_REQUIRED')
        current = self.inspect()
        require_budget_binding()
        pending = (current['pending_token'] == expected and
            current['head_sha256'] == expected and
            not current['review_required'] and not current['completed'])
        duplicate = (current['pending_token'] is None and
            current['head_sha256'] == current['last_result_head_sha256'] and
            current['last_attempt_head_sha256'] == expected and
            current['review_required'] and not current['completed'] and
            current['last_outcome'] == 'REVIEW_REQUIRED' and
            current['last_detail_code'] == 'OUTCOME_CLOCK_UNCERTAIN')
        if not pending and not duplicate:
            _fail('HEAD_CHANGED')
        if now < current['last_evaluated_at']:
            _fail('CLOCK_REGRESSION')
        retained = self.finish(expected, evaluated_at=now,
            outcome='REVIEW_REQUIRED',
            detail_code='OUTCOME_CLOCK_UNCERTAIN')
        require_budget_binding()
        if (not retained['review_required'] or retained['completed'] or
                retained['pending_token'] is not None or
                retained['last_attempt_head_sha256'] != expected or
                retained['last_outcome'] != 'REVIEW_REQUIRED' or
                retained['last_detail_code'] !=
                    'OUTCOME_CLOCK_UNCERTAIN' or
                retained['next_allowed_at'] != 0):
            _fail('OUTCOME_UNCERTAIN_CLOSE_UNCERTAIN')
        return retained, retained['duplicate_result']

    def abort_pending_for_emergency_stop(self, *, evaluated_at,
            expected_head, emergency_stop_check,
            confirm_emergency_stop_abort=False):
        """Durably retire one pending attempt while the stop is active.

        This local-only path never receives credentials or cursor evidence and
        cannot contact the provider. Exact replay supports recovery when the
        abort result committed but its acknowledgement was lost.
        """
        now = _clock(evaluated_at)
        expected = _digest(expected_head, 'EXPECTED_HEAD_INVALID')
        budget_binding = (self.path, self.account_sha256)

        def require_budget_binding():
            if not _budget_binding_matches(self, budget_binding):
                _fail('STORE_BINDING_CHANGED')

        if confirm_emergency_stop_abort is not True:
            _fail('EMERGENCY_STOP_ABORT_CONFIRMATION_REQUIRED')
        if not callable(emergency_stop_check):
            _fail('EMERGENCY_STOP_CHECK_INVALID')

        def stop_active():
            try:
                return emergency_stop_check() is True
            except Exception:
                return False

        active = stop_active()
        require_budget_binding()
        if not active:
            _fail('EMERGENCY_STOP_NOT_ACTIVE')
        current = self.inspect()
        require_budget_binding()
        duplicate = current['head_sha256'] != expected
        if duplicate:
            if (current['pending_token'] is not None or
                    not current['review_required'] or current['completed'] or
                    current['last_attempt_head_sha256'] != expected or
                    current['last_outcome'] != 'REVIEW_REQUIRED' or
                    current['last_detail_code'] != 'EMERGENCY_STOP_ACTIVE'):
                _fail('HEAD_CHANGED')
            return current, True
        if (current['pending_token'] is None or current['review_required'] or
                current['completed']):
            _fail('PENDING_RECONNECT_MISSING')
        if now < current['last_evaluated_at']:
            _fail('CLOCK_REGRESSION')
        # Re-sample immediately before the exact-token transactional result.
        # A lifted, indeterminate, or failing stop check cannot mutate state.
        active = stop_active()
        require_budget_binding()
        if not active:
            _fail('EMERGENCY_STOP_NOT_ACTIVE')
        retained = self.finish(current['pending_token'], evaluated_at=now,
            outcome='REVIEW_REQUIRED', detail_code='EMERGENCY_STOP_ACTIVE')
        require_budget_binding()
        if (not retained['review_required'] or retained['completed'] or
                retained['last_attempt_head_sha256'] != expected or
                retained['last_outcome'] != 'REVIEW_REQUIRED' or
                retained['last_detail_code'] != 'EMERGENCY_STOP_ACTIVE'):
            _fail('EMERGENCY_STOP_ABORT_UNCERTAIN')
        return retained, False

    def close_waiting_retry_for_emergency_stop(self, *, evaluated_at,
            expected_head, emergency_stop_check,
            confirm_emergency_stop_close=False):
        """Close a retry-waiting episode without reserving another attempt."""
        now = _clock(evaluated_at)
        expected = _digest(expected_head, 'EXPECTED_HEAD_INVALID')
        budget_binding = (self.path, self.account_sha256)

        def require_budget_binding():
            if not _budget_binding_matches(self, budget_binding):
                _fail('STORE_BINDING_CHANGED')

        if confirm_emergency_stop_close is not True:
            _fail('EMERGENCY_STOP_CLOSE_CONFIRMATION_REQUIRED')
        if not callable(emergency_stop_check):
            _fail('EMERGENCY_STOP_CHECK_INVALID')

        def stop_active():
            try:
                return emergency_stop_check() is True
            except Exception:
                return False

        active = stop_active()
        require_budget_binding()
        if not active:
            _fail('EMERGENCY_STOP_NOT_ACTIVE')
        current = self.inspect()
        require_budget_binding()
        duplicate = current['head_sha256'] != expected
        if duplicate:
            if (current['pending_token'] is not None or
                    not current['review_required'] or current['completed'] or
                    current['emergency_stop_prior_head_sha256'] != expected or
                    current['last_outcome'] != 'REVIEW_REQUIRED' or
                    current['last_detail_code'] != 'EMERGENCY_STOP_ACTIVE'):
                _fail('HEAD_CHANGED')
            return current, True
        if (current['pending_token'] is not None or
                current['review_required'] or current['completed'] or
                current['attempts'] <= 0 or
                current['last_outcome'] != 'RETRY_REQUIRED' or
                current['next_allowed_at'] <= 0):
            _fail('WAITING_RETRY_MISSING')
        if now < current['last_evaluated_at']:
            _fail('CLOCK_REGRESSION')
        body = dict(kind='EMERGENCY_STOP_CLOSE', policy=POLICY,
            account_sha256=self.account_sha256, evaluated_at=now,
            confirm_emergency_stop_close=True)
        db, _ = self._connect(write=True)
        with closing(db):
            with db:
                state = self._load(db)
                if state['head_sha256'] != expected:
                    _fail('HEAD_CHANGED')
                if (state['pending_token'] is not None or
                        state['review_required'] or state['completed'] or
                        state['attempts'] <= 0 or
                        state['last_outcome'] != 'RETRY_REQUIRED' or
                        state['next_allowed_at'] <= 0):
                    _fail('WAITING_RETRY_CHANGED')
                active = stop_active()
                require_budget_binding()
                if not active:
                    _fail('EMERGENCY_STOP_NOT_ACTIVE')
                head = self._insert(db, 2 * state['attempts'] + 1,
                    body, state['head_sha256'])
                loaded = self._load(db)
                if (loaded['head_sha256'] != head or
                        not loaded['review_required'] or
                        loaded['emergency_stop_prior_head_sha256'] !=
                            expected or
                        loaded['last_detail_code'] !=
                            'EMERGENCY_STOP_ACTIVE'):
                    _fail('EMERGENCY_STOP_CLOSE_UNCERTAIN')
        require_budget_binding()
        retained = self.inspect()
        require_budget_binding()
        return retained, False


def collect_and_checkpoint_due(api_key, api_secret, *, expected_account_id,
        initial_events, reconnect_reason, cursor_store, expected_cursor_head,
        reconnect_budget, expected_budget_head, evaluated_at,
        confirm_reconnect_attempt=False, confirm_read_only_paper=False,
        confirm_cursor_checkpoint=False, opener=None, wall_clock=None,
        elapsed_clock=None, outcome_clock=None, permission_check=None,
        emergency_stop_check=None, credential_check=None):
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
    cursor_binding = (cursor_store.path, cursor_store.account_sha256)
    budget_binding = (reconnect_budget.path,
        reconnect_budget.account_sha256)

    def require_store_bindings():
        if not _store_bindings_match(cursor_store, reconnect_budget,
                cursor_binding=cursor_binding,
                budget_binding=budget_binding):
            _fail('STORE_BINDING_CHANGED')

    result_clock = (outcome_clock if outcome_clock is not None else
        (wall_clock if wall_clock is not None else time.time))
    if not callable(result_clock):
        _fail('OUTCOME_CLOCK_INVALID')
    if not callable(emergency_stop_check):
        _fail('EMERGENCY_STOP_CHECK_INVALID')
    try:
        if emergency_stop_check() is not False:
            _fail('EMERGENCY_STOP_ACTIVE')
    except ActivitySSEReconnectBudgetError:
        require_store_bindings()
        raise
    except Exception:
        require_store_bindings()
        _fail('EMERGENCY_STOP_ACTIVE')
    require_store_bindings()
    before_head = _cursor_head_for_attempt(cursor_store)
    require_store_bindings()
    if before_head != expected_cursor_head:
        _fail('CURSOR_HEAD_CHANGED')
    try:
        reservation = reconnect_budget.begin(evaluated_at=evaluated_at,
            expected_cursor_head=expected_cursor_head,
            reconnect_reason=reconnect_reason,
            expected_head=expected_budget_head,
            emergency_stop_check=emergency_stop_check,
            confirm_reconnect_attempt=confirm_reconnect_attempt)
    except ActivitySSEReconnectBudgetError:
        require_store_bindings()
        raise
    except Exception:
        require_store_bindings()
        _fail('RESERVATION_STATE_UNCERTAIN')
    require_store_bindings()
    try:
        if type(reservation) is not dict:
            raise TypeError('reservation acknowledgement shape')
        token = _digest(reservation['token'], 'TOKEN_INVALID')
        head = _digest(reservation['head_sha256'], 'HEAD_INVALID')
        cursor_head = _digest(
            reservation['expected_cursor_head_sha256'],
            'CURSOR_HEAD_INVALID')
        attempt = reservation['attempt']
        duplicate = reservation['duplicate_reservation']
        request_authorized = reservation['provider_request_authorized']
        if (token != head or cursor_head != expected_cursor_head or
                type(attempt) is not int or
                not 1 <= attempt <= MAX_ATTEMPTS or
                type(duplicate) is not bool or
                type(request_authorized) is not bool or
                duplicate == request_authorized):
            raise ValueError('reservation acknowledgement disagreement')
    except Exception:
        # A malformed return may follow a committed BEGIN whose caller-side
        # acknowledgement was corrupted.  Do not infer that no reservation
        # exists and never construct a provider request from uncertain state.
        _fail('RESERVATION_STATE_UNCERTAIN')
    try:
        retained_reservation = _budget_state_for_attempt(reconnect_budget)
        require_store_bindings()
        if not _exact_pending_reservation(retained_reservation,
                token=token, attempt=attempt, cursor_head=cursor_head,
                expected_budget_head=expected_budget_head,
                evaluated_at=evaluated_at,
                reconnect_reason=reconnect_reason):
            raise ValueError('reservation acknowledgement not durable')
    except Exception:
        # A structurally valid acknowledgement is not request authority until
        # the private journal independently proves the exact pending BEGIN.
        _fail('RESERVATION_STATE_UNCERTAIN')
    if reservation['duplicate_reservation']:
        _fail('RESERVATION_REPLAY_REQUIRES_REVIEW')

    # Re-prove the exact pending reservation at every downstream permission
    # fence, including the transport's last checkpoint immediately before
    # ``opener.open``.  The journal can be changed by another cooperating
    # process after BEGIN acknowledgement; a stale token must never retain
    # provider-request authority in that interval.
    reservation_state_uncertain = False
    store_binding_changed = False
    request_permission_check = permission_check
    if callable(permission_check):
        def request_permission_check():
            nonlocal reservation_state_uncertain, store_binding_changed
            allowed = permission_check()
            if not _store_bindings_match(cursor_store, reconnect_budget,
                    cursor_binding=cursor_binding,
                    budget_binding=budget_binding):
                store_binding_changed = True
                return False
            if allowed is not True:
                return allowed
            try:
                current = _budget_state_for_attempt(reconnect_budget)
                exact = _exact_pending_reservation(current,
                    token=token, attempt=attempt, cursor_head=cursor_head,
                    expected_budget_head=expected_budget_head,
                    evaluated_at=evaluated_at,
                    reconnect_reason=reconnect_reason)
            except Exception:
                exact = False
            if not exact:
                reservation_state_uncertain = True
                return False
            return True

    options = dict(expected_account_id=expected_account_id,
        initial_events=initial_events, reconnect_reason=reconnect_reason,
        cursor_store=cursor_store, expected_cursor_head=expected_cursor_head,
        confirm_read_only_paper=confirm_read_only_paper,
        confirm_cursor_checkpoint=confirm_cursor_checkpoint, opener=opener,
        permission_check=request_permission_check,
        emergency_stop_check=emergency_stop_check,
        credential_check=credential_check)
    options['reconnect_reservation_token'] = token
    if wall_clock is not None:
        options['wall_clock'] = wall_clock
    if elapsed_clock is not None:
        options['elapsed_clock'] = elapsed_clock
    try:
        result = checkpoint.collect_and_checkpoint_once(api_key, api_secret,
            **options)
    except Exception as error:
        if (store_binding_changed or
                not _store_bindings_match(cursor_store, reconnect_budget,
                    cursor_binding=cursor_binding,
                    budget_binding=budget_binding)):
            _fail('STORE_BINDING_CHANGED')
        if reservation_state_uncertain:
            # The durable BEGIN no longer grants this caller authority.  Do
            # not overwrite a concurrent result or infer a retryable outcome.
            _fail('RESERVATION_STATE_UNCERTAIN')
        try:
            completed_at = _outcome_time(result_clock, evaluated_at)
        except ActivitySSEReconnectBudgetError:
            require_store_bindings()
            # A failed request with no committed cursor transition needs no
            # completion-time backoff. Close it from the trusted reservation
            # timestamp so it cannot linger pending or acquire retry authority.
            # If the cursor moved, preserve the reservation for the existing
            # exact no-network checkpoint reconciliation path instead.
            after_head = _cursor_head_for_attempt(cursor_store)
            if after_head == expected_cursor_head:
                _close_failed_clock_uncertainty(reconnect_budget,
                    reservation['token'], evaluated_at)
            raise
        require_store_bindings()
        after_head = _cursor_head_for_attempt(cursor_store)
        require_store_bindings()
        code = str(error)
        provider_retry_after = (error.provider_retry_after_seconds
            if isinstance(error, ActivitySSETransportError) else 0)
        if after_head != expected_cursor_head:
            outcome, detail = 'CHECKPOINT_UNCERTAIN', 'CURSOR_ADVANCED_WITHOUT_BUDGET_ACK'
        elif code in RETRYABLE_ERRORS and reservation['attempt'] < MAX_ATTEMPTS:
            outcome, detail = 'RETRY_REQUIRED', code
        else:
            outcome, detail = 'REVIEW_REQUIRED', 'NONRETRYABLE_FAILURE'
        if outcome != 'RETRY_REQUIRED':
            provider_retry_after = 0
        try:
            retained = reconnect_budget.finish(reservation['token'],
                evaluated_at=completed_at, outcome=outcome,
                detail_code=detail,
                emergency_stop_check=emergency_stop_check,
                provider_retry_after_seconds=provider_retry_after)
        except Exception:
            require_store_bindings()
            retained = _inspect_after_result_error(reconnect_budget,
                'RESULT_RECORD_UNCERTAIN')
        else:
            require_store_bindings()
            retained = _validated_budget_view(reconnect_budget, retained,
                'RESULT_RECORD_UNCERTAIN', result_acknowledgement=True)
        require_store_bindings()
        if not _exact_failure_result(retained, reservation['token'],
                completed_at, outcome, detail, provider_retry_after):
            _fail('RESULT_RECORD_UNCERTAIN')
        raise
    require_store_bindings()
    checkpoint_binding = _checkpoint_binding_for_attempt(cursor_store,
        result, token=token, expected_cursor_head=expected_cursor_head)
    require_store_bindings()
    completed_at = _outcome_time(result_clock, evaluated_at)
    require_store_bindings()
    result_acknowledgement_recovered = False
    try:
        retained = reconnect_budget.finish(reservation['token'],
            evaluated_at=completed_at, outcome='CHECKPOINT_COMMITTED',
            detail_code='SUCCESS',
            checkpoint_head_sha256=checkpoint_binding['head_sha256'],
            checkpoint_receipt_sha256=
                checkpoint_binding['checkpoint_receipt_sha256'])
    except Exception:
        require_store_bindings()
        retained = _inspect_after_result_error(reconnect_budget,
            'RESULT_RECORD_UNCERTAIN')
        result_acknowledgement_recovered = True
    else:
        require_store_bindings()
        retained = _validated_budget_view(reconnect_budget, retained,
            'RESULT_RECORD_UNCERTAIN', result_acknowledgement=True)
    require_store_bindings()
    try:
        stable_checkpoint_binding = _validated_checkpoint_binding(
            cursor_store, cursor_store.latest_checkpoint_binding(),
            'RESULT_RECORD_UNCERTAIN')
    except Exception:
        _fail('RESULT_RECORD_UNCERTAIN')
    require_store_bindings()
    stable_result = _inspect_after_result_error(reconnect_budget,
        'RESULT_RECORD_UNCERTAIN')
    require_store_bindings()
    if (stable_checkpoint_binding != checkpoint_binding or
            not _same_result_head(stable_result, retained) or
            not _exact_checkpoint_result(stable_result,
                reservation['token'], checkpoint_binding['head_sha256'],
                checkpoint_binding['checkpoint_receipt_sha256'],
                completed_at)):
        _fail('RESULT_RECORD_UNCERTAIN')
    return dict(result, mode='EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_ATTEMPT',
        reconnect_budget_policy=POLICY,
        reconnect_attempt_number=reservation['attempt'],
        reconnect_attempt_cap=MAX_ATTEMPTS,
        bounded_backoff_enforced=True, caller_driven_attempt=True,
        emergency_stop_admission_fenced=True,
        emergency_stop_sampled_before_reservation=True,
        result_acknowledgement_recovered=
            result_acknowledgement_recovered,
        automatic_retry=False, automatic_reconnect_performed=False,
        automatic_backoff_verified=False, actual_vault_access=False,
        ledger_applied=False, **{flag: False for flag in FLAGS},
        reconnect_budget_head_sha256=stable_result['head_sha256'],
        reconnect_budget_completed=True)


def abort_pending_reconnect_for_emergency_stop(*, reconnect_budget,
        expected_budget_head, evaluated_at, emergency_stop_check,
        confirm_emergency_stop_abort=False):
    """Retire a pending reconnect without credentials, cursor I/O, or network."""
    if type(reconnect_budget) is not ActivitySSEReconnectBudget:
        _fail('BUDGET_INVALID')
    budget_binding = (reconnect_budget.path,
        reconnect_budget.account_sha256)
    retained, duplicate = reconnect_budget.abort_pending_for_emergency_stop(
        evaluated_at=evaluated_at, expected_head=expected_budget_head,
        emergency_stop_check=emergency_stop_check,
        confirm_emergency_stop_abort=confirm_emergency_stop_abort)
    if not _budget_binding_matches(reconnect_budget, budget_binding):
        _fail('STORE_BINDING_CHANGED')
    return dict(mode=
            'EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_EMERGENCY_STOP_ABORT',
        status='REVIEW_REQUIRED', policy=POLICY,
        pending_reconnect_aborted=True, duplicate_abort=duplicate,
        explicit_emergency_stop_abort_confirmation=True,
        emergency_stop_observed_active=True,
        provider_request_performed=False, credential_access_performed=False,
        cursor_read_performed=False, cursor_write_performed=False,
        automatic_retry=False, automatic_reconnect_performed=False,
        reconnect_budget_head_sha256=retained['head_sha256'],
        **{flag: False for flag in FLAGS})


def close_pending_reconnect_for_outcome_uncertainty(*, reconnect_budget,
        expected_budget_head, evaluated_at,
        confirm_outcome_uncertain=False, confirm_no_retry=False):
    """Close a clock-uncertain attempt without external or cursor access."""
    if type(reconnect_budget) is not ActivitySSEReconnectBudget:
        _fail('BUDGET_INVALID')
    budget_binding = (reconnect_budget.path,
        reconnect_budget.account_sha256)
    retained, duplicate = \
        reconnect_budget.close_pending_for_outcome_uncertainty(
            evaluated_at=evaluated_at, expected_head=expected_budget_head,
            confirm_outcome_uncertain=confirm_outcome_uncertain,
            confirm_no_retry=confirm_no_retry)
    if not _budget_binding_matches(reconnect_budget, budget_binding):
        _fail('STORE_BINDING_CHANGED')
    return dict(mode=
            'EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_OUTCOME_UNCERTAIN_CLOSE',
        status='REVIEW_REQUIRED', policy=POLICY,
        pending_outcome_closed=True, duplicate_close=duplicate,
        explicit_outcome_uncertainty_confirmation=True,
        explicit_no_retry_confirmation=True,
        outcome_uncertainty_preserved=True,
        provider_request_performed=False, credential_access_performed=False,
        cursor_read_performed=False, cursor_write_performed=False,
        reconnect_attempt_reserved=False, reconnect_attempt_consumed=False,
        retry_scheduled=False, automatic_retry=False,
        automatic_reconnect_performed=False,
        reconnect_budget_head_sha256=retained['head_sha256'],
        **{flag: False for flag in FLAGS})


def close_waiting_reconnect_for_emergency_stop(*, reconnect_budget,
        expected_budget_head, evaluated_at, emergency_stop_check,
        confirm_emergency_stop_close=False):
    """Close a durable retry wait without credentials, cursor I/O, or network."""
    if type(reconnect_budget) is not ActivitySSEReconnectBudget:
        _fail('BUDGET_INVALID')
    budget_binding = (reconnect_budget.path,
        reconnect_budget.account_sha256)
    retained, duplicate = \
        reconnect_budget.close_waiting_retry_for_emergency_stop(
            evaluated_at=evaluated_at, expected_head=expected_budget_head,
            emergency_stop_check=emergency_stop_check,
            confirm_emergency_stop_close=confirm_emergency_stop_close)
    if not _budget_binding_matches(reconnect_budget, budget_binding):
        _fail('STORE_BINDING_CHANGED')
    return dict(mode=
            'EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_EMERGENCY_STOP_CLOSE',
        status='REVIEW_REQUIRED', policy=POLICY,
        waiting_retry_closed=True, duplicate_close=duplicate,
        explicit_emergency_stop_close_confirmation=True,
        emergency_stop_observed_active=True,
        provider_request_performed=False, credential_access_performed=False,
        cursor_read_performed=False, cursor_write_performed=False,
        reconnect_attempt_reserved=False, reconnect_attempt_consumed=False,
        automatic_retry=False, automatic_reconnect_performed=False,
        reconnect_budget_head_sha256=retained['head_sha256'],
        **{flag: False for flag in FLAGS})


def reconcile_pending_checkpoint(*, cursor_store, reconnect_budget,
        expected_budget_head, expected_checkpoint_head, evaluated_at,
        confirm_checkpoint_reconciliation=False):
    """Close one pending attempt only from its exact bound cursor receipt.

    This recovery path performs no provider or credential access and never
    retries the network request.  It exists solely for a process death after
    the cursor checkpoint committed but before the reconnect budget recorded
    that exact result.
    """
    if confirm_checkpoint_reconciliation is not True:
        _fail('RECONCILIATION_CONFIRMATION_REQUIRED')
    if type(cursor_store) is not ActivitySSECursorStore:
        _fail('CURSOR_STORE_INVALID')
    if type(reconnect_budget) is not ActivitySSEReconnectBudget:
        _fail('BUDGET_INVALID')
    budget_head = _digest(expected_budget_head, 'EXPECTED_HEAD_INVALID')
    checkpoint_head = _digest(expected_checkpoint_head,
        'CHECKPOINT_HEAD_INVALID')
    now = _clock(evaluated_at)
    if reconnect_budget.account_sha256 != cursor_store.account_sha256:
        _fail('ACCOUNT_DISAGREEMENT')
    budget_binding = (reconnect_budget.path,
        reconnect_budget.account_sha256)
    cursor_binding = (cursor_store.path, cursor_store.account_sha256)

    def require_store_bindings():
        if not _store_bindings_match(cursor_store, reconnect_budget,
                cursor_binding=cursor_binding,
                budget_binding=budget_binding):
            _fail('STORE_BINDING_CHANGED')

    try:
        state = _validated_budget_view(reconnect_budget,
            reconnect_budget.inspect(), 'RECOVERY_STATE_UNCERTAIN')
    except Exception:
        require_store_bindings()
        _fail('RECOVERY_STATE_UNCERTAIN')
    require_store_bindings()
    try:
        duplicate = state['head_sha256'] != budget_head
        if duplicate:
            if (not state['completed'] or state['review_required'] or
                    state['last_attempt_head_sha256'] != budget_head or
                    state['checkpoint_head_sha256'] != checkpoint_head):
                _fail('HEAD_CHANGED')
            reservation_token = budget_head
        else:
            if (state['pending_token'] is None or state['completed'] or
                    state['review_required']):
                _fail('PENDING_CHECKPOINT_MISSING')
            reservation_token = state['pending_token']
    except ActivitySSEReconnectBudgetError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError):
        _fail('RECOVERY_STATE_UNCERTAIN')
    try:
        retained = _validated_checkpoint_binding(cursor_store,
            cursor_store.latest_checkpoint_binding(),
            'CURSOR_BINDING_REVIEW_FAILED')
        retained_head = _digest(retained['head_sha256'],
            'CURSOR_BINDING_REVIEW_FAILED')
        retained_expected = _digest(retained['expected_head_sha256'],
            'CURSOR_BINDING_REVIEW_FAILED')
        reservation_bound = retained['reconnect_reservation_bound']
        if type(reservation_bound) is not bool:
            _fail('CURSOR_BINDING_REVIEW_FAILED')
        retained_reservation = (None if not reservation_bound else _digest(
            retained['reconnect_reservation_sha256'],
            'CURSOR_BINDING_REVIEW_FAILED'))
        binding_matches = (
            retained_head == checkpoint_head and
            retained_expected ==
                state['expected_cursor_head_sha256'] and
            reservation_bound is True and
            retained_reservation == sha(reservation_token))
    except Exception:
        require_store_bindings()
        _fail('CURSOR_BINDING_REVIEW_FAILED')
    require_store_bindings()
    if not binding_matches:
        _fail('CHECKPOINT_BINDING_DISAGREEMENT')
    result_acknowledgement_recovered = False
    if duplicate:
        if (state['checkpoint_receipt_sha256'] !=
                retained['checkpoint_receipt_sha256']):
            _fail('CHECKPOINT_BINDING_DISAGREEMENT')
        recovered = state
    else:
        try:
            recovered = reconnect_budget.finish(state['pending_token'],
                evaluated_at=now, outcome='CHECKPOINT_COMMITTED',
                detail_code='SUCCESS',
                checkpoint_head_sha256=retained['head_sha256'],
                checkpoint_receipt_sha256=
                    retained['checkpoint_receipt_sha256'])
        except Exception:
            require_store_bindings()
            try:
                recovered = _inspect_after_result_error(reconnect_budget,
                    'RECOVERY_RESULT_UNCERTAIN')
            except Exception:
                require_store_bindings()
                raise
            require_store_bindings()
            result_acknowledgement_recovered = True
        else:
            require_store_bindings()
            recovered = _validated_budget_view(reconnect_budget, recovered,
                'RECOVERY_RESULT_UNCERTAIN',
                result_acknowledgement=True)
            require_store_bindings()
    require_store_bindings()
    try:
        unchanged = _validated_checkpoint_binding(cursor_store,
            cursor_store.latest_checkpoint_binding(),
            'RECOVERY_RESULT_UNCERTAIN')
    except Exception:
        require_store_bindings()
        _fail('RECOVERY_RESULT_UNCERTAIN')
    require_store_bindings()
    try:
        stable = _inspect_after_result_error(reconnect_budget,
            'RECOVERY_RESULT_UNCERTAIN')
    except Exception:
        require_store_bindings()
        raise
    require_store_bindings()
    result_time = None if duplicate else now
    if (unchanged != retained or
            not _same_result_head(stable, recovered) or
            not _exact_checkpoint_result(stable, reservation_token,
                retained['head_sha256'],
                retained['checkpoint_receipt_sha256'], result_time)):
        _fail('RECOVERY_RESULT_UNCERTAIN')
    return dict(mode=
            'EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_RESULT_RECOVERY',
        status='REVIEW_REQUIRED', policy=POLICY,
        pending_checkpoint_reconciled=True,
        duplicate_recovery=duplicate,
        result_acknowledgement_recovered=
            result_acknowledgement_recovered,
        explicit_reconciliation_confirmation=True,
        provider_request_performed=False, automatic_retry=False,
        automatic_reconnect_performed=False,
        reconnect_budget_head_sha256=stable['head_sha256'],
        checkpoint_head_sha256=retained['head_sha256'],
        checkpoint_receipt_sha256=
            retained['checkpoint_receipt_sha256'],
        **{flag: False for flag in FLAGS})


def fixture_report():
    """Disposable timeout/backoff/success fixture; injected network only."""
    import copy
    from email.message import Message
    import io
    import tempfile
    from urllib.error import HTTPError
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

    class ProviderBackoffOpener:
        def __init__(self, status):
            self.status = status
            self.requests = []
            self.body = io.BytesIO(b'private fixture body')
        def open(self, request, timeout):
            self.requests.append((request, timeout))
            headers = Message()
            headers['Retry-After'] = '120'
            raise HTTPError(request.full_url, self.status,
                'private fixture reason',
                headers, self.body)

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
        replay_cursor = ActivitySSECursorStore(
            root / 'replay-cursor.sqlite3', account)
        replay_budget = ActivitySSEReconnectBudget(
            root / 'replay-budget.sqlite3', account)
        replay_reserved = replay_budget.begin(evaluated_at=998,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: False,
            confirm_reconnect_attempt=True)
        replay_bytes = replay_budget.path.read_bytes()
        replay_ack = replay_budget.begin(evaluated_at=998,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: False,
            confirm_reconnect_attempt=True)
        replay_opener = Opener(raw)
        replay_code = None
        try:
            collect_and_checkpoint_due('fixture_key_only_123',
                'fixture_secret_only_123', expected_budget_head=ZERO,
                evaluated_at=998, opener=replay_opener,
                **dict(common, cursor_store=replay_cursor,
                    reconnect_budget=replay_budget,
                    wall_clock=lambda: 998))
        except ActivitySSEReconnectBudgetError as error:
            replay_code = str(error)
        blocked_cursor = ActivitySSECursorStore(
            root / 'blocked-cursor.sqlite3', account)
        blocked_budget = ActivitySSEReconnectBudget(
            root / 'blocked-budget.sqlite3', account)
        blocked_opener = Opener(raw)
        blocked_code = None
        try:
            collect_and_checkpoint_due('fixture_key_only_123',
                'fixture_secret_only_123', expected_budget_head=ZERO,
                evaluated_at=999, opener=blocked_opener,
                **dict(common, cursor_store=blocked_cursor,
                    reconnect_budget=blocked_budget,
                    emergency_stop_check=lambda: True))
        except ActivitySSEReconnectBudgetError as error:
            blocked_code = str(error)
        try:
            collect_and_checkpoint_due('fixture_key_only_123',
                'fixture_secret_only_123', expected_budget_head=ZERO,
                evaluated_at=1000, opener=first, **common)
        except ActivitySSETransportError:
            pass
        after_timeout = budget.inspect()
        retry_result_bytes = budget.path.read_bytes()
        replayed_timeout = budget.finish(
            after_timeout['last_attempt_head_sha256'], evaluated_at=1000,
            outcome='RETRY_REQUIRED',
            detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
            emergency_stop_check=lambda: False)
        retry_result_replay_unchanged = \
            budget.path.read_bytes() == retry_result_bytes
        second = Opener(raw)
        common['wall_clock'] = lambda: 1001
        result = collect_and_checkpoint_due('fixture_key_only_123',
            'fixture_secret_only_123',
            expected_budget_head=after_timeout['head_sha256'],
            evaluated_at=1001, opener=second, **common)
        cold_budget = ActivitySSEReconnectBudget(
            budget.path, account).inspect()
        cold_cursor = ActivitySSECursorStore(cursor.path, account).inspect()
        recovery_cursor = ActivitySSECursorStore(
            root / 'recovery-cursor.sqlite3', account)
        recovery_budget = ActivitySSEReconnectBudget(
            root / 'recovery-budget.sqlite3', account)
        reserved = recovery_budget.begin(evaluated_at=2000,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: False,
            confirm_reconnect_attempt=True)
        recovery_opener = Opener(raw)
        committed = checkpoint.collect_and_checkpoint_once(
            'fixture_key_only_123', 'fixture_secret_only_123',
            expected_account_id=account, initial_events=initial,
            reconnect_reason='CONNECTION_DROPPED',
            cursor_store=recovery_cursor, expected_cursor_head=ZERO,
            confirm_read_only_paper=True, confirm_cursor_checkpoint=True,
            opener=recovery_opener, wall_clock=lambda: 2000,
            elapsed_clock=lambda: 50, permission_check=lambda: True,
            emergency_stop_check=lambda: False,
            credential_check=lambda: True,
            reconnect_reservation_token=reserved['token'])
        recovered = reconcile_pending_checkpoint(cursor_store=
            ActivitySSECursorStore(recovery_cursor.path, account),
            reconnect_budget=ActivitySSEReconnectBudget(
                recovery_budget.path, account),
            expected_budget_head=reserved['head_sha256'],
            expected_checkpoint_head=committed['checkpoint_head_sha256'],
            evaluated_at=2001, confirm_checkpoint_reconciliation=True)
        stop_budget = ActivitySSEReconnectBudget(
            root / 'stop-budget.sqlite3', account)
        stop_reserved = stop_budget.begin(evaluated_at=3000,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: False,
            confirm_reconnect_attempt=True)
        stop_samples = []
        stopped = abort_pending_reconnect_for_emergency_stop(
            reconnect_budget=stop_budget,
            expected_budget_head=stop_reserved['head_sha256'],
            evaluated_at=3001,
            emergency_stop_check=lambda: stop_samples.append(True) or True,
            confirm_emergency_stop_abort=True)
        waiting_budget = ActivitySSEReconnectBudget(
            root / 'waiting-budget.sqlite3', account)
        waiting_reserved = waiting_budget.begin(evaluated_at=4000,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: False,
            confirm_reconnect_attempt=True)
        waiting_budget.finish(waiting_reserved['token'], evaluated_at=4000,
            outcome='RETRY_REQUIRED',
            detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
            emergency_stop_check=lambda: False)
        waiting_head = waiting_budget.inspect()['head_sha256']
        waiting_samples = []
        waiting_closed = close_waiting_reconnect_for_emergency_stop(
            reconnect_budget=waiting_budget,
            expected_budget_head=waiting_head, evaluated_at=4000.5,
            emergency_stop_check=
                lambda: waiting_samples.append(True) or True,
            confirm_emergency_stop_close=True)
        retry_stop_budget = ActivitySSEReconnectBudget(
            root / 'retry-stop-budget.sqlite3', account)
        retry_stop_reserved = retry_stop_budget.begin(evaluated_at=5000,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: False,
            confirm_reconnect_attempt=True)
        retry_stop_values = iter((False, True))
        retry_stop_samples = []
        def retry_stop_check():
            value = next(retry_stop_values)
            retry_stop_samples.append(value)
            return value
        retry_stop_state = retry_stop_budget.finish(
            retry_stop_reserved['token'], evaluated_at=5000,
            outcome='RETRY_REQUIRED',
            detail_code='ALPACA_ACTIVITY_SSE_TRANSPORT_TRANSPORT_UNAVAILABLE',
            emergency_stop_check=retry_stop_check)
        rate_cursor = ActivitySSECursorStore(
            root / 'rate-cursor.sqlite3', account)
        rate_budget = ActivitySSEReconnectBudget(
            root / 'rate-budget.sqlite3', account)
        rate_opener = ProviderBackoffOpener(429)
        try:
            collect_and_checkpoint_due('fixture_key_only_123',
                'fixture_secret_only_123', expected_budget_head=ZERO,
                evaluated_at=6000, opener=rate_opener,
                **dict(common, cursor_store=rate_cursor,
                    reconnect_budget=rate_budget,
                    wall_clock=lambda: 6000,
                    outcome_clock=lambda: 6007))
        except ActivitySSETransportError:
            pass
        rate_state = rate_budget.inspect()
        service_cursor = ActivitySSECursorStore(
            root / 'service-cursor.sqlite3', account)
        service_budget = ActivitySSEReconnectBudget(
            root / 'service-budget.sqlite3', account)
        service_opener = ProviderBackoffOpener(503)
        try:
            collect_and_checkpoint_due('fixture_key_only_123',
                'fixture_secret_only_123', expected_budget_head=ZERO,
                evaluated_at=7000, opener=service_opener,
                **dict(common, cursor_store=service_cursor,
                    reconnect_budget=service_budget,
                    wall_clock=lambda: 7000,
                    outcome_clock=lambda: 7009))
        except ActivitySSETransportError:
            pass
        service_state = service_budget.inspect()
        automatic_uncertain_cursor = ActivitySSECursorStore(
            root / 'automatic-uncertain-cursor.sqlite3', account)
        automatic_uncertain_budget = ActivitySSEReconnectBudget(
            root / 'automatic-uncertain-budget.sqlite3', account)
        automatic_uncertain_opener = Opener(
            TimeoutError('fixture timeout'))
        automatic_uncertain_code = None
        try:
            collect_and_checkpoint_due('fixture_key_only_123',
                'fixture_secret_only_123', expected_budget_head=ZERO,
                evaluated_at=7500, opener=automatic_uncertain_opener,
                **dict(common, cursor_store=automatic_uncertain_cursor,
                    reconnect_budget=automatic_uncertain_budget,
                    wall_clock=lambda: 7500,
                    outcome_clock=lambda: 7499))
        except ActivitySSEReconnectBudgetError as error:
            automatic_uncertain_code = str(error)
        automatic_uncertain_state = automatic_uncertain_budget.inspect()
        uncertain_budget = ActivitySSEReconnectBudget(
            root / 'uncertain-budget.sqlite3', account)
        uncertain_reserved = uncertain_budget.begin(evaluated_at=8000,
            expected_cursor_head=ZERO,
            reconnect_reason='CONNECTION_DROPPED', expected_head=ZERO,
            emergency_stop_check=lambda: False,
            confirm_reconnect_attempt=True)
        uncertain_closed = \
            close_pending_reconnect_for_outcome_uncertainty(
                reconnect_budget=uncertain_budget,
                expected_budget_head=uncertain_reserved['head_sha256'],
                evaluated_at=8001, confirm_outcome_uncertain=True,
                confirm_no_retry=True)
        uncertain_bytes = uncertain_budget.path.read_bytes()
        uncertain_replay = \
            close_pending_reconnect_for_outcome_uncertainty(
                reconnect_budget=uncertain_budget,
                expected_budget_head=uncertain_reserved['head_sha256'],
                evaluated_at=8001, confirm_outcome_uncertain=True,
                confirm_no_retry=True)
        uncertain_state = uncertain_budget.inspect()
        encoded = json.dumps(
            [result, cold_budget, recovered, stopped, waiting_closed,
                retry_stop_state, rate_state, service_state,
                automatic_uncertain_state, uncertain_closed,
                uncertain_replay, uncertain_state],
            sort_keys=True)
        checks = dict(first_failure_retained_without_retry=
                after_timeout['last_outcome'] == 'RETRY_REQUIRED' and
                after_timeout['next_allowed_at'] == 1001 and
                len(first.requests) == 1,
            lost_reservation_ack_replays_without_provider_request=
                replay_ack['duplicate_reservation'] and
                not replay_ack['provider_request_authorized'] and
                replay_ack['token'] == replay_reserved['token'] and
                replay_budget.path.read_bytes() == replay_bytes and
                replay_budget.inspect()['attempts'] == 1 and
                replay_code is not None and replay_code.endswith(
                    'RESERVATION_REPLAY_REQUIRES_REVIEW') and
                not replay_opener.requests and
                not replay_cursor.path.exists(),
            lost_retry_result_ack_replays_without_new_attempt=
                replayed_timeout['duplicate_result'] and
                retry_result_replay_unchanged and
                replayed_timeout['attempts'] == 1 and
                replayed_timeout['last_outcome'] == 'RETRY_REQUIRED' and
                len(first.requests) == 1,
            emergency_stop_blocks_before_reconnect_storage=
                blocked_code is not None and
                blocked_code.endswith('EMERGENCY_STOP_ACTIVE') and
                not blocked_cursor.path.exists() and
                not blocked_budget.path.exists() and
                not blocked_opener.requests,
            due_second_attempt_explicit_and_single=
                result['reconnect_attempt_number'] == 2 and
                len(second.requests) == 1,
            checkpoint_and_budget_recover_together=
                cold_budget['completed'] and cold_cursor['initialized'] and
                cold_budget['checkpoint_head_sha256'] ==
                    cold_cursor['head_sha256'],
            pending_checkpoint_result_recovers_without_second_get=
                recovered['pending_checkpoint_reconciled'] and
                not recovered['provider_request_performed'] and
                len(recovery_opener.requests) == 1 and
                recovery_budget.inspect()['completed'],
            emergency_stop_retires_pending_without_external_access=
                stopped['pending_reconnect_aborted'] and
                len(stop_samples) == 2 and
                not stopped['provider_request_performed'] and
                not stopped['credential_access_performed'] and
                stop_budget.inspect()['review_required'],
            emergency_stop_closes_retry_wait_without_new_attempt=
                waiting_closed['waiting_retry_closed'] and
                len(waiting_samples) == 2 and
                waiting_budget.inspect()['attempts'] == 1 and
                not waiting_closed['reconnect_attempt_reserved'] and
                not waiting_closed['provider_request_performed'] and
                waiting_budget.inspect()['review_required'],
            emergency_stop_blocks_retry_schedule_during_result=
                retry_stop_samples == [False, True] and
                retry_stop_state['last_outcome'] == 'REVIEW_REQUIRED' and
                retry_stop_state['last_detail_code'] ==
                    'EMERGENCY_STOP_ACTIVE' and
                retry_stop_state['next_allowed_at'] == 0 and
                retry_stop_state['review_required'],
            provider_retry_after_extends_durable_backoff=
                len(rate_opener.requests) == 1 and
                rate_opener.body.closed and
                rate_state['last_outcome'] == 'RETRY_REQUIRED' and
                rate_state['last_detail_code'] ==
                    'ALPACA_ACTIVITY_SSE_TRANSPORT_RATE_LIMITED' and
                rate_state['last_requested_retry_after_seconds'] == 120 and
                rate_state['last_applied_backoff_seconds'] == 120 and
                rate_state['last_evaluated_at'] == 6007 and
                rate_state['next_allowed_at'] == 6127 and
                rate_state['provider_directed_backoff_observed'] and
                not rate_cursor.path.exists(),
            provider_service_retry_after_extends_durable_backoff=
                len(service_opener.requests) == 1 and
                service_opener.body.closed and
                service_state['last_outcome'] == 'RETRY_REQUIRED' and
                service_state['last_detail_code'] ==
                    'ALPACA_ACTIVITY_SSE_TRANSPORT_SERVICE_UNAVAILABLE' and
                service_state['last_requested_retry_after_seconds'] == 120 and
                service_state['last_applied_backoff_seconds'] == 120 and
                service_state['last_evaluated_at'] == 7009 and
                service_state['next_allowed_at'] == 7129 and
                service_state['provider_directed_backoff_observed'] and
                not service_cursor.path.exists(),
            attempt_completion_anchors_reconnect_cooldown=
                rate_state['last_evaluated_at'] == 6007 and
                rate_state['next_allowed_at'] == 6127 and
                service_state['last_evaluated_at'] == 7009 and
                service_state['next_allowed_at'] == 7129,
            failed_clock_uncertainty_closes_from_trusted_admission=
                automatic_uncertain_code is not None and
                automatic_uncertain_code.endswith(
                    'OUTCOME_CLOCK_REGRESSION') and
                len(automatic_uncertain_opener.requests) == 1 and
                not automatic_uncertain_cursor.path.exists() and
                automatic_uncertain_state['attempts'] == 1 and
                automatic_uncertain_state['pending_token'] is None and
                automatic_uncertain_state['review_required'] and
                not automatic_uncertain_state['completed'] and
                automatic_uncertain_state['last_evaluated_at'] == 7500 and
                automatic_uncertain_state['last_outcome'] ==
                    'REVIEW_REQUIRED' and
                automatic_uncertain_state['last_detail_code'] ==
                    'OUTCOME_CLOCK_UNCERTAIN' and
                automatic_uncertain_state['next_allowed_at'] == 0,
            uncertain_outcome_closes_without_retry_or_external_access=
                uncertain_closed['pending_outcome_closed'] and
                not uncertain_closed['duplicate_close'] and
                uncertain_closed['outcome_uncertainty_preserved'] and
                not uncertain_closed['provider_request_performed'] and
                not uncertain_closed['credential_access_performed'] and
                not uncertain_closed['cursor_read_performed'] and
                not uncertain_closed['cursor_write_performed'] and
                not uncertain_closed['retry_scheduled'] and
                uncertain_state['review_required'] and
                uncertain_state['last_detail_code'] ==
                    'OUTCOME_CLOCK_UNCERTAIN' and
                uncertain_state['next_allowed_at'] == 0,
            uncertain_outcome_close_replay_is_byte_stable=
                uncertain_replay['duplicate_close'] and
                uncertain_budget.path.read_bytes() == uncertain_bytes and
                uncertain_state['attempts'] == 1,
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
