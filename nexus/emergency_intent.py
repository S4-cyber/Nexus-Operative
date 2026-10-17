"""Bounded local cut latch independent of SQLite; never grants running permission.

The coordinator serializes writes/reset with trading control. Presence, including an
incomplete or unreadable file, means cut requested. No timestamps, secrets or ledger
payloads are stored. This is local crash recovery, not a distributed stop service.
"""
import os
from pathlib import Path

RECORD = b'NEXUS_LOCAL_TRADING_CUT_V1\n'


def path(order_path):
    return Path(order_path).parent / 'emergency_cut.intent'


def requested(order_path):
    # lstat deliberately treats malformed files, directories and symlinks as a cut.
    try:path(order_path).lstat()
    except FileNotFoundError:return False
    return True


def _sync_directory(directory):
    # Windows cannot open directories for fsync through the standard os API.
    if os.name != 'nt':
        fd = os.open(directory, os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)


def persist(order_path):
    target = path(order_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # A previous/interrupted latch is already conservative recovery evidence.
        # Do not follow it or rewrite it; only an explicit reset removes it.
        _sync_directory(target.parent)
        return
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(RECORD)
            stream.flush()
            os.fsync(stream.fileno())
    finally:_sync_directory(target.parent)


def clear(order_path):
    target = path(order_path)
    try:target.unlink()
    except FileNotFoundError:return
    _sync_directory(target.parent)
