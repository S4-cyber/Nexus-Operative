"""Process ownership for the opt-in forward worker, never an execution permission."""
import os
from pathlib import Path


class ForwardOwnership:
    def __init__(self,directory):
        self.path=Path(directory)/'paper_forward.owner.lock'
        self._fd=None

    @property
    def held(self):return self._fd is not None

    def acquire(self):
        if self.held:raise ValueError('PAPER_FORWARD_OWNER_ALREADY_HELD')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT,0o600)
        try:
            if os.fstat(fd).st_size==0:os.write(fd,b'0')
            os.lseek(fd,0,os.SEEK_SET)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
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
