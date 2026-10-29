"""Bind one exact provider POST history to guarded account reconciliation.

This private offline composition reads caller-selected journals and supplied
observations only.  It performs no provider or vault IO, writes nothing and
grants no order, ledger, funding or release authority.
"""
from decimal import Decimal, InvalidOperation
from pathlib import Path

from nexus.alpaca_paper_contract import sha
from nexus import paper_account_evidence_review as evidence
from nexus import paper_provider_post_activity_review as post_activity
from nexus.broker_activity_store import MAX_DATABASE_BYTES as ACTIVITY_DATABASE_BYTES
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.broker_request_journal import MAX_DATABASE_BYTES as REQUEST_DATABASE_BYTES
from nexus.paper_activity_acceptance_store import MAX_DATABASE_BYTES as ACCEPTANCE_DATABASE_BYTES
from nexus.paper_activity_acceptance_probe import status as acceptance_status
from nexus.paper_read_pacing import MAX_DATABASE_BYTES as PACING_DATABASE_BYTES
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from nexus.paper_provider_post_budget import MAX_DATABASE_BYTES as POST_DATABASE_BYTES
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities,
    require_file_fingerprints, require_file_identities)


POLICY = 'nexus.paper-provider-post-reconciliation-review.v2'
JOURNAL_LIMITS = (POST_DATABASE_BYTES, REQUEST_DATABASE_BYTES,
    PACING_DATABASE_BYTES, ACTIVITY_DATABASE_BYTES,
    ACCEPTANCE_DATABASE_BYTES)
FLAGS = tuple(dict.fromkeys((*post_activity.FLAGS, *evidence.FLAGS,
    'provider_account_accepted', 'balance_reconciled',
    'spendable_funds_verified', 'automatic_retry', 'automatic_continuation')))


def _fail(code):
    raise ValueError('PAPER_PROVIDER_POST_RECONCILIATION_' + code) from None


def _binding(*, account_id, journal, expected_request_head, pacing_path,
             activity_store_path, acceptance_store_path,
             expected_activity_head):
    return dict(account_id=account_id, journal=journal,
        expected_request_head=expected_request_head, pacing_path=pacing_path,
        activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path,
        expected_activity_head=expected_activity_head)


def _store_binding(post_budget, journal):
    """Capture the two mutable store objects used by this composition."""
    return (post_budget.path, post_budget.provider_scope,
        post_budget.provider_digest, journal.path, journal.account_digest)


def _require_store_binding(post_budget, journal, expected):
    if (type(post_budget) is not PaperProviderPostBudget or
            type(journal) is not BrokerRequestJournal or
            _store_binding(post_budget, journal) != expected):
        _fail('STORE_BINDING_CHANGED')


def _require_bindings(post_budget, journal, store_binding, paths,
                      file_binding, file_fingerprints):
    _require_store_binding(post_budget, journal, store_binding)
    require_file_identities(paths, file_binding,
        'PAPER_PROVIDER_POST_RECONCILIATION_')
    require_file_fingerprints(paths, JOURNAL_LIMITS, file_fingerprints,
        'PAPER_PROVIDER_POST_RECONCILIATION_')


def _stable_reader(post_budget, journal, store_binding, paths,
                   file_binding, file_fingerprints):
    """Recheck the exact five-file binding whenever one reader exits."""
    def read(reader):
        try:
            value = reader()
        except BaseException:
            _require_bindings(post_budget, journal, store_binding, paths,
                file_binding, file_fingerprints)
            raise
        _require_bindings(post_budget, journal, store_binding, paths,
            file_binding, file_fingerprints)
        return value
    return read


def _frozen_path(value, code):
    try:
        return Path(value)
    except (TypeError, ValueError, OSError, AttributeError):
        _fail(code)


def review(post_budget, *, expected_post_head, account_id, journal,
           expected_request_head, pacing_path, activity_store_path,
           acceptance_store_path, expected_activity_head,
           pre_opening_observation, opening_observation, closing_observation,
           post_closing_observation):
    """Compose exact POST/activity history with four-observation evidence.

    A complete result means only that the selected local histories agree.  In
    particular, a zero gross residual is retained as a diagnostic and is never
    promoted to fee, hold, spendable-funds or broker-account acceptance.
    """
    if type(post_budget) is not PaperProviderPostBudget:
        _fail('POST_BUDGET_REQUIRED')
    if type(journal) is not BrokerRequestJournal:
        _fail('REQUEST_JOURNAL_REQUIRED')
    pacing_path = _frozen_path(pacing_path, 'PACING_PATH_INVALID')
    activity_store_path = _frozen_path(
        activity_store_path, 'ACTIVITY_STORE_PATH_INVALID')
    acceptance_store_path = _frozen_path(
        acceptance_store_path, 'ACCEPTANCE_STORE_PATH_INVALID')
    store_binding = _store_binding(post_budget, journal)
    journal_account = journal.account_digest
    binding = _binding(account_id=account_id, journal=journal,
        expected_request_head=expected_request_head, pacing_path=pacing_path,
        activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path,
        expected_activity_head=expected_activity_head)
    paths = (post_budget.path, journal.path, pacing_path,
        activity_store_path, acceptance_store_path)
    post_activity._check_related_paths(paths)
    file_binding = capture_file_identities(paths,
        'PAPER_PROVIDER_POST_RECONCILIATION_')
    file_fingerprints = capture_file_fingerprints(paths, JOURNAL_LIMITS,
        'PAPER_PROVIDER_POST_RECONCILIATION_')
    _require_bindings(post_budget, journal, store_binding, paths,
        file_binding, file_fingerprints)
    stable_read = _stable_reader(post_budget, journal, store_binding, paths,
        file_binding, file_fingerprints)

    first_post = stable_read(post_budget.inspect)
    attempts = stable_read(post_budget.history)
    recoveries = stable_read(post_budget.recovery_history)
    first_request = stable_read(journal.recovery)
    first_activity = stable_read(lambda: acceptance_status(
        expected_account_id=account_id, pacing_path=pacing_path,
        activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path))
    if first_post['head_sha256'] != expected_post_head:
        _fail('POST_HEAD_CHANGED')
    if first_request['head_sha256'] != expected_request_head:
        _fail('REQUEST_HEAD_CHANGED')
    if first_activity['retention_head_sha256'] != expected_activity_head:
        _fail('ACTIVITY_HEAD_CHANGED')

    posts = stable_read(lambda: post_activity.review(post_budget,
        expected_post_head=expected_post_head, account_bindings=[binding]))
    if (posts.get('status') != 'COMPLETE_LOCAL_POST_ACTIVITY_CONSISTENT' or
            posts.get('read_only') is not True or
            posts.get('complete_local_post_activity_consistent') is not True or
            posts.get('account_histories') != 1 or posts.get('reasons') != [] or
            any(posts.get(flag) is not False for flag in post_activity.FLAGS)):
        _fail('POST_ACTIVITY_INCOMPLETE')

    account = stable_read(lambda: evidence.review_with_stability_guards(
        expected_account_id=account_id, request_journal_path=journal.path,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path,
        expected_request_head=expected_request_head,
        expected_activity_head=expected_activity_head,
        pre_opening_observation=pre_opening_observation,
        opening_observation=opening_observation,
        closing_observation=closing_observation,
        post_closing_observation=post_closing_observation))
    required = ('complete_accepted_history_replayed', 'component_heads_agree',
        'stability_guard_bound', 'guard_manifests_distinct',
        'guard_windows_ordered', 'adjacent_guard_balances_stable',
        'guard_declared_sources_agree',
        'guard_retained_fill_events_absent',
        'guard_collection_observed_fill_events_absent',
        'guard_collection_observed_fee_events_absent',
        'guard_retained_fee_date_overlaps_absent',
        'accepted_declared_windows_cover_guard_envelope',
        'journals_rechecked_unchanged',
        'journal_files_rechecked_unchanged',
        'semantic_reader_failures_rechecked',
        'inventory_arithmetic_evaluated')
    if (account.get('mode') !=
            'PAPER_ACCOUNT_EVIDENCE_STABILITY_GUARD_REVIEW' or
            account.get('status') != 'REVIEW_REQUIRED' or
            account.get('read_only') is not True or
            account.get('account_sha256') != journal_account or
            account.get('request_head_sha256') != expected_request_head or
            account.get('activity_head_sha256') != expected_activity_head or
            any(account.get(key) is not True for key in required) or
            any(account.get(flag) is not False for flag in evidence.FLAGS)):
        _fail('ACCOUNT_EVIDENCE_INVALID')

    current_activity = stable_read(lambda: acceptance_status(
        expected_account_id=account_id, pacing_path=pacing_path,
        activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path))
    current_post = stable_read(post_budget.inspect)
    current_attempts = stable_read(post_budget.history)
    current_recoveries = stable_read(post_budget.recovery_history)
    current_request = stable_read(journal.recovery)
    if (current_post != first_post or current_attempts != attempts or
            current_recoveries != recoveries):
        _fail('POST_HISTORY_CHANGED')
    if current_request != first_request or current_activity != first_activity:
        _fail('JOURNALS_CHANGED_DURING_REVIEW')

    gross = account.get('gross_only_minus_observed')
    try:
        gross_absent = (type(gross) is dict and bool(gross) and
            all(type(value) is str and Decimal(value) == 0
                for value in gross.values()))
    except (InvalidOperation, ValueError, TypeError):
        _fail('ACCOUNT_EVIDENCE_INVALID')
    if not gross_absent:
        # Preserve a legitimate nonzero residual as diagnostic.  Only malformed
        # component arithmetic is invalid; disagreement never becomes authority.
        try:
            if (type(gross) is not dict or not gross or
                    any(type(value) is not str for value in gross.values())):
                _fail('ACCOUNT_EVIDENCE_INVALID')
            for value in gross.values():
                Decimal(value)
        except (InvalidOperation, ValueError, TypeError):
            _fail('ACCOUNT_EVIDENCE_INVALID')
    scope = dict(policy=POLICY, account_sha256=journal_account,
        post_head_sha256=expected_post_head,
        request_head_sha256=expected_request_head,
        activity_head_sha256=expected_activity_head,
        pacing_head_sha256=account['pacing_head_sha256'],
        acceptance_head_sha256=account['acceptance_head_sha256'],
        provider_post_activity_sha256=sha(posts),
        account_evidence_sha256=sha(account),
        pre_opening_manifest_sha256=account['pre_opening_manifest_sha256'],
        opening_manifest_sha256=account['opening_manifest_sha256'],
        closing_manifest_sha256=account['closing_manifest_sha256'],
        post_closing_manifest_sha256=account['post_closing_manifest_sha256'])
    return dict(mode='PAPER_PROVIDER_POST_RECONCILIATION_REVIEW',
        status='REVIEW_REQUIRED', read_only=True, **scope,
        input_sha256=sha(scope), post_activity_component_bound=True,
        account_evidence_component_bound=True,
        exact_post_activity_bindings=True,
        complete_accepted_history_replayed=True,
        guarded_balance_evidence_bound=True,
        journals_rechecked_unchanged=True,
        journal_files_rechecked_unchanged=True,
        semantic_reader_failures_rechecked=True,
        post_attempts=posts['post_attempts'],
        exact_activity_bindings=posts['exact_activity_bindings'],
        exact_not_sent_bindings=posts['exact_not_sent_bindings'],
        original_requests_reviewed=account['original_requests_reviewed'],
        explicit_fills_reviewed=account['explicit_fills_reviewed'],
        inventory_arithmetic_evaluated=account['inventory_arithmetic_evaluated'],
        gross_only_difference_absent=gross_absent,
        uncertainty_preserved=True, **{flag: False for flag in FLAGS},
        limitations='One private exact-head local composition binds the selected provider POST history to complete accepted request/activity replay and four supplied guarded balance observations. The five selected journal files are sampled repeatedly with bounded private fingerprints, not held as an atomic snapshot or under writer exclusion. A zero gross-only residual omits fee obligations and proves neither complete activity, fee links, holds, settled/spendable funds, provider source, broker-account acceptance nor profitability. This performs no provider/vault IO, retry, continuation, ledger write, routing, funding, live trading or release approval.')


def _fixture(root, *, resolve_unknown=True, fee_mode=None):
    from nexus.paper_observation_review import _fixture_observation

    budget, head, binding = post_activity._case(root,
        resolve_unknown=resolve_unknown, fee_mode=fee_mode)
    epoch = 1792195200 + 18 * 3600
    observations = dict(
        pre_opening_observation=_fixture_observation(
            binding['account_id'], '1000', '0', epoch - 4, epoch - 3),
        opening_observation=_fixture_observation(
            binding['account_id'], '1000', '0', epoch - 2, epoch - 1),
        closing_observation=_fixture_observation(
            binding['account_id'], '802', '2', epoch + 3, epoch + 4),
        post_closing_observation=_fixture_observation(
            binding['account_id'], '802', '2', epoch + 6, epoch + 7))
    return dict(post_budget=budget, expected_post_head=head,
        account_id=binding['account_id'], journal=binding['journal'],
        expected_request_head=binding['expected_request_head'],
        pacing_path=binding['pacing_path'],
        activity_store_path=binding['activity_store_path'],
        acceptance_store_path=binding['acceptance_store_path'],
        expected_activity_head=binding['expected_activity_head'],
        **observations)


def fixture_report():
    """Synthetic positive and unresolved controls; no external IO."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix='nexus-post-reconciliation-') as directory:
        root = Path(directory)
        options = _fixture(root / 'resolved')
        before = {path.relative_to(root): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        result = review(**options)
        repeated = review(**options)
        unchanged = before == {path.relative_to(root): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        unresolved = _fixture(root / 'unresolved', resolve_unknown=False)
        unresolved_rejected = False
        try:
            review(**unresolved)
        except ValueError as error:
            unresolved_rejected = (str(error) ==
                'PAPER_PROVIDER_POST_RECONCILIATION_POST_ACTIVITY_INCOMPLETE')
        checks = dict(
            exact_post_activity_bound=result['post_activity_component_bound'],
            guarded_account_evidence_bound=
                result['account_evidence_component_bound'] and
                result['guarded_balance_evidence_bound'],
            accepted_history_replayed=
                result['complete_accepted_history_replayed'],
            journal_file_fingerprints_rechecked=
                result['journal_files_rechecked_unchanged'],
            semantic_reader_failures_rechecked=
                result['semantic_reader_failures_rechecked'],
            gross_only_residual_checked=
                result['inventory_arithmetic_evaluated'] and
                result['gross_only_difference_absent'],
            unresolved_unknown_rejected=unresolved_rejected,
            repeat_review_matches=result == repeated,
            journals_unchanged=unchanged,
            output_never_authoritative=
                all(result[flag] is False for flag in FLAGS))
        return dict(source='SYNTHETIC_LOCAL_PROVIDER_POST_RECONCILIATION_FIXTURE',
            passed=all(checks.values()), **checks,
            **{flag: False for flag in FLAGS})
