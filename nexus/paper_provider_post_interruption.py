"""Reviewed interruption of one replaced-owner paper POST budget tail.

This command performs no provider or credential IO.  It converts one exact
pending reservation to permanent unknown-outcome review only after the local
owner marker identity changed and both consequences are explicitly confirmed.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_provider_post_budget import (
    MAX_DATABASE_BYTES, PaperProviderPostBudget)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities,
    guard_file_set_finalization, require_file_fingerprints,
    require_file_identities)

CHECKS = ('ordinary_interrupt_refused_replaced_owner',
    'replaced_owner_interrupted_exactly_once',
    'lost_acknowledgement_cold_replayed',
    'unknown_outcome_and_cooldown_preserved')
FLAGS = ('actual_provider_access', 'actual_vault_access', 'owner_repaired',
    'attempt_rearmed', 'automatic_retry', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved')
PUBLIC_FIXTURE_KEYS = frozenset(('source','passed',*CHECKS,*FLAGS))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_PROVIDER_POST_INTERRUPTION_USAGE_ERROR\n')


def interrupt(post_budget_path, *, expected_head, expected_token,
        confirm_owner_replaced=False, confirm_uncertainty_preserved=False,
        admission=time.time):
    """Append at most one exact interruption and cold-replay its receipt."""
    if (confirm_owner_replaced is not True or
            confirm_uncertainty_preserved is not True):
        raise ValueError(
            'PAPER_PROVIDER_POST_INTERRUPTION_CONFIRMATIONS_REQUIRED')
    post_path, post_resolved = _private_path(post_budget_path)
    budget = PaperProviderPostBudget(post_path)
    identity = capture_file_identities((post_resolved,),
        'PAPER_PROVIDER_POST_INTERRUPTION_')
    fingerprint = capture_file_fingerprints((post_resolved,),
        (MAX_DATABASE_BYTES,), 'PAPER_PROVIDER_POST_INTERRUPTION_')

    def check_identity():
        require_file_identities((post_resolved,), identity,
            'PAPER_PROVIDER_POST_INTERRUPTION_')

    def check_source():
        check_identity()
        require_file_fingerprints((post_resolved,), (MAX_DATABASE_BYTES,),
            fingerprint, 'PAPER_PROVIDER_POST_INTERRUPTION_')

    before = budget.inspect(); check_source()
    already_recorded = False
    pending = None
    if (before['events'] > 0 and before['head_sha256'] == expected_head and
            before['pending_token'] == expected_token and
            before['review_required'] is False):
        pending = budget.pending_receipt(); check_source()
        if (pending['head_sha256'] != expected_head or
                pending['token'] != expected_token or
                pending['exact_pending_reservation_replayed'] is not True or
                pending['read_only'] is not True):
            raise ValueError(
                'PAPER_PROVIDER_POST_INTERRUPTION_SOURCE_REQUIRED')
    elif (before['events'] > 0 and before['pending_token'] is None and
            before['review_required'] is True):
        recorded = budget.replaced_owner_interruption_receipt(); check_source()
        if (recorded['head_sha256'] != before['head_sha256'] or
                recorded['previous_head_sha256'] != expected_head or
                recorded['token'] != expected_token or
                recorded['exact_interruption_replayed'] is not True or
                recorded['read_only'] is not True):
            raise ValueError(
                'PAPER_PROVIDER_POST_INTERRUPTION_SOURCE_REQUIRED')
        already_recorded = True
    else:
        raise ValueError('PAPER_PROVIDER_POST_INTERRUPTION_SOURCE_REQUIRED')

    with budget.ownership() as owner:
        replacement_owner = owner.identity()
        retained_owner = (recorded['retained_owner_sha256'] if
            already_recorded else pending['owner_sha256'])
        if replacement_owner == retained_owner:
            raise ValueError(
                'PAPER_PROVIDER_POST_INTERRUPTION_OWNER_REPLACEMENT_REQUIRED')
        if (already_recorded and
                replacement_owner != recorded['replacement_owner_sha256']):
            raise ValueError(
                'PAPER_PROVIDER_POST_INTERRUPTION_OWNER_REPLACEMENT_REQUIRED')
        check_source(); write_error = None
        if not already_recorded:
            try:
                budget.interrupt_replaced_owner(expected_token,
                    expected_head=expected_head,
                    confirm_owner_replaced=confirm_owner_replaced,
                    confirm_uncertainty_preserved=
                        confirm_uncertainty_preserved,
                    admission=admission, ownership=owner)
            except BaseException as error:
                # Acknowledgement may be lost after the exact append.  Never
                # retry the append; only the cold receipt may prove success.
                write_error = error
        receipt_error = None
        try:
            check_identity()
            committed_fingerprint = capture_file_fingerprints(
                (post_resolved,), (MAX_DATABASE_BYTES,),
                'PAPER_PROVIDER_POST_INTERRUPTION_')
            with guard_file_set_finalization((post_resolved,),
                    (MAX_DATABASE_BYTES,), identity, committed_fingerprint,
                    'PAPER_PROVIDER_POST_INTERRUPTION_'):
                receipt = budget.replaced_owner_interruption_receipt()
                check_identity()
                require_file_fingerprints((post_resolved,),
                    (MAX_DATABASE_BYTES,), committed_fingerprint,
                    'PAPER_PROVIDER_POST_INTERRUPTION_')
                receipt_again = budget.replaced_owner_interruption_receipt()
                check_identity()
                require_file_fingerprints((post_resolved,),
                    (MAX_DATABASE_BYTES,), committed_fingerprint,
                    'PAPER_PROVIDER_POST_INTERRUPTION_')
                after = budget.inspect()
                check_identity()
                require_file_fingerprints((post_resolved,),
                    (MAX_DATABASE_BYTES,), committed_fingerprint,
                    'PAPER_PROVIDER_POST_INTERRUPTION_')
                stable = (receipt == receipt_again and
                    receipt['head_sha256'] ==
                        receipt['interruption_head_sha256'] and
                    receipt['previous_head_sha256'] == expected_head and
                    receipt['token'] == expected_token and
                    receipt['retained_owner_sha256'] == retained_owner and
                    receipt['replacement_owner_sha256'] ==
                        replacement_owner and
                    receipt['retained_owner_sha256'] !=
                        receipt['replacement_owner_sha256'] and
                    receipt['outcome'] == 'INTERRUPTED_UNKNOWN' and
                    receipt['pending_token'] is None and
                    receipt['review_required'] is True and
                    receipt['next_allowed_at'] ==
                        receipt['interrupted_at'] + 86400 and
                    receipt['exact_interruption_replayed'] is True and
                    receipt['read_only'] is True and
                    after['head_sha256'] == receipt['head_sha256'] and
                    after['events'] == before['events'] +
                        (0 if already_recorded else 1) and
                    after['completed_attempts'] ==
                        before['completed_attempts'] +
                        (0 if already_recorded else 1) and
                    after['pending_token'] is None and
                    after['review_required'] is True and
                    all(receipt[key] is False for key in (
                        'automatic_retry','automatic_recovery',
                        'owner_repaired','attempt_rearmed',
                        'ready_for_routing','release_approved')))
                if not stable:
                    raise ValueError(
                        'PAPER_PROVIDER_POST_INTERRUPTION_DURABLE_STATE_CHANGED')
                return dict(
                    mode='PAPER_PROVIDER_POST_REPLACED_OWNER_INTERRUPTION',
                    status='REVIEW_REQUIRED',
                    head_sha256=receipt['interruption_head_sha256'],
                    previous_head_sha256=expected_head,
                    retained_owner_sha256=receipt['retained_owner_sha256'],
                    replacement_owner_sha256=
                        receipt['replacement_owner_sha256'],
                    outcome='INTERRUPTED_UNKNOWN', review_required=True,
                    unknown_cooldown_seconds=86400,
                    uncertainty_preserved=True, provider_accessed=False,
                    vault_accessed=False, automatic_retry=False,
                    automatic_recovery=False, owner_repaired=False,
                    attempt_rearmed=False, ready_for_routing=False,
                    live_orders_enabled=False, money_movement_enabled=False,
                    release_approved=False,
                    interruption_acknowledgement_cold_replayed=True,
                    interruption_already_recorded=already_recorded,
                    post_budget_file_identity_rechecked=True,
                    post_budget_file_fingerprint_rechecked=True,
                    post_budget_file_set_rechecked_on_return=True,
                    post_budget_ownership_rechecked_on_exit=True)
        except BaseException as error:
            receipt_error = error
        if (type(receipt_error) is ValueError and
                str(receipt_error).startswith((
                    'PAPER_PROVIDER_POST_INTERRUPTION_FILE_IDENTITY_',
                    'PAPER_PROVIDER_POST_INTERRUPTION_FILE_SET_',
                    'PAPER_PROVIDER_POST_INTERRUPTION_SQLITE_SIDECAR_'))):
            raise receipt_error
        if (type(write_error) is ValueError and
                str(write_error).startswith('PAPER_PROVIDER_POST_BUDGET_')):
            raise write_error
        raise ValueError(
            'PAPER_PROVIDER_POST_INTERRUPTION_DURABLE_STATE_CHANGED')


class _PrivateOutput:
    """Create-only receipt held across the irreversible journal append."""
    def __init__(self, output_path):
        self.path, _resolved = _private_path(output_path)
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        self.descriptor = os.open(self.path, flags, 0o600)
        observed = os.fstat(self.descriptor)
        path_observed = os.lstat(self.path)
        if (not stat.S_ISREG(observed.st_mode) or
                (observed.st_dev, observed.st_ino) !=
                (path_observed.st_dev, path_observed.st_ino)):
            self._abort()
            raise OSError('PAPER_PROVIDER_POST_INTERRUPTION_OUTPUT_CHANGED')
        self.identity = (observed.st_dev, observed.st_ino)
        self.written = False

    def __enter__(self):
        return self

    def write(self, value):
        payload = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
        offset = 0
        while offset < len(payload):
            count = os.write(self.descriptor, payload[offset:])
            if type(count) is not int or count <= 0:
                raise OSError(
                    'PAPER_PROVIDER_POST_INTERRUPTION_OUTPUT_SHORT_WRITE')
            offset += count
        os.fsync(self.descriptor)
        observed = os.fstat(self.descriptor)
        path_observed = os.lstat(self.path)
        if ((observed.st_dev, observed.st_ino) != self.identity or
                (path_observed.st_dev, path_observed.st_ino) != self.identity or
                observed.st_size != len(payload)):
            raise OSError('PAPER_PROVIDER_POST_INTERRUPTION_OUTPUT_CHANGED')
        if os.name != 'nt':
            directory = os.open(self.path.parent,
                os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self.written = True

    def _abort(self):
        descriptor = getattr(self, 'descriptor', None)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self.descriptor = None
        identity = getattr(self, 'identity', None)
        try:
            observed = os.lstat(self.path)
            if identity is None or (observed.st_dev, observed.st_ino) == identity:
                self.path.unlink()
        except OSError:
            pass

    def __exit__(self, error_type, _error, _traceback):
        try:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None
            if error_type is None and self.written:
                observed = os.lstat(self.path)
                if (observed.st_dev, observed.st_ino) != self.identity:
                    raise OSError(
                        'PAPER_PROVIDER_POST_INTERRUPTION_OUTPUT_CHANGED')
                return False
        except BaseException:
            self._abort()
            raise
        self._abort()
        return False


def _write_private_output(value, output_path):
    with _PrivateOutput(output_path) as output:
        output.write(value)


def fixture_report():
    """Disposable no-provider replacement and lost-ack composition."""
    import tempfile
    with tempfile.TemporaryDirectory(
            prefix='nexus-post-interruption-') as directory:
        root = Path(directory)
        budget = PaperProviderPostBudget(root/'post-budget.sqlite3')
        owner = budget.ownership().__enter__()
        token = budget.begin(
            account_id='01234567-89ab-cdef-0123-456789abcdef',
            request_before_head_sha256='a'*64, payload_sha256='b'*64,
            client_order_id='nx_replaced_owner_fixture',
            admission=lambda:1000, ownership=owner, expected_head='0'*64)
        owner.__exit__()
        replacement = root/'replacement-owner.lock'
        owner_path = budget.path.with_name(budget.path.name+'.owner.lock')
        replacement.write_bytes(owner_path.read_bytes())
        os.replace(replacement, owner_path)
        before = budget.inspect(); ordinary_refused = False
        try:
            budget.interrupt(token, expected_head=before['head_sha256'],
                confirm_interrupted=True, admission=lambda:1002)
        except ValueError as error:
            ordinary_refused = str(error).endswith('OWNER_SCOPE_CHANGED')
        original = PaperProviderPostBudget.interrupt_replaced_owner
        lost = 0
        def commit_then_lose_ack(self, *args, **kwargs):
            nonlocal lost
            lost += 1; original(self, *args, **kwargs)
            raise RuntimeError('fixture private lost acknowledgement')
        PaperProviderPostBudget.interrupt_replaced_owner = \
            commit_then_lose_ack
        try:
            result = interrupt(budget.path,
                expected_head=before['head_sha256'], expected_token=token,
                confirm_owner_replaced=True,
                confirm_uncertainty_preserved=True,
                admission=lambda:1002)
        finally:
            PaperProviderPostBudget.interrupt_replaced_owner = original
        after = budget.inspect(); history = budget.history()
        checks = dict(
            ordinary_interrupt_refused_replaced_owner=
                ordinary_refused,
            replaced_owner_interrupted_exactly_once=
                after['events'] == before['events'] + 1 and
                len(history) == 1 and history[0]['actual_posts'] is None,
            lost_acknowledgement_cold_replayed=
                lost == 1 and
                result['interruption_acknowledgement_cold_replayed'] is True,
            unknown_outcome_and_cooldown_preserved=
                history[0]['outcome'] == 'INTERRUPTED_UNKNOWN' and
                after['review_required'] is True and
                after['next_allowed_at'] == 87402)
        return dict(
            source='TEMPORARY_REPLACED_OWNER_INTERRUPTION_FIXTURE',
            passed=all(checks.values()), **checks,
            actual_provider_access=False, actual_vault_access=False,
            owner_repaired=False, attempt_rearmed=False,
            automatic_retry=False, ready_for_routing=False,
            live_orders_enabled=False, money_movement_enabled=False,
            release_approved=False)


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument('--post-budget', required=True)
    parser.add_argument('--expected-head', required=True)
    parser.add_argument('--expected-token', required=True)
    parser.add_argument('--confirm-owner-replaced', action='store_true',
        required=True)
    parser.add_argument('--confirm-uncertainty-preserved', action='store_true',
        required=True)
    parser.add_argument('--output', required=True,
        help='New private JSON receipt path outside the source/application tree.')
    args = parser.parse_args()
    try:
        # Reserve the create-only acknowledgement before the irreversible
        # append.  If writing it fails after commit, an exact rerun may only
        # cold-replay the already-recorded interruption.
        with _PrivateOutput(args.output) as output:
            result = interrupt(args.post_budget,
                expected_head=args.expected_head,
                expected_token=args.expected_token,
                confirm_owner_replaced=args.confirm_owner_replaced,
                confirm_uncertainty_preserved=
                    args.confirm_uncertainty_preserved)
            output.write(result)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error,
            OverflowError, MemoryError, RecursionError, KeyboardInterrupt):
        print('PAPER_PROVIDER_POST_INTERRUPTION_FAILED')
        return 2
    print('PAPER_PROVIDER_POST_INTERRUPTION_RECORDED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
