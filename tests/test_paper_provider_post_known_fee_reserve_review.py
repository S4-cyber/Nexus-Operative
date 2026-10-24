"""Provider POST/reference composition to retained known-fee reserve path."""
import json
from decimal import Decimal

import pytest

from nexus import paper_provider_post_known_fee_reserve_review as subject


def snapshot(root):
    return {path.relative_to(root): path.read_bytes()
        for path in root.rglob('*') if path.is_file()}


def test_known_fee_reserve_path_matches_supplied_closing_without_writes(tmp_path):
    args = subject._fixture(tmp_path)
    before = snapshot(tmp_path)
    result = subject.review(**args)
    assert result['fee_reference_component_bound']
    assert result['retained_known_fee_reservations_applied']
    assert result['retained_known_fee_debits_covered']
    assert result['known_fee_adjusted_closing_balances_match']
    assert result['accepted_orders_with_referenced_fees'] == 1
    assert result['execution_groups_reviewed'] == 2
    assert result['accepted_fills_reviewed'] == 2
    assert result['retained_fees_reviewed'] == 1
    assert result['retained_known_fee_debits'] == {'USD': '1', 'BTC': '0'}
    assert result['minimum_known_fee_reserved_balances'] == {
        'USD': '801', 'BTC': '0'}
    assert result['known_fee_reservation_is_retrospective']
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert subject.review(**args) == result
    assert snapshot(tmp_path) == before


def test_date_only_fee_is_reserved_without_inventing_posting_time(tmp_path):
    result = subject.review(**subject._fixture(tmp_path, fee_mode='linked'))
    assert result['retained_known_fee_reservations_applied']
    assert not result['known_fee_posting_verified']
    assert not result['fee_posting_interval_verified']


def test_zero_fee_record_cannot_make_reserve_path_vacuously_pass(tmp_path):
    from nexus.broker_activity_store import BrokerActivityStore
    args = subject._fixture(tmp_path)
    records = BrokerActivityStore(args['activity_store_path'],
        args['account_id']).inspect()['normalized_activities']
    for record in records:
        if record['kind'] != 'FILL':
            record['amount'] = '0'
    with pytest.raises(ValueError, match='POSITIVE_FEE_OBLIGATION_REQUIRED'):
        subject._reserve_path(records, {'USD': Decimal(1000),
            'BTC': Decimal(0)})


@pytest.mark.parametrize(('changes','code'), [
    ({'closing_cash': '802'}, 'KNOWN_FEE_ADJUSTED_BALANCE_DISAGREEMENT'),
    ({'opening_cash': '50', 'closing_cash': '1'},
        'KNOWN_FEE_DEBITS_NOT_COVERED'),
    ({'fee_mode': None}, 'PAPER_PROVIDER_POST_FEE_REFERENCE_FEES_REQUIRED')])
def test_balance_funding_and_missing_fee_fail_closed(tmp_path, changes, code):
    args = subject._fixture(tmp_path, **changes)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match=code):
        subject.review(**args)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('field', [
    'fee_coverage_complete', 'fee_execution_links_verified',
    'spendable_funds_verified', 'ready_for_routing'])
def test_reference_component_authority_promotion_is_rejected(
        tmp_path, monkeypatch, field):
    args = subject._fixture(tmp_path)
    original = subject.references.review
    def promoted(**kwargs):
        result = original(**kwargs)
        result[field] = True
        return result
    monkeypatch.setattr(subject.references, 'review', promoted)
    with pytest.raises(ValueError, match='REFERENCE_COMPONENT_INVALID'):
        subject.review(**args)


def test_concurrent_activity_change_is_retained_and_refused(tmp_path, monkeypatch):
    args = subject._fixture(tmp_path)
    original = subject._reserve_path
    def changed(records, opening):
        result = original(records, opening)
        from nexus.broker_activity_store import BrokerActivityStore
        BrokerActivityStore(args['activity_store_path'], args['account_id']).retain(
            [], [], after='2026-10-16T00:00:00Z',
            until='2026-10-17T00:00:00Z')
        return result
    monkeypatch.setattr(subject, '_reserve_path', changed)
    with pytest.raises(ValueError):
        subject.review(**args)


def test_fixture_is_public_aggregate_only():
    result = subject.fixture_report()
    assert result['passed']
    assert all(result[flag] is False for flag in subject.FLAGS)
    encoded = json.dumps(result)
    assert all(token not in encoded for token in (
        '01234567', 'retained_known_fee_path_sha256', '"retained_known_fee_debits":',
        'minimum_known_fee_reserved_balances', '2026-10-18'))
