"""Best-effort emergency-process scheduling priority; never grants authority.

The independent emergency command asks Windows to schedule its short local cut
path at high priority before touching application storage.  Failure to elevate
must never prevent the latch or durable halt attempt, and no realtime priority
class is requested.
"""
import os

HIGH_PRIORITY_CLASS = 0x00000080


def request():
    if os.name != 'nt':
        return dict(attempted=False, confirmed=False, priority_class='STANDARD')
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetPriorityClass.restype = wintypes.BOOL
        kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
        kernel.GetPriorityClass.restype = wintypes.DWORD
        process = kernel.GetCurrentProcess()
        confirmed = bool(process and kernel.SetPriorityClass(process, HIGH_PRIORITY_CLASS)
            and kernel.GetPriorityClass(process) == HIGH_PRIORITY_CLASS)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        confirmed = False
    return dict(attempted=True, confirmed=confirmed,
        priority_class='HIGH' if confirmed else 'STANDARD')
