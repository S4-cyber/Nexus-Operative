"""OFF-by-default one-attempt paper Activity SSE acceptance operator.

Status is local-only. Attempt mode uses a saved read-only paper credential
pair, a private retained initial segment, exact journal heads, and the
process-independent emergency-cut latch. It makes at most one bounded GET and
one cursor checkpoint; it never sleeps, retries, trades, moves funds, or
prints private events.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

from nexus import emergency_intent, paper_credentials
from nexus.alpaca_activity_sse_cursor_store import ActivitySSECursorStore
from nexus.alpaca_activity_sse_reconnect_budget import (
    ActivitySSEReconnectBudget, REASONS)
from nexus.alpaca_activity_sse_review import MAX_INPUT_BYTES
from nexus.alpaca_paper_contract import sha
from nexus.alpaca_paper_inspection import _uuid
from nexus.paper_acceptance_probe import FLAGS, ROOT
from nexus.sqlite_journal_guard import (capture_file_fingerprints,
    capture_file_identities, check_path_set, require_file_fingerprints,
    require_file_identities)


def _fail():
    raise ValueError('PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED') from None


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


def _scope(expected_account_id, cursor_path, budget_path, order_journal_path):
    account = _uuid(expected_account_id)
    cursor, cursor_resolved = _private_path(cursor_path)
    budget, budget_resolved = _private_path(budget_path)
    order, order_resolved = _private_path(order_journal_path)
    check_path_set((cursor_resolved, budget_resolved, order_resolved),
        'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
    return (account, ActivitySSECursorStore(cursor, account),
        ActivitySSEReconnectBudget(budget, account), order)


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


def status(*, expected_account_id, cursor_path, budget_path,
        order_journal_path, clock=time.time):
    _, cursor, budget, order = _scope(expected_account_id, cursor_path,
        budget_path, order_journal_path)
    cursor_state, budget_state = cursor.inspect(), budget.inspect()
    stop = emergency_intent.requested(order)
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
        reconnect_next_allowed_at=budget_state['next_allowed_at'],
        reconnect_pending=budget_state['pending_token'] is not None,
        reconnect_review_required=budget_state['review_required'],
        reconnect_completed=budget_state['completed'],
        emergency_cut_latch_active=stop,
        one_attempt_eligible=(not stop and
            budget_state['pending_token'] is None and
            not budget_state['review_required'] and
            not budget_state['completed'] and
            clock() >= budget_state['next_allowed_at']),
        automatic_sleep=False,
        actual_provider_access=False, actual_vault_access=False,
        **{flag: False for flag in FLAGS},
        limitations='Local replay and independent emergency-cut latch sample only. No credential or provider access, permission grant, or broker/source/delivery/fee/funds/reconciliation assertion. Exact heads can change after this sampled status. A clear local latch is necessary but not sufficient authority for an attempt.')


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
    account, cursor, budget, order = _scope(expected_account_id,
        cursor_path, budget_path, order_journal_path)
    initial_path, initial_resolved = _private_path(initial_events_path)
    check_path_set((cursor.path, budget.path, order, initial_resolved),
        'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
    identities = capture_file_identities((initial_path,),
        'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
    fingerprints = capture_file_fingerprints((initial_path,),
        (MAX_INPUT_BYTES,), 'PAPER_ACTIVITY_SSE_ACCEPTANCE_')

    def input_guard():
        require_file_identities((initial_path,), identities,
            'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
        require_file_fingerprints((initial_path,), (MAX_INPUT_BYTES,),
            fingerprints, 'PAPER_ACTIVITY_SSE_ACCEPTANCE_')
        return True

    events = _load_events(initial_path)
    input_guard()

    def stop_active():
        try:
            input_guard()
            return emergency_intent.requested(order)
        except Exception:
            return True

    if stop_active():
        _fail()
    evaluated_at = wall_clock()
    result = paper_credentials.collect_activity_sse_saved(
        expected_account_id=account, initial_events=events,
        reconnect_reason=reconnect_reason, cursor_store=cursor,
        expected_cursor_head=expected_cursor_head,
        reconnect_budget=budget, expected_budget_head=expected_budget_head,
        evaluated_at=evaluated_at,
        confirm_reconnect_attempt=confirm_reconnect_attempt,
        confirm_read_only_paper=confirm_read_only_paper,
        confirm_cursor_checkpoint=confirm_cursor_checkpoint, opener=opener,
        wall_clock=wall_clock, elapsed_clock=elapsed_clock,
        outcome_clock=outcome_clock, permission_check=input_guard,
        emergency_stop_check=stop_active)
    input_guard()
    after_cursor, after_budget = cursor.inspect(), budget.inspect()
    if (result['checkpoint_head_sha256'] != after_cursor['head_sha256'] or
            result['reconnect_budget_head_sha256'] !=
                after_budget['head_sha256'] or
            result['reconnect_budget_completed'] is not True or
            result['automatic_retry'] is not False or
            result['actual_vault_access'] is not True):
        _fail()
    return dict(mode='PAPER_ACTIVITY_SSE_ACCEPTANCE_ATTEMPT',
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
