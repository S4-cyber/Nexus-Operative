"""Exact provider POST reconciliation to retained fee-reference composition."""
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from nexus import paper_provider_post_fee_reference_review as subject
from nexus.alpaca_paper_contract import preview
from nexus.broker_request_journal import BrokerRequestJournal


def snapshot(root):
    return {path.relative_to(root): path.read_bytes()
        for path in root.rglob('*') if path.is_file()}


class MutablePath:
    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return str(self.path)


def test_binds_every_retained_fee_reference_without_writes(tmp_path):
    args = subject._fixture(tmp_path)
    before = snapshot(tmp_path)
    result = subject.review(**args)
    assert result['post_reconciliation_bound']
    assert result['complete_accepted_history_replayed']
    assert result['accepted_receipt_partition_complete']
    assert result['retained_fee_records'] == 1
    assert result['explicitly_referenced_fee_records'] == 1
    assert result['date_only_fee_references'] == 1
    assert result['timestamped_fee_references'] == 0
    assert result['all_references_resolve_to_accepted_original_fills']
    assert result['journal_files_rechecked_unchanged']
    assert result['semantic_reader_failures_rechecked']
    assert result['reference_semantics_unverified']
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert subject.review(**args) == result
    assert snapshot(tmp_path) == before


def test_independently_named_replay_matches_complete_primary_report(tmp_path):
    args = subject._fixture(tmp_path)
    assert subject.replay(**args) == subject.review(**args)


def test_timestamped_fee_reference_at_or_after_fill_is_counted(tmp_path):
    args = subject._fixture(tmp_path, fee_mode='linked_timestamp')
    result = subject.review(**args)
    assert result['timestamped_fee_references'] == 1
    assert result['date_only_fee_references'] == 0


def test_timestamped_fee_reference_before_fill_fails_at_guard(tmp_path):
    args = subject._fixture(tmp_path, fee_mode='early_timestamp')
    with pytest.raises(ValueError,
            match='PAPER_RECONCILIATION_ACCEPTANCE_GUARD_RETAINED_FEE_TIMING_AMBIGUOUS'):
        subject.review(**args)


@pytest.mark.parametrize(('mode','code'), [
    (None, 'FEES_REQUIRED'),
    ('unreferenced', 'REFERENCE_REQUIRED'),
    ('unknown', 'REFERENCE_EXECUTION_UNKNOWN')])
def test_missing_or_unresolved_fee_references_fail_closed(tmp_path, mode, code):
    args = subject._fixture(tmp_path, fee_mode=mode)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='PAPER_PROVIDER_POST_FEE_REFERENCE_' + code):
        subject.review(**args)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('field', [
    'fee_coverage_complete', 'fee_execution_links_verified',
    'spendable_funds_verified', 'ready_for_routing'])
def test_component_authority_promotion_is_rejected(tmp_path, monkeypatch, field):
    args = subject._fixture(tmp_path)
    original = subject.reconciliation.review
    def promoted(**kwargs):
        result = original(**kwargs)
        result[field] = True
        return result
    monkeypatch.setattr(subject.reconciliation, 'review', promoted)
    with pytest.raises(ValueError, match='RECONCILIATION_INVALID'):
        subject.review(**args)


@pytest.mark.parametrize(('field', 'value'), (
    ('policy', 'forged.policy'),
    ('account_sha256', 'f' * 64),
    ('input_sha256', 'f' * 64),
    ('post_attempts', 999),
    ('original_requests_reviewed', 999),
    ('gross_only_difference_absent', False),
    ('uncertainty_preserved', False),
    ('limitations', 'forged limitation'),
    ('private_extension', 'forged'),
))
def test_reconciliation_requires_independent_complete_component_replay(
        tmp_path, monkeypatch, field, value):
    args = subject._fixture(tmp_path)
    original = subject.reconciliation.review

    def changed(**kwargs):
        result = original(**kwargs)
        result[field] = value
        return result

    monkeypatch.setattr(subject.reconciliation, 'review', changed)
    with pytest.raises(ValueError, match='RECONCILIATION_COMPONENT_INVALID'):
        subject.review(**args)


@pytest.mark.parametrize(('field', 'value'), (
    ('mode', 'FORGED_MODE'),
    ('status', 'FORGED_STATUS'),
    ('policy', 'forged.policy'),
    ('input_sha256', 'f' * 64),
    ('request_origin_bound_transitions', 999),
    ('limitations', 'forged limitation'),
    ('private_extension', 'forged'),
))
def test_accepted_activity_requires_independent_complete_component_replay(
        tmp_path, monkeypatch, field, value):
    args = subject._fixture(tmp_path)
    original = subject.accepted.review
    original_reconciliation = subject.reconciliation.review
    original_reconciliation_replay = subject.reconciliation.replay

    def changed(**kwargs):
        result = original(**kwargs)
        result[field] = value
        return result

    def reconciliation_without_outer_mutation(**kwargs):
        with monkeypatch.context() as isolated:
            isolated.setattr(subject.accepted, 'review', original)
            return original_reconciliation(**kwargs)

    def replay_without_outer_mutation(**kwargs):
        with monkeypatch.context() as isolated:
            isolated.setattr(subject.accepted, 'review', original)
            return original_reconciliation_replay(**kwargs)

    monkeypatch.setattr(subject.reconciliation, 'review',
        reconciliation_without_outer_mutation)
    monkeypatch.setattr(subject.reconciliation, 'replay',
        replay_without_outer_mutation)
    monkeypatch.setattr(subject.accepted, 'review', changed)
    with pytest.raises(ValueError, match='ACCEPTED_ACTIVITY_COMPONENT_INVALID'):
        subject.review(**args)


def test_request_append_during_final_recheck_is_retained_and_refused(
        tmp_path, monkeypatch):
    args = subject._fixture(tmp_path)
    original = subject.reconciliation.review
    calls = []
    def changed(**kwargs):
        result = original(**kwargs)
        calls.append(1)
        if len(calls) == 1:
            asset = dict(symbol='BTC/USD', **{'class':'crypto'},
                status='active', tradable=True, fractionable=True,
                min_order_size='.5', min_trade_increment='.5',
                price_increment='.5')
            args['journal'].prepare(preview('later_fee_review_request',
                'BTC-USD', 'BUY', '1', '100', asset,
                metadata_at=3000, evaluated_at=3001))
        return result
    monkeypatch.setattr(subject.reconciliation, 'review', changed)
    with pytest.raises(ValueError):
        subject.review(**args)
    recovery = BrokerRequestJournal(args['journal'].path,
        args['account_id']).recovery()
    assert any(row['client_order_id'] == 'later_fee_review_request'
        for row in recovery['requests'])


@pytest.mark.parametrize('target', ('post_budget', 'journal'))
def test_store_objects_cannot_retarget_during_accepted_activity_review(
        tmp_path, monkeypatch, target):
    args=subject._fixture(tmp_path);store=args[target]
    original_path=store.path;clone=tmp_path/f'{target}-clone.sqlite3'
    original=subject.accepted.review

    def retarget(**kwargs):
        result=original(**kwargs)
        shutil.copyfile(original_path,clone)
        store.path=clone
        return result

    monkeypatch.setattr(subject.accepted,'review',retarget)
    with pytest.raises(ValueError,match='STORE_BINDING_CHANGED'):
        subject.review(**args)
    assert original_path.exists() and clone.exists()


@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_ancillary_paths_are_frozen_before_nested_reviews(
        tmp_path, monkeypatch, field):
    args=subject._fixture(tmp_path);original_path=Path(args[field])
    clone=tmp_path/f'{field}-clone.sqlite3'
    shutil.copyfile(original_path,clone)
    supplied=MutablePath(original_path);args[field]=supplied
    original_reconciliation=subject.reconciliation.review
    original_accepted=subject.accepted.review
    calls=[];seen=[]

    def retarget(**kwargs):
        result=original_reconciliation(**kwargs);calls.append(1)
        if len(calls)==1:supplied.path=clone
        return result

    def inspect_path(**kwargs):
        seen.append(Path(kwargs[field]))
        return original_accepted(**kwargs)

    monkeypatch.setattr(subject.reconciliation,'review',retarget)
    monkeypatch.setattr(subject.accepted,'review',inspect_path)
    result=subject.review(**args)
    assert result['journals_rechecked_unchanged']
    assert seen and all(path==original_path for path in seen)


@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_ancillary_parent_aliases_are_resolved_before_nested_reviews(
        tmp_path, monkeypatch, field):
    args=subject._fixture(tmp_path);original_path=Path(args[field]).resolve()
    alias=tmp_path/'caller-alias-component';alias.mkdir()
    args[field]=alias/'..'/original_path.name
    original_reconciliation=subject.reconciliation.review
    original_accepted=subject.accepted.review
    seen=[]

    def inspect_reconciliation(**kwargs):
        seen.append(Path(kwargs[field]))
        return original_reconciliation(**kwargs)

    def inspect_accepted(**kwargs):
        seen.append(Path(kwargs[field]))
        return original_accepted(**kwargs)

    monkeypatch.setattr(subject.reconciliation,'review',inspect_reconciliation)
    monkeypatch.setattr(subject.accepted,'review',inspect_accepted)
    result=subject.review(**args)
    assert result['journals_rechecked_unchanged']
    assert seen and all(path==original_path for path in seen)


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
    original=subject.reconciliation.review

    def replace(**kwargs):
        result=original(**kwargs)
        path.replace(backup);shutil.copyfile(clone,path)
        return result

    monkeypatch.setattr(subject.reconciliation,'review',replace)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_FEE_REFERENCE_FILE_IDENTITY_CHANGED'):
        subject.review(**args)
    assert path.read_bytes()==backup.read_bytes()==clone.read_bytes()


@pytest.mark.parametrize('target', (
    'post_budget', 'journal', 'pacing_path', 'activity_store_path',
    'acceptance_store_path'))
def test_same_head_journal_rewrite_fails_closed(tmp_path, monkeypatch, target):
    args = subject._fixture(tmp_path)
    path = Path(args[target].path if target in ('post_budget', 'journal')
        else args[target])
    original = subject.reconciliation.review

    def rewrite(**kwargs):
        result = original(**kwargs)
        with sqlite3.connect(path) as database:
            database.execute('PRAGMA user_version=1')
        return result

    monkeypatch.setattr(subject.reconciliation, 'review', rewrite)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_FEE_REFERENCE_FILE_SET_CHANGED'):
        subject.review(**args)


def _mutate_metadata_and_fail(path):
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    raise RuntimeError('injected post fee reference reader failure')


@pytest.mark.parametrize('target', (
    'post_budget', 'journal', 'pacing_path', 'activity_store_path',
    'acceptance_store_path'))
def test_initial_reconciliation_failure_rechecks_all_journals(
        tmp_path, monkeypatch, target):
    args = subject._fixture(tmp_path)
    path = Path(args[target].path if target in ('post_budget', 'journal')
        else args[target])
    before = snapshot(tmp_path)
    original = subject.reconciliation.review

    def failed_reconciliation(**kwargs):
        original(**kwargs)
        _mutate_metadata_and_fail(path)

    monkeypatch.setattr(subject.reconciliation, 'review',
        failed_reconciliation)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_FEE_REFERENCE_FILE_SET_CHANGED'):
        subject.review(**args)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('reader', (
    'accepted_activity', 'retained_activity', 'acceptance_scopes',
    'repeated_reconciliation', 'final_retained', 'final_scopes'))
def test_later_reader_failure_rechecks_all_journals(
        tmp_path, monkeypatch, reader):
    args = subject._fixture(tmp_path)
    before = snapshot(tmp_path)
    if reader == 'accepted_activity':
        original = subject.accepted.review

        def failed(**kwargs):
            original(**kwargs)
            _mutate_metadata_and_fail(args['journal'].path)

        monkeypatch.setattr(subject.accepted, 'review', failed)
    elif reader in ('retained_activity', 'final_retained'):
        original = subject.BrokerActivityStore.inspect
        calls = 0

        def failed(store):
            nonlocal calls
            result = original(store)
            calls += 1
            if calls == (1 if reader == 'retained_activity' else 2):
                _mutate_metadata_and_fail(args['activity_store_path'])
            return result

        monkeypatch.setattr(subject.BrokerActivityStore, 'inspect', failed)
    elif reader in ('acceptance_scopes', 'final_scopes'):
        original = subject.PaperActivityAcceptanceStore.replay_scopes
        calls = 0

        def failed(store):
            nonlocal calls
            result = original(store)
            calls += 1
            if calls == (1 if reader == 'acceptance_scopes' else 2):
                _mutate_metadata_and_fail(args['acceptance_store_path'])
            return result

        monkeypatch.setattr(subject.PaperActivityAcceptanceStore,
            'replay_scopes', failed)
    else:
        original = subject.reconciliation.review
        calls = 0

        def failed(**kwargs):
            nonlocal calls
            result = original(**kwargs)
            calls += 1
            if calls == 2:
                _mutate_metadata_and_fail(args['post_budget'].path)
            return result

        monkeypatch.setattr(subject.reconciliation, 'review', failed)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_FEE_REFERENCE_FILE_SET_CHANGED'):
        subject.review(**args)
    assert snapshot(tmp_path) == before


def test_rollback_sidecar_fails_before_outer_activity_inspection(
        tmp_path, monkeypatch):
    args = subject._fixture(tmp_path)
    sidecar = Path(str(args['post_budget'].path) + '-journal')
    original = subject.reconciliation.review
    inspected = False

    def add_sidecar(**kwargs):
        result = original(**kwargs)
        sidecar.write_bytes(b'uncommitted')
        return result

    def activity_store(*args, **kwargs):
        nonlocal inspected
        inspected = True

    monkeypatch.setattr(subject.reconciliation, 'review', add_sidecar)
    monkeypatch.setattr(subject, 'BrokerActivityStore', activity_store)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_FEE_REFERENCE_SQLITE_SIDECAR_REVIEW_REQUIRED'):
        subject.review(**args)
    assert not inspected


def test_fixture_is_public_aggregate_only():
    result = subject.fixture_report()
    assert result['passed']
    assert set(result) == subject.PUBLIC_FIXTURE_KEYS
    assert all(result[flag] is False for flag in subject.FLAGS)
    encoded = json.dumps(result)
    assert all(token not in encoded for token in (
        'nx_accept_fixture', '01234567', 'retained_fee_reference_sha256',
        'fee_amount', '2026-10-18'))

@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_selected_leaf_symlink_cannot_be_hidden_by_parent_pinning(tmp_path, field):
    args = subject._fixture(tmp_path)
    selected = Path(args[field])
    link = tmp_path / (field + '-selected-leaf.sqlite3')
    try:
        link.symlink_to(selected)
    except (OSError, NotImplementedError):
        pytest.skip('symlink creation unavailable for this Windows profile')
    alias = tmp_path / (field + '-parent-alias')
    alias.mkdir()
    selected_alias = alias / '..' / link.name
    assert subject._frozen_path(selected_alias, 'TEST_PATH_INVALID') == link
    args[field] = selected_alias
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='FILE_IDENTITY_UNAVAILABLE'):
        subject.review(**args)
    assert snapshot(tmp_path) == before

@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_selected_leaf_replaced_with_symlink_during_semantic_read_fails_closed(
        tmp_path, monkeypatch, field):
    args = subject._fixture(tmp_path)
    selected = Path(args[field])
    backup = tmp_path / (field + '-original.sqlite3')
    staged_link = tmp_path / (field + '-replacement-link.sqlite3')
    try:
        staged_link.symlink_to(backup)
    except (OSError, NotImplementedError):
        pytest.skip('symlink creation unavailable for this Windows profile')
    original_bytes = selected.read_bytes()
    original = subject.reconciliation.review

    def replace(*args, **kwargs):
        result = original(*args, **kwargs)
        selected.replace(backup)
        staged_link.replace(selected)
        return result

    monkeypatch.setattr(subject.reconciliation, 'review', replace)
    with pytest.raises(ValueError, match='PAPER_PROVIDER_POST_FEE_REFERENCE_FILE_IDENTITY_CHANGED'):
        subject.review(**args)
    assert selected.is_symlink()
    assert backup.read_bytes() == original_bytes

@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_selected_hardlink_alias_is_rejected_before_semantic_read(tmp_path, field):
    args = subject._fixture(tmp_path)
    selected = Path(args[field])
    alias = tmp_path / (field + '-hardlink.sqlite3')
    try:
        alias.hardlink_to(selected)
    except (OSError, NotImplementedError):
        pytest.skip('hardlink creation unavailable for this test volume')
    parent = tmp_path / (field + '-hardlink-parent')
    parent.mkdir()
    supplied = parent / '..' / alias.name
    assert subject._frozen_path(supplied, 'TEST_PATH_INVALID') == alias
    args[field] = supplied
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='FILE_IDENTITY_UNAVAILABLE'):
        subject.review(**args)
    assert snapshot(tmp_path) == before
