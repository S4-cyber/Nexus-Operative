"""Exact POST/activity/account-evidence composition tests."""
import copy
import shutil
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
    with pytest.raises(ValueError, match='JOURNALS_CHANGED_DURING_REVIEW'):
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


def test_fixture_is_private_and_never_authoritative():
    result = subject.fixture_report()
    assert result['passed']
    for key in ('exact_post_activity_bound',
                'guarded_account_evidence_bound',
                'accepted_history_replayed',
                'gross_only_residual_checked',
                'unresolved_unknown_rejected',
                'repeat_review_matches', 'journals_unchanged',
                'output_never_authoritative'):
        assert result[key]
    assert all(result[flag] is False for flag in subject.FLAGS)
