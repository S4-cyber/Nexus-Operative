"""Strict supplied Activity SSE review and private-output fences."""
import copy
import json

import pytest

from nexus import alpaca_activity_sse_review as subject


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'


def events():
    return [
        subject._event('01K00000000000000000000001',
            '11111111-1111-1111-1111-111111111111', 'TRD'),
        subject._event('01K00000000000000000000002',
            '22222222-2222-2222-2222-222222222222', 'FEE')]


def test_linked_fill_and_fee_are_reviewed_without_authority():
    result = subject.review(events(), expected_account_id=ACCOUNT)
    assert result['mode'] == 'SUPPLIED_ALPACA_ACTIVITY_SSE_LINK_REVIEW'
    assert result['read_only'] and result['events_reviewed'] == 2
    assert result['trades_reviewed'] == result['fees_reviewed'] == 1
    assert result['all_admitted_fees_parent_linked']
    assert result['event_cursor_order_strict']
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert subject.review(events(), expected_account_id=ACCOUNT) == result


@pytest.mark.parametrize(('mutation','code'), [
    (lambda rows: rows.reverse(), 'CURSOR_NOT_STRICTLY_INCREASING'),
    (lambda rows: rows[1].update(event_id=rows[0]['event_id']),
        'DUPLICATE_IDENTITY'),
    (lambda rows: rows[1]['details'].update(
        parent_id='33333333-3333-3333-3333-333333333333'),
        'FEE_PARENT_UNKNOWN'),
    (lambda rows: rows[1].update(previous_id=
        '11111111-1111-1111-1111-111111111111'),
        'CORRECTION_OR_BUST_REQUIRES_REVIEW'),
    (lambda rows: rows[1].update(activity_subtype='TAF'),
        'FEE_SUBTYPE_SCOPE'),
    (lambda rows: rows[1].update(executed_at=
        '2026-10-18T17:59:59.999999999Z'),
        'FEE_EXECUTION_PRECEDES_PARENT'),
    (lambda rows: rows[0]['details'].update(symbol='ETHUSD'),
        'TRADE_PRODUCT_OR_STATE_SCOPE'),
    (lambda rows: rows[0].update(net_amount='200'),
        'TRADE_CASH_DIRECTION'),
    (lambda rows: rows[1].update(account_id=
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
        'ACCOUNT_DISAGREEMENT'),
    (lambda rows: rows[1].update(details={'system_date':'2026-10-18'}),
        'FEE_DETAILS_OR_PARENT_SCOPE'),
    (lambda rows: rows[0].update(unexpected='value'), 'EVENT_SHAPE'),
])
def test_contradictions_and_unsupported_scope_fail_closed(mutation, code):
    rows = copy.deepcopy(events())
    mutation(rows)
    with pytest.raises(ValueError, match='ALPACA_ACTIVITY_SSE_' + code):
        subject.review(rows, expected_account_id=ACCOUNT)


def test_nested_or_oversized_values_are_rejected_before_hashing():
    rows = events()
    rows[0]['details']['symbol'] = {'nested': 'BTCUSD'}
    with pytest.raises(ValueError, match='FIELD_INVALID'):
        subject.review(rows, expected_account_id=ACCOUNT)
    rows = events()
    rows[0]['details']['symbol'] = 'B' * 4097
    with pytest.raises(ValueError, match='FIELD_CAP'):
        subject.review(rows, expected_account_id=ACCOUNT)


def test_huge_numeric_and_malformed_uuid_types_fail_with_bounded_codes():
    rows = events()
    rows[0]['qty'] = 1 << 1000
    with pytest.raises(ValueError, match='FIELD_INVALID'):
        subject.review(rows, expected_account_id=ACCOUNT)
    rows = events()
    rows[0]['account_id'] = 7
    with pytest.raises(ValueError, match='UUID_INVALID'):
        subject.review(rows, expected_account_id=ACCOUNT)
    with pytest.raises(ValueError, match='ACCOUNT_INVALID'):
        subject.review(events(), expected_account_id=7)


def test_fixture_is_aggregate_only_and_private_values_do_not_escape():
    result = subject.fixture_report()
    assert result['passed']
    encoded = json.dumps(result)
    assert all(token not in encoded for token in (
        '01234567', '11111111', '22222222', 'fixture_client',
        'SUPPLIED_ALPACA_ACTIVITY_SSE_LINK_REVIEW'))
