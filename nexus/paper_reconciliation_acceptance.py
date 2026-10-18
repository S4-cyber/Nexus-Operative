"""Bind a private observation-pair review to accepted activity history.

This offline composition replays the complete cooperating pacing, activity and
acceptance journals before and after balance arithmetic.  It never reads a
credential, contacts a provider, writes evidence or grants order authority.
"""
import argparse
import getpass
import json
import sqlite3

from nexus.alpaca_paper_contract import sha
from nexus.broker_activity_store import BrokerActivityStore
from nexus import paper_observation_review
from nexus.paper_activity_acceptance_probe import status as acceptance_status

POLICY = 'nexus.paper-reconciliation-acceptance.v1'


def _fail(code):
    raise ValueError('PAPER_RECONCILIATION_ACCEPTANCE_' + code)


def _accepted(status):
    """Require every retained cooperating transition, not merely accepted tail replay."""
    return (
        status['cross_journal_recovery_state'] == 'BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED'
        and status['cross_journal_heads_match'] is True
        and status['accepted_transitions'] > 0
        and status['preexisting_history_unverified'] is False
        and status['accepted_transitions'] == status['sessions']
        and status['accepted_transitions'] == status['retained_batches']
        and status['accepted_transitions'] == status['retained_collection_observations']
        and status['all_collection_cursor_sequences_complete'] is True
        and status['replayed_accepted_transitions'] == status['accepted_transitions']
        and status['latest_acceptance_record_replayed'] is True
        and status['all_accepted_transitions_replayed'] is True
    )


def review(*, expected_account_id, pacing_path, activity_store_path,
           acceptance_store_path, expected_activity_head,
           opening_observation, closing_observation):
    """Read-only composition of accepted collection history and balance review."""
    before = acceptance_status(expected_account_id=expected_account_id,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path)
    if not _accepted(before):
        _fail('ACCEPTED_HISTORY_REQUIRED')
    if before['retention_head_sha256'] != expected_activity_head:
        _fail('ACTIVITY_HEAD_DISAGREEMENT')

    component = paper_observation_review.review(
        BrokerActivityStore(activity_store_path, expected_account_id),
        expected_account_id=expected_account_id, expected_head=expected_activity_head,
        opening_observation=opening_observation, closing_observation=closing_observation)

    after = acceptance_status(expected_account_id=expected_account_id,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path)
    if before != after:
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    if component['head_sha256'] != after['retention_head_sha256']:
        _fail('COMPONENT_HEAD_DISAGREEMENT')

    scope = dict(policy=POLICY, account_sha256=after['account_sha256'],
        pacing_head_sha256=after['pacing_head_sha256'],
        activity_head_sha256=after['retention_head_sha256'],
        acceptance_head_sha256=after['acceptance_head_sha256'],
        accepted_transitions=after['accepted_transitions'],
        opening_manifest_sha256=component['opening_manifest_sha256'],
        closing_manifest_sha256=component['closing_manifest_sha256'],
        observation_review_sha256=component['input_sha256'])
    return dict(mode='PAPER_RECONCILIATION_ACCEPTANCE_REVIEW',
        status='REVIEW_REQUIRED', read_only=True, **scope, input_sha256=sha(scope),
        journals_rechecked_unchanged=True, complete_accepted_history_replayed=True,
        legacy_baseline_absent=True, complete_pacing_sessions_accepted=True,
        complete_retained_batches_accepted=True,
        complete_collection_observations_accepted=True,
        all_collection_cursor_sequences_complete=True,
        acceptance_recovery_state=after['cross_journal_recovery_state'],
        observation_review=component,
        admission_reserved=False, source_attested=False,
        source_coverage_verified=False, activity_coverage_verified=False,
        fee_coverage_complete=False, fee_execution_links_verified=False,
        balance_instants_verified=False, observation_windows_verified_quiet=False,
        holds_reconciled=False, broker_account_reconciled=False,
        spendable_funds_verified=False, ledger_applied=False,
        ready_for_routing=False, broker_connected=False, live_orders_enabled=False,
        money_movement_enabled=False, automatic_retry=False,
        automatic_continuation=False, release_approved=False,
        limitations='Complete local accepted-history replay binds this supplied observation pair to one exact retained activity head. It does not attest the provider, prove complete activity/fee coverage, turn sequential reads into atomic balances, verify holds or spendable funds, coordinate other hosts/writers, authorize orders or approve release. Keep all journals, observations and output outside Git; never erase uncertainty to retry.')


def fixture_report():
    """Synthetic disposable acceptance/reconciliation evidence; no external IO."""
    import tempfile
    from pathlib import Path
    from nexus.paper_activity_acceptance_evidence import ACCOUNT, _scope
    from nexus.paper_observation_review import _fixture_observation

    with tempfile.TemporaryDirectory(prefix='nexus-reconciliation-acceptance-') as directory:
        root = Path(directory)
        pacing, activity, acceptance, scope = _scope(root)
        acceptance.append(scope, admission=lambda:(pacing.inspect()['head_sha256'],
            activity.inspect()['head_sha256']))
        head = activity.inspect()['head_sha256']
        opening = _fixture_observation(ACCOUNT, '1000', '0', 1800000000, 1800000001)
        closing = _fixture_observation(ACCOUNT, '1000', '0', 1800000002, 1800000003)
        paths = dict(expected_account_id=ACCOUNT, pacing_path=root/'pacing.sqlite3',
            activity_store_path=root/'activity.sqlite3',
            acceptance_store_path=root/'acceptance.sqlite3',
            expected_activity_head=head, opening_observation=opening,
            closing_observation=closing)
        result = review(**paths)
        before = tuple((path.name, path.read_bytes()) for path in sorted(root.iterdir()))
        repeated = review(**paths)
        after = tuple((path.name, path.read_bytes()) for path in sorted(root.iterdir()))
        unbound_root = root/'unbound'; unbound_root.mkdir()
        _, unbound_activity, _, _ = _scope(unbound_root)
        unbound_rejected = False
        try:
            review(expected_account_id=ACCOUNT,
                pacing_path=unbound_root/'pacing.sqlite3',
                activity_store_path=unbound_root/'activity.sqlite3',
                acceptance_store_path=unbound_root/'acceptance.sqlite3',
                expected_activity_head=unbound_activity.inspect()['head_sha256'],
                opening_observation=opening, closing_observation=closing)
        except ValueError as error:
            unbound_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_ACCEPTED_HISTORY_REQUIRED'
        continuation_root = root/'continuation'; continuation_root.mkdir()
        partial_pacing, partial_activity, partial_acceptance, partial_scope = _scope(
            continuation_root, full_page=True)
        partial_acceptance.append(partial_scope, admission=lambda:(
            partial_pacing.inspect()['head_sha256'],partial_activity.inspect()['head_sha256']))
        continuation_rejected = False
        try:
            review(expected_account_id=ACCOUNT,
                pacing_path=continuation_root/'pacing.sqlite3',
                activity_store_path=continuation_root/'activity.sqlite3',
                acceptance_store_path=continuation_root/'acceptance.sqlite3',
                expected_activity_head=partial_activity.inspect()['head_sha256'],
                opening_observation=opening, closing_observation=closing)
        except ValueError as error:
            continuation_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_ACCEPTED_HISTORY_REQUIRED'
        exact_bound=(result['activity_head_sha256']==head and
            result['opening_manifest_sha256']==opening['manifest_sha256'] and
            result['closing_manifest_sha256']==closing['manifest_sha256'])
        replayed=(result['complete_accepted_history_replayed'] is True and
            result['journals_rechecked_unchanged'] is True and result['accepted_transitions']==1)
        complete=(result['legacy_baseline_absent'] is True and
            result['complete_pacing_sessions_accepted'] is True and
            result['complete_retained_batches_accepted'] is True and
            result['complete_collection_observations_accepted'] is True)
        cursor_complete=result['all_collection_cursor_sequences_complete'] is True
        arithmetic=result['observation_review']['arithmetic_evaluated'] is True
        passed=(result == repeated and before == after and unbound_rejected and continuation_rejected and
            exact_bound and replayed and complete and cursor_complete and arithmetic)
        return dict(passed=passed, repeat_review_matches=result == repeated,
            journals_unchanged=before == after, unbound_history_rejected=unbound_rejected,
            continuation_required_history_rejected=continuation_rejected,
            exact_activity_head_bound=exact_bound, complete_accepted_history_replayed=replayed,
            legacy_baseline_absent=result['legacy_baseline_absent'],
            complete_pacing_sessions_accepted=result['complete_pacing_sessions_accepted'],
            complete_retained_batches_accepted=result['complete_retained_batches_accepted'],
            complete_collection_observations_accepted=result['complete_collection_observations_accepted'],
            all_collection_cursor_sequences_complete=cursor_complete,
            observation_arithmetic_evaluated=arithmetic, private_scope_omitted=True,
            source='SYNTHETIC_LOCAL_ACCEPTED_RECONCILIATION_FIXTURE',
            actual_provider_access=False, actual_vault_access=False,
            broker_account_reconciled=False, spendable_funds_verified=False,
            ready_for_routing=False, broker_connected=False, live_orders_enabled=False,
            money_movement_enabled=False, release_approved=False)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_RECONCILIATION_ACCEPTANCE_USAGE_ERROR\n')


def main():
    parser = _Parser(description=__doc__)
    for name in ('expected-account','pacing','activity-store','acceptance-store',
                 'activity-head','opening','closing'):
        parser.add_argument('--'+name, required=True)
    for name in ('opening','closing'):
        parser.add_argument('--'+name+'-encrypted', action='store_true',
            help='Decrypt this bounded input in memory after a private prompt; write no plaintext file.')
    args = parser.parse_args()
    try:
        result = review(expected_account_id=args.expected_account,
            pacing_path=args.pacing, activity_store_path=args.activity_store,
            acceptance_store_path=args.acceptance_store,
            expected_activity_head=args.activity_head,
            opening_observation=paper_observation_review._load_file(args.opening,
                encrypted=args.opening_encrypted, label='Opening'),
            closing_observation=paper_observation_review._load_file(args.closing,
                encrypted=args.closing_encrypted, label='Closing'))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, EOFError,
            KeyboardInterrupt, MemoryError, RecursionError, getpass.GetPassWarning):
        print('PAPER_RECONCILIATION_ACCEPTANCE_FAILED')
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
