"""Explicit reviewed recovery for one blocked account paper-read journal.

This command never reads credentials, contacts a provider, retries a read,
reclaims capacity, shortens cooldowns or starts the desktop. It appends one
content-free recovery boundary to an existing private account journal.
"""
import argparse
import json
import sqlite3
import time

from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_read_pacing import MAX_DATABASE_BYTES, PaperReadPacing
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities,
    guard_file_set_finalization, require_file_fingerprints,
    require_file_identities)


class _Parser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,'PAPER_READ_PACING_RECOVERY_USAGE_ERROR\n')


def recover(path, *, expected_head, expected_account_id,
        confirm_block_reviewed=False, confirm_uncertainty_preserved=False,
        admission=time.time):
    """Recover one exact account-scoped blocked tail without creating state."""
    private_path,private_resolved=_private_path(path)
    gate=PaperReadPacing(private_path,expected_account_id)
    identity=capture_file_identities((private_resolved,),
        'PAPER_READ_PACING_RECOVERY_')
    fingerprint=capture_file_fingerprints((private_resolved,),
        (MAX_DATABASE_BYTES,),'PAPER_READ_PACING_RECOVERY_')
    def check_identity():
        require_file_identities((private_resolved,),identity,
            'PAPER_READ_PACING_RECOVERY_')
    def check_source():
        check_identity()
        require_file_fingerprints((private_resolved,),
            (MAX_DATABASE_BYTES,),fingerprint,'PAPER_READ_PACING_RECOVERY_')
    before=gate.inspect()
    check_source()
    history=gate.operation_history()
    check_source()
    if (before['events']==0 or before['head_sha256']!=expected_head or
            not before['review_required'] or before['pending_token'] is not None or
            not history or history[-1]['outcome'] not in
            ('AUTH_DENIED','REVIEW_REQUIRED','INTERRUPTED_UNKNOWN')):
        raise ValueError('PAPER_READ_PACING_RECOVERY_SOURCE_REQUIRED')
    with gate.ownership() as owner:
        check_source();write_error=None
        try:
            gate.recover_review_block(expected_head=expected_head,
                expected_account_id=expected_account_id,
                confirm_block_reviewed=confirm_block_reviewed,
                confirm_uncertainty_preserved=confirm_uncertainty_preserved,
                admission=admission,ownership=owner)
        except BaseException as error:
            write_error=error
        receipt_error=None
        try:
            check_identity()
            committed=capture_file_fingerprints((private_resolved,),
                (MAX_DATABASE_BYTES,),'PAPER_READ_PACING_RECOVERY_')
            with guard_file_set_finalization((private_resolved,),
                    (MAX_DATABASE_BYTES,),identity,committed,
                    'PAPER_READ_PACING_RECOVERY_'):
                receipt=gate.recovery_receipt()
                check_identity()
                require_file_fingerprints((private_resolved,),
                    (MAX_DATABASE_BYTES,),committed,
                    'PAPER_READ_PACING_RECOVERY_')
                receipt_again=gate.recovery_receipt()
                check_identity()
                require_file_fingerprints((private_resolved,),
                    (MAX_DATABASE_BYTES,),committed,
                    'PAPER_READ_PACING_RECOVERY_')
                stable=(receipt==receipt_again and
                    receipt['head_sha256']==receipt['recovery_head_sha256'] and
                    receipt['recoveries']==before['recoveries']+1 and
                    receipt['sessions']==before['sessions'] and
                    receipt['review_required'] is False and
                    receipt['pending_token'] is None and
                    receipt['last_evaluated_at']==receipt['recovered_at'] and
                    receipt['next_allowed_at']==before['next_allowed_at'] and
                    receipt['account_sha256']==gate.account_digest and
                    receipt['blocked_session_head_sha256']==
                        history[-1]['after_head_sha256'] and
                    receipt['blocked_outcome']==history[-1]['outcome'] and
                    receipt['exact_recovery_replayed'] is True and
                    receipt['read_only'] is True and
                    receipt['uncertainty_preserved'] is True and
                    all(receipt[key] is False for key in (
                        'history_rewritten','session_capacity_reclaimed',
                        'admission_reserved','automatic_retry',
                        'automatic_continuation','broker_wide_quota_verified',
                        'distributed_ownership_verified','ready_for_routing',
                        'live_orders_enabled','money_movement_enabled',
                        'release_approved')))
                if not stable:
                    raise ValueError(
                        'PAPER_READ_PACING_RECOVERY_DURABLE_STATE_CHANGED')
                return dict(mode='PAPER_READ_PACING_REVIEWED_RECOVERY',
                    status='REVIEW_REQUIRED',
                    head_sha256=receipt['recovery_head_sha256'],
                    blocked_outcome=receipt['blocked_outcome'],
                    review_block_cleared=True,
                    next_allowed_at=receipt['next_allowed_at'],
                    cooldown_shortened=False,uncertainty_preserved=True,
                    history_rewritten=False,session_capacity_reclaimed=False,
                    admission_reserved=False,automatic_retry=False,
                    automatic_continuation=False,
                    broker_wide_quota_verified=False,
                    distributed_ownership_verified=False,
                    ready_for_routing=False,live_orders_enabled=False,
                    money_movement_enabled=False,release_approved=False,
                    recovery_acknowledgement_cold_replayed=True,
                    pacing_file_identity_rechecked=True,
                    pacing_file_fingerprint_rechecked=True,
                    pacing_file_set_rechecked_on_return=True,
                    pacing_ownership_rechecked_on_exit=True)
        except BaseException as error:
            receipt_error=error;stable=False
        if not stable:
            if (type(receipt_error) is ValueError and
                    str(receipt_error).startswith((
                        'PAPER_READ_PACING_RECOVERY_FILE_IDENTITY_',
                        'PAPER_READ_PACING_RECOVERY_FILE_SET_',
                        'PAPER_READ_PACING_RECOVERY_SQLITE_SIDECAR_'))):
                raise receipt_error
            if (type(write_error) is ValueError and
                    str(write_error).startswith('PAPER_READ_PACING_')):
                raise write_error
            raise ValueError(
                'PAPER_READ_PACING_RECOVERY_DURABLE_STATE_CHANGED')


RECOVERY_CHECKS=('exact_review_block_bound',
    'recovery_acknowledgement_cold_replayed','pacing_file_identity_rechecked',
    'pacing_file_fingerprint_rechecked','pacing_file_set_rechecked_on_return',
    'pacing_ownership_rechecked_on_exit','blocked_history_preserved',
    'cooldown_preserved','session_capacity_not_reclaimed')
RECOVERY_FLAGS=('actual_provider_access','actual_vault_access',
    'broker_account_reconciled','spendable_funds_verified',
    'ready_for_routing','live_orders_enabled','money_movement_enabled',
    'release_approved')


def fixture_report():
    """Disposable lost-acknowledgement recovery; no credentials or IO."""
    import tempfile
    from pathlib import Path
    account='01234567-89ab-cdef-0123-456789abcdef'
    with tempfile.TemporaryDirectory(
            prefix='nexus-account-read-recovery-') as directory:
        gate=PaperReadPacing(Path(directory)/'pacing.sqlite3',account)
        token=gate.begin(admission=lambda:1000)
        gate.finish(token,admission=lambda:1001,status_code=401)
        before=gate.inspect();history=gate.operation_history()
        original=PaperReadPacing.recover_review_block
        lost_acknowledgements=0
        def commit_then_lose_ack(self,*args,**kwargs):
            nonlocal lost_acknowledgements
            lost_acknowledgements+=1
            original(self,*args,**kwargs)
            raise RuntimeError('fixture private lost recovery acknowledgement')
        PaperReadPacing.recover_review_block=commit_then_lose_ack
        try:
            result=recover(gate.path,expected_head=before['head_sha256'],
                expected_account_id=account,confirm_block_reviewed=True,
                confirm_uncertainty_preserved=True,admission=lambda:1002)
        finally:
            PaperReadPacing.recover_review_block=original
        after=gate.inspect()
        checks=dict(
            exact_review_block_bound=(result['blocked_outcome']=='AUTH_DENIED' and
                result['review_block_cleared'] is True),
            recovery_acknowledgement_cold_replayed=(
                result['recovery_acknowledgement_cold_replayed'] is True and
                lost_acknowledgements==1 and after['recoveries']==1),
            pacing_file_identity_rechecked=
                result['pacing_file_identity_rechecked'] is True,
            pacing_file_fingerprint_rechecked=
                result['pacing_file_fingerprint_rechecked'] is True,
            pacing_file_set_rechecked_on_return=
                result['pacing_file_set_rechecked_on_return'] is True,
            pacing_ownership_rechecked_on_exit=
                result['pacing_ownership_rechecked_on_exit'] is True,
            blocked_history_preserved=gate.operation_history()==history,
            cooldown_preserved=(result['next_allowed_at']==
                before['next_allowed_at']==after['next_allowed_at']),
            session_capacity_not_reclaimed=(
                after['sessions']==before['sessions']==1))
        return dict(source='TEMPORARY_ACCOUNT_READ_RECOVERY_FIXTURE',
            passed=all(checks.values()),**checks,
            actual_provider_access=False,actual_vault_access=False,
            broker_account_reconciled=False,spendable_funds_verified=False,
            ready_for_routing=False,live_orders_enabled=False,
            money_movement_enabled=False,release_approved=False)


def main():
    parser=_Parser(description=__doc__)
    parser.add_argument('--pacing',required=True,
        help='Existing private account pacing SQLite path outside the application/source directory.')
    parser.add_argument('--expected-head',required=True,
        help='Exact current account pacing SHA-256 head reviewed by the operator.')
    parser.add_argument('--expected-account',required=True,
        help='Exact paper account UUID bound to the pacing journal.')
    parser.add_argument('--confirm-reviewed-block',action='store_true',required=True,
        help='Confirm the retained authentication/review/interruption block was reviewed.')
    parser.add_argument('--confirm-preserve-uncertainty',action='store_true',required=True,
        help='Confirm retained uncertainty and cooldown must remain unchanged.')
    args=parser.parse_args()
    try:
        result=recover(args.pacing,expected_head=args.expected_head,
            expected_account_id=args.expected_account,
            confirm_block_reviewed=args.confirm_reviewed_block,
            confirm_uncertainty_preserved=args.confirm_preserve_uncertainty)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,OverflowError,
            MemoryError,RecursionError,KeyboardInterrupt):
        print('PAPER_READ_PACING_RECOVERY_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
