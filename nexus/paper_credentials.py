"""Explicit Windows paper-read credentials. No startup, order or funding authority.

One OS entry keeps the pair together. It is not a transactional database or a
cross-process lease. Python cannot promise erasure of immutable secret strings.
"""
import json
import os
import re
import threading
import time
from uuid import UUID, uuid4

from nexus.alpaca_paper_inspection import inspect_account

SERVICE = 'NexusOperativeBrokerPaper'
ACCOUNT = 'AlpacaPaperReadOnlyV1'
MAX_RECORD = 1024
_operations = threading.Lock()
_control = threading.Lock()
_epoch = 0
_blocked = False


class PaperVaultError(ValueError):
    pass


def _fail(code):
    raise PaperVaultError('PAPER_VAULT_' + code)


def _vault():
    if os.name != 'nt':
        _fail('WINDOWS_ONLY')
    try:
        import keyring
        backend = keyring.get_keyring()
        if type(backend).__module__ != 'keyring.backends.Windows' or type(backend).__name__ != 'WinVaultKeyring':
            _fail('TRUSTED_BACKEND_REQUIRED')
        return backend  # Keep the exact checked backend; no later fallback lookup.
    except PaperVaultError:
        raise
    except Exception:
        raise PaperVaultError('PAPER_VAULT_UNAVAILABLE') from None


def _credential(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', value):
        _fail('CREDENTIAL_FORMAT_INVALID')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail('RECORD_INVALID')
        result[key] = value
    return result


def _decode(raw):
    if not isinstance(raw, str) or len(raw) > MAX_RECORD:
        _fail('RECORD_INVALID')
    try:
        record = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(record, dict) or set(record) != {'version', 'provider', 'environment', 'scope', 'revision', 'api_key', 'api_secret'}:
            _fail('RECORD_INVALID')
        if type(record['version']) is not int or record['version'] != 1 or record['provider'] != 'alpaca' or record['environment'] != 'paper' or record['scope'] != 'account_read':
            _fail('RECORD_INVALID')
        revision = record['revision']
        if not isinstance(revision, str) or len(revision) != 36 or str(UUID(revision)) != revision or UUID(revision).version != 4:
            _fail('RECORD_INVALID')
        _credential(record['api_key']); _credential(record['api_secret'])
        return record
    except PaperVaultError:
        raise
    except Exception:
        raise PaperVaultError('PAPER_VAULT_RECORD_INVALID') from None


def _summary(configured):
    with _control:
        blocked = _blocked
    return dict(configured=configured, storage='WINDOWS_CREDENTIAL_MANAGER',
                scope='PAPER_ACCOUNT_READ_ONLY', review_required=blocked,
                broker_connected=False, automatic_requests=False,
                ready_for_routing=False, live_orders_enabled=False, money_movement_enabled=False)


def _begin_mutation():
    global _epoch, _blocked
    with _control:
        _epoch += 1
        _blocked = True  # Revoke reads before attempting vault IO or waiting.
        epoch = _epoch
    if not _operations.acquire(blocking=False):
        _fail('BUSY_REVIEW_REQUIRED')
    return epoch


def _complete_mutation(epoch):
    global _blocked
    with _control:
        if epoch != _epoch:
            _fail('MUTATION_SUPERSEDED')
        _blocked = False


def install(api_key, api_secret, *, confirm_store=False):
    """Explicit save/replace only; acknowledges after exact OS readback."""
    if confirm_store is not True:
        _fail('STORE_CONFIRMATION_REQUIRED')
    _credential(api_key); _credential(api_secret)
    epoch = _begin_mutation()
    try:
        backend = _vault()
        record = dict(version=1, provider='alpaca', environment='paper', scope='account_read',
                      revision=str(uuid4()), api_key=api_key, api_secret=api_secret)
        raw = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(raw) > MAX_RECORD:
            _fail('RECORD_INVALID')
        backend.set_password(SERVICE, ACCOUNT, raw)
        if backend.get_password(SERVICE, ACCOUNT) != raw:
            _fail('SAVE_UNCONFIRMED')
        _complete_mutation(epoch)
        return _summary(True)
    except PaperVaultError:
        raise
    except Exception:
        raise PaperVaultError('PAPER_VAULT_SAVE_UNCONFIRMED') from None
    finally:
        _operations.release()


def status():
    """Explicit local OS inspection only; never a provider request or permission."""
    if not _operations.acquire(blocking=False):
        _fail('BUSY_REVIEW_REQUIRED')
    try:
        raw = _vault().get_password(SERVICE, ACCOUNT)
        if raw is not None:
            _decode(raw)
        return _summary(raw is not None)
    except PaperVaultError:
        raise
    except Exception:
        raise PaperVaultError('PAPER_VAULT_STATUS_UNAVAILABLE') from None
    finally:
        _operations.release()


def remove(*, confirm_remove=False):
    """Revoke this process first, then delete and verify absence; no false ack."""
    if confirm_remove is not True:
        _fail('REMOVE_CONFIRMATION_REQUIRED')
    epoch = _begin_mutation()
    try:
        backend = _vault()
        if backend.get_password(SERVICE, ACCOUNT) is not None:
            backend.delete_password(SERVICE, ACCOUNT)
        if backend.get_password(SERVICE, ACCOUNT) is not None:
            _fail('DELETE_UNCONFIRMED')
        _complete_mutation(epoch)
        return _summary(False)
    except PaperVaultError:
        raise
    except Exception:
        raise PaperVaultError('PAPER_VAULT_DELETE_UNCONFIRMED') from None
    finally:
        _operations.release()


def _current(epoch):
    with _control:
        return not _blocked and epoch == _epoch


def _capture():
    if not _operations.acquire(blocking=False):
        _fail('BUSY_REVIEW_REQUIRED')
    try:
        with _control:
            if _blocked:
                _fail('LOCAL_REVIEW_REQUIRED')
            epoch = _epoch
        raw = _vault().get_password(SERVICE, ACCOUNT)
        if raw is None:
            _fail('NOT_CONFIGURED')
        record = _decode(raw)
        if not _current(epoch):
            _fail('READ_REVOKED')
        return epoch, raw, record
    except PaperVaultError:
        raise
    except Exception:
        raise PaperVaultError('PAPER_VAULT_STATUS_UNAVAILABLE') from None
    finally:
        _operations.release()


def inspect_saved(*, confirm_read_only_paper=False, expected_account_id=None,
                  opener=None, wall_clock=time.time, elapsed_clock=time.monotonic, pacing=None):
    """One explicitly confirmed read. Keep secrets out of all returned records.

    Local mutation epochs fence IO checkpoints. Final OS readback also rejects a
    changed entry from another process; it cannot physically cancel remote IO or
    establish cross-process ownership. No automatic retry/rearm after a failure.
    """
    if confirm_read_only_paper is not True:
        _fail('READ_CONFIRMATION_REQUIRED')
    epoch, raw, record = _capture()
    result = inspect_account(record['api_key'], record['api_secret'], confirm_read_only_paper=True,
        expected_account_id=expected_account_id, opener=opener, wall_clock=wall_clock,
        elapsed_clock=elapsed_clock, permission_check=lambda: _current(epoch), pacing=pacing)
    current_epoch, current_raw, _ = _capture()
    if epoch != current_epoch or raw != current_raw or not _current(epoch):
        _fail('READ_REVOKED')
    return result


def recover_saved(journal, client_order_id, *, expected_account_id,
                  confirm_read_only_paper=False, opener=None, wall_clock=time.time,
                  elapsed_clock=time.monotonic, pacing=None):
    """Internal opt-in original-ID recovery; no application endpoint/default path."""
    if confirm_read_only_paper is not True:
        _fail('READ_CONFIRMATION_REQUIRED')
    from nexus.broker_paper_recovery import recover_original
    epoch, raw, record = _capture()
    def credential_check():
        current_epoch, current_raw, _ = _capture()
        return epoch == current_epoch and raw == current_raw and _current(epoch)
    result = recover_original(journal, client_order_id, record['api_key'], record['api_secret'],
        expected_account_id=expected_account_id, confirm_read_only_paper=True, opener=opener,
        wall_clock=wall_clock, elapsed_clock=elapsed_clock, permission_check=lambda: _current(epoch),
        credential_check=credential_check, pacing=pacing)

    if not credential_check():
        _fail('READ_REVOKED')
    return result


def collect_saved(orders, *, expected_account_id, after, until,
                  confirm_read_only_paper=False, opener=None, wall_clock=time.time,
                  elapsed_clock=time.monotonic, activity_store=None, pacing=None):
    """Internal one-shot activity collection; no runtime route or automatic start."""
    if confirm_read_only_paper is not True:
        _fail('READ_CONFIRMATION_REQUIRED')
    from nexus.alpaca_activity_collection import collect_activities
    epoch, raw, record = _capture()
    def credential_check():
        current_epoch, current_raw, _ = _capture()
        return epoch == current_epoch and raw == current_raw and _current(epoch)
    result = collect_activities(record['api_key'], record['api_secret'], orders,
        expected_account_id=expected_account_id, after=after, until=until,
        confirm_read_only_paper=True, opener=opener, wall_clock=wall_clock,
        elapsed_clock=elapsed_clock, permission_check=lambda: _current(epoch),
        credential_check=credential_check, activity_store=activity_store, pacing=pacing)
    if not credential_check():
        _fail('READ_REVOKED')
    return result
