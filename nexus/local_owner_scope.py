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
