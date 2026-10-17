"""One backend's nonblocking research job admission; no execution permission."""
import threading
BUILD_LOCK=threading.Lock()
