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
from nexus.paper_read_pacing import PaperReadPacing


class _Parser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,'PAPER_READ_PACING_RECOVERY_USAGE_ERROR\n')


def recover(path, *, expected_head, expected_account_id,
        confirm_block_reviewed=False, confirm_uncertainty_preserved=False,
        admission=time.time):
    """Recover one exact account-scoped blocked tail without creating state."""
    private_path,_=_private_path(path)
    gate=PaperReadPacing(private_path,expected_account_id)
    before=gate.inspect()
    if before['events']==0 or before['head_sha256']!=expected_head:
        raise ValueError('PAPER_READ_PACING_RECOVERY_SOURCE_REQUIRED')
    with gate.ownership() as owner:
        return gate.recover_review_block(expected_head=expected_head,
            expected_account_id=expected_account_id,
            confirm_block_reviewed=confirm_block_reviewed,
            confirm_uncertainty_preserved=confirm_uncertainty_preserved,
            admission=admission,ownership=owner)


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
