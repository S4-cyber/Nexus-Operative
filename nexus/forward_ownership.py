"""Process ownership for the opt-in forward worker, never an execution permission."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from nexus.local_owner_scope import ProcessOwnerClaim


def _fail(code):
    raise ValueError('PAPER_FORWARD_OWNER_'+code) from None


def _opened_identity(path, fd, before=None, *, allow_empty=False):
    try:
        opened=os.fstat(fd);current=path.lstat()
    except OSError:_fail('STORE_UNAVAILABLE')
    if (stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(opened.st_mode) or
            not stat.S_ISREG(current.st_mode) or opened.st_nlink!=1 or
            current.st_nlink!=1 or not os.path.samestat(opened,current) or
            (before is not None and not os.path.samestat(before,opened)) or
            opened.st_size not in ((0,1) if allow_empty else (1,))):
        _fail('STORE_UNAVAILABLE')
    return opened


def _marker_content(fd, *, changed=False):
    """Require the fixed owner marker without changing it."""
    try:
        os.lseek(fd,0,os.SEEK_SET)
        marker=os.read(fd,2)
        os.lseek(fd,0,os.SEEK_SET)
    except OSError:_fail('STORE_CHANGED' if changed else 'STORE_UNAVAILABLE')
    if marker!=b'0':_fail('STORE_CHANGED' if changed else 'STORE_UNAVAILABLE')
    return True


def _posix_owner_scope(info, *, directory=False, changed=False):
    """Reject a POSIX owner scope another local account may replace or edit."""
    if os.name=='nt':return True
    mode=stat.S_IMODE(info.st_mode)
    safe=(info.st_uid==os.geteuid() and not mode&0o022 and
        (directory or mode&0o600==0o600))
    if not safe:_fail('STORE_CHANGED' if changed else 'STORE_UNAVAILABLE')
    return True


def _directory_identity(path, before=None, *, changed=False):
    """Sample one real directory and optionally require its prior identity."""
    try:current=path.lstat()
    except OSError:_fail('STORE_CHANGED' if changed else 'STORE_UNAVAILABLE')
    if (stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode) or
            (before is not None and not os.path.samestat(before,current))):
        _fail('STORE_CHANGED' if changed else 'STORE_UNAVAILABLE')
    _posix_owner_scope(current,directory=True,changed=changed)
    return current


def _lock(fd):
    os.lseek(fd,0,os.SEEK_SET)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)


def _unlock(fd):
    os.lseek(fd,0,os.SEEK_SET)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(fd,fcntl.LOCK_UN)


class ForwardOwnership:
    def __init__(self,directory):
        self.directory=Path(directory)
        self.path=self.directory/'paper_forward.owner.lock'
        self._fd=None
        self._identity=None
        self._directory_identity=None
        self._process_claim=ProcessOwnerClaim(self.path)

    @property
    def held(self):return self._fd is not None

    def validate(self):
        """Require the lock and its public directory to retain acquired identities."""
        fd=self._fd;identity=self._identity;directory_identity=self._directory_identity
        if fd is None or identity is None or directory_identity is None:_fail('NOT_HELD')
        directory=_directory_identity(self.directory,changed=True)
        if (directory.st_dev,directory.st_ino)!=directory_identity:_fail('STORE_CHANGED')
        try:opened=os.fstat(fd);current=self.path.lstat()
        except OSError:_fail('STORE_CHANGED')
        if (stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(opened.st_mode) or
                not stat.S_ISREG(current.st_mode) or opened.st_nlink!=1 or
                current.st_nlink!=1 or opened.st_size!=1 or current.st_size!=1 or
                not os.path.samestat(opened,current) or
                (opened.st_dev,opened.st_ino)!=identity):
            _fail('STORE_CHANGED')
        _posix_owner_scope(opened,changed=True)
        _marker_content(fd,changed=True)
        # Bracket the public lock-path sample with the acquired directory.
        # Moving the same locked inode into a replacement directory between
        # the first directory sample and path lstat must not acknowledge.
        _directory_identity(self.directory,directory,changed=True)
        return True

    def available(self):
        """Sample cross-process availability without creating or reserving it."""
        if self.held:return False
        claim=ProcessOwnerClaim(self.path)
        if not claim.acquire():return False
        try:return self._available_claimed()
        finally:claim.release()

    def _available_claimed(self):
        """Availability sample after the exact process path is reserved."""
        try:directory=self.directory.lstat()
        except FileNotFoundError:return True
        except OSError:_fail('STORE_UNAVAILABLE')
        directory=_directory_identity(self.directory,directory)
        try:before=self.path.lstat()
        except FileNotFoundError:
            _directory_identity(self.directory,directory)
            return True
        except OSError:_fail('STORE_UNAVAILABLE')
        if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
                before.st_nlink!=1 or before.st_size!=1):
            _fail('STORE_UNAVAILABLE')
        flags=(os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|
            getattr(os,'O_CLOEXEC',0))
        try:fd=os.open(self.path,flags)
        except PermissionError:
            if os.name=='nt':return False
            _fail('STORE_UNAVAILABLE')
        except OSError:_fail('STORE_UNAVAILABLE')
        locked=False
        available=False
        try:
            opened=_opened_identity(self.path,fd,before)
            _posix_owner_scope(opened)
            try:_lock(fd);locked=True;available=True
            except OSError:pass
            # Windows byte-range locks deny reads through a second handle.
            # Probe the lock before reading the marker so a healthy external
            # owner is reported as busy instead of as a corrupt store.
            if locked:_marker_content(fd)
            opened=_opened_identity(self.path,fd,before)
            _posix_owner_scope(opened,changed=True)
            if locked:_marker_content(fd,changed=True)
        finally:
            if locked:
                try:_unlock(fd)
                except OSError:pass
            os.close(fd)
        _directory_identity(self.directory,directory)
        return available

    def acquire_existing(self):
        """Hold an existing owner file without creating or modifying it.

        This is for stable read-only evidence review.  It cannot bootstrap an
        owner path and grants no execution permission; a retained history whose
        owner file is absent therefore needs explicit review instead of having
        a coordination file silently created by a diagnostic.
        """
        if self.held:raise ValueError('PAPER_FORWARD_OWNER_ALREADY_HELD')
        if not self._process_claim.acquire():
            raise ValueError('PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER')
        fd=None
        try:self._acquire_existing_claimed()
        except BaseException:
            self._process_claim.release()
            raise

    def _acquire_existing_claimed(self):
        try:directory=self.directory.lstat()
        except OSError:_fail('STORE_UNAVAILABLE')
        directory=_directory_identity(self.directory,directory)
        try:before=self.path.lstat()
        except OSError:_fail('STORE_UNAVAILABLE')
        if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
                before.st_nlink!=1 or before.st_size!=1):
            _fail('STORE_UNAVAILABLE')
        flags=(os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|
            getattr(os,'O_CLOEXEC',0))
        try:fd=os.open(self.path,flags)
        except PermissionError as exc:
            if os.name=='nt':
                raise ValueError('PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER') from exc
            _fail('STORE_UNAVAILABLE')
        except OSError:_fail('STORE_UNAVAILABLE')
        try:
            opened=_opened_identity(self.path,fd,before)
            _posix_owner_scope(opened)
            try:_lock(fd)
            except OSError as exc:
                raise ValueError('PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER') from exc
            _marker_content(fd)
            opened=_opened_identity(self.path,fd,before)
            _posix_owner_scope(opened,changed=True)
            _marker_content(fd,changed=True)
            _directory_identity(self.directory,directory)
        except BaseException:
            os.close(fd);raise
        self._fd=fd;self._identity=(opened.st_dev,opened.st_ino)
        self._directory_identity=(directory.st_dev,directory.st_ino)
        try:self.validate()
        except BaseException:
            self._fd=None;self._identity=None;self._directory_identity=None;os.close(fd);raise

    @contextmanager
    def existing_lease(self):
        """Bracket a read-only review with the existing cross-process lease."""
        self.acquire_existing();body_error=None
        try:yield self
        except BaseException as error:
            body_error=error
            raise
        finally:
            try:self.release()
            except BaseException:
                # A cleanup disagreement must not replace KeyboardInterrupt,
                # SystemExit, or the original review failure.
                if body_error is None:raise

    def acquire(self):
        if self.held:raise ValueError('PAPER_FORWARD_OWNER_ALREADY_HELD')
        if not self._process_claim.acquire():
            raise ValueError('PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER')
        try:self._acquire_claimed()
        except BaseException:
            self._process_claim.release()
            raise

    def _acquire_claimed(self):
        try:self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        except OSError:_fail('STORE_UNAVAILABLE')
        directory=_directory_identity(self.directory)
        try:before=self.path.lstat()
        except FileNotFoundError:before=None
        except OSError:_fail('STORE_UNAVAILABLE')
        if before is not None and (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or
                before.st_size not in (0,1)):
            _fail('STORE_UNAVAILABLE')
        flags=(os.O_RDWR|os.O_CREAT|getattr(os,'O_BINARY',0)|
            getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0))
        try:fd=os.open(self.path,flags,0o600)
        except PermissionError as exc:
            if os.name=='nt':
                raise ValueError('PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER') from exc
            _fail('STORE_UNAVAILABLE')
        except OSError:_fail('STORE_UNAVAILABLE')
        try:
            opened=_opened_identity(self.path,fd,before,allow_empty=True)
            _posix_owner_scope(opened)
            if opened.st_size==0:
                os.write(fd,b'0')
                opened=_opened_identity(self.path,fd,before)
                _posix_owner_scope(opened)
            _lock(fd)
            # An inherited Windows marker is unreadable until this process
            # owns its byte-range lock. A fresh marker is likewise validated
            # only after ownership is established.
            _marker_content(fd)
            opened=_opened_identity(self.path,fd,before)
            _posix_owner_scope(opened,changed=True)
            _marker_content(fd,changed=True)
            _directory_identity(self.directory,directory)
        except OSError as exc:
            os.close(fd)
            raise ValueError('PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER') from exc
        except BaseException:
            os.close(fd);raise
        self._fd=fd;self._identity=(opened.st_dev,opened.st_ino)
        self._directory_identity=(directory.st_dev,directory.st_ino)
        try:self.validate()
        except BaseException:
            self._fd=None;self._identity=None;self._directory_identity=None;os.close(fd);raise

    def release(self):
        fd=self._fd
        if fd is None:return
        exit_error=None
        try:
            # Keep the opened lease authoritative through finalization. A
            # replacement pathname can otherwise admit another cooperating
            # worker while this descriptor still owns only the retired inode.
            self.validate()
        except BaseException as error:exit_error=error
        finally:
            self._fd=None;self._identity=None;self._directory_identity=None
            try:
                # Closing releases the OS lock, including after process death.
                os.close(fd)
            except BaseException:
                if exit_error is None:raise
            finally:self._process_claim.release()
        if exit_error is not None:raise exit_error

    def __del__(self):
        try:self.release()
        except BaseException:pass
