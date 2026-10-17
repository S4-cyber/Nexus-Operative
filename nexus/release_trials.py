"""Bounded offline release trials in disposable child-process data directories.

This report cannot approve release, attest market realism or grant order authority.
Run: python -m nexus.release_trials --output release-trials.json
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time

PROFILES = ('forward', 'operations', 'recovery', 'capacity', 'http', 'activity')


def _require(condition, code):
    if not condition: raise ValueError('RELEASE_TRIAL_' + code)


def _http_trial():
    """Real TCP/HTTP + ASGI lifespan, with external connections/vault access denied."""
    import socket
    import threading
    from urllib.request import build_opener, ProxyHandler
    import keyring
    import uvicorn
    attempts = dict(external_connections=0, credential_operations=0)
    def audit(event, args):
        if event == 'socket.connect':
            address = args[1]
            if not isinstance(address, tuple) or address[0] != '127.0.0.1':
                attempts['external_connections'] += 1
                raise RuntimeError('RELEASE_TRIAL_EXTERNAL_CONNECTION_DENIED')
    sys.addaudithook(audit)  # Disposable child only; never installed in the application.
    def denied(*args, **kwargs):
        attempts['credential_operations'] += 1
        raise RuntimeError('RELEASE_TRIAL_CREDENTIAL_ACCESS_DENIED')
    for name in ('get_keyring', 'get_password', 'set_password', 'delete_password'):
        setattr(keyring, name, denied)
    # Exercise the Windows-only credential gate on every host using injected
    # policy metadata, while keyring itself remains denied above.
    from types import SimpleNamespace
    from nexus import credential_vault
    credential_vault.os = SimpleNamespace(name='nt')
    from nexus.app import app
    root = Path(os.environ['NEXUS_PORTABLE_DATA'])
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0)); listener.listen(32)
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start(); started = time.monotonic(); responses = {}
    try:
        while not server.started:
            _require(thread.is_alive() and time.monotonic() - started < 15, 'HTTP_STARTUP')
            time.sleep(.01)
        opener = build_opener(ProxyHandler({}))
        base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        for path in ('/api/status', '/api/roadmap', '/api/onboarding/tasks', '/api/onboarding/readiness', '/api/paper/ai-provider/status', '/', '/portal'):
            with opener.open(base + path, timeout=5) as response:
                body = response.read(2 * 1024 * 1024)
                _require(response.status == 200, 'HTTP_STATUS')
                responses[path] = json.loads(body) if path.startswith('/api/') else body.decode('utf-8')
        status = responses['/api/status']
        _require(status['portal_enabled'] is False, 'ENGINE_AUTOSTART')
        for name in ('broker_connected', 'live_orders_enabled', 'money_movement_enabled'):
            _require(status[name] is False, 'AUTHORITY_ENABLED')
        _require(responses['/api/roadmap']['read_only'] is True, 'ROADMAP_MUTABLE')
        _require('nexus' in responses['/portal'].lower() and 'url=/portal' in responses['/'], 'PORTAL_CONTENT')
        _require(not list(root.rglob('*')), 'READ_ONLY_STARTUP_WROTE_DATA')
        _require(not any(attempts.values()), 'UNEXPECTED_EXTERNAL_ACCESS')
        result = dict(passed=True, transport='REAL_LOOPBACK_TCP_HTTP', startup_engine_stopped=True,
            endpoints_checked=list(responses), startup_data_files=0, **attempts,
            credential_policy_branch='INJECTED_WINDOWS_ONLY_GATE',
            limitations='Real local source-process HTTP only. No rendered browser, Windows executable, actual provider, order or sustained market test.')
    finally:
        server.should_exit = True; thread.join(timeout=10); listener.close()
    _require(not thread.is_alive(), 'HTTP_SHUTDOWN')
    result['graceful_shutdown'] = True
    return result


def _execute(profile):
    if profile == 'forward':
        from nexus.forward_drill import run
        return run(20)
    if profile == 'operations':
        from nexus.operation_drill import drill
        return drill(24)
    if profile == 'recovery':
        from nexus.recovery_drill import run
        return run()
    if profile == 'capacity':
        from nexus.durable_benchmark import benchmark
        return benchmark(30)
    if profile == 'http': return _http_trial()
    if profile == 'activity':
        from nexus.broker_activity_review import fixture_report
        return fixture_report()
    raise ValueError('RELEASE_TRIAL_UNKNOWN_PROFILE')


def _validate(profile, result):
    _require(profile in PROFILES and isinstance(result, dict), 'RESULT_SHAPE')
    # Each profile has explicit acceptance assertions; process exit 0 alone is insufficient.
    if profile == 'capacity':
        _require(len(result['treatments']) == 4, 'CAPACITY_TREATMENTS')
        _require(all(t['accounting_consistent'] is True and t['broker_orders'] == 0 and t['cycles'] == 30
                     for t in result['treatments']), 'CAPACITY_ACCOUNTING')
        for name in ('burst_treatment', 'atomic_burst_treatment'):
            _require(result[name]['accounting_consistent'] is True, 'BURST_ACCOUNTING')
        _require(result['target_validated'] is False, 'UNSUPPORTED_MARKET_SPEED_CLAIM')
    else: _require(result.get('passed') is True, 'PROFILE_FAILED')
    if profile == 'http':
        _require(result['transport'] == 'REAL_LOOPBACK_TCP_HTTP' and result['graceful_shutdown'] is True
                 and result['startup_engine_stopped'] is True, 'HTTP_LIFECYCLE')
        _require(result['external_connections'] == result['credential_operations'] == result['startup_data_files'] == 0, 'HTTP_SIDE_EFFECTS')
    if profile == 'activity':
        reviewed = result['review']
        _require(reviewed['retained_batches'] == 2 and reviewed['retained_activities'] == 5
                 and reviewed['normalization_evaluated'] is True, 'ACTIVITY_MERGE')
        _require(reviewed['activity_coverage_verified'] is False and reviewed['fee_coverage_complete'] is False
                 and reviewed['ready_for_routing'] is False, 'ACTIVITY_AUTHORITY')
        _require(reviewed['review_reasons'] == ['ACTIVITY_COVERAGE_UNVERIFIED', 'FEE_LINK_AND_TIMING_UNVERIFIED'], 'ACTIVITY_CONSISTENCY')
    if profile == 'forward':
        _require(result['rounds'] == 20 and len(result['recovered']['sessions']) == 20, 'FORWARD_SESSIONS')
        _require(result['recovered']['flat'] is True and result['recovered']['forward_stopped'] is True
                 and result['recovered']['portfolio_reconciled'] is True, 'FORWARD_RECOVERY')
    if profile == 'operations':
        _require(result['cycles'] == result['buy_fills'] == result['sell_fills'] == result['tier_vetoes'] == 24, 'OPERATION_COUNTS')
        _require(result['history_deleted'] is False and result['old_id_deduplicated'] is True, 'HISTORY_LOSS')
    if profile == 'recovery':
        _require(result['recovered']['halted'] is True and result['recovered']['portfolio_reconciled'] is True, 'RESTORE_ACCOUNTING')
    for name in ('broker_connected', 'live_orders_enabled', 'release_approved', 'application_accounts_changed'):
        if name in result: _require(result[name] is False, 'UNEXPECTED_AUTHORITY')


def source_digest(root):
    """Fingerprint executed source and test inputs; excludes generated reports/docs."""
    root = Path(root); entries = []
    paths = set(root.glob('nexus/*.py')) | set(root.glob('tests/*.py')) | set(root.glob('tools/*.py')) | set(root.glob('tools/*.cjs'))
    paths |= set(root.glob('.github/workflows/*.yml'))
    paths |= {root/'pyproject.toml', root/'dashboard/portal_preview.html', root/'installer/NexusOperative.iss'}
    for path in sorted(paths):
        entries.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(files=len(entries), sha256=hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest())


def run():
    root = Path(__file__).resolve().parents[1]
    source = source_digest(root); results = {}; timings = {}
    with tempfile.TemporaryDirectory(prefix='nexus-release-trials-') as temporary:
        for profile in PROFILES:
            directory = Path(temporary)/profile; directory.mkdir()
            env = {**os.environ, 'NEXUS_PORTABLE_DATA': str(directory)}
            expression = 'import json; from nexus.release_trials import _execute; print(json.dumps(_execute(' + repr(profile) + '),allow_nan=False))'
            started = time.perf_counter()
            child = subprocess.run([sys.executable, '-c', expression], cwd=root, env=env,
                                   capture_output=True, text=True, timeout=240)
            _require(child.returncode == 0, profile.upper() + '_CHILD_FAILED')
            try: result = json.loads(child.stdout)
            except ValueError: raise ValueError('RELEASE_TRIAL_INVALID_CHILD_REPORT') from None
            _validate(profile, result)
            results[profile] = result; timings[profile] = round(time.perf_counter()-started, 3)
    _require(source_digest(root) == source, 'SOURCE_CHANGED_DURING_RUN')
    from nexus.release_roadmap import REVISION
    return dict(mode='BOUNDED_OFFLINE_RELEASE_TRIALS', version=REVISION, source_fingerprint=source,
        passed=True, environment=dict(python=platform.python_version(), system=platform.system(), sqlite=sqlite3.sqlite_version),
        profiles=results, elapsed_seconds=timings,
        production_release_ready=False, release_approved=False, real_market_realism_authenticated=False,
        actual_broker_acceptance=False, sustained_forward_acceptance=False, native_windows_acceptance=False,
        live_orders_enabled=False, money_movement_enabled=False,
        limitations='Disposable offline process/HTTP/accounting trials. Wall-clock local storage timings include current machine load and are not broker or market latency. Synthetic quotes and assumed liquidity/fees cannot authenticate real-market performance, profitability, source provenance or complete 1.0 acceptance.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path); args = parser.parse_args()
    try:
        report = run(); encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(encoded, encoding='utf-8')
        print(encoded, end='')
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        # Raw child stderr/environment/credential values are never included in errors.
        parser.exit(1, 'Release trials failed: ' + type(error).__name__ + '\n')


if __name__ == '__main__': main()
