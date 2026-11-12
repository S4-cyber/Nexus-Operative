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


def open_owner_file(path):
    """Open an owner file and distinguish an exclusive creation from inheritance."""
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    try:
        return os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600), True
    except FileExistsError:
        return os.open(path, flags), False


def owner_marker_valid(fd):
    """Require the complete fixed owner marker without changing it."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        marker = os.read(fd, 2)
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        return False
    return marker == b'0'


def _sync_owner(path, fd, directory_identity):
    """Durably publish one exact owner marker where supported."""
    try:
        os.fsync(fd)
    except OSError:
        return False
    if os.name == 'nt':
        # Python exposes no portable Windows directory-fsync API.  The exact
        # file barrier remains mandatory; native owner-profile acceptance is
        # an independent release gate.
        return True
    flags = (os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
        getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        directory_fd = os.open(path.parent, flags)
    except OSError:
        return False
    try:
        opened = os.fstat(directory_fd)
        if (not stat.S_ISDIR(opened.st_mode) or
                (opened.st_dev, opened.st_ino) != directory_identity or
                not posix_owner_directory_safe(opened)):
            return False
        os.fsync(directory_fd)
        final = os.fstat(directory_fd)
        return (stat.S_ISDIR(final.st_mode) and
            (final.st_dev, final.st_ino) == directory_identity and
            posix_owner_directory_safe(final) and
            owner_directory_matches(path.parent, directory_identity))
    except OSError:
        return False
    finally:
        os.close(directory_fd)


def prepare_owner_file(path, fd, created, directory_identity):
    """Initialize only a fresh owner file; inherited state is never repaired."""
    try:
        current = path.stat()
        opened = os.fstat(fd)
    except OSError:
        return False
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
            (current.st_dev, current.st_ino) !=
            (opened.st_dev, opened.st_ino) or
            not posix_owner_file_safe(current) or
            not posix_owner_file_safe(opened) or
            not owner_directory_matches(path.parent, directory_identity)):
        return False
    if created:
        if opened.st_size != 0:
            return False
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.write(fd, b'0') != 1:
                return False
        except OSError:
            return False
    elif opened.st_size != 1:
        return False
    if not owner_marker_valid(fd):
        return False
    if not _sync_owner(path, fd, directory_identity):
        return False
    try:
        current = path.stat()
        opened = os.fstat(fd)
    except OSError:
        return False
    return (stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
        opened.st_size == 1 and
        (current.st_dev, current.st_ino) ==
        (opened.st_dev, opened.st_ino) and
        posix_owner_file_safe(current) and
        posix_owner_file_safe(opened) and
        owner_directory_matches(path.parent, directory_identity) and
        owner_marker_valid(fd))
