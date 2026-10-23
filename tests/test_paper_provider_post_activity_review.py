"""Exact-head provider POST to accepted-activity composition tests."""
import json

import pytest

from nexus import paper_provider_post_activity_review as subject
from nexus.alpaca_paper_contract import preview
from nexus.broker_request_journal import BrokerRequestJournal


def prepared(tmp_path, *, resolve_unknown=True):
    budget, head, binding = subject._case(tmp_path, resolve_unknown=resolve_unknown)
    return budget, head, binding


def files(root):
    return {path.relative_to(root): path.read_bytes()
        for path in root.rglob('*') if path.is_file()}


def test_recovered_unknown_and_not_sent_bind_exact_accepted_history(tmp_path):
    budget, head, binding = prepared(tmp_path)
    before = files(tmp_path)
    result = subject.review(budget, expected_post_head=head,
        account_bindings=[binding])
    assert result['status'] == 'COMPLETE_LOCAL_POST_ACTIVITY_CONSISTENT'
    assert result['complete_local_post_activity_consistent']
    assert result['post_attempts'] == 2
    assert result['exact_activity_bindings'] == 1
    assert result['exact_not_sent_bindings'] == 1
    assert result['unresolved_unknown_attempts'] == 0
    assert result['reasons'] == []
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert files(tmp_path) == before
    encoded = json.dumps(result)
    assert 'nx_accept_fixture' not in encoded
    assert 'nx_not_sent' not in encoded
    assert binding['account_id'] not in encoded


def test_unresolved_unknown_stays_review_required_even_with_accepted_activity(tmp_path):
    budget, head, binding = prepared(tmp_path, resolve_unknown=False)
    result = subject.review(budget, expected_post_head=head,
        account_bindings=[binding])
    assert result['status'] == 'REVIEW_REQUIRED'
    assert not result['complete_local_post_activity_consistent']
    assert result['exact_activity_bindings'] == 0
    assert result['unresolved_unknown_attempts'] == 1
    assert result['reasons'] == ['UNRESOLVED_POST_OUTCOME']
    assert result['uncertainty_preserved'] and not result['automatic_retry']


def test_unbound_prepared_request_row_requires_review(tmp_path):
    budget, head, binding = prepared(tmp_path)
    journal = binding['journal']
    asset = dict(symbol='BTC/USD', **{'class': 'crypto'}, status='active',
        tradable=True, fractionable=True, min_order_size='.5',
        min_trade_increment='.5', price_increment='.5')
    journal.prepare(preview('nx_unbound', 'BTC-USD', 'BUY', '2', '100', asset,
        metadata_at=90000, evaluated_at=90001))
    binding['expected_request_head'] = journal.recovery()['head_sha256']
    result = subject.review(budget, expected_post_head=head,
        account_bindings=[binding])
    assert not result['complete_local_post_activity_consistent']
    assert result['unbound_request_activity_rows'] == 1
    assert 'ACTIVITY_HISTORY_DISAGREEMENT' in result['reasons']
    assert 'UNBOUND_REQUEST_ACTIVITY_ROW' in result['reasons']


def test_activity_row_disagreement_is_separate_from_post_history(monkeypatch,
        tmp_path):
    budget, head, binding = prepared(tmp_path)
    original = subject.activity.review
    def changed(**kwargs):
        result = original(**kwargs)
        rows = result['request_activity_review']['request_reviews']
        rows[0]['payload_sha256'] = 'f' * 64
        return result
    monkeypatch.setattr(subject.activity, 'review', changed)
    result = subject.review(budget, expected_post_head=head,
        account_bindings=[binding])
    assert not result['complete_local_post_activity_consistent']
    assert result['attempt_activity_disagreements'] == 1
    assert result['reasons'] == ['POST_ACTIVITY_BINDING_DISAGREEMENT']


def test_stale_post_head_and_aliased_paths_fail_before_mutation(tmp_path):
    budget, head, binding = prepared(tmp_path)
    before = files(tmp_path)
    with pytest.raises(ValueError, match='POST_HEAD_CHANGED'):
        subject.review(budget, expected_post_head='f' * 64,
            account_bindings=[binding])
    assert files(tmp_path) == before
    alias = dict(binding, pacing_path=binding['journal'].path)
    with pytest.raises(ValueError, match='RELATED_PATHS_REQUIRED_DISTINCT'):
        subject.review(budget, expected_post_head=head,
            account_bindings=[alias])


def test_request_change_after_activity_component_fails_final_recheck(
        tmp_path, monkeypatch):
    budget, head, binding = prepared(tmp_path)
    original = subject.activity.review
    def changed(**kwargs):
        result = original(**kwargs)
        asset = dict(symbol='BTC/USD', **{'class': 'crypto'}, status='active',
            tradable=True, fractionable=True, min_order_size='.5',
            min_trade_increment='.5', price_increment='.5')
        binding['journal'].prepare(preview('nx_raced', 'BTC-USD', 'BUY', '2',
            '100', asset, metadata_at=91000, evaluated_at=91001))
        return result
    monkeypatch.setattr(subject.activity, 'review', changed)
    with pytest.raises(ValueError, match='JOURNALS_CHANGED_DURING_REVIEW'):
        subject.review(budget, expected_post_head=head,
            account_bindings=[binding])


def test_account_scope_and_binding_shape_fail_closed(tmp_path):
    budget, head, binding = prepared(tmp_path)
    wrong = dict(binding, journal=BrokerRequestJournal(
        binding['journal'].path, 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'))
    with pytest.raises(ValueError, match='ACCOUNT_SCOPE_DISAGREEMENT'):
        subject.review(budget, expected_post_head=head,
            account_bindings=[wrong])
    malformed = dict(binding); malformed['extra'] = True
    with pytest.raises(ValueError, match='ACCOUNT_BINDING_INVALID'):
        subject.review(budget, expected_post_head=head,
            account_bindings=[malformed])


def test_path_guard_supports_full_seven_account_shape_and_rejects_aliases(
        tmp_path):
    paths = [tmp_path / 'post.sqlite3']
    for index in range(7):
        paths.extend(tmp_path / f'account-{index}-{name}.sqlite3'
            for name in ('request', 'pacing', 'activity', 'acceptance'))
    subject._check_related_paths(paths)
    paths[-1] = paths[1].with_name(paths[1].name + '-wal')
    with pytest.raises(ValueError, match='RELATED_PATHS_REQUIRED_DISTINCT'):
        subject._check_related_paths(paths)


def test_fixture_is_private_and_never_authoritative():
    result = subject.fixture_report()
    assert result['passed']
    assert result['recovered_unknown_bound_to_accepted_activity']
    assert result['not_sent_bound_without_activity']
    assert result['unresolved_unknown_requires_review']
    assert result['journals_unchanged']
    assert result['output_never_authoritative']
    assert all(result[flag] is False for flag in subject.FLAGS)
