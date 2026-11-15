"""Disposable provider-budget write acknowledgement controls.

The fixture uses only local SQLite files and injected mutations. It performs no
provider request, reads no account credential, and grants no routing authority.
"""
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from nexus import alpaca_paper_contract as contract
from nexus import broker_request_journal as request_module
from nexus import paper_provider_post_budget as post_module
from nexus import paper_provider_read_budget as read_module
from nexus import paper_read_pacing as pacing_module
from nexus.paper_provider_post_budget import PaperProviderPostBudget, ZERO
from nexus.paper_provider_read_budget import PaperProviderReadBudget
from nexus.paper_read_pacing import PaperReadPacing
from nexus.broker_request_journal import BrokerRequestJournal

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
CHECKS = (
    'post_connection_content_pinned',
    'post_postclose_content_pinned',
    'read_connection_content_pinned',
    'read_postclose_content_pinned',
    'post_main_flush_required',
    'read_main_flush_required',
    'post_new_entry_flush_policy_pinned',
    'read_new_entry_flush_policy_pinned',
    'account_read_main_flush_required',
    'account_read_new_entry_flush_policy_pinned',
    'request_journal_main_flush_required',
    'request_journal_new_entry_flush_policy_pinned',
    'portable_windows_ctime_policy_pinned',
    'committed_uncertainty_retained',
)
FLAGS = (
    'actual_provider_access', 'actual_account_acceptance',
    'ready_for_routing', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved',
)


def _connection(counter, mutate_after_close=None):
    class EvidenceConnection(sqlite3.Connection):
        def serialize(self, *args, **kwargs):
            result = super().serialize(*args, **kwargs)
            counter.append(len(result))
            return result

        def close(self):
            super().close()
            if mutate_after_close is not None:
                observed = mutate_after_close.stat()
                os.utime(mutate_after_close, ns=(observed.st_atime_ns,
                    observed.st_mtime_ns + 1_000_000_000))
    return EvidenceConnection


def _deny_content(subject, invoke, code):
    counter = []
    original_connect = subject.sqlite3.connect
    original_fingerprint = subject._main_fingerprint

    def connect(database, *args, **kwargs):
        if '?mode=rw' in str(database):
            kwargs['factory'] = _connection(counter)
        return original_connect(database, *args, **kwargs)

    def changed(path):
        result = original_fingerprint(path)
        if counter:
            altered = list(result)
            altered[7] = 'f' * 64
            return tuple(altered)
        return result

    subject.sqlite3.connect = connect
    subject._main_fingerprint = changed
    try:
        invoke()
    except ValueError as error:
        denied = str(error) == code
    else:
        denied = False
    finally:
        subject.sqlite3.connect = original_connect
        subject._main_fingerprint = original_fingerprint
    return denied and len(counter) == 1


def _deny_postclose(subject, path, invoke, code):
    counter = []
    original_connect = subject.sqlite3.connect

    def connect(database, *args, **kwargs):
        if '?mode=rw' in str(database):
            kwargs['factory'] = _connection(counter, path)
        return original_connect(database, *args, **kwargs)

    subject.sqlite3.connect = connect
    try:
        invoke()
    except ValueError as error:
        denied = str(error) == code
    else:
        denied = False
    finally:
        subject.sqlite3.connect = original_connect
    return denied and len(counter) == 1


def _post_attempt(budget, client_order_id, ownership=None):
    if ownership is None:
        with budget.ownership() as owner:
            return _post_attempt(budget, client_order_id, owner)
    owner = ownership
    budget.begin(account_id=ACCOUNT,
        request_before_head_sha256='a' * 64,
        payload_sha256='b' * 64, client_order_id=client_order_id,
        admission=lambda: 1000, ownership=owner, expected_head=ZERO)


def _read_attempt(budget, ownership=None):
    if ownership is None:
        with budget.ownership() as owner:
            return _read_attempt(budget, owner)
    owner = ownership
    budget.begin(account_id=ACCOUNT,
        account_before_head_sha256=ZERO,
        operation='BTC_ASSET_RULES', admission=lambda: 1000,
        ownership=owner)


def _account_read_attempt(budget, ownership=None):
    if ownership is None:
        with budget.ownership() as owner:
            return _account_read_attempt(budget, owner)
    return budget.begin(admission=lambda: 1000, ownership=ownership)


def _deny_main_flush(subject, budget, invoke, code):
    original = subject.os.fsync
    attempts = []

    def fsync(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            attempts.append('file')
            raise OSError('injected private main-file flush failure')
        return original(descriptor)

    with budget.ownership() as owner:
        subject.os.fsync = fsync
        try:
            invoke(owner)
        except ValueError as error:
            denied = str(error) == code
        else:
            denied = False
        finally:
            subject.os.fsync = original
    return denied and attempts == ['file']


def _observe_new_entry_flush(subject, budget, invoke):
    original = subject.os.fsync
    calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)

    with budget.ownership() as owner:
        subject.os.fsync = fsync
        try:
            invoke(owner)
        finally:
            subject.os.fsync = original
    return calls == (['file'] if os.name == 'nt' else ['file', 'directory'])


def _request_plan():
    asset = dict(symbol='BTC/USD', **{'class': 'crypto'}, status='active',
        tradable=True, fractionable=True, min_order_size='.5',
        min_trade_increment='.5', price_increment='.5')
    return contract.preview('nx_request_flush_evidence', 'BTC-USD', 'BUY',
        '2', '100', asset, metadata_at=1000, evaluated_at=1001)


def _deny_request_main_flush(subject, invoke, code):
    original = subject.os.fsync
    attempts = []

    def fsync(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            attempts.append('file')
            raise OSError('injected private request-journal flush failure')
        return original(descriptor)

    subject.os.fsync = fsync
    try:
        invoke()
    except ValueError as error:
        denied = str(error) == code
    else:
        denied = False
    finally:
        subject.os.fsync = original
    return denied and attempts == ['file']


def _observe_request_new_entry_flush(subject, invoke):
    original = subject.os.fsync
    calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)

    subject.os.fsync = fsync
    try:
        invoke()
    finally:
        subject.os.fsync = original
    return calls == (['file'] if os.name == 'nt' else ['file', 'directory'])


def fixture_report():
    """Prove bounded content acknowledgement without external authority."""
    with tempfile.TemporaryDirectory(
            prefix='nexus-provider-budget-ack-') as directory:
        root = Path(directory)

        post_content = PaperProviderPostBudget(root / 'post-content.sqlite3')
        post_connection_content_pinned = _deny_content(
            post_module,
            lambda: _post_attempt(post_content, 'nx_content_evidence'),
            'PAPER_PROVIDER_POST_BUDGET_CONNECTION_CONTENT_CHANGED')

        post_close = PaperProviderPostBudget(root / 'post-close.sqlite3')
        post_postclose_content_pinned = _deny_postclose(
            post_module, post_close.path,
            lambda: _post_attempt(post_close, 'nx_postclose_evidence'),
            'PAPER_PROVIDER_POST_BUDGET_FILE_SET_CHANGED')

        read_content = PaperProviderReadBudget(root / 'read-content.sqlite3')
        read_connection_content_pinned = _deny_content(
            read_module, lambda: _read_attempt(read_content),
            'PAPER_PROVIDER_READ_BUDGET_CONNECTION_CONTENT_CHANGED')

        read_close = PaperProviderReadBudget(root / 'read-close.sqlite3')
        read_postclose_content_pinned = _deny_postclose(
            read_module, read_close.path, lambda: _read_attempt(read_close),
            'PAPER_PROVIDER_READ_BUDGET_FILE_SET_CHANGED')

        post_flush = PaperProviderPostBudget(root / 'post-flush.sqlite3')
        post_main_flush_required = _deny_main_flush(
            post_module, post_flush,
            lambda owner: _post_attempt(post_flush,
                'nx_post_flush_evidence', owner),
            'PAPER_PROVIDER_POST_BUDGET_COMMIT_DURABILITY_UNCERTAIN')

        read_flush = PaperProviderReadBudget(root / 'read-flush.sqlite3')
        read_main_flush_required = _deny_main_flush(
            read_module, read_flush,
            lambda owner: _read_attempt(read_flush, owner),
            'PAPER_PROVIDER_READ_BUDGET_COMMIT_DURABILITY_UNCERTAIN')

        post_entry = PaperProviderPostBudget(root / 'post-entry.sqlite3')
        post_new_entry_flush_policy_pinned = _observe_new_entry_flush(
            post_module, post_entry,
            lambda owner: _post_attempt(post_entry,
                'nx_post_entry_evidence', owner))

        read_entry = PaperProviderReadBudget(root / 'read-entry.sqlite3')
        read_new_entry_flush_policy_pinned = _observe_new_entry_flush(
            read_module, read_entry,
            lambda owner: _read_attempt(read_entry, owner))

        account_flush = PaperReadPacing(
            root / 'account-read-flush.sqlite3', ACCOUNT)
        account_read_main_flush_required = _deny_main_flush(
            pacing_module, account_flush,
            lambda owner: _account_read_attempt(account_flush, owner),
            'PAPER_READ_PACING_COMMIT_DURABILITY_UNCERTAIN')

        account_entry = PaperReadPacing(
            root / 'account-read-entry.sqlite3', ACCOUNT)
        account_read_new_entry_flush_policy_pinned = (
            _observe_new_entry_flush(
                pacing_module, account_entry,
                lambda owner: _account_read_attempt(account_entry, owner)))

        request_flush = BrokerRequestJournal(
            root / 'request-flush.sqlite3', ACCOUNT)
        request_flush.prepare(_request_plan())
        request_journal_main_flush_required = _deny_request_main_flush(
            request_module,
            lambda: request_flush.mark_submission_uncertain(
                'nx_request_flush_evidence'),
            'BROKER_JOURNAL_COMMIT_DURABILITY_UNCERTAIN')

        request_entry = BrokerRequestJournal(
            root / 'request-entry.sqlite3', ACCOUNT)
        request_journal_new_entry_flush_policy_pinned = (
            _observe_request_new_entry_flush(request_module,
                lambda: request_entry.prepare(_request_plan())))

        windows_fingerprint = ('C:/nexus/private.sqlite3', 1, 2, 4096,
            3, None, 1, 'a' * 64)
        invalid_windows_fingerprint = (*windows_fingerprint[:5], 4,
            *windows_fingerprint[6:])
        def rejects_nonportable_ctime(subject):
            try:
                subject._committed_fingerprint_boundary(
                    invalid_windows_fingerprint, host='nt')
            except RuntimeError:
                return True
            return False
        portable_windows_ctime_policy_pinned = (
            pacing_module._committed_fingerprint_boundary(
                windows_fingerprint, host='nt') == windows_fingerprint and
            request_module._committed_fingerprint_boundary(
                windows_fingerprint, host='nt') == windows_fingerprint and
            pacing_module._captured_fingerprint_boundary(
                invalid_windows_fingerprint, host='nt') ==
                    windows_fingerprint and
            request_module._captured_fingerprint_boundary(
                invalid_windows_fingerprint, host='nt') ==
                    windows_fingerprint and
            rejects_nonportable_ctime(pacing_module) and
            rejects_nonportable_ctime(request_module))

        states = (post_content.inspect(), post_close.inspect(),
            read_content.inspect(), read_close.inspect(), post_flush.inspect(),
            read_flush.inspect(), post_entry.inspect(), read_entry.inspect())
        provider_uncertainty_retained = all(
            state['pending_token'] is not None and
            state['ready_for_routing'] is False and
            state['release_approved'] is False
            for state in states)
        account_states = (account_flush.inspect(), account_entry.inspect())
        account_uncertainty_retained = all(
            state['pending_token'] is not None and
            state['ready_for_routing'] is False
            for state in account_states)
        request_state = request_flush.recovery()['requests'][0]
        request_uncertainty_retained = (
            request_state['submission_attempt_marked'] is True and
            request_state['automatic_retry'] is False and
            request_state['sendable'] is False)
        committed_uncertainty_retained = (
            provider_uncertainty_retained and account_uncertainty_retained and
            request_uncertainty_retained)

    checks = dict(
        post_connection_content_pinned=post_connection_content_pinned,
        post_postclose_content_pinned=post_postclose_content_pinned,
        read_connection_content_pinned=read_connection_content_pinned,
        read_postclose_content_pinned=read_postclose_content_pinned,
        post_main_flush_required=post_main_flush_required,
        read_main_flush_required=read_main_flush_required,
        post_new_entry_flush_policy_pinned=
            post_new_entry_flush_policy_pinned,
        read_new_entry_flush_policy_pinned=
            read_new_entry_flush_policy_pinned,
        account_read_main_flush_required=account_read_main_flush_required,
        account_read_new_entry_flush_policy_pinned=
            account_read_new_entry_flush_policy_pinned,
        request_journal_main_flush_required=
            request_journal_main_flush_required,
        request_journal_new_entry_flush_policy_pinned=
            request_journal_new_entry_flush_policy_pinned,
        portable_windows_ctime_policy_pinned=
            portable_windows_ctime_policy_pinned,
        committed_uncertainty_retained=committed_uncertainty_retained,
    )
    return dict(source='TEMPORARY_PROVIDER_BUDGET_ACK_FIXTURE',
        passed=all(checks.values()), **checks,
        **{flag: False for flag in FLAGS},
        limitations=('Injected local SQLite content and metadata changes only; '
            'explicit local budget/request flush observation is not '
            'physical-power-loss or native Windows acceptance; no provider '
            'connection, account '
            'acceptance, routing, funds, live trading or release approval.'))
