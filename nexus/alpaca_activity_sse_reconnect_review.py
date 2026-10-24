"""Supplied Activity SSE reconnect/cursor consistency review; no transport.

This composes two finite supplied stream segments around one caller-supplied
persisted cursor.  It validates exact ``since_id`` reuse, immutable duplicate
redelivery by stable ``ref_id``, and forward-only checkpoint advancement.  It
does not persist a cursor, open an SSE connection, reconnect, or prove delivery.
"""
from nexus.alpaca_activity_sse_review import (
    FLAGS as ENVELOPE_FLAGS, POLICY as ENVELOPE_POLICY, SOURCES,
    ULID, _linked, _normalize)
from nexus.alpaca_paper_contract import sha


POLICY = 'nexus.supplied-alpaca-activity-sse-reconnect-review.v1'
REASONS = frozenset(('CONNECTION_DROPPED', 'SLOW_CONSUMER',
    'INTERNAL_SERVER_ERROR'))
FLAGS = (*ENVELOPE_FLAGS, 'cursor_persisted_durably',
    'actual_reconnect_performed', 'automatic_backoff_verified',
    'slow_consumer_recovery_verified', 'historical_replay_complete')


def _fail(code):
    raise ValueError('ALPACA_ACTIVITY_SSE_RECONNECT_' + code) from None


def _cursor(value, code):
    if not isinstance(value, str) or ULID.fullmatch(value) is None:
        _fail(code)
    return value


def _canonical(row):
    return sha(dict(row))


def review(initial_events, replay_events, *, expected_account_id,
        persisted_event_id, reconnect_since_id, reconnect_reason):
    """Review one supplied disconnect/replay boundary without performing I/O."""
    persisted = _cursor(persisted_event_id, 'PERSISTED_CURSOR_INVALID')
    since_id = _cursor(reconnect_since_id, 'SINCE_ID_INVALID')
    if persisted != since_id:
        _fail('SINCE_ID_CURSOR_DISAGREEMENT')
    if reconnect_reason not in REASONS:
        _fail('RECONNECT_REASON_INVALID')
    initial_account, initial = _normalize(initial_events,
        expected_account_id=expected_account_id)
    replay_account, replay = _normalize(replay_events,
        expected_account_id=expected_account_id)
    if initial_account != replay_account:
        _fail('ACCOUNT_DISAGREEMENT')
    if initial[-1]['event_id'] != persisted:
        _fail('PERSISTED_CURSOR_NOT_LAST_PROCESSED')

    by_event = {row['event_id']: row for row in initial}
    by_ref = {row['ref_id']: row for row in initial}
    unique = list(initial)
    duplicates = 0
    new_events = 0
    for row in replay:
        prior_event = by_event.get(row['event_id'])
        prior_ref = by_ref.get(row['ref_id'])
        if prior_event is not None or prior_ref is not None:
            if (prior_event is None or prior_ref is None or
                    prior_event['event_id'] != prior_ref['event_id'] or
                    _canonical(prior_event) != _canonical(row)):
                _fail('REDELIVERY_IDENTITY_OR_CONTENT_CONFLICT')
            duplicates += 1
            continue
        if row['event_id'] <= persisted:
            _fail('NEW_EVENT_NOT_AFTER_PERSISTED_CURSOR')
        by_event[row['event_id']] = row
        by_ref[row['ref_id']] = row
        unique.append(row)
        new_events += 1
    if duplicates == 0 or new_events == 0:
        _fail('NONVACUOUS_REDELIVERY_AND_ADVANCE_REQUIRED')
    trades, fees, linked_orders = _linked(unique)
    next_cursor = unique[-1]['event_id']
    if next_cursor <= persisted:
        _fail('CURSOR_DID_NOT_ADVANCE')
    canonical = dict(policy=POLICY, envelope_policy=ENVELOPE_POLICY,
        account_sha256=sha(initial_account), reconnect_reason=reconnect_reason,
        persisted_event_id=persisted, reconnect_since_id=since_id,
        initial=initial, replay=replay, next_cursor=next_cursor)
    return dict(mode='SUPPLIED_ALPACA_ACTIVITY_SSE_RECONNECT_REVIEW',
        status='REVIEW_REQUIRED', read_only=True,
        input_sha256=sha(canonical), account_sha256=sha(initial_account),
        reconnect_reason=reconnect_reason,
        initial_events_reviewed=len(initial),
        replay_events_reviewed=len(replay),
        redelivered_events_deduplicated=duplicates,
        new_events_processed=new_events, unique_events_reviewed=len(unique),
        unique_trades_reviewed=len(trades), unique_fees_reviewed=len(fees),
        linked_orders=len(linked_orders),
        persisted_cursor_sha256=sha(persisted),
        reconnect_since_id_sha256=sha(since_id),
        next_cursor_sha256=sha(next_cursor),
        exact_since_id_reused=True, duplicate_ref_ids_idempotent=True,
        duplicate_payloads_immutable=True, cursor_advanced=True,
        all_unique_fees_parent_linked=True,
        sources=SOURCES,
        **{flag: False for flag in FLAGS},
        limitations='Two supplied finite segments only. No cursor was written or recovered from durable storage; no SSE connection, authentication, entitlement check, disconnect, reconnect, backoff, slow-consumer event, historical replay or provider delivery occurred. Duplicate and cursor checks prove only local transcript consistency, not source, completeness, fee posting, holds, settlement, spendable funds, broker reconciliation, routing, live trading or release approval.')


def fixture_report():
    """Aggregate-only synthetic replay fixture; no provider or storage I/O."""
    from nexus.alpaca_activity_sse_review import _event
    account = '01234567-89ab-cdef-0123-456789abcdef'
    initial = [
        _event('01K00000000000000000000001',
            '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event('01K00000000000000000000002',
            '22222222-2222-2222-2222-222222222222', 'FEE')]
    later_trade = _event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333', 'TRD',
        details={**initial[0]['details'],
            'order_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id': 'fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    later_fee = _event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444', 'FEE',
        details=dict(parent_id=later_trade['ref_id'],
            system_date='2026-10-18'),
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    replay = [*initial, later_trade, later_fee]
    result = review(initial, replay, expected_account_id=account,
        persisted_event_id=initial[-1]['event_id'],
        reconnect_since_id=initial[-1]['event_id'],
        reconnect_reason='CONNECTION_DROPPED')
    checks = dict(exact_since_id_reused=result['exact_since_id_reused'],
        duplicate_redelivery_idempotent=
            result['redelivered_events_deduplicated'] == 2 and
            result['duplicate_ref_ids_idempotent'] and
            result['duplicate_payloads_immutable'],
        forward_cursor_advanced=result['cursor_advanced'] and
            result['new_events_processed'] == 2,
        fee_links_rechecked=result['all_unique_fees_parent_linked'],
        output_never_authoritative=all(result[flag] is False for flag in FLAGS))
    return dict(source='SYNTHETIC_SUPPLIED_ACTIVITY_SSE_RECONNECT_FIXTURE',
        passed=all(checks.values()), **checks,
        actual_provider_access=False, actual_vault_access=False,
        actual_storage_access=False, **{flag: False for flag in FLAGS})
