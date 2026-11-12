"""POSIX mode-bit fences shared by broker-paper coordination locks."""
import os
import stat

import pytest

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
