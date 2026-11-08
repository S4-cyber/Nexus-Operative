"""Bounded local cut latch independent of SQLite; never grants running permission.

The coordinator serializes writes/reset with trading control. Presence, including an
incomplete or unreadable file, means cut requested. No timestamps, secrets or ledger
payloads are stored. This is local crash recovery, not a distributed stop service.
"""
import os
import stat
from pathlib import Path

RECORD = b'NEXUS_LOCAL_TRADING_CUT_V1\n'


def path(order_path):
    return Path(order_path).parent / 'emergency_cut.intent'


def requested(order_path):
    # lstat deliberately treats malformed files, directories and symlinks as a cut.
    try:path(order_path).lstat()
    except FileNotFoundError:return False
    return True


def _pin_directory(directory):
    # Windows cannot open directories for fsync through the standard os API.
    if os.name == 'nt':return None
    before=directory.lstat()
    flags=os.O_RDONLY | getattr(os,'O_DIRECTORY',0) | getattr(os,'O_NOFOLLOW',0)
    fd=os.open(directory,flags)
    try:
        opened=os.fstat(fd);after=directory.lstat()
        if (not stat.S_ISDIR(opened.st_mode) or
                not os.path.samestat(before,opened) or
                not os.path.samestat(opened,after)):
            raise OSError('EMERGENCY_INTENT_DIRECTORY_CHANGED')
    except BaseException:
        os.close(fd);raise
    return fd


def _sync_directory(directory,fd):
    if fd is None:return
    # Sync the directory actually used for the entry operation, then ensure its
    # pathname still resolves to that same directory before acknowledging it.
    os.fsync(fd)
    current=directory.lstat();opened=os.fstat(fd)
    if not os.path.samestat(opened,current):
        raise OSError('EMERGENCY_INTENT_DIRECTORY_CHANGED')


def _entry_requested(order_path,target,fd):
    if fd is None:return requested(order_path)
    try:os.stat(target.name,dir_fd=fd,follow_symlinks=False)
    except FileNotFoundError:return False
    return True


def persist(order_path):
    target = path(order_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    directory_fd=_pin_directory(target.parent)
    try:
        entry=target if directory_fd is None else target.name
        options={} if directory_fd is None else {'dir_fd':directory_fd}
        try:fd = os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,**options)
        except (FileExistsError, PermissionError):
            # Windows reports an existing directory as EACCES rather than EEXIST.
            # A genuine denial with no observable latch must still fail closed.
            if not _entry_requested(order_path,target,directory_fd):raise
            # A previous/interrupted latch is already conservative recovery evidence.
            # Do not follow it or rewrite it; only an explicit reset removes it.
            _sync_directory(target.parent,directory_fd)
            return
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(RECORD)
                stream.flush()
                os.fsync(stream.fileno())
        finally:_sync_directory(target.parent,directory_fd)
    finally:
        if directory_fd is not None:os.close(directory_fd)


def clear(order_path):
    target = path(order_path)
    try:directory_fd=_pin_directory(target.parent)
    except FileNotFoundError:return
    try:
        try:
            if directory_fd is None:target.unlink()
            else:os.unlink(target.name,dir_fd=directory_fd)
        except FileNotFoundError:return
        _sync_directory(target.parent,directory_fd)
    finally:
        if directory_fd is not None:os.close(directory_fd)
