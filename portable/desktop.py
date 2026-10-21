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

PAPER_TOOLS={
    'activity-response-review':'nexus.paper_activity_response_review',
    'account-response-review':'nexus.paper_account_response_review',
    'account-recovery':'nexus.paper_read_pacing_recovery',
    'activity':'nexus.paper_activity_acceptance_probe',
    'account-probe':'nexus.paper_acceptance_probe',
    'account-review':'nexus.paper_account_evidence_review',
    'observation-crypto':'nexus.encrypted_observation',
    'order-offload':'nexus.order_offload',
    'quality-review':'nexus.paper_execution_quality_review',
    'provider-results':'nexus.paper_provider_result_tool',
    'provider-recovery':'nexus.paper_provider_budget_recovery',
    'quote-capture':'nexus.paper_quote_capture',
    'quote-evidence':'nexus.paper_quote_bundle',
    'recovery':'nexus.paper_recovery_step',
}


def _paper_tool(args):
    """Explicit allowlist only; never initialize the desktop as a fallback."""
    if args in (['--help'],['--paper-tool','list']):
        import json
        from nexus.release_roadmap import REVISION
        print(json.dumps(dict(mode='NEXUS_PACKAGED_PAPER_TOOLS',version=REVISION,
            commands={name:'--paper-tool '+name+' --help' for name in sorted(PAPER_TOOLS)},
            emergency_stop_command='--emergency-stop',
            default_data_created=False,desktop_started=False,automatic_read=False,
            automatic_retry=False,broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,
            scope='Explicit private paper diagnostics only. Account/activity/quote/recovery reads require their own confirmation and saved Windows keys. Account-response-review replays four private response bodies against one exact retained account session; activity-response-review replays exact private account/page bodies against one retained activity receipt. Neither tool uses credentials or network access. Account-recovery and provider-recovery append exact-head, account- and ownership-fenced reviewed boundaries without reading keys, shortening cooldown, erasing uncertainty, reclaiming capacity, retrying or continuing. Read-only status/reviews, observation/quote-evidence encryption and offline order-history copy need no broker keys. Supply private paths outside the app/source folder; encrypted-input reviews decrypt in memory and print private plaintext. Observation crypto creates a separate authenticated encrypted copy and never stores its password. Order offload requires a stopped, flat, reconciled, logically archived journal and an application-closed confirmation. Rotation-plan is read-only; replacement install requires verified archive/candidate inputs plus separate exact recovery storage, stays halted, and supports explicit recovery/rollback without deleting preserved history. No order submission or automatic continuation.'),sort_keys=True,indent=2))
        return 0
    if len(args)<2 or args[0]!='--paper-tool' or args[1] not in PAPER_TOOLS:
        print('NEXUS_PAPER_TOOL_USAGE_ERROR',file=sys.stderr);return 2
    import importlib
    saved=sys.argv
    try:
        sys.argv=[saved[0],*args[2:]]
        result=importlib.import_module(PAPER_TOOLS[args[1]]).main()
        return result if type(result) is int and result in (0,2) else 2
    except SystemExit as error:
        return error.code if type(error.code) is int and error.code in (0,2) else 2
    except (Exception,KeyboardInterrupt):
        print('NEXUS_PAPER_TOOL_FAILED');return 2
    finally:sys.argv=saved


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if args:
        # Source-script entry also works without editable installation. Frozen
        # tools use bundled imports, never add the installation folder to paths.
        if not getattr(sys,'frozen',False):
            root=installation_root()
            if str(root) not in sys.path:sys.path.insert(0,str(root))
        if args==['--emergency-stop']:
            try:
                configure()
                from nexus.emergency_cut_tool import main as emergency_stop
                return emergency_stop()
            except (Exception,KeyboardInterrupt):
                print('NEXUS_EMERGENCY_STOP_FAILED')
                return 2
        return _paper_tool(args)
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
    raise SystemExit(main())

