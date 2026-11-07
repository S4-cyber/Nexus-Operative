"""Exact POST/activity/account-evidence composition tests."""
import copy
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from nexus import paper_provider_post_reconciliation_review as subject
from nexus.alpaca_paper_contract import preview


def files(root):
    return {path.relative_to(root): path.read_bytes()
        for path in root.rglob('*') if path.is_file()}


class MutablePath:
    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return str(self.path)


def _rebind_account_review(result):
    review = result['account_evidence_review']
    scope = dict(policy=review['policy'],
        **{key: review[key] for key in subject.evidence.HEADS},
        accepted_transitions=review['accepted_transitions'],
        opening_manifest_sha256=review['opening_manifest_sha256'],
        closing_manifest_sha256=review['closing_manifest_sha256'],
        request_review_sha256=review['request_review_sha256'],
        inventory_review_sha256=review['inventory_review_sha256'])
    review['input_sha256'] = subject.sha(scope)
    result['account_evidence_sha256'] = review['input_sha256']


def _rebind_stability_guard(result):
    review = result['stability_guard_review']
    scope = {key: review[key] for key in (
        'policy', 'account_sha256', 'activity_head_sha256',
        'accepted_reconciliation_sha256', 'pre_opening_manifest_sha256',
        'opening_manifest_sha256', 'closing_manifest_sha256',
        'post_closing_manifest_sha256',
        'declared_guard_window_union_sha256')}
    review['input_sha256'] = subject.sha(scope)
    result['stability_guard_sha256'] = review['input_sha256']


def _rebind_outer_account(result):
    scope = dict(policy=result['policy'],
        **{key: result[key] for key in subject.evidence.HEADS},
        account_evidence_sha256=result['account_evidence_sha256'],
        stability_guard_sha256=result['stability_guard_sha256'],
        **{key: result[key] for key in (
            'pre_opening_manifest_sha256', 'opening_manifest_sha256',
            'closing_manifest_sha256', 'post_closing_manifest_sha256')})
    result['input_sha256'] = subject.sha(scope)


def _rebind_observation_review(result):
    accepted = result['stability_guard_review'][
        'accepted_reconciliation_review']
    review = accepted['observation_review']
    scope = {key: review[key] for key in (
        'policy', 'account_sha256', 'head_sha256',
        'opening_manifest_sha256', 'closing_manifest_sha256')}
    review['input_sha256'] = subject.sha(scope)
    accepted['observation_review_sha256'] = review['input_sha256']


def _rebind_retained_balance_review(result):
    review = result['stability_guard_review'][
        'accepted_reconciliation_review']['observation_review'][
            'retained_balance_review']
    scope = {key: review[key] for key in (
        'policy', 'account_sha256', 'head_sha256', 'opening_balances',
        'opening_at', 'observed_balances', 'observed_at')}
    review['input_sha256'] = subject.sha(scope)


def _rebind_accepted_reconciliation(result):
    review = result['stability_guard_review'][
        'accepted_reconciliation_review']
    scope = {key: review[key] for key in (
        'policy', 'account_sha256', 'pacing_head_sha256',
        'activity_head_sha256', 'acceptance_head_sha256',
        'declared_collection_window_union_sha256', 'accepted_transitions',
        'opening_manifest_sha256', 'closing_manifest_sha256',
        'observation_review_sha256')}
    review['input_sha256'] = subject.sha(scope)
    result['stability_guard_review'][
        'accepted_reconciliation_sha256'] = review['input_sha256']
    result['account_evidence_review'][
        'inventory_review_sha256'] = subject.sha(review)
    _rebind_account_review(result)
    _rebind_stability_guard(result)
    _rebind_outer_account(result)


def test_exact_post_activity_and_guarded_balance_evidence_bind_without_writes(tmp_path):
    options = subject._fixture(tmp_path)
    before = files(tmp_path)
    result = subject.review(**options)
    assert result['mode'] == 'PAPER_PROVIDER_POST_RECONCILIATION_REVIEW'
    assert result['status'] == 'REVIEW_REQUIRED' and result['read_only']
    assert result['post_activity_component_bound']
    assert result['account_evidence_component_bound']
    assert result['exact_post_activity_bindings']
    assert result['guarded_balance_evidence_bound']
    assert result['journals_rechecked_unchanged']
    assert result['journal_files_rechecked_unchanged']
    assert result['semantic_reader_failures_rechecked']
    assert result['post_attempts'] == 2
    assert result['exact_activity_bindings'] == 1
    assert result['exact_not_sent_bindings'] == 1
    assert result['original_requests_reviewed'] == 2
    assert result['explicit_fills_reviewed'] == 2
    assert result['inventory_arithmetic_evaluated']
    assert result['gross_only_difference_absent']
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert files(tmp_path) == before
    assert subject.review(**options) == result


def test_independently_named_replay_matches_complete_primary_report(tmp_path):
    args = subject._fixture(tmp_path)
    assert subject.replay(**args) == subject.review(**args)


def test_unresolved_post_outcome_refuses_before_balance_promotion(tmp_path):
    options = subject._fixture(tmp_path, resolve_unknown=False)
    before = files(tmp_path)
    with pytest.raises(ValueError, match='POST_ACTIVITY_INCOMPLETE'):
        subject.review(**options)
    assert files(tmp_path) == before


def test_stale_heads_and_related_aliases_fail_closed(tmp_path):
    options = subject._fixture(tmp_path)
    for field in ('expected_post_head', 'expected_request_head',
                  'expected_activity_head'):
        changed = dict(options, **{field: '0' * 64})
        with pytest.raises(ValueError, match='HEAD_CHANGED'):
            subject.review(**changed)
    changed = dict(options, pacing_path=options['journal'].path)
    with pytest.raises(ValueError, match='RELATED_PATHS_REQUIRED_DISTINCT'):
        subject.review(**changed)


def test_component_authority_or_guard_disagreement_refuses(tmp_path, monkeypatch):
    options = subject._fixture(tmp_path)
    original_post = subject.post_activity.review

    def authoritative(*args, **kwargs):
        result = original_post(*args, **kwargs)
        result['ready_for_routing'] = True
        return result

    monkeypatch.setattr(subject.post_activity, 'review', authoritative)
    with pytest.raises(ValueError, match='POST_ACTIVITY_INCOMPLETE'):
        subject.review(**options)
    monkeypatch.setattr(subject.post_activity, 'review', original_post)
    original_evidence = subject.evidence.review_with_stability_guards

    def unguarded(**kwargs):
        result = original_evidence(**kwargs)
        result['guard_windows_ordered'] = False
        return result

    monkeypatch.setattr(subject.evidence, 'review_with_stability_guards', unguarded)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


def test_component_cannot_claim_guarded_evidence_without_arithmetic(tmp_path, monkeypatch):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def blocked(**kwargs):
        result = original(**kwargs)
        result['inventory_arithmetic_evaluated'] = False
        result['gross_only_minus_observed'] = None
        return result

    monkeypatch.setattr(subject.evidence, 'review_with_stability_guards', blocked)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('residual', ('NaN', 'Infinity', '-Infinity'))
def test_nonfinite_gross_residual_fails_closed(tmp_path, monkeypatch,
        residual):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def nonfinite(**kwargs):
        result = original(**kwargs)
        result['gross_only_minus_observed'] = {
            'USD': residual, 'BTC': '0'}
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', nonfinite)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('gross', (
    {'USD': '0'},
    {'USD': '0', 'BTC': '0', 'private': '1'},
    {'USD': '1e3', 'BTC': '0'},
    {'USD': '0' * 65, 'BTC': '0'},
))
def test_malformed_gross_residual_shape_fails_closed(tmp_path, monkeypatch,
        gross):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result['gross_only_minus_observed'] = gross
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('post_attempts', None),
    ('post_attempts', 3),
    ('exact_activity_bindings', True),
    ('exact_not_sent_bindings', -1),
))
def test_malformed_post_component_projection_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.post_activity.review

    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        if value is None:
            result.pop(field)
        else:
            result[field] = value
        return result

    monkeypatch.setattr(subject.post_activity, 'review', malformed)
    with pytest.raises(ValueError, match='POST_ACTIVITY_INCOMPLETE'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('unresolved_unknown_attempts', 999),
    ('attempt_activity_disagreements', 999),
    ('unbound_request_activity_rows', 999),
    ('untracked_activity_orders', 999),
    ('unexpected_activity_blockers', 999),
    ('limitations', 'forged limitation'),
    ('private_extension', 'forged'),
))
def test_rehashed_post_activity_replay_disagreement_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.post_activity.review

    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        result[field] = value
        return result

    monkeypatch.setattr(subject.post_activity, 'review', malformed)
    with pytest.raises(ValueError, match='POST_ACTIVITY_INCOMPLETE'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('pacing_head_sha256', None),
    ('acceptance_head_sha256', {'private': 'shape'}),
    ('pre_opening_manifest_sha256', 'A' * 64),
    ('original_requests_reviewed', True),
    ('explicit_fills_reviewed', -1),
))
def test_malformed_account_component_projection_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        if value is None:
            result.pop(field)
        else:
            result[field] = value
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('component', 'diagnostic'), (
    ('post_activity', 'POST_ACTIVITY_INCOMPLETE'),
    ('account_evidence', 'ACCOUNT_EVIDENCE_INVALID'),
))
def test_non_mapping_component_envelope_fails_closed(
        tmp_path, monkeypatch, component, diagnostic):
    options = subject._fixture(tmp_path)
    if component == 'post_activity':
        monkeypatch.setattr(subject.post_activity, 'review',
            lambda *args, **kwargs: [])
    else:
        monkeypatch.setattr(subject.evidence,
            'review_with_stability_guards', lambda **kwargs: [])
    with pytest.raises(ValueError, match=diagnostic):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('mode', 'WRONG_COMPONENT_MODE'),
    ('journal_files_rechecked_unchanged', False),
    ('semantic_reader_failures_rechecked', False),
    ('uncertainty_preserved', False),
    ('private_uncanonical_value', 'object'),
))
def test_invalid_post_component_envelope_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.post_activity.review

    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        result[field] = object() if value == 'object' else value
        return result

    monkeypatch.setattr(subject.post_activity, 'review', malformed)
    with pytest.raises(ValueError, match='POST_ACTIVITY_INCOMPLETE'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('policy', 'wrong.account.policy'),
    ('private_uncanonical_value', 'object'),
))
def test_invalid_account_component_envelope_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result[field] = object() if value == 'object' else value
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'input_sha256',
    'account_evidence_sha256',
    'stability_guard_sha256',
))
def test_mismatched_account_component_binding_hash_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result[field] = '0' * 64
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    (None, []),
    ('mode', 'WRONG_COMPONENT_MODE'),
    ('policy', 'wrong.account.policy'),
    ('status', 'COMPLETE'),
    ('read_only', False),
    ('input_sha256', '0' * 64),
))
def test_invalid_nested_account_evidence_envelope_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        if field is None:
            result['account_evidence_review'] = value
        else:
            result['account_evidence_review'][field] = value
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', subject.evidence.FLAGS)
def test_rehashed_nested_account_evidence_authority_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def authoritative(**kwargs):
        result = original(**kwargs)
        result['account_evidence_review'][field] = True
        _rebind_account_review(result)
        _rebind_outer_account(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', authoritative)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('local_request_terms_agree', True),
    ('all_retained_request_origins_bound', True),
    ('request_agreement_blockers', ['FORGED_BLOCKER']),
    ('review_reasons', ['FORGED_REASON']),
    ('original_requests_reviewed', 999),
    ('explicit_fills_reviewed', 999),
    ('inventory_arithmetic_evaluated', False),
    ('gross_only_minus_observed', {'USD': '0', 'BTC': '999'}),
    ('limitations', 'forged limitation'),
    ('private_extension', 'forged'),
))
def test_rehashed_nested_account_evidence_replay_disagreement_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result['account_evidence_review'][field] = value
        _rebind_account_review(result)
        _rebind_outer_account(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    (None, []),
    ('mode', 'WRONG_COMPONENT_MODE'),
    ('policy', 'wrong.guard.policy'),
    ('status', 'COMPLETE'),
    ('read_only', False),
    ('input_sha256', '0' * 64),
))
def test_invalid_nested_stability_guard_envelope_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        if field is None:
            result['stability_guard_review'] = value
        else:
            result['stability_guard_review'][field] = value
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field',
    subject.RETAINED_RECONCILIATION_DENIALS)
def test_rehashed_nested_stability_guard_authority_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def authoritative(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review'][field] = True
        _rebind_stability_guard(result)
        _rebind_outer_account(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', authoritative)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('guard_windows', {'forged': {'started_at': 'forged'}}),
    ('guard_manifests_distinct', False),
    ('guard_windows_ordered', False),
    ('adjacent_guard_balances_stable', False),
    ('guard_declared_sources_agree', False),
    ('accepted_declared_windows_cover_guard_envelope', False),
    ('journals_rechecked_unchanged', False),
    ('journal_files_rechecked_unchanged', False),
    ('journal_file_identities_rechecked', False),
    ('journal_store_bindings_rechecked', False),
    ('semantic_reader_failures_rechecked', False),
    ('guard_retained_fill_events', 999),
    ('guard_collection_observed_fill_events', 999),
    ('guard_collection_observed_fee_events', 999),
    ('guard_retained_fee_date_overlaps', 999),
    ('limitations', 'forged limitation'),
    ('private_extension', 'forged'),
))
def test_rehashed_nested_stability_guard_replay_disagreement_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review'][field] = value
        _rebind_stability_guard(result)
        _rebind_outer_account(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('component', 'field'), (
    ('account_evidence_review', 'request_review_sha256'),
    ('account_evidence_review', 'inventory_review_sha256'),
    ('stability_guard_review', 'accepted_reconciliation_sha256'),
    ('stability_guard_review', 'declared_guard_window_union_sha256'),
))
def test_stale_nested_component_scope_hash_fails_closed(
        tmp_path, monkeypatch, component, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result[component][field] = '0' * 64
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('component', 'field'), (
    ('account_evidence_review', 'account_sha256'),
    ('account_evidence_review', 'opening_manifest_sha256'),
    ('stability_guard_review', 'activity_head_sha256'),
    ('stability_guard_review', 'post_closing_manifest_sha256'),
))
def test_rehashed_nested_component_disagreement_fails_closed(
        tmp_path, monkeypatch, component, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result[component][field] = '0' * 64
        if component == 'account_evidence_review':
            _rebind_account_review(result)
        else:
            _rebind_stability_guard(result)
        _rebind_outer_account(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'declared_collection_window_union_sha256',
    'accepted_transitions',
))
def test_stale_retained_reconciliation_scope_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        review = result['stability_guard_review'][
            'accepted_reconciliation_review']
        review[field] = ('0' * 64 if field.endswith('_sha256')
            else review[field] + 1)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('mode', 'WRONG_COMPONENT_MODE'),
    ('policy', 'wrong.reconciliation.policy'),
    ('status', 'COMPLETE'),
    ('read_only', False),
))
def test_rehashed_retained_reconciliation_identity_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review'][
            'accepted_reconciliation_review'][field] = value
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'pacing_head_sha256',
    'acceptance_head_sha256',
))
def test_rehashed_retained_reconciliation_projection_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review'][
            'accepted_reconciliation_review'][field] = '0' * 64
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    (None, []),
    ('mode', 'WRONG_OBSERVATION_MODE'),
))
def test_stale_retained_observation_envelope_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        accepted = result['stability_guard_review'][
            'accepted_reconciliation_review']
        if field is None:
            accepted['observation_review'] = value
        else:
            accepted['observation_review'][field] = value
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'head_sha256',
    'opening_manifest_sha256',
))
def test_rehashed_retained_observation_projection_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review']['accepted_reconciliation_review'][
            'observation_review'][field] = '0' * 64
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(
    'field', subject.RETAINED_RECONCILIATION_DENIALS)
def test_rehashed_retained_reconciliation_authority_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def authoritative(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review'][
            'accepted_reconciliation_review'][field] = True
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', authoritative)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(
    'field', subject.RETAINED_RECONCILIATION_ASSURANCES)
def test_rehashed_retained_reconciliation_assurance_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def incomplete(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review'][
            'accepted_reconciliation_review'][field] = False
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', incomplete)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('declared_window_coverage_scope', 'PROVIDER_RECEIVED_COVERAGE'),
    ('requested_window_boundaries_verified', True),
    ('acceptance_recovery_state', 'FORGED_ACCEPTED'),
    ('limitations', 'forged limitation'),
    ('private_extension', 'forged'),
))
def test_rehashed_retained_reconciliation_replay_disagreement_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result['stability_guard_review'][
            'accepted_reconciliation_review'][field] = value
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', subject.RETAINED_OBSERVATION_DENIALS)
def test_rehashed_retained_observation_authority_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def authoritative(**kwargs):
        result = original(**kwargs)
        observation = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review']
        observation[field] = True
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', authoritative)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'head_rechecked',
    'arithmetic_evaluated',
))
def test_rehashed_retained_observation_assurance_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def incomplete(**kwargs):
        result = original(**kwargs)
        observation = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review']
        observation[field] = False
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', incomplete)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('component', (
    'retained_balance_review',
    'retained_activity_review',
))
def test_rehashed_malformed_retained_observation_component_fails_closed(
        tmp_path, monkeypatch, component):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        observation = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review']
        observation[component] = []
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('mode', 'WRONG_BALANCE_MODE'),
    ('policy', 'wrong.balance.policy'),
    ('status', 'COMPLETE'),
    ('read_only', False),
    ('source', 'SOURCE_ATTESTED'),
    ('retained_activity_review_location', 'EMBEDDED_DUPLICATE'),
    ('account_sha256', '0' * 64),
    ('head_sha256', '0' * 64),
))
def test_rehashed_retained_balance_envelope_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        balance = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review'][
                'retained_balance_review']
        balance[field] = value
        _rebind_retained_balance_review(result)
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'head_rechecked',
    'arithmetic_evaluated',
))
def test_rehashed_retained_balance_assurance_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def incomplete(**kwargs):
        result = original(**kwargs)
        balance = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review'][
                'retained_balance_review']
        balance[field] = False
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', incomplete)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'admission_reserved', 'atomic_snapshot', 'source_coverage_verified',
    'fee_coverage_complete', 'fee_execution_links_verified',
    'holds_reconciled', 'broker_account_reconciled',
    'spendable_funds_verified', 'ledger_applied', 'ready_for_routing',
    'broker_connected', 'live_orders_enabled', 'automatic_retry',
    'automatic_continuation',
))
def test_rehashed_retained_balance_authority_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def authoritative(**kwargs):
        result = original(**kwargs)
        balance = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review'][
                'retained_balance_review']
        balance[field] = True
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', authoritative)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('mode', 'WRONG_ACTIVITY_MODE'),
    ('status', 'COMPLETE'),
    ('read_only', False),
    ('source', 'SOURCE_ATTESTED'),
    ('snapshot_selection', 'PROVIDER_SEQUENCE_ATTESTED'),
    ('normalization_evaluated', False),
    ('account_sha256', '0' * 64),
    ('head_sha256', '0' * 64),
))
def test_rehashed_retained_activity_envelope_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        activity = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review'][
                'retained_activity_review']
        activity[field] = value
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize('field', (
    'activity_coverage_verified', 'fee_coverage_complete',
    'broker_account_reconciled', 'spendable_funds_verified',
    'ledger_applied', 'broker_connected', 'ready_for_routing',
    'live_orders_enabled', 'automatic_continuation',
))
def test_rehashed_retained_activity_authority_fails_closed(
        tmp_path, monkeypatch, field):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def authoritative(**kwargs):
        result = original(**kwargs)
        activity = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review'][
                'retained_activity_review']
        activity[field] = True
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', authoritative)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('path', 'value'), (
    (('retained_batches',), 2),
    (('retained_activities',), 3),
    (('distinct_orders',), 2),
    (('distinct_order_snapshots',), 2),
    (('ambiguous_order_ids',), ['forged-order']),
    (('snapshot_revision_order_ids',), ['forged-order']),
    (('review_reasons',), []),
    (('limitations',), 'forged limitation'),
    (('private_extension',), 'forged'),
    (('activity_normalization', 'mode'), 'FORGED_NORMALIZATION'),
    (('activity_normalization', 'input_sha256'), '0' * 64),
    (('activity_normalization', 'executions'), []),
    (('activity_normalization', 'fee_debits'), [{'private': 'forged'}]),
    (('activity_normalization', 'recognized_fee_debits'),
        {'USD': '1', 'BTC': '0'}),
    (('activity_normalization', 'duplicate_activities_ignored'), 1),
    (('activity_normalization', 'review_reasons'), []),
    (('activity_normalization', 'limitations'), 'forged limitation'),
    (('collection_observation_review', 'retained_pages'), 2),
    (('collection_observation_review', 'declared_transports'),
        ['PAPER_HTTPS_READS']),
    (('requested_window_review', 'unique_windows'), 2),
    (('requested_window_review', 'components'), []),
))
def test_rehashed_retained_activity_replay_disagreement_fails_closed(
        tmp_path, monkeypatch, path, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        activity = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review'][
                'retained_activity_review']
        target = activity
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('path', 'value'), (
    (('observation_windows',), {'private': 'forged'}),
    (('sampled_balance_changes',), {'cash': '999'}),
    (('change_scope',), 'PROVIDER_SETTLEMENT_ATTESTED'),
    (('boundary_fill_events',), 1),
    (('boundary_fills_by_window',), {'opening': 1, 'closing': 0}),
    (('boundary_fill_samples',), [{'private': 'forged'}]),
    (('boundary_sample_limit',), 19),
    (('boundary_samples_truncated',), True),
    (('review_reasons',), []),
    (('source',), 'PROVIDER_ATTESTED'),
    (('limitations',), 'forged limitation'),
    (('private_extension',), 'forged'),
    (('retained_balance_review', 'review_reasons'), []),
    (('retained_balance_review', 'limitations'), 'forged limitation'),
    (('retained_balance_review', 'private_extension'), 'forged'),
))
def test_rehashed_retained_observation_replay_disagreement_fails_closed(
        tmp_path, monkeypatch, path, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        observation = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review']
        target = observation
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        _rebind_observation_review(result)
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('gross_execution_ending_balances', {'USD': '999', 'BTC': '2'}),
    ('gross_minus_supplied_observed', {'USD': '1', 'BTC': '0'}),
    ('gross_only_debits_covered', False),
    ('gross_only_minimum_group_end_balances', {'USD': '0', 'BTC': '0'}),
    ('execution_groups', 999),
    ('executions', True),
    ('equal_nanosecond_groups', -1),
    ('gross_only_shortfalls', [{'private': 'forged'}]),
    ('gross_only_shortfall_events', 1),
    ('shortfall_sample_limit', 19),
    ('shortfall_samples_truncated', True),
    ('all_retained_unallocated_fee_debits', {'USD': '1', 'BTC': '0'}),
    ('retained_fee_records_by_asset', {'USD': 1, 'BTC': 0}),
    ('residual_equals_all_retained_fee_debits', {'USD': False, 'BTC': False}),
    ('exact_interval_fee_debits', {'USD': '1', 'BTC': '0'}),
    ('exact_interval_fee_records_by_asset', {'USD': 1, 'BTC': 0}),
    ('date_only_fee_records_by_asset', {'USD': 1, 'BTC': 0}),
    ('exact_fee_records_outside_interval_by_asset', {'USD': 1, 'BTC': 0}),
    ('fee_temporal_scope_complete_by_asset', {'USD': True, 'BTC': True}),
    ('residual_equals_exact_interval_fee_debits', {'USD': True, 'BTC': True}),
    ('fee_scope', 'PROVIDER_POSTING_VERIFIED'),
    ('arithmetic_scope', 'FEES_ALLOCATED_AND_SPENT'),
    ('time_scope', 'BROKER_ATOMIC_SEQUENCE'),
    ('fee_posting_interval_verified', True),
    ('fee_debits_allocated_to_interval', True),
    ('fee_execution_links_verified', True),
    ('fee_reserved_funding_verified', True),
    ('private_extension', 'forged'),
))
def test_rehashed_retained_gross_fill_arithmetic_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        gross = result['stability_guard_review'][
            'accepted_reconciliation_review']['observation_review'][
                'retained_balance_review']['gross_fill_review']
        gross[field] = value
        _rebind_accepted_reconciliation(result)
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


@pytest.mark.parametrize(('field', 'value'), (
    ('gross_only_ending_balances', {'USD': '999', 'BTC': '2'}),
    ('gross_only_minus_observed', {'USD': '1', 'BTC': '0'}),
    ('gross_only_debits_covered', False),
    ('retained_unallocated_fee_debits', {'USD': '1', 'BTC': '0'}),
    ('retained_fee_record_counts', {'USD': 1, 'BTC': 0}),
))
def test_outer_gross_projection_disagreement_fails_closed(
        tmp_path, monkeypatch, field, value):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def malformed(**kwargs):
        result = original(**kwargs)
        result[field] = value
        return result

    monkeypatch.setattr(subject.evidence,
        'review_with_stability_guards', malformed)
    with pytest.raises(ValueError, match='ACCOUNT_EVIDENCE_INVALID'):
        subject.review(**options)


def test_final_request_race_refuses(tmp_path, monkeypatch):
    options = subject._fixture(tmp_path)
    original = subject.evidence.review_with_stability_guards

    def raced(**kwargs):
        result = original(**kwargs)
        asset = dict(symbol='BTC/USD', **{'class': 'crypto'}, status='active',
            tradable=True, fractionable=True, min_order_size='.5',
            min_trade_increment='.5', price_increment='.5')
        options['journal'].prepare(preview('post_reconciliation_race',
            'BTC-USD', 'BUY', '2', '100', asset,
            metadata_at=3000, evaluated_at=3001))
        return result

    monkeypatch.setattr(subject.evidence, 'review_with_stability_guards', raced)
    with pytest.raises(ValueError, match='FILE_SET_CHANGED'):
        subject.review(**options)


@pytest.mark.parametrize('target', ('post_budget', 'journal'))
def test_store_objects_cannot_retarget_to_identical_clones(
        tmp_path, monkeypatch, target):
    options = subject._fixture(tmp_path)
    store = options[target]
    original_path = store.path
    clone = tmp_path / f'{target}-clone.sqlite3'
    component = (subject.post_activity if target == 'post_budget'
        else subject.evidence)
    method = ('review' if target == 'post_budget'
        else 'review_with_stability_guards')
    original = getattr(component, method)

    def retarget(*args, **kwargs):
        result = original(*args, **kwargs)
        shutil.copyfile(original_path, clone)
        store.path = clone
        return result

    monkeypatch.setattr(component, method, retarget)
    with pytest.raises(ValueError, match='STORE_BINDING_CHANGED'):
        subject.review(**options)
    assert original_path.exists() and clone.exists()


@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_ancillary_paths_are_frozen_before_component_composition(
        tmp_path, monkeypatch, field):
    options=subject._fixture(tmp_path);original_path=Path(options[field])
    clone=tmp_path/f'{field}-clone.sqlite3'
    shutil.copyfile(original_path,clone)
    supplied=MutablePath(original_path);options[field]=supplied
    original_status=subject.acceptance_status
    original_posts=subject.post_activity.review
    original_evidence=subject.evidence.review_with_stability_guards
    status_seen=[];component_seen=[];calls=[]

    def retarget_status(**kwargs):
        status_seen.append(Path(kwargs[field]))
        result=original_status(**kwargs);calls.append(1)
        if len(calls)==1:supplied.path=clone
        return result

    def inspect_posts(*args,**kwargs):
        component_seen.append(Path(kwargs['account_bindings'][0][field]))
        return original_posts(*args,**kwargs)

    def inspect_evidence(**kwargs):
        component_seen.append(Path(kwargs[field]))
        return original_evidence(**kwargs)

    monkeypatch.setattr(subject,'acceptance_status',retarget_status)
    monkeypatch.setattr(subject.post_activity,'review',inspect_posts)
    monkeypatch.setattr(subject.evidence,'review_with_stability_guards',
        inspect_evidence)
    result=subject.review(**options)
    assert result['journals_rechecked_unchanged']
    assert status_seen and component_seen
    assert all(path==original_path for path in status_seen+component_seen)


@pytest.mark.parametrize('field', (
    'pacing_path', 'activity_store_path', 'acceptance_store_path'))
def test_ancillary_file_replacement_at_same_path_fails_closed(
        tmp_path, monkeypatch, field):
    options=subject._fixture(tmp_path);path=Path(options[field])
    clone=tmp_path/f'{field}-clone.sqlite3'
    backup=tmp_path/f'{field}-original.sqlite3'
    shutil.copyfile(path,clone)
    original=subject.acceptance_status;calls=[]

    def replace(**kwargs):
        result=original(**kwargs);calls.append(1)
        if len(calls)==1:
            path.replace(backup);shutil.copyfile(clone,path)
        return result

    monkeypatch.setattr(subject,'acceptance_status',replace)
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_POST_RECONCILIATION_FILE_IDENTITY_CHANGED'):
        subject.review(**options)
    assert path.read_bytes()==backup.read_bytes()==clone.read_bytes()


@pytest.mark.parametrize('target', (
    'post_budget', 'request_journal', 'pacing_path',
    'activity_store_path', 'acceptance_store_path'))
def test_same_head_journal_rewrite_fails_closed(
        tmp_path, monkeypatch, target):
    options = subject._fixture(tmp_path)
    paths = {
        'post_budget': options['post_budget'].path,
        'request_journal': options['journal'].path,
        'pacing_path': options['pacing_path'],
        'activity_store_path': options['activity_store_path'],
        'acceptance_store_path': options['acceptance_store_path'],
    }
    path = Path(paths[target])
    original = subject.post_activity.review

    def rewrite(*args, **kwargs):
        result = original(*args, **kwargs)
        with sqlite3.connect(path) as database:
            database.execute('PRAGMA user_version=1')
        return result

    monkeypatch.setattr(subject.post_activity, 'review', rewrite)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_RECONCILIATION_FILE_SET_CHANGED'):
        subject.review(**options)
    if target == 'post_budget':
        assert (options['post_budget'].inspect()['head_sha256'] ==
            options['expected_post_head'])


def _mutate_metadata_and_fail(path):
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    raise RuntimeError('injected provider reconciliation reader failure')


@pytest.mark.parametrize('target', (
    'post_budget', 'request_journal', 'pacing_path',
    'activity_store_path', 'acceptance_store_path'))
def test_initial_post_reader_failure_rechecks_all_journals(
        tmp_path, monkeypatch, target):
    options = subject._fixture(tmp_path)
    paths = {
        'post_budget': options['post_budget'].path,
        'request_journal': options['journal'].path,
        'pacing_path': options['pacing_path'],
        'activity_store_path': options['activity_store_path'],
        'acceptance_store_path': options['acceptance_store_path'],
    }
    before = files(tmp_path)
    original = options['post_budget'].inspect

    def failed_inspect():
        original()
        _mutate_metadata_and_fail(paths[target])

    monkeypatch.setattr(options['post_budget'], 'inspect', failed_inspect)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_RECONCILIATION_FILE_SET_CHANGED'):
        subject.review(**options)
    assert files(tmp_path) == before


@pytest.mark.parametrize('component', ('post_activity', 'account_evidence'))
def test_component_failure_rechecks_all_journals(tmp_path, monkeypatch,
        component):
    options = subject._fixture(tmp_path)
    before = files(tmp_path)
    if component == 'post_activity':
        original = subject.post_activity.review

        def failed_component(*args, **kwargs):
            original(*args, **kwargs)
            _mutate_metadata_and_fail(options['journal'].path)

        monkeypatch.setattr(subject.post_activity, 'review', failed_component)
    else:
        original = subject.evidence.review_with_stability_guards

        def failed_component(**kwargs):
            original(**kwargs)
            _mutate_metadata_and_fail(options['acceptance_store_path'])

        monkeypatch.setattr(subject.evidence,
            'review_with_stability_guards', failed_component)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_RECONCILIATION_FILE_SET_CHANGED'):
        subject.review(**options)
    assert files(tmp_path) == before


def test_final_status_reader_failure_rechecks_all_journals(
        tmp_path, monkeypatch):
    options = subject._fixture(tmp_path)
    before = files(tmp_path)
    original = subject.acceptance_status
    calls = 0

    def failed_status(**kwargs):
        nonlocal calls
        result = original(**kwargs)
        calls += 1
        if calls == 2:
            _mutate_metadata_and_fail(options['activity_store_path'])
        return result

    monkeypatch.setattr(subject, 'acceptance_status', failed_status)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_RECONCILIATION_FILE_SET_CHANGED'):
        subject.review(**options)
    assert calls == 2
    assert files(tmp_path) == before


def test_post_budget_rollback_sidecar_fails_before_account_review(
        tmp_path, monkeypatch):
    options = subject._fixture(tmp_path)
    original = subject.post_activity.review
    sidecar = Path(str(options['post_budget'].path) + '-journal')
    account_called = False

    def add_sidecar(*args, **kwargs):
        result = original(*args, **kwargs)
        sidecar.write_bytes(b'uncommitted')
        return result

    def account(**kwargs):
        nonlocal account_called
        account_called = True

    monkeypatch.setattr(subject.post_activity, 'review', add_sidecar)
    monkeypatch.setattr(subject.evidence, 'review_with_stability_guards',
        account)
    with pytest.raises(ValueError, match=
            'PAPER_PROVIDER_POST_RECONCILIATION_SQLITE_SIDECAR_REVIEW_REQUIRED'):
        subject.review(**options)
    assert not account_called


def test_fixture_is_private_and_never_authoritative():
    result = subject.fixture_report()
    assert result['passed']
    assert set(result) == subject.PUBLIC_FIXTURE_KEYS
    for key in ('exact_post_activity_bound',
                'guarded_account_evidence_bound',
                'accepted_history_replayed',
                'journal_file_fingerprints_rechecked',
                'semantic_reader_failures_rechecked',
                'gross_only_residual_checked',
                'unresolved_unknown_rejected',
                'repeat_review_matches', 'journals_unchanged',
                'output_never_authoritative'):
        assert result[key]
    assert all(result[flag] is False for flag in subject.FLAGS)
