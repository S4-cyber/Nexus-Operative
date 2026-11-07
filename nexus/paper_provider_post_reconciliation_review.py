"""Bind one exact provider POST history to guarded account reconciliation.

This private offline composition reads caller-selected journals and supplied
observations only.  It performs no provider or vault IO, writes nothing and
grants no order, ledger, funding or release authority.
"""
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from nexus.alpaca_paper_contract import sha
from nexus import paper_account_evidence_review as evidence
from nexus import paper_observation_review as observation
from nexus import paper_provider_post_activity_review as post_activity
from nexus import retained_balance_review
from nexus.broker_activity_store import BrokerActivityStore
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
SHA256_PATTERN = re.compile(r'[0-9a-f]{64}')
RETAINED_RECONCILIATION_ASSURANCES = (
    'journals_rechecked_unchanged',
    'journal_files_rechecked_unchanged',
    'journal_file_identities_rechecked',
    'journal_store_bindings_rechecked',
    'semantic_reader_failures_rechecked',
    'complete_accepted_history_replayed',
    'legacy_baseline_absent',
    'complete_pacing_sessions_accepted',
    'accepted_pacing_operations_typed',
    'accepted_pacing_result_receipts_bound',
    'accepted_pacing_request_targets_bound',
    'complete_retained_batches_accepted',
    'complete_collection_observations_accepted',
    'all_collection_cursor_sequences_complete',
    'declared_collection_windows_cover_observation_envelope',
)
RETAINED_RECONCILIATION_DENIALS = (
    'admission_reserved', 'source_attested', 'source_coverage_verified',
    'activity_coverage_verified', 'fee_coverage_complete',
    'fee_execution_links_verified', 'balance_instants_verified',
    'observation_windows_verified_quiet', 'holds_reconciled',
    'broker_account_reconciled', 'spendable_funds_verified',
    'ledger_applied', 'ready_for_routing', 'broker_connected',
    'live_orders_enabled', 'money_movement_enabled', 'automatic_retry',
    'automatic_continuation', 'release_approved',
)
RETAINED_OBSERVATION_DENIALS = (
    'admission_reserved', 'source_attested', 'source_coverage_verified',
    'balance_instants_verified', 'observation_windows_verified_quiet',
    'atomic_snapshot', 'fee_coverage_complete',
    'fee_execution_links_verified', 'holds_reconciled',
    'broker_account_reconciled', 'spendable_funds_verified',
    'ledger_applied', 'ready_for_routing', 'broker_connected',
    'live_orders_enabled', 'automatic_retry', 'automatic_continuation',
)
RETAINED_BALANCE_DENIALS = (
    'admission_reserved', 'atomic_snapshot', 'source_coverage_verified',
    'fee_coverage_complete', 'fee_execution_links_verified',
    'holds_reconciled', 'broker_account_reconciled',
    'spendable_funds_verified', 'ledger_applied', 'ready_for_routing',
    'broker_connected', 'live_orders_enabled', 'automatic_retry',
    'automatic_continuation',
)
RETAINED_ACTIVITY_DENIALS = (
    'activity_coverage_verified', 'fee_coverage_complete',
    'broker_account_reconciled', 'spendable_funds_verified',
    'ledger_applied', 'broker_connected', 'ready_for_routing',
    'live_orders_enabled', 'automatic_continuation',
)


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


def _component_sha256(value):
    return type(value) is str and SHA256_PATTERN.fullmatch(value) is not None


def _component_count(value):
    return type(value) is int and value >= 0


def _component_digest(value, code):
    try:
        return sha(value)
    except (TypeError, ValueError, OverflowError, RecursionError):
        _fail(code)


def _retained_gross_fill_arithmetic(value, balance, activity):
    """Replay retained gross arithmetic and bind every outer projection."""
    normalization = activity.get('activity_normalization')
    gross = balance.get('gross_fill_review')
    if type(normalization) is not dict or type(gross) is not dict:
        return False
    records = normalization.get('normalized_activities')
    if (type(records) is not list or
            len(records) > retained_balance_review.adapter.MAX_ACTIVITIES or
            any(type(record) is not dict for record in records)):
        return False
    try:
        opening = retained_balance_review.balances(
            balance.get('opening_balances'))
        observed = retained_balance_review.balances(
            balance.get('observed_balances'))
        start = retained_balance_review.adapter._instant(
            balance.get('opening_at'))
        end = retained_balance_review.adapter._instant(
            balance.get('observed_at'))
        if (any(retained_balance_review.text(opening[asset]) !=
                balance['opening_balances'][asset] for asset in ('USD', 'BTC')) or
                any(retained_balance_review.text(observed[asset]) !=
                balance['observed_balances'][asset]
                for asset in ('USD', 'BTC')) or
                start != balance.get('opening_at') or
                end != balance.get('observed_at') or
                retained_balance_review._time_key(end) <
                    retained_balance_review._time_key(start)):
            return False
        expected = retained_balance_review._gross_review(
            records, opening, observed, start, end)
        projections = {
            'gross_only_ending_balances':
                'gross_execution_ending_balances',
            'gross_only_minus_observed': 'gross_minus_supplied_observed',
            'gross_only_debits_covered': 'gross_only_debits_covered',
            'retained_unallocated_fee_debits':
                'all_retained_unallocated_fee_debits',
            'retained_fee_record_counts': 'retained_fee_records_by_asset',
        }
        return (gross == expected and
            all(value.get(outer) == expected[inner]
                for outer, inner in projections.items()))
    except (AttributeError, KeyError, TypeError, ValueError,
            ArithmeticError, OverflowError, RecursionError):
        return False


def _retained_observation_components(retained_observation, value):
    """Require both shared retained subreviews to remain non-authoritative."""
    balance = retained_observation.get('retained_balance_review')
    activity = retained_observation.get('retained_activity_review')
    if (type(balance) is not dict or type(activity) is not dict or
            balance.get('mode') !=
                'HEAD_BOUND_RETAINED_GROSS_BALANCE_REVIEW' or
            balance.get('policy') != retained_balance_review.POLICY or
            balance.get('status') != 'REVIEW_REQUIRED' or
            balance.get('read_only') is not True or
            balance.get('source') != 'SUPPLIED_UNATTESTED' or
            balance.get('retained_activity_review_location') !=
                'TOP_LEVEL_SHARED_REVIEW' or
            'retained_activity_review' in balance or
            balance.get('head_rechecked') is not True or
            balance.get('arithmetic_evaluated') is not True or
            type(balance.get('gross_fill_review')) is not dict or
            type(balance.get('review_reasons')) is not list or
            any(balance.get(key) is not False
                for key in RETAINED_BALANCE_DENIALS) or
            activity.get('mode') != 'UNATTESTED_RETAINED_ACTIVITY_REVIEW' or
            activity.get('status') != 'REVIEW_REQUIRED' or
            activity.get('read_only') is not True or
            activity.get('source') != 'SUPPLIED_UNATTESTED' or
            activity.get('snapshot_selection') !=
                'REPORTED_UPDATED_AT_DIAGNOSTIC_ONLY' or
            activity.get('normalization_evaluated') is not True or
            type(activity.get('activity_normalization')) is not dict or
            type(activity.get('collection_observation_review')) is not dict or
            type(activity.get('requested_window_review')) is not dict or
            type(activity.get('review_reasons')) is not list or
            any(not _component_count(activity.get(key)) for key in (
                'retained_batches', 'retained_activities', 'distinct_orders',
                'distinct_order_snapshots')) or
            any(activity.get(key) is not False
                for key in RETAINED_ACTIVITY_DENIALS) or
            not _retained_gross_fill_arithmetic(value, balance, activity)):
        return False
    account = retained_observation.get('account_sha256')
    head = retained_observation.get('head_sha256')
    if (balance.get('account_sha256') != account or
            balance.get('head_sha256') != head or
            activity.get('account_sha256') != account or
            activity.get('head_sha256') != head or
            not _component_sha256(balance.get('input_sha256'))):
        return False
    balance_scope = {key: balance.get(key) for key in (
        'policy', 'account_sha256', 'head_sha256', 'opening_balances',
        'opening_at', 'observed_balances', 'observed_at')}
    try:
        return balance.get('input_sha256') == sha(balance_scope)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _retained_reconciliation_envelope(value, account_review, guard_review,
                                      replayed_observation):
    accepted = guard_review.get('accepted_reconciliation_review')
    if type(accepted) is not dict:
        return False
    retained_observation = accepted.get('observation_review')
    if (type(retained_observation) is not dict or
            retained_observation != replayed_observation or
            accepted.get('mode') !=
                'PAPER_RECONCILIATION_ACCEPTANCE_REVIEW' or
            accepted.get('policy') != evidence.inventory.POLICY or
            accepted.get('status') != 'REVIEW_REQUIRED' or
            accepted.get('read_only') is not True or
            retained_observation.get('mode') !=
                'PORTABLE_OBSERVATION_RETAINED_ACTIVITY_REVIEW' or
            retained_observation.get('policy') != observation.POLICY or
            retained_observation.get('status') != 'REVIEW_REQUIRED' or
            retained_observation.get('read_only') is not True or
            any(accepted.get(key) is not True
                for key in RETAINED_RECONCILIATION_ASSURANCES) or
            any(accepted.get(key) is not False
                for key in RETAINED_RECONCILIATION_DENIALS) or
            retained_observation.get('head_rechecked') is not True or
            retained_observation.get('arithmetic_evaluated') is not True or
            any(retained_observation.get(key) is not False
                for key in RETAINED_OBSERVATION_DENIALS) or
            not _retained_observation_components(retained_observation,
                value)):
        return False
    accepted_hashes = tuple(accepted.get(key) for key in (
        'input_sha256', 'account_sha256', 'pacing_head_sha256',
        'activity_head_sha256', 'acceptance_head_sha256',
        'declared_collection_window_union_sha256',
        'opening_manifest_sha256', 'closing_manifest_sha256',
        'observation_review_sha256'))
    observation_hashes = tuple(retained_observation.get(key) for key in (
        'input_sha256', 'account_sha256', 'head_sha256',
        'opening_manifest_sha256', 'closing_manifest_sha256'))
    if (any(not _component_sha256(item) for item in (
            *accepted_hashes, *observation_hashes)) or
            not _component_count(accepted.get('accepted_transitions'))):
        return False
    accepted_scope = {key: accepted.get(key) for key in (
        'policy', 'account_sha256', 'pacing_head_sha256',
        'activity_head_sha256', 'acceptance_head_sha256',
        'declared_collection_window_union_sha256', 'accepted_transitions',
        'opening_manifest_sha256', 'closing_manifest_sha256',
        'observation_review_sha256')}
    observation_scope = {key: retained_observation.get(key) for key in (
        'policy', 'account_sha256', 'head_sha256',
        'opening_manifest_sha256', 'closing_manifest_sha256')}
    if (any(accepted.get(key) != value.get(key) for key in (
            'account_sha256', 'pacing_head_sha256',
            'activity_head_sha256', 'acceptance_head_sha256',
            'opening_manifest_sha256', 'closing_manifest_sha256')) or
            accepted.get('accepted_transitions') !=
                account_review.get('accepted_transitions') or
            accepted.get('accepted_transitions') !=
                value.get('accepted_transitions') or
            retained_observation.get('account_sha256') !=
                value.get('account_sha256') or
            retained_observation.get('head_sha256') !=
                value.get('activity_head_sha256') or
            any(retained_observation.get(key) != value.get(key) for key in (
                'opening_manifest_sha256', 'closing_manifest_sha256'))):
        return False
    try:
        return (retained_observation.get('input_sha256') ==
                sha(observation_scope) and
            accepted.get('observation_review_sha256') ==
                retained_observation.get('input_sha256') and
            accepted.get('input_sha256') == sha(accepted_scope) and
            guard_review.get('accepted_reconciliation_sha256') ==
                accepted.get('input_sha256') and
            account_review.get('inventory_review_sha256') == sha(accepted))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _account_component_envelope(value, replayed_observation):
    account_review = value.get('account_evidence_review')
    guard_review = value.get('stability_guard_review')
    if (type(account_review) is not dict or type(guard_review) is not dict or
            account_review.get('mode') !=
                'PAPER_ACCOUNT_EVIDENCE_BUNDLE_REVIEW' or
            account_review.get('policy') != evidence.POLICY or
            account_review.get('status') != 'REVIEW_REQUIRED' or
            account_review.get('read_only') is not True or
            guard_review.get('mode') !=
                'PAPER_RECONCILIATION_STABILITY_GUARD_REVIEW' or
            guard_review.get('policy') != evidence.inventory.STABILITY_POLICY or
            guard_review.get('status') != 'REVIEW_REQUIRED' or
            guard_review.get('read_only') is not True or
            not _retained_reconciliation_envelope(
                value, account_review, guard_review, replayed_observation)):
        return False
    account_hash = value.get('account_evidence_sha256')
    guard_hash = value.get('stability_guard_sha256')
    input_hash = value.get('input_sha256')
    if (any(not _component_sha256(item) for item in (
            account_hash, guard_hash, input_hash)) or
            account_review.get('input_sha256') != account_hash or
            guard_review.get('input_sha256') != guard_hash):
        return False
    account_scope = dict(policy=account_review.get('policy'),
        **{key: account_review.get(key) for key in evidence.HEADS},
        accepted_transitions=account_review.get('accepted_transitions'),
        opening_manifest_sha256=account_review.get(
            'opening_manifest_sha256'),
        closing_manifest_sha256=account_review.get(
            'closing_manifest_sha256'),
        request_review_sha256=account_review.get('request_review_sha256'),
        inventory_review_sha256=account_review.get(
            'inventory_review_sha256'))
    guard_scope = {key: guard_review.get(key) for key in (
        'policy', 'account_sha256', 'activity_head_sha256',
        'accepted_reconciliation_sha256', 'pre_opening_manifest_sha256',
        'opening_manifest_sha256', 'closing_manifest_sha256',
        'post_closing_manifest_sha256',
        'declared_guard_window_union_sha256')}
    if (any(account_review.get(key) != value.get(key)
            for key in evidence.HEADS) or
            any(account_review.get(key) != value.get(key) for key in (
                'opening_manifest_sha256', 'closing_manifest_sha256')) or
            any(guard_review.get(key) != value.get(key) for key in (
                'account_sha256', 'activity_head_sha256',
                'pre_opening_manifest_sha256',
                'opening_manifest_sha256', 'closing_manifest_sha256',
                'post_closing_manifest_sha256'))):
        return False
    component_scope = dict(policy=value.get('policy'),
        **{key: value.get(key) for key in evidence.HEADS},
        account_evidence_sha256=account_hash,
        stability_guard_sha256=guard_hash,
        **{key: value.get(key) for key in (
            'pre_opening_manifest_sha256', 'opening_manifest_sha256',
            'closing_manifest_sha256', 'post_closing_manifest_sha256')})
    try:
        return (account_hash == sha(account_scope) and
            guard_hash == sha(guard_scope) and
            input_hash == sha(component_scope))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


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
    if type(posts) is not dict:
        _fail('POST_ACTIVITY_INCOMPLETE')
    post_counts = tuple(posts.get(key) for key in (
        'post_attempts', 'exact_activity_bindings',
        'exact_not_sent_bindings'))
    if (posts.get('mode') != 'READ_ONLY_PAPER_PROVIDER_POST_ACTIVITY_REVIEW' or
            posts.get('status') != 'COMPLETE_LOCAL_POST_ACTIVITY_CONSISTENT' or
            posts.get('read_only') is not True or
            posts.get('complete_local_post_activity_consistent') is not True or
            posts.get('journal_files_rechecked_unchanged') is not True or
            posts.get('semantic_reader_failures_rechecked') is not True or
            posts.get('uncertainty_preserved') is not True or
            posts.get('account_histories') != 1 or posts.get('reasons') != [] or
            any(not _component_count(value) for value in post_counts) or
            post_counts[0] != len(attempts) or
            post_counts[1] + post_counts[2] != post_counts[0] or
            any(posts.get(flag) is not False for flag in post_activity.FLAGS)):
        _fail('POST_ACTIVITY_INCOMPLETE')
    posts_digest = _component_digest(posts, 'POST_ACTIVITY_INCOMPLETE')

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
    replayed_observation = stable_read(lambda: observation.review(
        BrokerActivityStore(activity_store_path, account_id),
        expected_account_id=account_id,
        expected_head=expected_activity_head,
        opening_observation=opening_observation,
        closing_observation=closing_observation))
    if type(account) is not dict:
        _fail('ACCOUNT_EVIDENCE_INVALID')
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
    account_hashes = tuple(account.get(key) for key in (
        'pacing_head_sha256', 'acceptance_head_sha256',
        'pre_opening_manifest_sha256', 'opening_manifest_sha256',
        'closing_manifest_sha256', 'post_closing_manifest_sha256'))
    account_counts = tuple(account.get(key) for key in (
        'original_requests_reviewed', 'explicit_fills_reviewed'))
    request_rows = first_request.get('requests')
    retained_activities = first_activity.get('retained_activities')
    if (account.get('mode') !=
            'PAPER_ACCOUNT_EVIDENCE_STABILITY_GUARD_REVIEW' or
            account.get('policy') != evidence.STABILITY_POLICY or
            account.get('status') != 'REVIEW_REQUIRED' or
            account.get('read_only') is not True or
            not _account_component_envelope(account, replayed_observation) or
            account.get('account_sha256') != journal_account or
            account.get('request_head_sha256') != expected_request_head or
            account.get('activity_head_sha256') != expected_activity_head or
            any(account.get(key) is not True for key in required) or
            any(not _component_sha256(value) for value in account_hashes) or
            any(not _component_count(value) for value in account_counts) or
            type(request_rows) is not list or
            not _component_count(retained_activities) or
            account_counts[0] != len(request_rows) or
            account_counts[1] > retained_activities or
            any(account.get(flag) is not False for flag in evidence.FLAGS)):
        _fail('ACCOUNT_EVIDENCE_INVALID')
    account_digest = _component_digest(account, 'ACCOUNT_EVIDENCE_INVALID')

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
        if (type(gross) is not dict or set(gross) != {'USD', 'BTC'} or
                any(type(value) is not str or re.fullmatch(
                    r'-?(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,36})?',
                    value) is None for value in gross.values())):
            _fail('ACCOUNT_EVIDENCE_INVALID')
        gross_values = tuple(Decimal(value) for value in gross.values())
    except (InvalidOperation, ValueError, TypeError):
        _fail('ACCOUNT_EVIDENCE_INVALID')
    if any(not value.is_finite() for value in gross_values):
        _fail('ACCOUNT_EVIDENCE_INVALID')
    # Preserve a legitimate finite nonzero residual as a diagnostic.  A
    # disagreement never becomes authority, while NaN/infinities cannot enter
    # a reviewed arithmetic result merely because Decimal can parse them.
    gross_absent = all(value == 0 for value in gross_values)
    scope = dict(policy=POLICY, account_sha256=journal_account,
        post_head_sha256=expected_post_head,
        request_head_sha256=expected_request_head,
        activity_head_sha256=expected_activity_head,
        pacing_head_sha256=account['pacing_head_sha256'],
        acceptance_head_sha256=account['acceptance_head_sha256'],
        provider_post_activity_sha256=posts_digest,
        account_evidence_sha256=account_digest,
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
