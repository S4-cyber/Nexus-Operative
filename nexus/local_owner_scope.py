"""Small POSIX mode-bit fence for cooperating local owner-lock files.

This is deliberately not an ACL, administrator, sandbox, or distributed
ownership boundary.  Windows callers retain their existing OS-lock behavior;
POSIX mode bits must not be presented as Windows ACL evidence.
"""
import os
import stat


def posix_owner_file_safe(info):
    """Return whether one opened/current lock file has a safe POSIX scope."""
    if os.name == 'nt':
        return True
    mode = stat.S_IMODE(info.st_mode)
    return (info.st_uid == os.geteuid() and mode & 0o600 == 0o600 and
        mode & 0o022 == 0)


def posix_owner_directory_safe(info):
    """Return whether the exact lock directory resists peer-account writes."""
    if os.name == 'nt':
        return True
    mode = stat.S_IMODE(info.st_mode)
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
        mode & 0o022 == 0)


def owner_directory_identity(path):
    """Return one safe exact-directory identity, or ``None`` on disagreement."""
    try:
        info = path.stat()
    except OSError:
        return None
    if not posix_owner_directory_safe(info):
        return None
    return info.st_dev, info.st_ino


def owner_directory_matches(path, identity):
    """Recheck a previously sampled exact-directory identity and mode scope."""
    return identity is not None and owner_directory_identity(path) == identity


def owner_marker_valid(fd):
    """Require the complete fixed owner marker without changing it."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        marker = os.read(fd, 2)
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        return False
    return marker == b'0'
