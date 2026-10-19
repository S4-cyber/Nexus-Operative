"""Bounded preflight for private DELETE-mode journals, before SQLite opens.

Sampled local file checks only; not an atomic descriptor binding, OS ACL audit,
encryption, database replay or protection against an administrator racing paths.
"""
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
