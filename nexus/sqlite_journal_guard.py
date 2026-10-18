"""Bounded preflight for private DELETE-mode journals, before SQLite opens.

Sampled local file checks only; not an atomic descriptor binding, OS ACL audit,
encryption, database replay or protection against an administrator racing paths.
"""
import os
from pathlib import Path
import stat


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
