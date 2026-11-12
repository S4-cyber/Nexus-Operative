"""POSIX mode-bit fences shared by broker-paper coordination locks."""
import os
import stat

import pytest

import nexus.local_owner_scope as owner_scope
from nexus.alpaca_activity_sse_cursor_store import ActivitySSECursorStore
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from nexus.paper_provider_read_budget import PaperProviderReadBudget
from nexus.paper_read_pacing import PaperReadPacing


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'


def subjects(root):
    return (
        ('PAPER_PROVIDER_POST_BUDGET', PaperProviderPostBudget(root / 'post.sqlite3')),
        ('PAPER_PROVIDER_READ_BUDGET', PaperProviderReadBudget(root / 'provider-read.sqlite3')),
        ('PAPER_READ_PACING', PaperReadPacing(root / 'account-read.sqlite3', ACCOUNT)),
        ('ALPACA_ACTIVITY_SSE_CURSOR_STORE',
            ActivitySSECursorStore(root / 'sse-cursor.sqlite3', ACCOUNT)),
    )


@pytest.mark.skipif(os.name == 'nt',
    reason='POSIX mode bits are not Windows ACL evidence')
@pytest.mark.parametrize('index', range(4))
def test_created_broker_owner_lock_is_private(tmp_path, index):
    prefix, subject = subjects(tmp_path)[index]
    with subject.ownership() as owner:
        assert stat.S_IMODE(owner.path.stat().st_mode) == 0o600
        assert owner.path.stat().st_uid == os.geteuid()
        assert owner.path.parent.stat().st_uid == os.geteuid()
    assert prefix


@pytest.mark.parametrize('index', range(4))
def test_created_broker_owner_marker_is_synced_before_use(
        tmp_path, monkeypatch, index):
    _, subject = subjects(tmp_path)[index]
    original = owner_scope.os.fsync
    synced = []

    def fsync(descriptor):
        synced.append('directory' if stat.S_ISDIR(
            os.fstat(descriptor).st_mode) else 'file')
        return original(descriptor)

    monkeypatch.setattr(owner_scope.os, 'fsync', fsync)
    with subject.ownership() as owner:
        owner.validate()
    assert synced == (['file'] if os.name == 'nt' else
        ['file', 'directory'])


@pytest.mark.parametrize('index', range(4))
def test_created_broker_owner_rejects_file_sync_uncertainty(
        tmp_path, monkeypatch, index):
    prefix, subject = subjects(tmp_path)[index]
    original = owner_scope.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError('injected owner-marker flush failure')
        return original(descriptor)

    monkeypatch.setattr(owner_scope.os, 'fsync', fsync)
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert failed


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
def test_created_broker_owner_rejects_parent_sync_uncertainty(
        tmp_path, monkeypatch):
    prefix, subject = subjects(tmp_path)[0]
    original = owner_scope.os.fsync
    failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError('injected owner-directory flush failure')
        return original(descriptor)

    monkeypatch.setattr(owner_scope.os, 'fsync', fsync)
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert failed


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
def test_created_broker_owner_binds_parent_sync_descriptor(
        tmp_path, monkeypatch):
    prefix, subject = subjects(tmp_path)[0]
    foreign = tmp_path / 'foreign-owner-root'
    foreign.mkdir()
    original_open = owner_scope.os.open
    original_fsync = owner_scope.os.fsync
    substituted = False
    directory_syncs = 0

    def open_file(path, flags, *args, **kwargs):
        nonlocal substituted
        if not substituted and flags & getattr(os, 'O_DIRECTORY', 0):
            substituted = True
            return original_open(foreign, flags, *args, **kwargs)
        return original_open(path, flags, *args, **kwargs)

    def fsync(descriptor):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
        return original_fsync(descriptor)

    monkeypatch.setattr(owner_scope.os, 'open', open_file)
    monkeypatch.setattr(owner_scope.os, 'fsync', fsync)
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert substituted and directory_syncs == 0


@pytest.mark.parametrize('index', range(4))
def test_inherited_broker_owner_repeats_durability_barrier(
        tmp_path, monkeypatch, index):
    _, subject = subjects(tmp_path)[index]
    lock = subject.path.resolve().with_name(subject.path.name + '.owner.lock')
    lock.write_bytes(b'0')
    original = owner_scope.os.fsync
    synced = []

    def fsync(descriptor):
        synced.append('directory' if stat.S_ISDIR(
            os.fstat(descriptor).st_mode) else 'file')
        return original(descriptor)

    monkeypatch.setattr(owner_scope.os, 'fsync', fsync)
    with subject.ownership() as owner:
        owner.validate()
    assert synced == (['file'] if os.name == 'nt' else
        ['file', 'directory'])


@pytest.mark.skipif(os.name == 'nt',
    reason='POSIX mode bits are not Windows ACL evidence')
@pytest.mark.parametrize('index', range(4))
def test_inherited_broker_owner_lock_rejects_foreign_write_without_repair(
        tmp_path, index):
    prefix, subject = subjects(tmp_path)[index]
    lock = subject.path.resolve().with_name(subject.path.name + '.owner.lock')
    lock.write_bytes(b'0')
    lock.chmod(0o660)
    before = lock.read_bytes()
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert lock.read_bytes() == before
    assert stat.S_IMODE(lock.stat().st_mode) == 0o660


@pytest.mark.skipif(os.name == 'nt',
    reason='POSIX mode bits are not Windows ACL evidence')
@pytest.mark.parametrize('index', range(4))
def test_active_broker_owner_detects_posix_mode_drift(tmp_path, index):
    prefix, subject = subjects(tmp_path)[index]
    owner = subject.ownership()
    owner.__enter__()
    owner.path.chmod(0o660)
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_CHANGED$'):
        owner.validate()
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_CHANGED$'):
        owner.__exit__(None, None, None)
    assert owner.fd is None


@pytest.mark.skipif(os.name == 'nt',
    reason='POSIX mode bits are not Windows ACL evidence')
@pytest.mark.parametrize('index', range(4))
def test_broker_owner_rejects_writable_exact_directory_without_repair(
        tmp_path, index):
    root = tmp_path / 'shared'
    root.mkdir()
    root.chmod(0o770)
    prefix, subject = subjects(root)[index]
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert stat.S_IMODE(root.stat().st_mode) == 0o770
    assert not subject.path.resolve().with_name(
        subject.path.name + '.owner.lock').exists()


@pytest.mark.parametrize('index', range(4))
def test_inherited_broker_owner_rejects_corrupt_marker_without_repair(
        tmp_path, index):
    prefix, subject = subjects(tmp_path)[index]
    lock = subject.path.resolve().with_name(subject.path.name + '.owner.lock')
    lock.write_bytes(b'1')
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert lock.read_bytes() == b'1'


@pytest.mark.parametrize('index', range(4))
def test_inherited_empty_broker_owner_is_not_initialized(
        tmp_path, index):
    prefix, subject = subjects(tmp_path)[index]
    lock = subject.path.resolve().with_name(subject.path.name + '.owner.lock')
    lock.touch()
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert lock.read_bytes() == b''


@pytest.mark.skipif(os.name == 'nt',
    reason='POSIX hard-link scope is not Windows ACL evidence')
@pytest.mark.parametrize('index', range(4))
def test_inherited_multilink_broker_owner_is_not_initialized(
        tmp_path, index):
    prefix, subject = subjects(tmp_path)[index]
    lock = subject.path.resolve().with_name(subject.path.name + '.owner.lock')
    lock.touch()
    alias = tmp_path / f'owner-alias-{index}.lock'
    os.link(lock, alias)
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_INVALID$'):
        with subject.ownership():
            pass
    assert lock.read_bytes() == alias.read_bytes() == b''
    assert lock.stat().st_nlink == 2


@pytest.mark.parametrize('index', range(4))
def test_inherited_valid_broker_owner_remains_usable(
        tmp_path, index):
    _, subject = subjects(tmp_path)[index]
    lock = subject.path.resolve().with_name(subject.path.name + '.owner.lock')
    lock.write_bytes(b'0')
    with subject.ownership() as owner:
        owner.validate()
    assert lock.read_bytes() == b'0'


@pytest.mark.parametrize('index', range(4))
def test_active_broker_owner_detects_same_inode_marker_corruption(
        tmp_path, index):
    prefix, subject = subjects(tmp_path)[index]
    owner = subject.ownership()
    owner.__enter__()
    owner.path.write_bytes(b'1')
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_CHANGED$'):
        owner.validate()
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_CHANGED$'):
        owner.__exit__(None, None, None)
    assert owner.fd is None and owner.path.read_bytes() == b'1'


@pytest.mark.parametrize('index', range(4))
def test_active_broker_owner_detects_exact_directory_replacement(
        tmp_path, index):
    root = tmp_path / 'owner-root'
    root.mkdir()
    prefix, subject = subjects(root)[index]
    owner = subject.ownership()
    owner.__enter__()
    if os.name == 'nt':
        owner.directory_identity = (-1, -1)
    else:
        retired = tmp_path / 'retired-owner-root'
        os.replace(root, retired)
        root.mkdir()
        os.replace(retired / owner.path.name, root / owner.path.name)
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_CHANGED$'):
        owner.validate()
    with pytest.raises(ValueError, match=f'^{prefix}_OWNER_FILE_CHANGED$'):
        owner.__exit__(None, None, None)
    assert owner.fd is None and owner.directory_identity is None
