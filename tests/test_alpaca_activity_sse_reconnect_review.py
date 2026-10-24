"""Supplied Activity SSE reconnect/cursor composition tests."""
import copy
import json

import pytest

from nexus import alpaca_activity_sse_reconnect_review as subject
from nexus.alpaca_activity_sse_review import _event


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'


def transcript():
    initial = [
        _event('01K00000000000000000000001',
            '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event('01K00000000000000000000002',
            '22222222-2222-2222-2222-222222222222', 'FEE')]
    trade = _event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333', 'TRD',
        details={**initial[0]['details'],
            'order_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id': 'fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    fee = _event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444', 'FEE',
        details={'parent_id': trade['ref_id'], 'system_date': '2026-10-18'},
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    return initial, [*copy.deepcopy(initial), trade, fee]


def call(initial, replay, **changes):
    args=dict(expected_account_id=ACCOUNT,
        persisted_event_id=initial[-1]['event_id'],
        reconnect_since_id=initial[-1]['event_id'],
        reconnect_reason='CONNECTION_DROPPED')
    args.update(changes)
    return subject.review(initial, replay, **args)


def test_exact_cursor_reuse_deduplicates_and_advances_without_authority():
    initial, replay = transcript()
    result = call(initial, replay)
    assert result['exact_since_id_reused']
    assert result['redelivered_events_deduplicated'] == 2
    assert result['new_events_processed'] == 2
    assert result['unique_events_reviewed'] == 4
    assert result['cursor_advanced'] and result['all_unique_fees_parent_linked']
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert call(initial, replay) == result


@pytest.mark.parametrize(('mutation','code'), [
    (lambda initial, replay, args: args.update(
        reconnect_since_id='01K00000000000000000000001'),
        'SINCE_ID_CURSOR_DISAGREEMENT'),
    (lambda initial, replay, args: args.update(
        persisted_event_id=initial[0]['event_id'],
        reconnect_since_id=initial[0]['event_id']),
        'PERSISTED_CURSOR_NOT_LAST_PROCESSED'),
    (lambda initial, replay, args: args.update(reconnect_reason='UNKNOWN'),
        'RECONNECT_REASON_INVALID'),
    (lambda initial, replay, args: replay[0].update(net_amount='-201'),
        'REDELIVERY_IDENTITY_OR_CONTENT_CONFLICT'),
    (lambda initial, replay, args: replay[0].update(
        ref_id='55555555-5555-5555-5555-555555555555'),
        'REDELIVERY_IDENTITY_OR_CONTENT_CONFLICT'),
    (lambda initial, replay, args: replay[2].update(
        event_id='01J00000000000000000000000'),
        'CURSOR_NOT_STRICTLY_INCREASING'),
    (lambda initial, replay, args: replay[3]['details'].update(
        parent_id='55555555-5555-5555-5555-555555555555'),
        'FEE_PARENT_UNKNOWN'),
])
def test_reconnect_contradictions_fail_closed(mutation, code):
    initial, replay = transcript()
    args=dict(expected_account_id=ACCOUNT,
        persisted_event_id=initial[-1]['event_id'],
        reconnect_since_id=initial[-1]['event_id'],
        reconnect_reason='CONNECTION_DROPPED')
    mutation(initial, replay, args)
    with pytest.raises(ValueError, match=code):
        subject.review(initial, replay, **args)


def test_reconnect_requires_both_redelivery_and_forward_progress():
    initial, replay = transcript()
    with pytest.raises(ValueError, match='NONVACUOUS_REDELIVERY_AND_ADVANCE'):
        call(initial, replay[2:])
    with pytest.raises(ValueError, match='NONVACUOUS_REDELIVERY_AND_ADVANCE'):
        call(initial, replay[:2])


def test_cross_segment_ref_reuse_with_new_event_id_is_refused():
    initial, replay = transcript()
    replay[0]['event_id'] = '01K00000000000000000000005'
    replay.sort(key=lambda row: row['event_id'])
    with pytest.raises(ValueError, match='REDELIVERY_IDENTITY_OR_CONTENT_CONFLICT'):
        call(initial, replay)


def test_new_identity_at_or_before_persisted_cursor_is_refused():
    initial, replay = transcript()
    replay[2]['event_id'] = '01J00000000000000000000000'
    replay = [replay[2], *replay[:2], replay[3]]
    with pytest.raises(ValueError, match='NEW_EVENT_NOT_AFTER_PERSISTED_CURSOR'):
        call(initial, replay)


def test_public_fixture_is_aggregate_only():
    result = subject.fixture_report()
    assert result['passed']
    encoded = json.dumps(result)
    assert all(token not in encoded for token in (
        '01234567', '11111111', '22222222', '33333333',
        'fixture_client', 'SUPPLIED_ALPACA_ACTIVITY_SSE_RECONNECT_REVIEW'))
