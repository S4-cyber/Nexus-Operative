"""Desktop launcher; research simulation only."""
import os
import sys
import socket
import threading
import time
import webbrowser
from pathlib import Path

def installation_root():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]

def configure(root=None):
    root = Path(root or installation_root()).resolve()
    data = Path(os.environ.get("NEXUS_PORTABLE_DATA", str((Path(os.environ.get("LOCALAPPDATA", str(root))) / "NexusOperative" / "storage") if getattr(sys, "frozen", False) and os.name == "nt" else (root / "storage")))).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    for key, filename in [("NEXUS_AUDIT_PATH", "audit.sqlite3"), ("NEXUS_RESERVE_PATH", "reserve.sqlite3"), ("NEXUS_STRATEGY_MEMORY_PATH", "strategy_memory.sqlite3")]:
        os.environ.setdefault(key, str(data / filename))
    return root, data

def select_port(preferred=8000):
    preferred = int(preferred)
    if not 1024 <= preferred <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

def main():
    root, data = configure()
    os.chdir(root)
    import uvicorn
    from nexus.app import app
    port = select_port(os.environ.get("NEXUS_PORT", "8000"))
    url = f"http://127.0.0.1:{port}/portal"
    print("Nexus Operative — LOCAL SIMULATION ONLY")
    print("Data:", data)
    def open_when_ready():
        for _ in range(100):
            time.sleep(.15)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    webbrowser.open(url)
                    return
            except OSError:
                continue
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)

if __name__ == "__main__":
    main()
