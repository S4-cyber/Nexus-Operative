"""Explicit one-attempt Activity SSE reconnect and cursor checkpoint.

This private coordinator composes the bounded paper transport, strict wire and
envelope reviews, supplied reconnect review, and exact-head cursor journal.  A
caller must supply the last privately retained segment and opt into both the
read and checkpoint.  The function makes one GET and can commit one cursor
advance; it never retries, loops, backs off, applies a ledger, or places an
order.
"""
import copy
import json
import tempfile
from pathlib import Path

from nexus import alpaca_activity_sse_reconnect_review as reconnect
from nexus import alpaca_activity_sse_review as envelope
from nexus import alpaca_activity_sse_transport as transport
from nexus.alpaca_activity_sse_cursor_store import (
    ZERO, ActivitySSECursorStore)
from nexus.alpaca_paper_contract import canonical, sha


POLICY = 'nexus.explicit-alpaca-paper-activity-sse-checkpoint.v1'
FLAGS = ('provider_source_attested', 'stream_delivery_verified',
    'historical_replay_complete', 'native_power_loss_durability_verified',
    'fee_coverage_complete', 'broker_account_reconciled',
    'ready_for_routing', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved')


class ActivitySSECheckpointError(ValueError):
    """Fixed diagnostics only; never echo credentials, events, or cursors."""


def _fail(code):
    raise ActivitySSECheckpointError(
        'ALPACA_ACTIVITY_SSE_CHECKPOINT_' + code) from None


def _frozen_events(value, code):
    if type(value) is not list:
        _fail(code)
    try:
        frozen = copy.deepcopy(value)
        # Prove that the private input is canonical-JSON serializable before IO.
        json.loads(canonical(frozen))
    except Exception:
        _fail(code)
    return frozen


def _safety(permission_check, emergency_stop_check, credential_check):
    try:
        if permission_check() is not True:
            return False
        if emergency_stop_check() is not False:
            return False
        if credential_check() is not True:
            return False
    except Exception:
        return False
    return True


def _binding(store):
    return store.path, store.account_sha256


def _reservation(value):
    if value is None:
        return None
    if (type(value) is not str or len(value) != 64 or
            any(character not in '0123456789abcdef' for character in value)):
        _fail('RECONNECT_RESERVATION_INVALID')
    return value


def collect_and_checkpoint_once(api_key, api_secret, *, expected_account_id,
        initial_events, reconnect_reason, cursor_store, expected_cursor_head,
        confirm_read_only_paper=False, confirm_cursor_checkpoint=False,
        opener=None, wall_clock=None, elapsed_clock=None,
        permission_check=None, emergency_stop_check=None,
        credential_check=None, reconnect_reservation_token=None):
    """Perform one explicit resumption GET and exact-head cursor checkpoint.

    ``initial_events`` remains private caller evidence and must end at the
    journal's current cursor when the journal is initialized.  A fresh journal
    may establish its first checkpoint from that supplied segment.  Any
    disagreement before or after IO fails closed without a cursor mutation.
    """
    if confirm_read_only_paper is not True:
        _fail('READ_AUTHORIZATION_REQUIRED')
    if confirm_cursor_checkpoint is not True:
        _fail('CHECKPOINT_AUTHORIZATION_REQUIRED')
    if type(cursor_store) is not ActivitySSECursorStore:
        _fail('CURSOR_STORE_INVALID')
    reservation = _reservation(reconnect_reservation_token)
    if not all(callable(item) for item in (
            permission_check, emergency_stop_check, credential_check)):
        _fail('CHECK_INVALID')
    initial = _frozen_events(initial_events, 'INITIAL_EVENTS_INVALID')
    try:
        initial_review = envelope.review(initial,
            expected_account_id=expected_account_id)
        persisted = initial[-1]['event_id']
        if envelope.ULID.fullmatch(persisted) is None:
            _fail('INITIAL_CURSOR_INVALID')
    except ActivitySSECheckpointError:
        raise
    except Exception:
        _fail('INITIAL_EVENTS_INVALID')
    if initial_review['account_sha256'] != cursor_store.account_sha256:
        _fail('ACCOUNT_DISAGREEMENT')
    store_binding = _binding(cursor_store)
    if (type(expected_cursor_head) is not str or
            len(expected_cursor_head) != 64 or
            any(character not in '0123456789abcdef'
                for character in expected_cursor_head)):
        _fail('EXPECTED_HEAD_INVALID')
    try:
        before = cursor_store.inspect()
    except Exception:
        _fail('CURSOR_STORE_REVIEW_FAILED')
    if before['head_sha256'] != expected_cursor_head:
        _fail('CURSOR_HEAD_CHANGED')
    if (before['initialized'] and
            before['current_event_id'] != persisted):
        _fail('PERSISTED_CURSOR_DISAGREEMENT')
    if not _safety(permission_check, emergency_stop_check, credential_check):
        _fail('ADMISSION_REVOKED')

    options = dict(expected_account_id=expected_account_id,
        since_id=persisted, confirm_read_only_paper=True, opener=opener,
        permission_check=permission_check,
        emergency_stop_check=emergency_stop_check,
        credential_check=credential_check)
    if wall_clock is not None:
        options['wall_clock'] = wall_clock
    if elapsed_clock is not None:
        options['elapsed_clock'] = elapsed_clock
    transport_review, replay = transport._collect_once_private(
        api_key, api_secret, **options)
    if _binding(cursor_store) != store_binding:
        _fail('CURSOR_STORE_CHANGED')
    replay = _frozen_events(replay, 'REPLAY_EVENTS_INVALID')
    if (transport_review['gap_or_server_error_observed'] or
            transport_review['reconnect_review_required']):
        _fail('INTERRUPTED_SEGMENT_INELIGIBLE')
    try:
        checked = reconnect.review(initial, replay,
            expected_account_id=expected_account_id,
            persisted_event_id=persisted, reconnect_since_id=persisted,
            reconnect_reason=reconnect_reason)
    except Exception:
        _fail('RECONNECT_REVIEW_FAILED')
    try:
        unchanged = cursor_store.inspect()
    except Exception:
        _fail('CURSOR_STORE_REVIEW_FAILED')
    if (unchanged['head_sha256'] != expected_cursor_head or
            unchanged['current_event_id'] != before['current_event_id'] or
            _binding(cursor_store) != store_binding):
        _fail('CURSOR_HEAD_CHANGED')

    private_input_sha256 = sha(dict(initial=initial, replay=replay))
    review_input_sha256 = checked['input_sha256']

    def admission():
        if (not _safety(permission_check, emergency_stop_check,
                credential_check) or
                _binding(cursor_store) != store_binding or
                sha(dict(initial=initial, replay=replay)) !=
                    private_input_sha256):
            return None
        return review_input_sha256

    try:
        saved = cursor_store.checkpoint(checked,
            previous_cursor=persisted,
            next_cursor=replay[-1]['event_id'],
            expected_head=expected_cursor_head, admission=admission,
            reconnect_reservation_token=reservation)
    except ValueError:
        # Cursor-store diagnostics are already fixed and contain no private data.
        raise
    return dict(mode='EXPLICIT_ALPACA_ACTIVITY_SSE_CHECKPOINT',
        status='REVIEW_REQUIRED', policy=POLICY,
        source=transport_review['source'],
        account_sha256=initial_review['account_sha256'],
        transport_input_sha256=transport_review['input_sha256'],
        reconnect_input_sha256=checked['input_sha256'],
        checkpoint_head_sha256=saved['head_sha256'],
        checkpoint_receipt_sha256=saved['receipt_sha256'],
        checkpoint_cursor_sha256=saved['cursor_sha256'],
        initial_events_reviewed=checked['initial_events_reviewed'],
        replay_events_reviewed=checked['replay_events_reviewed'],
        redelivered_events_deduplicated=
            checked['redelivered_events_deduplicated'],
        new_events_processed=checked['new_events_processed'],
        single_get_attempt=True, single_reconnect_attempt=True,
        explicit_read_authorization=True,
        explicit_checkpoint_authorization=True,
        cursor_checkpoint_eligible=True, cursor_written=True,
        local_cursor_checkpoint_committed=True,
        process_restart_recoverable=True,
        reconnect_reservation_bound=saved['reconnect_reservation_bound'],
        complete_review_counts_bound=saved['complete_review_counts_bound'],
        duplicate_checkpoint=saved['duplicate'],
        actual_provider_access=transport_review['actual_provider_access'],
        actual_stream_connection=transport_review['actual_stream_connection'],
        actual_reconnect_performed=
            transport_review['actual_stream_connection'],
        actual_vault_access=False, automatic_retry=False,
        automatic_reconnect_performed=False, automatic_backoff_verified=False,
        ledger_applied=False, **{flag: False for flag in FLAGS},
        limitations='One explicitly authorized bounded resumption GET and one '
            'local exact-head cursor checkpoint. Private supplied prior events '
            'and returned replay events passed strict local duplicate, cursor, '
            'trade and fee-link review. This does not attest provider source, '
            'complete delivery or historical replay, native power-loss '
            'durability, fee coverage, balances, holds, funds, routing, live '
            'trading or release approval. There is no automatic retry, loop, '
            'backoff, ledger application, order submission or money movement.')


def fixture_report():
    """Disposable aggregate-only checkpoint fixture with injected transport."""
    import io
    from nexus.alpaca_activity_sse_review import _event

    account = '01234567-89ab-cdef-0123-456789abcdef'
    first = [_event('01K00000000000000000000001',
        '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event('01K00000000000000000000002',
        '22222222-2222-2222-2222-222222222222', 'FEE')]
    trade = _event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333', 'TRD',
        details={**first[0]['details'],
            'order_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id': 'fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    fee = _event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444', 'FEE',
        details={'parent_id': trade['ref_id'], 'system_date': '2026-10-18'},
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    first.append(copy.deepcopy(trade))
    raw = ''.join('data: ' + canonical(row) + '\n\n'
        for row in [copy.deepcopy(trade), fee]).encode()

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
        def __init__(self): self.requests = []
        def open(self, request, timeout):
            self.requests.append((request, timeout))
            return Response(request.full_url)

    with tempfile.TemporaryDirectory(prefix='nexus-sse-checkpoint-') as folder:
        store = ActivitySSECursorStore(Path(folder) / 'cursor.sqlite3', account)
        opener = Opener()
        result = collect_and_checkpoint_once(
            'fixture_key_only_123', 'fixture_secret_only_123',
            expected_account_id=account, initial_events=first,
            reconnect_reason='CONNECTION_DROPPED', cursor_store=store,
            expected_cursor_head=ZERO, confirm_read_only_paper=True,
            confirm_cursor_checkpoint=True, opener=opener,
            wall_clock=lambda: 1000, elapsed_clock=lambda: 50,
            permission_check=lambda: True,
            emergency_stop_check=lambda: False,
            credential_check=lambda: True)
        cold = ActivitySSECursorStore(store.path, account).inspect()
        encoded = json.dumps(result, sort_keys=True)
        checks = dict(single_injected_get=len(opener.requests) == 1,
            duplicate_redelivery_and_advance=
                result['redelivered_events_deduplicated'] == 1 and
                result['new_events_processed'] == 1,
            exact_head_checkpoint_committed=
                result['cursor_written'] and cold['initialized'] and
                cold['head_sha256'] == result['checkpoint_head_sha256'],
            no_private_identifier_or_credential_echo=all(token not in encoded
                for token in (account, '01K0000', '11111111',
                    'fixture_key_only_123', 'fixture_secret_only_123')),
            no_automatic_or_financial_authority=
                not result['automatic_retry'] and
                not result['automatic_reconnect_performed'] and
                not result['ledger_applied'] and
                all(result[flag] is False for flag in FLAGS))
    return dict(source='TEMPORARY_INJECTED_ACTIVITY_SSE_CHECKPOINT_FIXTURE',
        passed=all(checks.values()), **checks,
        actual_storage_retained=False, actual_provider_access=False,
        actual_stream_connection=False, actual_vault_access=False,
        **{flag: False for flag in FLAGS})
