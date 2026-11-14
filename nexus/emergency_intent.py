"""Bounded local cut latch independent of SQLite; never grants running permission.

The coordinator serializes writes/reset with trading control. Presence, including an
incomplete or unreadable file, means cut requested. No timestamps, secrets or ledger
payloads are stored. This is local crash recovery, not a distributed stop service.
"""
import os
import stat
from pathlib import Path

RECORD = b'NEXUS_LOCAL_TRADING_CUT_V1\n'
WINDOWS_LEASE_NAME = '.nexus-emergency-directory.lease'


class _WindowsDirectoryLease:
    """Win32 directory and child handles opened without delete sharing."""
    __slots__=('handle','child_handle','_close')

    def __init__(self,handle,close,child_handle=None):
        self.handle=handle;self.child_handle=child_handle;self._close=close

    def close(self):
        import ctypes
        handles=(self.child_handle,self.handle)
        self.child_handle=None;self.handle=None
        error=None
        for handle in handles:
            if handle is not None and not self._close(handle) and error is None:
                error=ctypes.get_last_error()
        if error is not None:
            raise ctypes.WinError(error)


def path(order_path):
    return Path(order_path).parent / 'emergency_cut.intent'


def requested(order_path):
    # lstat deliberately treats malformed files, directories and symlinks as a cut.
    try:path(order_path).lstat()
    except FileNotFoundError:return False
    return True


def _sample_directory(directory):
    """Capture one resolved parent identity for descriptorless fallbacks."""
    resolved_before=directory.resolve(strict=True)
    before=directory.lstat()
    resolved_after=directory.resolve(strict=True)
    after=directory.lstat()
    if (resolved_before!=resolved_after or
            not stat.S_ISDIR(before.st_mode) or
            not stat.S_ISDIR(after.st_mode) or
            not os.path.samestat(before,after)):
        raise OSError('EMERGENCY_INTENT_DIRECTORY_CHANGED')
    return resolved_after,after


def _verify_directory(directory,identity,fd):
    """Require the sampled public parent and optional owner descriptor."""
    expected_resolved,expected_stat=identity
    try:
        resolved_before=directory.resolve(strict=True)
        before=directory.lstat()
        resolved_after=directory.resolve(strict=True)
        after=directory.lstat()
        opened=None if fd is None or isinstance(fd,_WindowsDirectoryLease) else os.fstat(fd)
    except OSError:
        raise OSError('EMERGENCY_INTENT_DIRECTORY_CHANGED') from None
    if (resolved_before!=expected_resolved or
            resolved_after!=expected_resolved or
            not stat.S_ISDIR(before.st_mode) or
            not stat.S_ISDIR(after.st_mode) or
            not os.path.samestat(expected_stat,before) or
            not os.path.samestat(before,after) or
            (opened is not None and (
                not stat.S_ISDIR(opened.st_mode) or
                not os.path.samestat(expected_stat,opened)))):
        raise OSError('EMERGENCY_INTENT_DIRECTORY_CHANGED')


def _pin_directory(directory):
    if os.name == 'nt':return _windows_pin_directory(directory)
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


def _windows_pin_directory(directory):
    """Hold the exact public directory against retargeting until acknowledgement.

    Windows permits a directory rename despite a handle opened without delete
    sharing on some supported filesystems. A fixed regular child opened without
    delete sharing supplies the effective parent-rename barrier. Both handles
    remain necessary: the directory handle proves the selected public object,
    while the child handle keeps that object named until the operation is
    durably acknowledged.
    """
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW;inspect=kernel.GetFileInformationByHandle
    close=kernel.CloseHandle
    class FileInformation(ctypes.Structure):
        _fields_=(('dwFileAttributes',wintypes.DWORD),
                  ('ftCreationTime',wintypes.FILETIME),
                  ('ftLastAccessTime',wintypes.FILETIME),
                  ('ftLastWriteTime',wintypes.FILETIME),
                  ('dwVolumeSerialNumber',wintypes.DWORD),
                  ('nFileSizeHigh',wintypes.DWORD),
                  ('nFileSizeLow',wintypes.DWORD),
                  ('nNumberOfLinks',wintypes.DWORD),
                  ('nFileIndexHigh',wintypes.DWORD),
                  ('nFileIndexLow',wintypes.DWORD))
    create.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                     wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    create.restype=wintypes.HANDLE
    inspect.argtypes=(wintypes.HANDLE,ctypes.POINTER(FileInformation))
    inspect.restype=wintypes.BOOL
    close.argtypes=(wintypes.HANDLE,);close.restype=wintypes.BOOL
    # FILE_READ_ATTRIBUTES; share reads/writes but deliberately not DELETE.
    # Open the public path itself rather than its resolved target. Combined
    # with OPEN_REPARSE_POINT, this lets the handle inspection reject a late
    # junction/symlink replacement instead of leasing its redirected target.
    handle=create(str(directory.absolute()),0x00000080,0x00000003,
                  None,3,0x02000000|0x00200000,None)
    if handle in (None,ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    lease=_WindowsDirectoryLease(handle,close);information=FileInformation()
    try:
        if not inspect(handle,ctypes.byref(information)):
            error=ctypes.get_last_error()
            raise ctypes.WinError(error)
        # FILE_ATTRIBUTE_DIRECTORY and FILE_ATTRIBUTE_REPARSE_POINT.
        if (not information.dwFileAttributes&0x00000010 or
                information.dwFileAttributes&0x00000400):
            raise OSError('EMERGENCY_INTENT_DIRECTORY_CHANGED')
        child=create(str((directory/WINDOWS_LEASE_NAME).absolute()),
                     0x00000080,0x00000003,None,4,0x00200000,None)
        if child in (None,ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        lease.child_handle=child;child_information=FileInformation()
        if not inspect(child,ctypes.byref(child_information)):
            error=ctypes.get_last_error()
            raise ctypes.WinError(error)
        if (child_information.dwFileAttributes&0x00000010 or
                child_information.dwFileAttributes&0x00000400 or
                child_information.nNumberOfLinks!=1):
            raise OSError('EMERGENCY_INTENT_DIRECTORY_CHANGED')
    except BaseException:
        lease.close();raise
    return lease


def _directory_fd(lease):
    return None if lease is None or isinstance(lease,_WindowsDirectoryLease) else lease


def _close_directory(lease):
    if lease is None:return
    if isinstance(lease,_WindowsDirectoryLease):lease.close()
    else:os.close(lease)


def _sync_directory(directory,fd,identity):
    # Sync the directory actually used for the entry operation, then ensure its
    # pathname still resolves to that same directory before acknowledging it.
    # Windows has no standard directory-fsync descriptor, so the resolved-path
    # and identity sample remains mandatory even when ``fd`` is unavailable.
    if fd is not None and not isinstance(fd,_WindowsDirectoryLease):os.fsync(fd)
    _verify_directory(directory,identity,fd)


def _entry_requested(order_path,target,fd):
    fd=_directory_fd(fd)
    if fd is None:return requested(order_path)
    try:os.stat(target.name,dir_fd=fd,follow_symlinks=False)
    except FileNotFoundError:return False
    return True


def persist(order_path):
    target = path(order_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    directory_identity=_sample_directory(target.parent)
    directory_fd=_pin_directory(target.parent)
    try:
        _verify_directory(target.parent,directory_identity,directory_fd)
        entry_fd=_directory_fd(directory_fd)
        entry=target if entry_fd is None else target.name
        options={} if entry_fd is None else {'dir_fd':entry_fd}
        try:fd = os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,**options)
        except (FileExistsError, PermissionError):
            # Windows reports an existing directory as EACCES rather than EEXIST.
            # A genuine denial with no observable latch must still fail closed.
            if not _entry_requested(order_path,target,directory_fd):raise
            # A previous/interrupted latch is already conservative recovery evidence.
            # Do not follow it or rewrite it; only an explicit reset removes it.
            _sync_directory(target.parent,directory_fd,directory_identity)
            return
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(RECORD)
                stream.flush()
                os.fsync(stream.fileno())
        finally:_sync_directory(target.parent,directory_fd,directory_identity)
    finally:
        _close_directory(directory_fd)


def clear(order_path):
    target = path(order_path)
    try:directory_identity=_sample_directory(target.parent)
    except FileNotFoundError:return
    directory_fd=_pin_directory(target.parent)
    try:
        _verify_directory(target.parent,directory_identity,directory_fd)
        entry_fd=_directory_fd(directory_fd)
        try:
            if entry_fd is None:target.unlink()
            else:os.unlink(target.name,dir_fd=entry_fd)
        except FileNotFoundError:pass
        _sync_directory(target.parent,directory_fd,directory_identity)
    finally:
        _close_directory(directory_fd)
