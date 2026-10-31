"""Explicit reviewed recovery for one blocked local provider read budget.

This command never reads credentials, contacts a provider, retries a read,
reclaims capacity, shortens cooldowns or starts the desktop.  It appends one
content-free recovery boundary to an existing private journal.
"""
import argparse
import hashlib
import json
import sqlite3
import time

from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_provider_read_budget import (
    MAX_DATABASE_BYTES, PaperProviderReadBudget)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities,
    guard_file_set_finalization, require_file_fingerprints,
    require_file_identities)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_PROVIDER_BUDGET_RECOVERY_USAGE_ERROR\n')


def recover(path, *, expected_head, expected_account_id,
        confirm_block_reviewed=False, confirm_uncertainty_preserved=False,
        admission=time.time):
    """Recover one exact blocked tail while preserving its retained uncertainty."""
    private_path, private_resolved = _private_path(path)
    budget = PaperProviderReadBudget(private_path)
    budget_identity = capture_file_identities((private_resolved,),
        'PAPER_PROVIDER_BUDGET_RECOVERY_')
    budget_fingerprint = capture_file_fingerprints((private_resolved,),
        (MAX_DATABASE_BYTES,), 'PAPER_PROVIDER_BUDGET_RECOVERY_')
    def check_budget_identity():
        require_file_identities((private_resolved,), budget_identity,
            'PAPER_PROVIDER_BUDGET_RECOVERY_')
    def check_budget_source():
        check_budget_identity()
        require_file_fingerprints((private_resolved,),
            (MAX_DATABASE_BYTES,), budget_fingerprint,
            'PAPER_PROVIDER_BUDGET_RECOVERY_')
    # A recovery command never creates a provider journal.  This read-only
    # sample is repeated under the owned write transaction by the budget.
    before = budget.inspect()
    check_budget_source()
    if before['events'] == 0 or before['head_sha256'] != expected_head:
        raise ValueError('PAPER_PROVIDER_BUDGET_RECOVERY_SOURCE_REQUIRED')
    completed = budget.history()
    check_budget_source()
    if (not before['review_required'] or before['pending_token'] is not None or
            not completed or completed[-1]['outcome'] not in
            ('AUTH_DENIED', 'REVIEW_REQUIRED', 'INTERRUPTED_UNKNOWN')):
        raise ValueError('PAPER_PROVIDER_BUDGET_RECOVERY_SOURCE_REQUIRED')
    with budget.ownership() as owner:
        check_budget_source()
        write_error = None
        try:
            budget.recover_review_block(expected_head=expected_head,
                expected_account_id=expected_account_id,
                confirm_block_reviewed=confirm_block_reviewed,
                confirm_uncertainty_preserved=confirm_uncertainty_preserved,
                admission=admission, ownership=owner)
        except BaseException as error:
            # The append may have committed before its local acknowledgement
            # was lost.  Trust neither the return nor raised detail; only an
            # exact cold replay can acknowledge the durable recovery.
            write_error = error
        receipt_error = None
        try:
            check_budget_identity()
            committed_fingerprint = capture_file_fingerprints(
                (private_resolved,), (MAX_DATABASE_BYTES,),
                'PAPER_PROVIDER_BUDGET_RECOVERY_')
            with guard_file_set_finalization((private_resolved,),
                    (MAX_DATABASE_BYTES,), budget_identity,
                    committed_fingerprint,
                    'PAPER_PROVIDER_BUDGET_RECOVERY_'):
                receipt = budget.recovery_receipt()
                check_budget_identity()
                require_file_fingerprints((private_resolved,),
                    (MAX_DATABASE_BYTES,), committed_fingerprint,
                    'PAPER_PROVIDER_BUDGET_RECOVERY_')
                receipt_again = budget.recovery_receipt()
                check_budget_identity()
                require_file_fingerprints((private_resolved,),
                    (MAX_DATABASE_BYTES,), committed_fingerprint,
                    'PAPER_PROVIDER_BUDGET_RECOVERY_')
                account_digest = hashlib.sha256(
                    expected_account_id.encode()).hexdigest()
                stable = (receipt == receipt_again and
                    receipt['head_sha256'] == receipt['recovery_head_sha256'] and
                    receipt['recoveries'] == before['recoveries'] + 1 and
                    receipt['sessions'] == before['sessions'] and
                    receipt['review_required'] is False and
                    receipt['pending_token'] is None and
                    receipt['last_evaluated_at'] == receipt['recovered_at'] and
                    receipt['next_allowed_at'] == before['next_allowed_at'] and
                    receipt['account_sha256'] == account_digest and
                    receipt['blocked_session_head_sha256'] ==
                        completed[-1]['after_head_sha256'] and
                    receipt['blocked_outcome'] == completed[-1]['outcome'] and
                    receipt['exact_recovery_replayed'] is True and
                    receipt['read_only'] is True and
                    receipt['uncertainty_preserved'] is True and
                    all(receipt[key] is False for key in (
                        'history_rewritten', 'session_capacity_reclaimed',
                        'admission_reserved', 'automatic_retry',
                        'automatic_continuation', 'broker_wide_quota_verified',
                        'distributed_ownership_verified', 'ready_for_routing',
                        'live_orders_enabled', 'money_movement_enabled',
                        'release_approved')))
                if not stable:
                    raise ValueError(
                        'PAPER_PROVIDER_BUDGET_RECOVERY_DURABLE_STATE_CHANGED')
                return dict(mode='PAPER_PROVIDER_READ_BUDGET_REVIEWED_RECOVERY',
                    status='REVIEW_REQUIRED',
                    head_sha256=receipt['recovery_head_sha256'],
                    blocked_outcome=receipt['blocked_outcome'],
                    review_block_cleared=True,
                    next_allowed_at=receipt['next_allowed_at'],
                    cooldown_shortened=False, uncertainty_preserved=True,
                    history_rewritten=False, session_capacity_reclaimed=False,
                    admission_reserved=False, automatic_retry=False,
                    automatic_continuation=False, broker_wide_quota_verified=False,
                    distributed_ownership_verified=False, ready_for_routing=False,
                    live_orders_enabled=False, money_movement_enabled=False,
                    release_approved=False,
                    recovery_acknowledgement_cold_replayed=True,
                    provider_budget_file_identity_rechecked=True,
                    provider_budget_file_fingerprint_rechecked=True,
                    provider_budget_file_set_rechecked_on_return=True,
                    provider_budget_ownership_rechecked_on_exit=True)
        except BaseException as error:
            receipt_error = error
            stable = False
        if not stable:
            if (type(receipt_error) is ValueError and
                    str(receipt_error).startswith((
                        'PAPER_PROVIDER_BUDGET_RECOVERY_FILE_IDENTITY_',
                        'PAPER_PROVIDER_BUDGET_RECOVERY_FILE_SET_',
                        'PAPER_PROVIDER_BUDGET_RECOVERY_SQLITE_SIDECAR_'))):
                raise receipt_error
            if (type(write_error) is ValueError and
                    str(write_error).startswith(
                        'PAPER_PROVIDER_READ_BUDGET_')):
                raise write_error
            raise ValueError(
                'PAPER_PROVIDER_BUDGET_RECOVERY_DURABLE_STATE_CHANGED')


def fixture_report():
    """Disposable lost-acknowledgement recovery; no credentials or IO."""
    import tempfile
    from pathlib import Path

    account = '01234567-89ab-cdef-0123-456789abcdef'
    with tempfile.TemporaryDirectory(
            prefix='nexus-provider-read-recovery-') as directory:
        budget = PaperProviderReadBudget(Path(directory) / 'provider.sqlite3')
        with budget.ownership() as owner:
            token = budget.begin(account_id=account,
                account_before_head_sha256='0' * 64,
                operation='ACCOUNT_INVENTORY', admission=lambda:1000,
                ownership=owner)
            budget.finish(token, account_after_head_sha256='a' * 64,
                actual_gets=4, admission=lambda:1001, ownership=owner,
                status_code=401)
        before = budget.inspect(); history = budget.history()
        original = PaperProviderReadBudget.recover_review_block
        lost_acknowledgements = 0
        def commit_then_lose_ack(self, *args, **kwargs):
            nonlocal lost_acknowledgements
            lost_acknowledgements += 1
            original(self, *args, **kwargs)
            raise RuntimeError('fixture private lost recovery acknowledgement')
        PaperProviderReadBudget.recover_review_block = commit_then_lose_ack
        try:
            result = recover(budget.path, expected_head=before['head_sha256'],
                expected_account_id=account, confirm_block_reviewed=True,
                confirm_uncertainty_preserved=True, admission=lambda:1002)
        finally:
            PaperProviderReadBudget.recover_review_block = original
        after = budget.inspect()
        checks = dict(
            exact_review_block_bound=(result['blocked_outcome'] ==
                'AUTH_DENIED' and result['review_block_cleared'] is True),
            recovery_acknowledgement_cold_replayed=(
                result['recovery_acknowledgement_cold_replayed'] is True and
                lost_acknowledgements == 1 and after['recoveries'] == 1),
            provider_budget_file_identity_rechecked=
                result['provider_budget_file_identity_rechecked'] is True,
            provider_budget_file_fingerprint_rechecked=
                result['provider_budget_file_fingerprint_rechecked'] is True,
            provider_budget_file_set_rechecked_on_return=
                result['provider_budget_file_set_rechecked_on_return'] is True,
            provider_budget_ownership_rechecked_on_exit=
                result['provider_budget_ownership_rechecked_on_exit'] is True,
            blocked_history_preserved=budget.history() == history,
            cooldown_preserved=(result['next_allowed_at'] ==
                before['next_allowed_at'] == after['next_allowed_at']),
            session_capacity_not_reclaimed=(
                after['sessions'] == before['sessions'] == 1))
        return dict(source=
            'TEMPORARY_PROVIDER_READ_BUDGET_RECOVERY_FIXTURE',
            passed=all(checks.values()), **checks,
            actual_provider_access=False, actual_vault_access=False,
            broker_account_reconciled=False, spendable_funds_verified=False,
            ready_for_routing=False, live_orders_enabled=False,
            money_movement_enabled=False, release_approved=False)


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument('--provider-budget', required=True,
        help='Existing private provider-budget SQLite path outside the application/source directory.')
    parser.add_argument('--expected-head', required=True,
        help='Exact current provider-budget SHA-256 head reviewed by the operator.')
    parser.add_argument('--expected-account', required=True,
        help='Exact paper account UUID whose blocked tail was reviewed.')
    parser.add_argument('--confirm-reviewed-block', action='store_true', required=True,
        help='Confirm the retained authentication/review/interruption block was reviewed.')
    parser.add_argument('--confirm-preserve-uncertainty', action='store_true', required=True,
        help='Confirm retained uncertainty and cooldown must remain unchanged.')
    args = parser.parse_args()
    try:
        result = recover(args.provider_budget, expected_head=args.expected_head,
            expected_account_id=args.expected_account,
            confirm_block_reviewed=args.confirm_reviewed_block,
            confirm_uncertainty_preserved=args.confirm_preserve_uncertainty)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, OverflowError,
            MemoryError, RecursionError, KeyboardInterrupt):
        print('PAPER_PROVIDER_BUDGET_RECOVERY_FAILED')
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
