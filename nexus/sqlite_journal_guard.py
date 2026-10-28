"""Bounded preflight for private DELETE-mode journals, before SQLite opens.

Sampled local file checks only; not an atomic descriptor binding, OS ACL audit,
encryption, database replay or protection against an administrator racing paths.
"""
import hashlib
import os
from pathlib import Path
import stat


def check_path_set(paths, prefix):
    """Read-only bounded relationship check, not path ownership or an ACL audit."""
    def fail():raise ValueError(prefix+'RELATED_PATHS_REQUIRED_DISTINCT') from None
    if type(paths) not in (list,tuple) or not 2<=len(paths)<=8:fail()
    try:resolved=tuple(Path(p).resolve() for p in paths)
    except (OSError,RuntimeError,ValueError,TypeError):fail()
    for index,path in enumerate(resolved):
        if not path.name:fail()
        occupied=(path,*(path.with_name(path.name+suffix) for suffix in ('-journal','-wal','-shm','.owner.lock')))
        for other_index,other in enumerate(resolved):
            if index==other_index:continue
            if any(other==slot or other.is_relative_to(slot) or slot.is_relative_to(other) for slot in occupied):fail()
    return resolved


def capture_file_identities(paths, prefix):
    """Sample existing regular-file identities without retaining descriptors.

    This detects pathname replacement between explicit composition boundaries.
    It is not an atomic snapshot, lock, ownership check or protection against a
    writer that changes bytes through the same file identity.
    """
    def fail(code): raise ValueError(prefix + code) from None
    if type(paths) not in (list, tuple) or not 1 <= len(paths) <= 32:
        fail('FILE_IDENTITY_SCOPE_INVALID')
    identities = []
    for value in paths:
        try:
            path = Path(value)
            observed = path.lstat()
            if (stat.S_ISLNK(observed.st_mode) or
                    not stat.S_ISREG(observed.st_mode) or
                    observed.st_nlink != 1):
                fail('FILE_IDENTITY_UNAVAILABLE')
            resolved = path.resolve()
            flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) |
                getattr(os, 'O_NOFOLLOW', 0) |
                getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(os.open(resolved, flags), 'rb', buffering=0) as stream:
                opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                    (opened.st_dev, opened.st_ino) !=
                    (observed.st_dev, observed.st_ino)):
                fail('FILE_IDENTITY_CHANGED')
            identities.append((str(resolved), opened.st_dev, opened.st_ino))
        except ValueError:
            raise
        except (OSError, RuntimeError, TypeError):
            fail('FILE_IDENTITY_UNAVAILABLE')
    return tuple(identities)


def require_file_identities(paths, expected, prefix):
    """Fail closed when a sampled pathname now names a different file."""
    def fail(): raise ValueError(prefix + 'FILE_IDENTITY_CHANGED') from None
    if type(expected) is not tuple:
        fail()
    try:
        current = capture_file_identities(paths, prefix)
    except ValueError:
        fail()
    if current != expected:
        fail()


def _fingerprint_boundary(item, *, host=None):
    # Windows path and handle APIs can disagree on creation-time precision.
    # Compare ctime within each API below; retain all common identity fields.
    host=os.name if host is None else host
    return (item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,
        item.st_ctime_ns if host!='nt' else None,item.st_nlink)


def capture_file_fingerprints(paths, maximum_bytes, prefix):
    """Sample bounded single-link files and their bytes without path following.

    The result is private process-local comparison material.  It is not an
    atomic multi-file snapshot, a writer lock, an ACL check or external
    anchoring.  Rollback/WAL/shared-memory sidecars fail closed because a
    reconciliation review must not claim a stable file set while SQLite
    recovery or another writer may be active.
    """
    def fail(code): raise ValueError(prefix + code) from None
    if (type(paths) not in (list, tuple) or type(maximum_bytes) not in (list, tuple) or
            not 1 <= len(paths) <= 32 or len(paths) != len(maximum_bytes) or
            any(type(limit) is not int or limit <= 0 for limit in maximum_bytes)):
        fail('FILE_FINGERPRINT_SCOPE_INVALID')
    fingerprints = []
    for value, limit in zip(paths, maximum_bytes):
        path = Path(value)
        for suffix in ('-journal', '-wal', '-shm'):
            try: path.with_name(path.name + suffix).lstat()
            except FileNotFoundError: continue
            except OSError: fail('FILE_SET_UNAVAILABLE')
            fail('SQLITE_SIDECAR_REVIEW_REQUIRED')
        try:
            observed = path.lstat()
            if (stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode) or
                    observed.st_nlink != 1 or not 1 <= observed.st_size <= limit):
                fail('FILE_FINGERPRINT_UNAVAILABLE')
            resolved = path.resolve()
            flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) |
                getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            digest = hashlib.sha256()
            with os.fdopen(os.open(resolved, flags), 'rb', buffering=0) as stream:
                before = os.fstat(stream.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                        not 1 <= before.st_size <= limit or
                        (before.st_dev, before.st_ino) != (observed.st_dev, observed.st_ino)):
                    fail('FILE_FINGERPRINT_CHANGED')
                remaining = limit + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk: break
                    digest.update(chunk); remaining -= len(chunk)
                after = os.fstat(stream.fileno())
            final = path.lstat()
            boundary = _fingerprint_boundary
            if (remaining == 0 or boundary(observed) != boundary(before) or
                    boundary(before) != boundary(after) or boundary(after) != boundary(final) or
                    before.st_ctime_ns != after.st_ctime_ns or
                    observed.st_ctime_ns != final.st_ctime_ns or
                    not stat.S_ISREG(final.st_mode)):
                fail('FILE_FINGERPRINT_CHANGED')
            for suffix in ('-journal', '-wal', '-shm'):
                try: path.with_name(path.name + suffix).lstat()
                except FileNotFoundError: continue
                except OSError: fail('FILE_SET_UNAVAILABLE')
                fail('SQLITE_SIDECAR_REVIEW_REQUIRED')
            # A later review compares the same path API ctime as well as bytes.
            stable=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,
                final.st_ctime_ns,after.st_nlink)
            fingerprints.append((str(resolved), *stable, digest.hexdigest()))
        except ValueError:
            raise
        except (OSError, RuntimeError, TypeError):
            fail('FILE_SET_UNAVAILABLE')
    return tuple(fingerprints)


def require_file_fingerprints(paths, maximum_bytes, expected, prefix):
    """Fail closed if a bounded private file set changed since its sample."""
    if type(expected) is not tuple:
        raise ValueError(prefix + 'FILE_SET_CHANGED') from None
    try: current = capture_file_fingerprints(paths, maximum_bytes, prefix)
    except ValueError as error:
        if str(error) == prefix + 'SQLITE_SIDECAR_REVIEW_REQUIRED':
            raise
        raise ValueError(prefix + 'FILE_SET_CHANGED') from None
    if current != expected:
        raise ValueError(prefix + 'FILE_SET_CHANGED') from None


PATH_CHECKS=('recovery_companions_rejected','ancestors_rejected','normalized_aliases_rejected','unrelated_paths_unchanged')
PATH_FLAGS=('actual_provider_access','actual_vault_access','paths_reserved','atomic_path_binding',
            'ready_for_routing','live_orders_enabled','money_movement_enabled','release_approved')


def path_fixture_report():
    """Temporary missing paths only; aggregate report contains no private names."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nexus-journal-paths-') as directory:
        root=Path(directory);base=root/'first.sqlite3';rejected=[]
        def denied(paths):
            try:check_path_set(paths,'FIXTURE_')
            except ValueError as error:return str(error)=='FIXTURE_RELATED_PATHS_REQUIRED_DISTINCT'
            return False
        for suffix in ('-journal','-wal','-shm','.owner.lock'):
            companion=base.with_name(base.name+suffix)
            rejected.extend((denied((base,companion)),denied((companion,base))))
        unrelated=check_path_set((base,root/'second.sqlite3'),'FIXTURE_')
        checks=dict(recovery_companions_rejected=len(rejected)==8 and all(rejected),
            ancestors_rejected=denied((base,base/'child.sqlite3')) and denied((base,base.with_name(base.name+'-journal')/'child.sqlite3')),
            normalized_aliases_rejected=denied((base,root/'nested'/'..'/'first.sqlite3')),
            unrelated_paths_unchanged=len(unrelated)==2 and not list(root.iterdir()))
        return dict(source='TEMPORARY_MISSING_JOURNAL_PATH_FIXTURE',passed=all(checks.values()),**checks,**{k:False for k in PATH_FLAGS})


def check_existing(path, maximum_bytes, prefix, *, cap_code='DATABASE_CAP', symlink_code='SYMLINK'):
    path = Path(path)
    def fail(code): raise ValueError(prefix + code) from None
    # Even orphaned WAL sidecars are retained evidence, never safe to erase or
    # silently reuse while initializing a missing main database.
    for suffix in ('-wal', '-shm'):
        try: path.with_name(path.name + suffix).lstat()
        except FileNotFoundError: continue
        except OSError: fail('FILE_UNAVAILABLE')
        fail('WAL_SIDECAR_REVIEW_REQUIRED')
    try: observed = path.lstat()
    except FileNotFoundError: return False
    except OSError: fail('FILE_UNAVAILABLE')
    if stat.S_ISLNK(observed.st_mode): fail(symlink_code)
    if not stat.S_ISREG(observed.st_mode): fail('REGULAR_FILE_REQUIRED')
    if observed.st_nlink != 1: fail('HARDLINK')
    if observed.st_size > maximum_bytes: fail(cap_code)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        with os.fdopen(os.open(path, flags), 'rb', buffering=0) as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode): fail('REGULAR_FILE_REQUIRED')
            if opened.st_nlink != 1: fail('HARDLINK')
            if opened.st_size > maximum_bytes: fail(cap_code)
            if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino): fail('FILE_CHANGED')
            header = stream.read(20)
    except OSError: fail('FILE_UNAVAILABLE')
    # Empty/invalid files still fail ordinary schema/binding replay. Reject WAL
    # before SQLite can create -wal/-shm, or convert history on a write attempt.
    if header[:16] == b'SQLite format 3\x00' and len(header) == 20 and header[18:20] != b'\x01\x01':
        fail('JOURNAL_MODE_REVIEW_REQUIRED')
    return True
