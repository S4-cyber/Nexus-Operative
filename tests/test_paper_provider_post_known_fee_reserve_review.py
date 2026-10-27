"""Provider POST/reference composition to retained known-fee reserve path."""
import json
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from nexus import paper_provider_post_known_fee_reserve_review as subject


def snapshot(root):
    return {path.relative_to(root): path.read_bytes()
        for path in root.rglob('*') if path.is_file()}


class MutablePath:
    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return str(self.path)


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


@pytest.mark.parametrize('target', ('post_budget', 'journal'))
def test_store_objects_cannot_retarget_during_fee_reserve_path(
        tmp_path, monkeypatch, target):
    args=subject._fixture(tmp_path);store=args[target]
    original_path=store.path;clone=tmp_path/f'{target}-clone.sqlite3'
    original=subject._reserve_path

    def retarget(records,opening):
        result=original(records,opening)
        shutil.copyfile(original_path,clone)
        store.path=clone
        return result

    monkeypatch.setattr(subject,'_reserve_path',retarget)
    with pytest.raises(ValueError,match='STORE_BINDING_CHANGED'):
        subject.review(**args)
    assert original_path.exists() and clone.exists()


@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_ancillary_paths_are_frozen_before_reference_review(
        tmp_path, monkeypatch, field):
    args=subject._fixture(tmp_path);original_path=Path(args[field])
    clone=tmp_path/f'{field}-clone.sqlite3'
    shutil.copyfile(original_path,clone)
    supplied=MutablePath(original_path);args[field]=supplied
    original=subject.references.review
    calls=[];seen=[]

    def retarget(**kwargs):
        seen.append(Path(kwargs[field]))
        result=original(**kwargs);calls.append(1)
        if len(calls)==1:supplied.path=clone
        return result

    monkeypatch.setattr(subject.references,'review',retarget)
    result=subject.review(**args)
    assert result['journals_rechecked_unchanged']
    assert len(seen)>=2 and all(path==original_path for path in seen)


@pytest.mark.parametrize('target', (
    'post_budget', 'journal', 'pacing_path', 'activity_store_path',
    'acceptance_store_path'))
def test_selected_file_replacement_at_same_path_fails_closed(
        tmp_path, monkeypatch, target):
    args=subject._fixture(tmp_path)
    path=Path(args[target].path if target in ('post_budget','journal')
        else args[target])
    clone=tmp_path/f'{target}-replacement.sqlite3'
    backup=tmp_path/f'{target}-original.sqlite3'
    shutil.copyfile(path,clone)
    original=subject.references.review

    def replace(**kwargs):
        result=original(**kwargs)
        path.replace(backup);shutil.copyfile(clone,path)
        return result

    monkeypatch.setattr(subject.references,'review',replace)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_KNOWN_FEE_RESERVE_FILE_IDENTITY_CHANGED'):
        subject.review(**args)
    assert path.read_bytes()==backup.read_bytes()==clone.read_bytes()


@pytest.mark.parametrize('target', (
    'post_budget', 'journal', 'pacing_path', 'activity_store_path',
    'acceptance_store_path'))
def test_same_head_journal_rewrite_fails_closed(tmp_path, monkeypatch, target):
    args = subject._fixture(tmp_path)
    path = Path(args[target].path if target in ('post_budget', 'journal')
        else args[target])
    original = subject.references.review

    def rewrite(**kwargs):
        result = original(**kwargs)
        with sqlite3.connect(path) as database:
            database.execute('PRAGMA user_version=1')
        return result

    monkeypatch.setattr(subject.references, 'review', rewrite)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_KNOWN_FEE_RESERVE_FILE_SET_CHANGED'):
        subject.review(**args)


def test_rollback_sidecar_fails_before_reserve_path(tmp_path, monkeypatch):
    args = subject._fixture(tmp_path)
    sidecar = Path(str(args['post_budget'].path) + '-journal')
    original = subject.references.review
    reserve_called = False

    def add_sidecar(**kwargs):
        result = original(**kwargs)
        sidecar.write_bytes(b'uncommitted')
        return result

    def reserve(records, opening):
        nonlocal reserve_called
        reserve_called = True

    monkeypatch.setattr(subject.references, 'review', add_sidecar)
    monkeypatch.setattr(subject, '_reserve_path', reserve)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_KNOWN_FEE_RESERVE_SQLITE_SIDECAR_REVIEW_REQUIRED'):
        subject.review(**args)
    assert not reserve_called


def test_fixture_is_public_aggregate_only():
    result = subject.fixture_report()
    assert result['passed']
    assert all(result[flag] is False for flag in subject.FLAGS)
    encoded = json.dumps(result)
    assert all(token not in encoded for token in (
        '01234567', 'retained_known_fee_path_sha256', '"retained_known_fee_debits":',
        'minimum_known_fee_reserved_balances', '2026-10-18'))
