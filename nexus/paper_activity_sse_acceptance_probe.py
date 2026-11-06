"""OFF-by-default one-attempt paper Activity SSE acceptance operator.

Status is local-only. Attempt mode uses a saved read-only paper credential
pair, a private retained initial segment, exact journal heads, and the
process-independent emergency-cut latch. It makes at most one bounded GET and
one cursor checkpoint; it never sleeps, retries, trades, moves funds, or
prints private events.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

from nexus import emergency_intent, paper_credentials
from nexus.alpaca_activity_sse_cursor_store import (
    ActivitySSECursorStore, FLAGS as CURSOR_FLAGS,
    MAX_CHECKPOINTS as MAX_CURSOR_CHECKPOINTS,
    MAX_DATABASE_BYTES as MAX_CURSOR_DATABASE_BYTES, ZERO)
from nexus.alpaca_activity_sse_reconnect_budget import (
    ActivitySSEReconnectBudget, BACKOFF_SECONDS,
    BUDGET_VIEW_KEYS, FLAGS as BUDGET_FLAGS,
    MAX_ATTEMPTS as MAX_RECONNECT_ATTEMPTS,
    MAX_DATABASE_BYTES as MAX_BUDGET_DATABASE_BYTES,
    MAX_PROVIDER_RETRY_AFTER, OUTCOMES, PROVIDER_DELAY_ERRORS, REASONS,
    RETRYABLE_ERRORS)
from nexus.alpaca_activity_sse_review import MAX_INPUT_BYTES, ULID
from nexus.alpaca_paper_contract import sha
from nexus.alpaca_paper_inspection import _uuid
from nexus.paper_acceptance_probe import FLAGS, ROOT
from nexus.sqlite_journal_guard import (capture_file_fingerprints,
    capture_file_identities, check_path_set, require_file_fingerprints,
    require_file_identities)


ATTEMPT_SOURCES = frozenset(('INJECTED_UNATTESTED', 'PAPER_HTTPS_READS'))
CURSOR_VIEW_KEYS = frozenset((
    'mode', 'status', 'initialized', 'account_sha256', 'head_sha256',
    'current_event_id', 'current_cursor_sha256', 'retained_checkpoints',
    'read_only', 'local_cursor_checkpoint_committed',
    'process_restart_recoverable', 'complete_review_counts_bound',
    *CURSOR_FLAGS))


def _fail():
    raise ValueError('PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED') from None


def _digest(value):
    return (type(value) is str and len(value) == 64 and
        all(character in '0123456789abcdef' for character in value))


def _status_code(value):
    return (type(value) is str and 1 <= len(value) <= 96 and
        all(character in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for
            character in value))


def _status_budget_lifecycle(value):
    """Validate cross-field reconnect lifecycle semantics independently.

    ``inspect`` owns replay validation.  The installed status boundary still
    treats its returned dictionary as untrusted: a schema-correct but
    contradictory lifecycle must not be rendered as eligible or safe.
    """
    attempts = value['attempts']
    head = value['head_sha256']
    pending = value['pending_token']
    expected_cursor = value['expected_cursor_head_sha256']
    next_allowed = value['next_allowed_at']
    evaluated = value['last_evaluated_at']
    outcome = value['last_outcome']
    detail = value['last_detail_code']
    requested_outcome = value['last_requested_outcome']
    requested_detail = value['last_requested_detail_code']
    requested_delay = value['last_requested_retry_after_seconds']
    applied_backoff = value['last_applied_backoff_seconds']
    provider_backoff = value['provider_directed_backoff_observed']
    begin_head = value['last_begin_expected_head_sha256']
    reason = value['last_reconnect_reason']
    review = value['review_required']
    completed = value['completed']
    checkpoint_head = value['checkpoint_head_sha256']
    checkpoint_receipt = value['checkpoint_receipt_sha256']
    attempt_head = value['last_attempt_head_sha256']
    result_head = value['last_result_head_sha256']
    emergency_head = value['emergency_stop_prior_head_sha256']
    if (type(evaluated) not in (int, float) or
            not math.isfinite(evaluated) or evaluated < 0 or
            type(requested_delay) is not int or
            not 0 <= requested_delay <= MAX_PROVIDER_RETRY_AFTER or
            type(applied_backoff) is not int or
            not 0 <= applied_backoff <= MAX_PROVIDER_RETRY_AFTER or
            type(provider_backoff) is not bool or
            any(field is not None and not _digest(field) for field in
                (expected_cursor, begin_head, checkpoint_head,
                    checkpoint_receipt, attempt_head, result_head,
                    emergency_head))):
        return False
    if attempts == 0:
        return (head == ZERO and pending is None and
            expected_cursor is None and next_allowed == 0 and
            evaluated == 0 and outcome is None and detail is None and
            requested_outcome is None and requested_detail is None and
            requested_delay == 0 and applied_backoff == 0 and
            not provider_backoff and begin_head is None and reason is None and
            not review and not completed and checkpoint_head is None and
            checkpoint_receipt is None and attempt_head is None and
            result_head is None and emergency_head is None)
    if (head == ZERO or not _digest(expected_cursor) or reason not in REASONS or
            review and completed):
        return False
    if (requested_outcome == 'RETRY_REQUIRED' and
            ((requested_detail in PROVIDER_DELAY_ERRORS) !=
                (requested_delay > 0))):
        return False
    if pending is not None:
        if (pending != head or review or completed or
                checkpoint_head is not None or checkpoint_receipt is not None or
                emergency_head is not None):
            return False
        if outcome is None:
            return (attempts == 1 and detail is None and
                requested_outcome is None and requested_detail is None and
                requested_delay == 0 and applied_backoff == 0 and
                not provider_backoff and begin_head == ZERO and
                attempt_head is None and
                result_head is None and next_allowed == 0)
        if attempts < 2:
            return False
        applied = max(BACKOFF_SECONDS[attempts - 2], requested_delay)
        return (attempts >= 2 and outcome == 'RETRY_REQUIRED' and
            requested_outcome == outcome and detail == requested_detail and
            detail in RETRYABLE_ERRORS and _digest(attempt_head) and
            _digest(result_head) and begin_head == result_head and
            attempt_head != result_head and pending != result_head and
            provider_backoff == (requested_detail in PROVIDER_DELAY_ERRORS and
                requested_delay > 0) and applied_backoff == applied and
            0 < next_allowed <= evaluated)
    if (outcome not in OUTCOMES or requested_outcome not in OUTCOMES or
            not _status_code(detail) or not _status_code(requested_detail) or
            not _digest(attempt_head) or not _digest(result_head) or
            provider_backoff != (requested_detail in PROVIDER_DELAY_ERRORS and
                requested_delay > 0)):
        return False
    if emergency_head is None:
        if head != result_head:
            return False
    elif (not review or detail != 'EMERGENCY_STOP_ACTIVE' or
            emergency_head != result_head or head == result_head):
        return False
    converted_stop = (emergency_head is None and review and
        outcome == 'REVIEW_REQUIRED' and detail == 'EMERGENCY_STOP_ACTIVE' and
        requested_outcome == 'RETRY_REQUIRED' and
        requested_detail in RETRYABLE_ERRORS)
    emergency_close = (emergency_head is not None and
        requested_outcome == 'RETRY_REQUIRED' and
        requested_detail in RETRYABLE_ERRORS)
    ordinary_result = (requested_outcome == outcome and
        requested_detail == detail)
    if not (ordinary_result or converted_stop or emergency_close):
        return False
    expected_backoff = max(BACKOFF_SECONDS[attempts - 1], requested_delay)
    if completed:
        return (not review and ordinary_result and
            outcome == 'CHECKPOINT_COMMITTED' and detail == 'SUCCESS' and
            requested_delay == 0 and applied_backoff == 0 and
            not provider_backoff and next_allowed == 0 and
            _digest(checkpoint_head) and _digest(checkpoint_receipt) and
            checkpoint_head != expected_cursor and
            attempt_head != result_head)
    if review:
        if (outcome != 'REVIEW_REQUIRED' and
                outcome != 'CHECKPOINT_UNCERTAIN'):
            return False
        if (next_allowed != 0 or checkpoint_head is not None or
                checkpoint_receipt is not None):
            return False
        if emergency_close:
            return (attempts < MAX_RECONNECT_ATTEMPTS and
                applied_backoff == expected_backoff and
                attempt_head != result_head)
        return (applied_backoff == 0 and
            (converted_stop or
                (ordinary_result and requested_delay == 0 and
                    not provider_backoff)) and
            attempt_head != result_head)
    return (ordinary_result and outcome == 'RETRY_REQUIRED' and
        detail in RETRYABLE_ERRORS and attempts < MAX_RECONNECT_ATTEMPTS and
        applied_backoff == expected_backoff and
        next_allowed == evaluated + applied_backoff and
        checkpoint_head is None and checkpoint_receipt is None and
        attempt_head != result_head)


def _status_pair_lifecycle(cursor, budget):
    """Bind stable terminal budget state to its retained cursor head."""
    if budget['attempts'] == 0:
        return True
    if budget['pending_token'] is not None:
        return (cursor['head_sha256'] ==
            budget['expected_cursor_head_sha256'])
    if budget['completed']:
        return (cursor['initialized'] and
            cursor['local_cursor_checkpoint_committed'] and
            cursor['process_restart_recoverable'] and
            cursor['complete_review_counts_bound'] and
            cursor['head_sha256'] == budget['checkpoint_head_sha256'])
    return (cursor['head_sha256'] ==
        budget['expected_cursor_head_sha256'])


def _attempt_result(value, account_sha256):
    """Validate the saved-pair acknowledgement before using any field.

    The durable coordinator owns the detailed protocol validation.  This
    final installed-tool boundary still treats its aggregate return as an
    untrusted acknowledgement: malformed values must never escape as raw
    indexing/type errors or be rendered as partial success after a commit.
    """
    counts = ('initial_events_reviewed', 'replay_events_reviewed',
        'redelivered_events_deduplicated', 'new_events_processed')
    required_true = ('local_cursor_checkpoint_committed',
        'process_restart_recoverable', 'complete_review_counts_bound',
        'reconnect_budget_completed', 'actual_vault_access')
    required_false = ('automatic_retry',)
    if (type(value) is not dict or
            value.get('mode') !=
                'EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_ATTEMPT' or
            value.get('status') != 'REVIEW_REQUIRED' or
            value.get('account_sha256') != account_sha256 or
            value.get('source') not in ATTEMPT_SOURCES or
            not _digest(value.get('checkpoint_head_sha256')) or
            not _digest(value.get('reconnect_budget_head_sha256')) or
            any(type(value.get(key)) is not int or
                not 0 < value[key] <= 2000 for key in counts) or
            value.get('replay_events_reviewed') !=
                value.get('redelivered_events_deduplicated', 0) +
                value.get('new_events_processed', 0) or
            type(value.get('reconnect_attempt_number')) is not int or
            type(value.get('reconnect_attempt_cap')) is not int or
            not 1 <= value['reconnect_attempt_number'] <=
                value['reconnect_attempt_cap'] <= 100 or
            any(value.get(key) is not True for key in required_true) or
            any(value.get(key) is not False for key in required_false) or
            type(value.get('actual_provider_access')) is not bool or
            type(value.get('actual_stream_connection')) is not bool or
            (value['actual_stream_connection'] and
                not value['actual_provider_access'])):
        _fail()
    return value


def _private_path(value):
    try:
        path = Path(value)
        absolute, resolved = path.absolute(), path.resolve()
        protected = [ROOT]
        if getattr(sys, 'frozen', False):
            protected.append(Path(sys.executable).resolve().parent)
        if any(absolute.is_relative_to(folder) or
                resolved.is_relative_to(folder) for folder in protected):
            _fail()
        return path, resolved
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail()


def _private_input_scope(value):
    """Pin one regular private input and its initial file evidence."""
    try:
        path, resolved = _private_path(value)
        observed = path.lstat()
        pinned = path.absolute().parent.resolve() / path.name
        if stat.S_ISLNK(observed.st_mode) or pinned != resolved:
            _fail()
        identities = capture_file_identities((pinned,),
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
        fingerprints = capture_file_fingerprints((pinned,),
            (MAX_INPUT_BYTES,), 'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
        return pinned, identities, fingerprints
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail()


def _private_journal_path(value):
    """Pin one journal parent plus any safe existing leaf identity.

    Resolving the complete caller path would erase the distinction between a
    journal pathname and a leaf symlink to another file.  Resolve only the
    parent for the retained operational path, then require that it names the
    same target observed by the complete-path check.  An existing leaf must
    also already be one regular single-link file.  This keeps legitimate
    parent aliases pinned while refusing leaf aliases, directories, devices
    and multi-link files before any journal is inspected or created.  The
    resolved parent identity is sampled separately because leaf checks alone
    cannot detect replacement of an otherwise empty journal directory.
    """
    try:
        path, resolved = _private_path(value)
        try:
            observed = path.lstat()
        except FileNotFoundError:
            observed = None
        pinned = path.absolute().parent.resolve() / path.name
        if (pinned != resolved or
                observed is not None and
                (not stat.S_ISREG(observed.st_mode) or
                    observed.st_nlink != 1)):
            _fail()
        parent_identity = _journal_parent_identity(pinned.parent)
        identity = (None if observed is None else
            capture_file_identities((pinned,),
                'PAPER_ACTIVITY_SSE_ACCEPTANCE_JOURNAL_'))
        return pinned, identity, parent_identity
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail()


def _journal_parent_identity(path):
    """Sample one resolved journal directory without retaining a handle."""
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(observed.st_mode) or
            not stat.S_ISDIR(observed.st_mode)):
        _fail()
    return (str(path), observed.st_dev, observed.st_ino)


def _journal_parent_matches(path, identity, *, allow_created=False):
    """Require a sampled directory identity or its permitted creation state."""
    try:
        current = _journal_parent_identity(path)
    except Exception:
        return False
    if identity is not None:
        return current == identity
    return current is None or allow_created


def _journal_scope_matches(paths, identities, parent_identities, *,
        allow_created=False):
    """Recheck the sampled journal namespace without exposing path details.

    Existing parent directories and leaves remain bound to their sampled
    identities.  Missing paths must remain missing until the explicit
    pre-vault head check has completed; after the coordinator may legitimately
    initialize them, only the directory and regular single-link admission
    rules are repeated.  This is sampled pathname evidence, not an atomic
    descriptor or ACL boundary.
    """
    if (type(paths) is not tuple or type(identities) is not tuple or
            type(parent_identities) is not tuple or len(paths) != 3 or
            len(identities) != 3 or len(parent_identities) != 3):
        return False
    for path, identity, parent_identity in zip(paths, identities,
            parent_identities):
        if not _journal_parent_matches(path.parent, parent_identity,
                allow_created=allow_created):
            return False
        if identity is not None:
            try:
                require_file_identities((path,), identity,
                    'PAPER_ACTIVITY_SSE_ACCEPTANCE_JOURNAL_')
            except Exception:
                return False
            continue
        try:
            observed = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if (not allow_created or not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink != 1):
            return False
    return True


def _final_journal_scope(paths, identities, parent_identities):
    """Bind every journal directory and leaf after coordinator completion.

    The coordinator may legitimately create an initially missing cursor or
    reconnect-budget database.  Once it returns, creation is no longer part
    of the installed-tool acknowledgement path: promote every now-present
    safe leaf to an identity-bound member and require the whole namespace to
    survive a repeated check.  A still-missing leaf remains required to stay
    missing, so a late emergency-cut journal cannot appear unnoticed between
    the final cursor and budget reads.
    """
    if (type(paths) is not tuple or type(identities) is not tuple or
            type(parent_identities) is not tuple or len(paths) != 3 or
            len(identities) != 3 or len(parent_identities) != 3):
        _fail()
    promoted_parents = []
    for path, parent_identity in zip(paths, parent_identities):
        try:
            current = _journal_parent_identity(path.parent)
        except Exception:
            _fail()
        if parent_identity is not None and current != parent_identity:
            _fail()
        promoted_parents.append(current)
    promoted = []
    for path, identity in zip(paths, identities):
        if identity is not None:
            try:
                require_file_identities((path,), identity,
                    'PAPER_ACTIVITY_SSE_ACCEPTANCE_JOURNAL_')
            except Exception:
                _fail()
            promoted.append(identity)
            continue
        try:
            observed = path.lstat()
        except FileNotFoundError:
            promoted.append(None)
            continue
        except OSError:
            _fail()
        if (not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink != 1):
            _fail()
        try:
            promoted.append(capture_file_identities((path,),
                'PAPER_ACTIVITY_SSE_ACCEPTANCE_JOURNAL_'))
        except Exception:
            _fail()
    result = (paths, tuple(promoted), tuple(promoted_parents))
    if not _journal_scope_matches(*result):
        _fail()
    return result


def _final_storage_scope(cursor, budget):
    """Capture bounded cursor/budget content after coordinator completion."""
    paths = (cursor.path, budget.path)
    maximum_bytes = (MAX_CURSOR_DATABASE_BYTES, MAX_BUDGET_DATABASE_BYTES)
    try:
        identities = capture_file_identities(paths,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_FINAL_')
        fingerprints = capture_file_fingerprints(paths, maximum_bytes,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_FINAL_')
        require_file_identities(paths, identities,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_FINAL_')
        require_file_fingerprints(paths, maximum_bytes, fingerprints,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_FINAL_')
    except Exception:
        _fail()
    return (paths, maximum_bytes, identities, fingerprints)


def _final_storage_matches(scope):
    """Recheck the bounded final cursor/budget content without path detail."""
    if type(scope) is not tuple or len(scope) != 4:
        return False
    paths, maximum_bytes, identities, fingerprints = scope
    try:
        require_file_identities(paths, identities,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_FINAL_')
        require_file_fingerprints(paths, maximum_bytes, fingerprints,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_FINAL_')
    except Exception:
        return False
    return True


def _scope(expected_account_id, cursor_path, budget_path, order_journal_path):
    account = _uuid(expected_account_id)
    cursor, cursor_identity, cursor_parent = _private_journal_path(cursor_path)
    budget, budget_identity, budget_parent = _private_journal_path(budget_path)
    order, order_identity, order_parent = _private_journal_path(
        order_journal_path)
    paths = (cursor, budget, order)
    identities = (cursor_identity, budget_identity, order_identity)
    parent_identities = (cursor_parent, budget_parent, order_parent)
    check_path_set(paths,
        'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
    if not _journal_scope_matches(paths, identities, parent_identities):
        _fail()
    # Retain the validated canonical targets, not caller-controlled aliases.
    # Otherwise a mutable parent symlink can redirect every operational
    # journal after the relationship check, hide an already-persisted
    # emergency latch and move later writes into a different directory.
    return (account, ActivitySSECursorStore(cursor, account),
        ActivitySSEReconnectBudget(budget, account), order,
        (paths, identities, parent_identities))


def _load_events(path):
    path, _ = _private_path(path)
    try:
        observed = path.lstat()
        if (stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink != 1 or
                not 1 <= observed.st_size <= MAX_INPUT_BYTES):
            _fail()
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if ((opened.st_dev, opened.st_ino, opened.st_size) !=
                    (observed.st_dev, observed.st_ino, observed.st_size)):
                _fail()
            raw = stream.read(MAX_INPUT_BYTES + 1)
            closed = os.fstat(stream.fileno())
        final = path.lstat()
        if (len(raw) > MAX_INPUT_BYTES or
                (closed.st_dev, closed.st_ino, closed.st_size) !=
                    (opened.st_dev, opened.st_ino, opened.st_size) or
                (final.st_dev, final.st_ino, final.st_size) !=
                    (observed.st_dev, observed.st_ino, observed.st_size)):
            _fail()

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    _fail()
                result[key] = value
            return result

        events = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=lambda _: _fail())
        if type(events) is not list or not events:
            _fail()
        return events
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError,
            MemoryError):
        _fail()


def _status_clock(clock):
    """Return one bounded wall-clock sample without breaking local status."""
    try:
        value = clock()
    except Exception:
        return None
    if (type(value) not in (int, float) or not math.isfinite(value) or
            not 0 <= value <= 1e12):
        return None
    return value


def _stop_sample(order_path):
    """Sample the independent cut leaf without reading or following it.

    Any observed leaf remains an active cut, including a directory, symlink or
    otherwise malformed entry.  Metadata is retained only for repeated local
    comparison so a pathname replacement cannot look like two equal boolean
    samples.  Ordinary inspection failure is indeterminate and therefore
    active/fail-closed to callers.
    """
    try:
        observed = emergency_intent.path(order_path).lstat()
    except FileNotFoundError:
        return ('MISSING',)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return ('PRESENT', stat.S_IFMT(observed.st_mode), observed.st_dev,
        observed.st_ino, observed.st_size, observed.st_mtime_ns,
        observed.st_ctime_ns, observed.st_nlink)


def _stop_sample_active(sample):
    return sample != ('MISSING',)


def _attempt_clock(clock):
    """Return one bounded admission timestamp or fail with a fixed result.

    Attempt mode must not let an untrusted clock callable expose private
    exception details or reach the credential/provider/storage boundary with
    an invalid timestamp.  Process-control signals deliberately remain
    independently observable.
    """
    try:
        value = clock()
    except Exception:
        _fail()
    if (type(value) not in (int, float) or not math.isfinite(value) or
            not 0 <= value <= 1e12):
        _fail()
    return value


def _status_file_sample(path, maximum_bytes, prefix):
    """Sample one optional private journal without surfacing path details."""
    try:
        if not path.exists():
            return ('MISSING',)
        return ('PRESENT',
            capture_file_identities((path,), prefix),
            capture_file_fingerprints((path,), (maximum_bytes,), prefix))
    except Exception:
        return None


def _attempt_head_preflight(cursor, budget, expected_cursor_head,
        expected_budget_head, evaluated_at):
    """Require one stable, eligible exact-head snapshot before credentials."""
    if not _digest(expected_cursor_head) or not _digest(expected_budget_head):
        _fail()
    try:
        cursor_before = _status_store_inspect(cursor, 'cursor')
        cursor_file_before = _status_file_sample(cursor.path,
            MAX_CURSOR_DATABASE_BYTES,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_CURSOR_')
        budget_before = _status_store_inspect(budget, 'budget')
        budget_file_before = _status_file_sample(budget.path,
            MAX_BUDGET_DATABASE_BYTES,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_BUDGET_')
        cursor_after = _status_store_inspect(cursor, 'cursor')
        cursor_file_after = _status_file_sample(cursor.path,
            MAX_CURSOR_DATABASE_BYTES,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_CURSOR_')
        budget_after = _status_store_inspect(budget, 'budget')
        budget_file_after = _status_file_sample(budget.path,
            MAX_BUDGET_DATABASE_BYTES,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_BUDGET_')
    except Exception:
        _fail()
    if (cursor_file_before is None or budget_file_before is None or
            cursor_before != cursor_after or budget_before != budget_after or
            cursor_file_before != cursor_file_after or
            budget_file_before != budget_file_after or
            not _status_pair_lifecycle(cursor_before, budget_before) or
            cursor_before.get('head_sha256') != expected_cursor_head or
            budget_before.get('head_sha256') != expected_budget_head or
            (cursor_before['initialized'] and
                not cursor_before['complete_review_counts_bound']) or
            budget_before['pending_token'] is not None or
            budget_before['review_required'] or budget_before['completed'] or
            budget_before['attempts'] >= budget_before['max_attempts'] or
            evaluated_at < budget_before['next_allowed_at']):
        _fail()


def _status_store_inspect(store, role):
    """Read one local-only store status without exposing private faults."""
    try:
        value = store.inspect()
    except Exception:
        _fail()
    if type(value) is not dict:
        _fail()
    if role == 'cursor':
        initialized = value.get('initialized')
        head = value.get('head_sha256')
        event_id = value.get('current_event_id')
        current = value.get('current_cursor_sha256')
        retained = value.get('retained_checkpoints')
        committed = value.get('local_cursor_checkpoint_committed')
        recoverable = value.get('process_restart_recoverable')
        complete = value.get('complete_review_counts_bound')
        if (set(value) != CURSOR_VIEW_KEYS or
                value.get('mode') !=
                'PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_HISTORY' or
                value.get('status') != 'REVIEW_REQUIRED' or
                value.get('account_sha256') != store.account_sha256 or
                value.get('read_only') is not True or
                any(value.get(flag) is not False for flag in CURSOR_FLAGS) or
                type(initialized) is not bool or
                not _digest(head) or
                (event_id is not None and
                    (type(event_id) is not str or
                        ULID.fullmatch(event_id) is None)) or
                (current is not None and not _digest(current)) or
                type(retained) is not int or
                not 0 <= retained <= MAX_CURSOR_CHECKPOINTS or
                type(committed) is not bool or
                type(recoverable) is not bool or
                type(complete) is not bool or
                initialized != (event_id is not None) or
                initialized != (current is not None) or
                initialized != (retained > 0) or
                initialized != committed or initialized != recoverable or
                (initialized and current != sha(event_id)) or
                (not initialized and (head != ZERO or complete)) or
                (initialized and head == ZERO)):
            _fail()
    elif role == 'budget':
        head = value.get('head_sha256')
        attempts = value.get('attempts')
        pending = value.get('pending_token')
        next_allowed = value.get('next_allowed_at')
        if (set(value) != BUDGET_VIEW_KEYS or
                value.get('mode') !=
                'PRIVATE_ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET' or
                value.get('status') != 'REVIEW_REQUIRED' or
                value.get('account_sha256') != store.account_sha256 or
                value.get('backoff_seconds') != BACKOFF_SECONDS or
                value.get('automatic_retry') is not False or
                value.get('automatic_sleep') is not False or
                any(value.get(flag) is not False for flag in BUDGET_FLAGS) or
                not _digest(head) or
                value.get('max_attempts') != MAX_RECONNECT_ATTEMPTS or
                type(attempts) is not int or
                not 0 <= attempts <= MAX_RECONNECT_ATTEMPTS or
                'pending_token' not in value or
                (pending is not None and not _digest(pending)) or
                (pending is not None and pending != head) or
                type(next_allowed) not in (int, float) or
                not math.isfinite(next_allowed) or next_allowed < 0 or
                any(type(value.get(field)) is not bool for field in
                    ('review_required', 'completed')) or
                not _status_budget_lifecycle(value)):
            _fail()
    else:
        _fail()
    return value


def status(*, expected_account_id, cursor_path, budget_path,
        order_journal_path, clock=time.time):
    _, cursor, budget, order, journal_scope = _scope(expected_account_id, cursor_path,
        budget_path, order_journal_path)
    journal_before = _journal_scope_matches(*journal_scope)
    clock_before = _status_clock(clock)
    stop_before = _stop_sample(order)
    cursor_before = _status_store_inspect(cursor, 'cursor')
    budget_before = _status_store_inspect(budget, 'budget')
    cursor_file_before = _status_file_sample(cursor.path,
        MAX_CURSOR_DATABASE_BYTES, 'PAPER_ACTIVITY_SSE_ACCEPTANCE_CURSOR_')
    budget_file_before = _status_file_sample(budget.path,
        MAX_BUDGET_DATABASE_BYTES, 'PAPER_ACTIVITY_SSE_ACCEPTANCE_BUDGET_')
    evaluated_at = _status_clock(clock)
    cursor_state = _status_store_inspect(cursor, 'cursor')
    budget_state = _status_store_inspect(budget, 'budget')
    cursor_file_after = _status_file_sample(cursor.path,
        MAX_CURSOR_DATABASE_BYTES, 'PAPER_ACTIVITY_SSE_ACCEPTANCE_CURSOR_')
    budget_file_after = _status_file_sample(budget.path,
        MAX_BUDGET_DATABASE_BYTES, 'PAPER_ACTIVITY_SSE_ACCEPTANCE_BUDGET_')
    stop_after = _stop_sample(order)
    journal_after = _journal_scope_matches(*journal_scope)
    pair_before = _status_pair_lifecycle(cursor_before, budget_before)
    pair_after = _status_pair_lifecycle(cursor_state, budget_state)
    if (cursor_before == cursor_state and budget_before == budget_state and
            not pair_before):
        _fail()
    clock_consistent = (clock_before is not None and
        evaluated_at is not None and evaluated_at >= clock_before)
    journal_consistent = journal_before and journal_after
    storage_consistent = (journal_consistent and
        cursor_file_before is not None and
        budget_file_before is not None and
        cursor_file_before == cursor_file_after and
        budget_file_before == budget_file_after)
    stop_consistent = (stop_before is not None and
        stop_before == stop_after)
    snapshot_consistent = (clock_consistent and storage_consistent and
        cursor_before == cursor_state and
        budget_before == budget_state and pair_before and pair_after and
        stop_consistent)
    stop = (_stop_sample_active(stop_before) or
        _stop_sample_active(stop_after))
    return dict(mode='PAPER_ACTIVITY_SSE_ACCEPTANCE_STATUS',
        status='REVIEW_REQUIRED', read_only=True,
        account_sha256=cursor_state['account_sha256'],
        cursor_initialized=cursor_state['initialized'],
        cursor_head_sha256=cursor_state['head_sha256'],
        cursor_sha256=cursor_state['current_cursor_sha256'],
        retained_checkpoints=cursor_state['retained_checkpoints'],
        complete_review_counts_bound=
            cursor_state['complete_review_counts_bound'],
        reconnect_budget_head_sha256=budget_state['head_sha256'],
        reconnect_attempts=budget_state['attempts'],
        reconnect_attempt_cap=budget_state['max_attempts'],
        reconnect_next_allowed_at=budget_state['next_allowed_at'],
        reconnect_pending=budget_state['pending_token'] is not None,
        reconnect_review_required=budget_state['review_required'],
        reconnect_completed=budget_state['completed'],
        emergency_cut_latch_active=stop,
        emergency_cut_scope_consistent=stop_consistent,
        status_evaluated_at=evaluated_at,
        status_clock_consistent=clock_consistent,
        journal_scope_consistent=journal_consistent,
        storage_snapshot_consistent=storage_consistent,
        snapshot_consistent=snapshot_consistent,
        one_attempt_eligible=(snapshot_consistent and not stop and
            (not cursor_state['initialized'] or
                cursor_state['complete_review_counts_bound']) and
            budget_state['pending_token'] is None and
            not budget_state['review_required'] and
            not budget_state['completed'] and
            budget_state['attempts'] < budget_state['max_attempts'] and
            evaluated_at >= budget_state['next_allowed_at']),
        automatic_sleep=False,
        actual_provider_access=False, actual_vault_access=False,
        **{flag: False for flag in FLAGS},
        limitations='Local replay and independent emergency-cut latch sample only. Eligibility requires the initially validated journal parent-directory and leaf namespace to remain identity-consistent, two valid non-regressing wall-clock samples, matching bounded identities and fingerprints across two cursor and reconnect-budget reads, and matching emergency-cut leaf metadata samples. No credential or provider access, permission grant, or broker/source/delivery/fee/funds/reconciliation assertion. Exact heads can change after this sampled status. A clear local latch is necessary but not sufficient authority for an attempt.')


def attempt(*, expected_account_id, initial_events_path, cursor_path,
        budget_path, order_journal_path, expected_cursor_head,
        expected_budget_head, reconnect_reason,
        confirm_reconnect_attempt=False, confirm_read_only_paper=False,
        confirm_cursor_checkpoint=False, opener=None, wall_clock=time.time,
        elapsed_clock=time.monotonic, outcome_clock=time.time):
    if (confirm_reconnect_attempt is not True or
            confirm_read_only_paper is not True or
            confirm_cursor_checkpoint is not True or
            reconnect_reason not in REASONS):
        _fail()
    account, cursor, budget, order, journal_scope = _scope(expected_account_id,
        cursor_path, budget_path, order_journal_path)
    initial_path, identities, fingerprints = _private_input_scope(
        initial_events_path)
    check_path_set((cursor.path, budget.path, order, initial_path),
        'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
    if not _journal_scope_matches(*journal_scope):
        _fail()

    def input_guard():
        try:
            require_file_identities((initial_path,), identities,
                'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
            require_file_fingerprints((initial_path,), (MAX_INPUT_BYTES,),
                fingerprints, 'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
            return True
        except Exception:
            _fail()

    input_guard()
    events = _load_events(initial_path)
    input_guard()

    coordinator_started = False
    coordinator_completed = False
    active_journal_scope = journal_scope

    def stop_active():
        try:
            input_guard()
            if not _journal_scope_matches(*active_journal_scope,
                    allow_created=(coordinator_started and
                        not coordinator_completed)):
                return True
            return _stop_sample_active(_stop_sample(order))
        except Exception:
            return True

    if stop_active():
        _fail()
    evaluated_at = _attempt_clock(wall_clock)
    _attempt_head_preflight(cursor, budget, expected_cursor_head,
        expected_budget_head, evaluated_at)
    if not _journal_scope_matches(*journal_scope):
        _fail()
    coordinator_started = True
    try:
        result = paper_credentials.collect_activity_sse_saved(
            expected_account_id=account, initial_events=events,
            reconnect_reason=reconnect_reason, cursor_store=cursor,
            expected_cursor_head=expected_cursor_head,
            reconnect_budget=budget,
            expected_budget_head=expected_budget_head,
            evaluated_at=evaluated_at,
            confirm_reconnect_attempt=confirm_reconnect_attempt,
            confirm_read_only_paper=confirm_read_only_paper,
            confirm_cursor_checkpoint=confirm_cursor_checkpoint,
            opener=opener, wall_clock=wall_clock,
            elapsed_clock=elapsed_clock, outcome_clock=outcome_clock,
            permission_check=input_guard,
            emergency_stop_check=stop_active)
    except Exception:
        # The coordinator owns the detailed private failure state.  The
        # installed direct API exposes only its fixed diagnostic regardless
        # of whether the bounded request had not started or its durable
        # checkpoint had already committed.  Process-control signals remain
        # independently observable and no retry is introduced here.
        _fail()
    coordinator_completed = True
    active_journal_scope = _final_journal_scope(*journal_scope)
    final_storage_scope = _final_storage_scope(cursor, budget)
    result = _attempt_result(result, cursor.account_sha256)
    # The coordinator has already made its one provider attempt and may have
    # committed the checkpoint.  Independently resample the external cut and
    # retained-input identity around every final journal read: a late stop or
    # input change must withhold the installed-tool success acknowledgement,
    # but must never cause another GET or rewrite the completed journals.
    if (not _journal_scope_matches(*active_journal_scope) or
            not _final_storage_matches(final_storage_scope) or
            stop_active()):
        _fail()
    after_cursor = _status_store_inspect(cursor, 'cursor')
    if not _final_storage_matches(final_storage_scope) or stop_active():
        _fail()
    after_budget = _status_store_inspect(budget, 'budget')
    if not _final_storage_matches(final_storage_scope) or stop_active():
        _fail()
    if (not _status_pair_lifecycle(after_cursor, after_budget) or
            after_budget['completed'] is not True or
            result['checkpoint_head_sha256'] != after_cursor['head_sha256'] or
            result['reconnect_budget_head_sha256'] !=
                after_budget['head_sha256'] or
            result['reconnect_attempt_number'] != after_budget['attempts'] or
            result['reconnect_attempt_cap'] != after_budget['max_attempts'] or
            result['reconnect_budget_completed'] is not True or
            result['automatic_retry'] is not False or
            result['actual_vault_access'] is not True):
        _fail()
    response = dict(mode='PAPER_ACTIVITY_SSE_ACCEPTANCE_ATTEMPT',
        status='REVIEW_REQUIRED', read_completed=True,
        source=result['source'], account_sha256=result['account_sha256'],
        initial_events_sha256=sha(events),
        initial_events_reviewed=result['initial_events_reviewed'],
        replay_events_reviewed=result['replay_events_reviewed'],
        redelivered_events_deduplicated=
            result['redelivered_events_deduplicated'],
        new_events_processed=result['new_events_processed'],
        reconnect_attempt_number=result['reconnect_attempt_number'],
        reconnect_attempt_cap=result['reconnect_attempt_cap'],
        cursor_head_sha256=after_cursor['head_sha256'],
        cursor_sha256=after_cursor['current_cursor_sha256'],
        reconnect_budget_head_sha256=after_budget['head_sha256'],
        local_cursor_checkpoint_committed=True,
        process_restart_recoverable=True,
        complete_review_counts_bound=
            result['complete_review_counts_bound'],
        emergency_stop_admission_fenced=True,
        actual_provider_access=result['actual_provider_access'],
        actual_stream_connection=result['actual_stream_connection'],
        actual_vault_access=True, automatic_retry=False,
        automatic_sleep=False, broker_connected=False,
        live_orders_enabled=False, money_movement_enabled=False,
        ready_for_routing=False, release_approved=False,
        limitations='One explicitly confirmed saved-credential paper Activity SSE GET and exact-head local checkpoint. Aggregate counts and hashes omit private event/order identities. This is not provider source attestation, complete historical delivery, fee coverage, broker reconciliation, spendable-funds proof, native power-loss proof, routing authority, live-trading permission or release approval. Preserve every private input and journal outside Git and review uncertainty without automatic retry or reset.')
    if not _final_storage_matches(final_storage_scope) or stop_active():
        _fail()
    return response


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_USAGE_ERROR\n')


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--cursor-store', required=True)
    parser.add_argument('--reconnect-budget', required=True)
    parser.add_argument('--order-journal', required=True)
    parser.add_argument('--initial-events')
    parser.add_argument('--expected-cursor-head')
    parser.add_argument('--expected-budget-head')
    parser.add_argument('--reconnect-reason', choices=sorted(REASONS))
    parser.add_argument('--confirm-reconnect-attempt', action='store_true')
    parser.add_argument('--confirm-read-only-paper', action='store_true')
    parser.add_argument('--confirm-cursor-checkpoint', action='store_true')
    parser.add_argument('--status-only', action='store_true')
    args = parser.parse_args()
    try:
        attempt_fields=(args.initial_events,args.expected_cursor_head,
            args.expected_budget_head,args.reconnect_reason)
        confirmations=(args.confirm_reconnect_attempt,
            args.confirm_read_only_paper,args.confirm_cursor_checkpoint)
        if args.status_only:
            if any(value is not None for value in attempt_fields) or any(confirmations):
                _fail()
            result=status(expected_account_id=args.expected_account,
                cursor_path=args.cursor_store,
                budget_path=args.reconnect_budget,
                order_journal_path=args.order_journal)
        else:
            if any(value is None for value in attempt_fields):
                _fail()
            result=attempt(expected_account_id=args.expected_account,
                initial_events_path=args.initial_events,
                cursor_path=args.cursor_store,
                budget_path=args.reconnect_budget,
                order_journal_path=args.order_journal,
                expected_cursor_head=args.expected_cursor_head,
                expected_budget_head=args.expected_budget_head,
                reconnect_reason=args.reconnect_reason,
                confirm_reconnect_attempt=args.confirm_reconnect_attempt,
                confirm_read_only_paper=args.confirm_read_only_paper,
                confirm_cursor_checkpoint=args.confirm_cursor_checkpoint)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error,
            MemoryError, RecursionError, KeyboardInterrupt):
        print('PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED')
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
