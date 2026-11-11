"""Process ownership for the opt-in forward worker, never an execution permission."""
import os
from pathlib import Path
import stat


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
        self.path=Path(directory)/'paper_forward.owner.lock'
        self._fd=None

    @property
    def held(self):return self._fd is not None

    def available(self):
        """Sample cross-process availability without creating or reserving it."""
        if self.held:return False
        try:before=self.path.lstat()
        except FileNotFoundError:return True
        except OSError:_fail('STORE_UNAVAILABLE')
        if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
                before.st_nlink!=1 or before.st_size!=1):
            _fail('STORE_UNAVAILABLE')
        flags=(os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|
            getattr(os,'O_CLOEXEC',0))
        try:fd=os.open(self.path,flags)
        except OSError:_fail('STORE_UNAVAILABLE')
        locked=False
        try:
            _opened_identity(self.path,fd,before)
            try:_lock(fd);locked=True
            except OSError:return False
            _opened_identity(self.path,fd,before)
            return True
        finally:
            if locked:
                try:_unlock(fd)
                except OSError:pass
            os.close(fd)

    def acquire(self):
        if self.held:raise ValueError('PAPER_FORWARD_OWNER_ALREADY_HELD')
        self.path.parent.mkdir(parents=True,exist_ok=True)
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
        except OSError:_fail('STORE_UNAVAILABLE')
        try:
            opened=_opened_identity(self.path,fd,before,allow_empty=True)
            if opened.st_size==0:
                os.write(fd,b'0')
                _opened_identity(self.path,fd,before)
            _lock(fd)
            _opened_identity(self.path,fd,before)
        except OSError as exc:
            os.close(fd)
            raise ValueError('PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER') from exc
        except BaseException:
            os.close(fd);raise
        self._fd=fd

    def release(self):
        fd=self._fd
        if fd is None:return
        self._fd=None
        # Closing the descriptor releases the OS lock, including after process death.
        os.close(fd)

    def __del__(self):
        self.release()
