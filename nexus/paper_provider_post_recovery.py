"""Reviewed recovery for one resolved ambiguous paper POST budget tail.

This command performs no provider IO.  It only clears the shared-local POST
budget review latch after the exact original client ID has a retained terminal
observation in the request journal.  The original request remains permanently
marked attempted and can never be submitted again through this path.
"""
import argparse
import hashlib
import json
import sqlite3
import time

from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_provider_post_budget import (
    MAX_DATABASE_BYTES, PaperProviderPostBudget)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities, check_path_set,
    guard_file_set_finalization, require_file_fingerprints,
    require_file_identities)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_PROVIDER_POST_RECOVERY_USAGE_ERROR\n')


def _proof(snapshot, client_order_id, expected_request_head):
    row = snapshot['request']
    if (snapshot['head_sha256'] != expected_request_head or
            row['client_order_id'] != client_order_id):
        raise ValueError('PAPER_PROVIDER_POST_RECOVERY_REQUEST_HEAD_CHANGED')
    return dict(account_sha256=snapshot['account_sha256'],
        payload_sha256=row['payload_sha256'],
        client_order_sha256=hashlib.sha256(client_order_id.encode()).hexdigest(),
        request_resolution_head_sha256=expected_request_head,
        recovery_state=row['recovery_state'],
        lookup_by_original_client_id_required=row['lookup_by_original_client_id_required'])


def recover(post_budget_path, request_journal_path, *, expected_account_id,
        client_order_id, expected_post_budget_head, expected_request_head,
        confirm_original_id_resolved=False, confirm_no_resubmission=False,
        admission=time.time):
    """Append one exact recovery boundary after replaying both private journals."""
    post_path, post_resolved = _private_path(post_budget_path)
    request_path, request_resolved = _private_path(request_journal_path)
    check_path_set((post_resolved, request_resolved), 'PAPER_PROVIDER_POST_RECOVERY_')
    budget = PaperProviderPostBudget(post_path)
    journal = BrokerRequestJournal(request_path, expected_account_id)
    post_identity = capture_file_identities((post_resolved,),
        'PAPER_PROVIDER_POST_RECOVERY_')
    post_fingerprint = capture_file_fingerprints((post_resolved,),
        (MAX_DATABASE_BYTES,), 'PAPER_PROVIDER_POST_RECOVERY_')
    def check_post_identity():
        require_file_identities((post_resolved,), post_identity,
            'PAPER_PROVIDER_POST_RECOVERY_')
    def check_post_source():
        check_post_identity()
        require_file_fingerprints((post_resolved,), (MAX_DATABASE_BYTES,),
            post_fingerprint, 'PAPER_PROVIDER_POST_RECOVERY_')
    before = budget.inspect()
    check_post_source()
    if before['events'] == 0 or before['head_sha256'] != expected_post_budget_head:
        raise ValueError('PAPER_PROVIDER_POST_RECOVERY_SOURCE_REQUIRED')
    completed = budget.history()
    check_post_source()
    if (not before['review_required'] or before['pending_token'] is not None or
            not completed or completed[-1]['outcome'] != 'OUTCOME_UNKNOWN'):
        raise ValueError('PAPER_PROVIDER_POST_RECOVERY_SOURCE_REQUIRED')
    with budget.ownership() as owner:
        with journal.locked_request_snapshot(client_order_id,
                expected_head=expected_request_head) as read_request:
            expected = _proof(read_request(), client_order_id,
                expected_request_head)
            if (expected['recovery_state'] !=
                    'OBSERVED_TERMINAL_RECONCILIATION_REQUIRED' or
                    expected['lookup_by_original_client_id_required'] is not False):
                raise ValueError(
                    'PAPER_PROVIDER_POST_RECOVERY_TERMINAL_PROOF_REQUIRED')
            def check():
                return _proof(read_request(), client_order_id,
                    expected_request_head)
            check_post_source()
            write_error = None
            try:
                budget.recover_resolved_unknown(
                    expected_head=expected_post_budget_head,
                    expected_account_id=expected_account_id,
                    expected_payload_sha256=expected['payload_sha256'],
                    expected_client_order_id=client_order_id,
                    expected_request_resolution_head=expected_request_head,
                    resolution_check=check,
                    confirm_original_id_resolved=confirm_original_id_resolved,
                    confirm_no_resubmission=confirm_no_resubmission,
                    admission=admission, ownership=owner)
            except BaseException as error:
                # The recovery commit acknowledgement may be lost.  Trust
                # neither the return value nor raised detail; the exact
                # durable recovery is proved below while request-writer and
                # provider-budget ownership are still held.
                write_error = error
            receipt_error = None
            try:
                check_post_identity()
                committed_fingerprint = capture_file_fingerprints(
                    (post_resolved,), (MAX_DATABASE_BYTES,),
                    'PAPER_PROVIDER_POST_RECOVERY_')
                with guard_file_set_finalization((post_resolved,),
                        (MAX_DATABASE_BYTES,), post_identity,
                        committed_fingerprint,
                        'PAPER_PROVIDER_POST_RECOVERY_'):
                    receipt = budget.recovery_receipt()
                    check_post_identity()
                    require_file_fingerprints((post_resolved,),
                        (MAX_DATABASE_BYTES,), committed_fingerprint,
                        'PAPER_PROVIDER_POST_RECOVERY_')
                    receipt_again = budget.recovery_receipt()
                    check_post_identity()
                    require_file_fingerprints((post_resolved,),
                        (MAX_DATABASE_BYTES,), committed_fingerprint,
                        'PAPER_PROVIDER_POST_RECOVERY_')
                    # The budget recovery and both of its cold
                    # acknowledgements are one cross-journal proof window.
                    # Re-read the exact request proof after the
                    # acknowledgements so a non-cooperating same-inode writer
                    # cannot alter the request journal after the transaction's
                    # final resolution check and still receive success.
                    final_request_proof = check()
                    account_digest = hashlib.sha256(
                        expected_account_id.encode()).hexdigest()
                    client_digest = hashlib.sha256(
                        client_order_id.encode()).hexdigest()
                    stable = (final_request_proof == expected and
                        receipt == receipt_again and
                        receipt['head_sha256'] ==
                            receipt['recovery_head_sha256'] and
                        receipt['recoveries'] == before['recoveries'] + 1 and
                        receipt['review_required'] is False and
                        receipt['pending_token'] is None and
                        receipt['last_evaluated_at'] ==
                            receipt['recovered_at'] and
                        receipt['next_allowed_at'] ==
                            before['next_allowed_at'] and
                        receipt['account_sha256'] == account_digest and
                        receipt['payload_sha256'] ==
                            expected['payload_sha256'] and
                        receipt['client_order_sha256'] == client_digest and
                        receipt['blocked_attempt_head_sha256'] ==
                            completed[-1]['after_head_sha256'] and
                        receipt['request_resolution_head_sha256'] ==
                            expected_request_head and
                        receipt['exact_recovery_replayed'] is True and
                        receipt['read_only'] is True and
                        all(receipt[key] is False for key in (
                            'original_request_rearmed', 'automatic_retry',
                            'automatic_continuation',
                            'broker_account_reconciled', 'ready_for_routing',
                            'live_orders_enabled',
                            'money_movement_enabled', 'release_approved')))
                    if not stable:
                        raise ValueError(
                            'PAPER_PROVIDER_POST_RECOVERY_DURABLE_STATE_CHANGED')
                    result = dict(
                        mode='PAPER_PROVIDER_POST_BUDGET_RESOLVED_UNKNOWN_RECOVERY',
                        status='REVIEW_REQUIRED',
                        head_sha256=receipt['recovery_head_sha256'],
                        request_resolution_head_sha256=expected_request_head,
                        blocked_outcome='OUTCOME_UNKNOWN',
                        review_block_cleared=True,
                        next_allowed_at=receipt['next_allowed_at'],
                        cooldown_shortened=False, uncertainty_preserved=True,
                        original_request_rearmed=False, history_rewritten=False,
                        attempt_capacity_reclaimed=False, admission_reserved=False,
                        automatic_retry=False, automatic_continuation=False,
                        broker_wide_quota_verified=False,
                        distributed_ownership_verified=False,
                        broker_account_reconciled=False, ready_for_routing=False,
                        live_orders_enabled=False, money_movement_enabled=False,
                        release_approved=False,
                        recovery_acknowledgement_cold_replayed=True,
                        post_budget_file_identity_rechecked=True,
                        post_budget_file_fingerprint_rechecked=True,
                        post_budget_file_set_rechecked_on_return=True)
                    return dict(result, request_writer_exclusion_held=True,
                        request_path_identity_rechecked=True,
                        request_file_fingerprint_rechecked=True,
                        request_replay_fingerprint_rechecked_after_load=True,
                        request_proof_final_before_budget_commit=True,
                        request_proof_rechecked_after_budget_receipts=True,
                        request_snapshot_rechecked_on_exit=True,
                        post_budget_ownership_rechecked_on_exit=True)
            except BaseException as error:
                receipt_error = error
                stable = False
            if not stable:
                if (type(receipt_error) is ValueError and
                        str(receipt_error).startswith((
                            'PAPER_PROVIDER_POST_RECOVERY_FILE_IDENTITY_',
                            'PAPER_PROVIDER_POST_RECOVERY_FILE_SET_',
                            'PAPER_PROVIDER_POST_RECOVERY_SQLITE_SIDECAR_',
                            'BROKER_JOURNAL_LOCKED_SNAPSHOT_'))):
                    raise receipt_error
                if (type(write_error) is ValueError and
                        str(write_error).startswith((
                            'PAPER_PROVIDER_POST_BUDGET_',
                            'BROKER_JOURNAL_'))):
                    raise write_error
                raise ValueError(
                    'PAPER_PROVIDER_POST_RECOVERY_DURABLE_STATE_CHANGED')


def fixture_report():
    """Disposable terminal-resolution composition; no credentials or IO."""
    import tempfile
    from pathlib import Path
    from nexus import alpaca_paper_contract as contract

    account = '01234567-89ab-cdef-0123-456789abcdef'
    asset = dict(symbol='BTC/USD', **{'class':'crypto'}, status='active',
        tradable=True, fractionable=True, min_order_size='.5',
        min_trade_increment='.5', price_increment='.5')
    plan = contract.preview('nx_post_recovery_fixture','BTC-USD','BUY','2','100',
        asset,metadata_at=1000,evaluated_at=1001)
    raw = dict(**plan['payload'],id='01234567-89ab-cdef-0123-456789abcdef',
        asset_class='crypto',status='filled',filled_qty='2',filled_avg_price='99',
        updated_at='2026-10-23T18:00:00Z')
    with tempfile.TemporaryDirectory(prefix='nexus-post-recovery-') as directory:
        root=Path(directory);journal=BrokerRequestJournal(root/'requests.sqlite3',account)
        budget=PaperProviderPostBudget(root/'post-budget.sqlite3')
        journal.prepare(plan);selected=journal.lookup_request('nx_post_recovery_fixture')
        with budget.ownership() as owner:
            token=budget.begin(account_id=account,
                request_before_head_sha256=selected['head_sha256'],
                payload_sha256=plan['payload_sha256'],
                client_order_id='nx_post_recovery_fixture',admission=lambda:1000,
                ownership=owner,expected_head='0'*64)
            journal.mark_submission_uncertain('nx_post_recovery_fixture')
            budget.finish(token,outcome='OUTCOME_UNKNOWN',
                request_after_head_sha256=journal.recovery()['head_sha256'],
                actual_posts=1,admission=lambda:1001,ownership=owner)
        blocked=budget.inspect();history=budget.history();unresolved_denied=False
        try:
            recover(budget.path,journal.path,expected_account_id=account,
                client_order_id='nx_post_recovery_fixture',
                expected_post_budget_head=blocked['head_sha256'],
                expected_request_head=journal.recovery()['head_sha256'],
                confirm_original_id_resolved=True,confirm_no_resubmission=True,
                admission=lambda:1002)
        except ValueError:
            unresolved_denied=budget.inspect()==blocked
        journal.observe('nx_post_recovery_fixture',raw)
        original_recover = PaperProviderPostBudget.recover_resolved_unknown
        lost_acknowledgements = 0
        def commit_then_lose_ack(self,*args,**kwargs):
            nonlocal lost_acknowledgements
            lost_acknowledgements += 1
            original_recover(self,*args,**kwargs)
            raise RuntimeError('fixture private lost recovery acknowledgement')
        PaperProviderPostBudget.recover_resolved_unknown = commit_then_lose_ack
        try:
            result=recover(budget.path,journal.path,expected_account_id=account,
                client_order_id='nx_post_recovery_fixture',
                expected_post_budget_head=blocked['head_sha256'],
                expected_request_head=journal.recovery()['head_sha256'],
                confirm_original_id_resolved=True,confirm_no_resubmission=True,
                admission=lambda:1002)
        finally:
            PaperProviderPostBudget.recover_resolved_unknown = original_recover
        retained=journal.recovery()['requests'][0];after=budget.inspect()
        checks=dict(unresolved_request_denied=unresolved_denied,
            exact_terminal_resolution_bound=result['review_block_cleared'] is True,
            request_writer_excluded_through_commit=
                result['request_writer_exclusion_held'] is True,
            request_path_identity_rechecked=
                result['request_path_identity_rechecked'] is True,
            request_file_fingerprint_rechecked=
                result['request_file_fingerprint_rechecked'] is True,
            request_replay_fingerprint_rechecked_after_load=
                result['request_replay_fingerprint_rechecked_after_load'] is True,
            request_proof_final_before_budget_commit=
                result['request_proof_final_before_budget_commit'] is True,
            request_proof_rechecked_after_budget_receipts=
                result['request_proof_rechecked_after_budget_receipts'] is True,
            request_snapshot_rechecked_on_exit=
                result['request_snapshot_rechecked_on_exit'] is True,
            post_budget_ownership_rechecked_on_exit=
                result['post_budget_ownership_rechecked_on_exit'] is True,
            recovery_acknowledgement_cold_replayed=
                result['recovery_acknowledgement_cold_replayed'] is True and
                lost_acknowledgements == 1 and after['recoveries'] == 1,
            post_budget_file_identity_rechecked=
                result['post_budget_file_identity_rechecked'] is True,
            post_budget_file_fingerprint_rechecked=
                result['post_budget_file_fingerprint_rechecked'] is True,
            post_budget_file_set_rechecked_on_return=
                result['post_budget_file_set_rechecked_on_return'] is True,
            original_request_not_rearmed=result['original_request_rearmed'] is False and
                retained['submission_attempt_marked'],
            unknown_history_preserved=budget.history()==history,
            cooldown_preserved=after['next_allowed_at']==blocked['next_allowed_at'] and
                result['cooldown_shortened'] is False)
        return dict(source='TEMPORARY_RESOLVED_UNKNOWN_POST_RECOVERY_FIXTURE',
            passed=all(checks.values()),**checks,actual_provider_access=False,
            actual_vault_access=False,broker_account_reconciled=False,
            spendable_funds_verified=False,ready_for_routing=False,
            live_orders_enabled=False,money_movement_enabled=False,
            release_approved=False)


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument('--post-budget', required=True,
        help='Existing private provider POST-budget SQLite path outside the application/source directory.')
    parser.add_argument('--request-journal', required=True,
        help='Existing private broker request-journal SQLite path outside the application/source directory.')
    parser.add_argument('--expected-account', required=True,
        help='Exact paper account UUID binding both journals.')
    parser.add_argument('--client-order-id', required=True,
        help='Original client order ID whose terminal lookup was reviewed.')
    parser.add_argument('--expected-post-budget-head', required=True,
        help='Exact current provider POST-budget SHA-256 head.')
    parser.add_argument('--expected-request-head', required=True,
        help='Exact request-journal SHA-256 head containing the terminal observation.')
    parser.add_argument('--confirm-original-id-resolved', action='store_true', required=True,
        help='Confirm the original client ID has a retained terminal observation.')
    parser.add_argument('--confirm-no-resubmission', action='store_true', required=True,
        help='Confirm the original request must remain permanently non-resubmittable.')
    args = parser.parse_args()
    try:
        result = recover(args.post_budget, args.request_journal,
            expected_account_id=args.expected_account,
            client_order_id=args.client_order_id,
            expected_post_budget_head=args.expected_post_budget_head,
            expected_request_head=args.expected_request_head,
            confirm_original_id_resolved=args.confirm_original_id_resolved,
            confirm_no_resubmission=args.confirm_no_resubmission)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, OverflowError,
            MemoryError, RecursionError, KeyboardInterrupt):
        print('PAPER_PROVIDER_POST_RECOVERY_FAILED')
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
