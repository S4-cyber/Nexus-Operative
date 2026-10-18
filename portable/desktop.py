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
    os.environ.setdefault('NEXUS_PORTABLE_DATA', str(data))
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

def wait_for_ready(port, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.35):
                return True
        except OSError:
            time.sleep(.15)
    return False

def main():
    root, data = configure()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import uvicorn
    from nexus.app import app
    port = select_port(os.environ.get("NEXUS_PORT", "8000"))
    url = f"http://127.0.0.1:{port}/portal"
    print("Nexus Operative — LOCAL PAPER SIMULATION ONLY")
    print("Data:", data)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning", proxy_headers=False))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    if not wait_for_ready(port):
        raise RuntimeError("Local PORTAL backend failed to start")

    try:
        # API/install checks and explicitly headless local operation never spawn UI.
        if os.environ.get('NEXUS_HEADLESS_MODE') == '1':
            worker.join()
            return
        # CI and systems without WebView2 can explicitly use the standard browser.
        if os.environ.get("NEXUS_BROWSER_MODE") == "1":
            webbrowser.open(url)
            worker.join()
            return
        try:
            import webview
            webview.create_window("Nexus Operative | PORTAL", url, width=1250, height=850, min_size=(950,650), background_color="#030613")
            webview.start(gui="edgechromium" if os.name == "nt" else None)
        except Exception as exc:
            print("Native window unavailable; switching to browser:", exc)
            webbrowser.open(url)
            worker.join()
    finally:
        server.should_exit = True
        worker.join(timeout=8)

if __name__ == "__main__":
    main()

