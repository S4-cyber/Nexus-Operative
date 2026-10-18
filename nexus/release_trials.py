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
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

PROFILES = ('forward', 'operations', 'recovery', 'capacity', 'http', 'activity', 'activity_acceptance', 'reconciliation_acceptance', 'pacing', 'balance', 'collection', 'retained_balance', 'inspection_inventory', 'observation_review', 'open_order_terms', 'encrypted_observation')
PAPER_HTTP_CHECKS = ('missing_status_created_nothing', 'status_vault_silent',
    'unconfirmed_read_denied', 'invalid_scope_denied', 'foreign_origin_denied',
    'inspection_inventory_validated', 'cooldown_prevented_transport',
    'removal_completed_during_read', 'revoked_read_returned_no_observation',
    'pending_history_preserved', 'fresh_process_preserved_pending_history')
SAFE_FAILURE_CODE = re.compile(r'RELEASE_TRIAL_[A-Z0-9_]+\Z')


class ReleaseTrialError(ValueError):
    """Fixed-code release failure safe to expose in public CI output."""


def _require(condition, code):
    if not condition: raise ReleaseTrialError('RELEASE_TRIAL_' + code)


def _safe_failure_code(error):
    """Return only a fixed release-trial code; never child stderr or paths."""
    value = str(error)
    if isinstance(error, ReleaseTrialError) and SAFE_FAILURE_CODE.fullmatch(value):
        return value
    return 'RELEASE_TRIAL_INTERNAL_ERROR'


def _pacing_restart_review(path):
    """Disposable child only: replay fixture history with network/vault denied."""
    from nexus import paper_portal_probe, paper_credentials
    attempts = dict(external_connections=0, credential_operations=0)
    def audit(event, args):
        if event == 'socket.connect':
            attempts['external_connections'] += 1
            raise RuntimeError('RELEASE_TRIAL_RESTART_NETWORK_DENIED')
    def denied():
        attempts['credential_operations'] += 1
        raise RuntimeError('RELEASE_TRIAL_RESTART_VAULT_DENIED')
    sys.addaudithook(audit)
    paper_credentials._vault = denied
    path = Path(path); before = path.read_bytes()
    paper_portal_probe._managed_path = lambda account: path
    result = paper_portal_probe.status(expected_account_id='01234567-89ab-cdef-0123-456789abcdef')
    _require(result['pending_read'] is True and result['sessions'] == 2 and
        result['next_allowed_at'] == 1060 and result['suggested_action'] ==
        'REVIEW_RETAINED_PENDING_READ_NO_AUTOMATIC_RETRY', 'HTTP_RESTART_PENDING')
    _require(path.read_bytes() == before and not any(attempts.values()), 'HTTP_RESTART_SIDE_EFFECT')
    return dict(preserved=True, sessions=result['sessions'], **attempts)


def _http_pacing_trial(base, root):
    """Actual Portal HTTP actions; temporary storage and injected OS/HTTPS seams."""
    import io
    import threading
    from urllib.request import Request, build_opener, ProxyHandler
    from urllib.error import HTTPError
    from nexus import paper_portal_probe as portal, paper_credentials as credentials
    from nexus.alpaca_paper_inspection import URLS
    from nexus.portable_paper_observation import validate, FLAGS
    from nexus.paper_acceptance_probe import FLAGS as STATUS_FLAGS
    account_id = '01234567-89ab-cdef-0123-456789abcdef'
    key, secret = 'fixture_key_only_123', 'fixture_secret_only_123'
    path = root/'managed-paper-read-fixture'/'pacing.sqlite3'
    original_path, original_vault, original_inspect = portal._managed_path, credentials._vault, portal.inspect_saved
    started, resume = threading.Event(), threading.Event()
    workers = []; outcomes = []; now = [1000]; blocking = [False]
    state = dict(saved=None, reads=0, writes=0, deletes=0, requests=0, closed=0)
    def post(action, payload, *, origin=base, expected=200):
        request = Request(base+'/api/execution/alpaca-paper/local-credentials/'+action,
            data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','Origin':origin}, method='POST')
        try: response = build_opener(ProxyHandler({})).open(request, timeout=15)
        except HTTPError as error: response = error
        with response:
            raw = response.read(2*1024*1024)
            _require(response.status == expected and response.headers.get('Cache-Control') == 'no-store', 'HTTP_PAPER_STATUS')
        _require(key.encode() not in raw and secret.encode() not in raw, 'HTTP_PAPER_SECRET_ECHO')
        return json.loads(raw)
    class Vault:
        def get_password(self, service, account):
            _require((service, account) == (credentials.SERVICE, credentials.ACCOUNT), 'HTTP_VAULT_SCOPE')
            state['reads'] += 1; return state['saved']
        def set_password(self, service, account, value):
            _require((service, account) == (credentials.SERVICE, credentials.ACCOUNT), 'HTTP_VAULT_SCOPE')
            state['writes'] += 1; state['saved'] = value
        def delete_password(self, service, account):
            _require((service, account) == (credentials.SERVICE, credentials.ACCOUNT), 'HTTP_VAULT_SCOPE')
            state['deletes'] += 1; state['saved'] = None
    account = dict(id=account_id, currency='USD', status='ACTIVE', crypto_status='ACTIVE',
        account_blocked=False, trading_blocked=False, trade_suspended_by_user=False,
        cash='1000', non_marginable_buying_power='900', pending_transfer_in='0',
        pending_transfer_out='0', accrued_fees='0')
    class Response:
        status=200; headers={'Content-Type':'application/json'}
        def __init__(self, url, value): self.url=url; self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self): return self.url
        def read(self, size): return self.body.read(size)
        def __enter__(self): return self
        def __exit__(self, *args): self.body.close(); state['closed'] += 1
    class Opener:
        def open(self, request, timeout):
            _require(request.get_method() == 'GET' and request.full_url in URLS, 'HTTP_PAPER_GET_SCOPE')
            state['requests'] += 1
            if blocking[0]:
                started.set(); _require(resume.wait(10), 'HTTP_PAPER_REMOVAL_WAIT')
            return Response(request.full_url, account if request.full_url == URLS[0] else [])
    try:
        portal._managed_path = lambda account: path
        missing = post('pacing-status', dict(expected_account_id=account_id))
        _require(missing['journal_present'] is False and not path.parent.exists(), 'HTTP_PAPER_MISSING_WRITE')
        post('paced-inspect', dict(expected_account_id=account_id,confirm_read_only_paper=False), expected=503)
        post('paced-inspect', dict(expected_account_id=account_id,confirm_read_only_paper=True,pacing_path='forbidden'), expected=422)
        post('paced-inspect', dict(confirm_read_only_paper=True), expected=422)
        post('paced-inspect', dict(expected_account_id=account_id,confirm_read_only_paper=True), origin='https://foreign.invalid', expected=403)
        _require(not path.parent.exists(), 'HTTP_PAPER_DENIAL_WRITE')
        fixture_vault = Vault(); credentials._vault = lambda: fixture_vault
        portal.inspect_saved = lambda **kw: original_inspect(**kw,opener=Opener(),wall_clock=lambda:now[0],elapsed_clock=lambda:50)
        saved = post('save', dict(api_key=key,api_secret=secret,confirm_store=True))
        _require(saved['configured'] is True and saved['ready_for_routing'] is False, 'HTTP_PAPER_SAVE')
        payload = dict(expected_account_id=account_id,confirm_read_only_paper=True)
        observed = post('paced-inspect', payload)
        portable = validate(observed['portable_observation'])
        _require(observed['durable_local_pacing'] is True and portable['account_id'] == account_id and
            portable['source'] == 'INJECTED_UNATTESTED' and observed['ready_for_routing'] is False and
            all(observed[flag] is False for flag in FLAGS) and
            observed['local_pacing']['sessions'] == 1 and observed['local_pacing']['next_allowed_at'] == 1060 and
            state['requests'] == state['closed'] == 4, 'HTTP_PAPER_INSPECTION')
        before = path.read_bytes()
        post('paced-inspect', payload, expected=503)
        _require(state['requests'] == 4 and path.read_bytes() == before, 'HTTP_PAPER_COOLDOWN')
        now[0] = 1060; blocking[0] = True
        def read():
            try: outcomes.append(post('paced-inspect', payload, expected=503))
            except Exception: outcomes.append(None)
        worker = threading.Thread(target=read, daemon=True); workers.append(worker); worker.start()
        _require(started.wait(10), 'HTTP_PAPER_READ_NOT_STARTED')
        removed = post('remove', dict(confirm_remove=True))
        _require(removed['configured'] is False and worker.is_alive() and state['saved'] is None and
            state['writes'] == state['deletes'] == 1, 'HTTP_PAPER_REMOVAL')
        resume.set(); worker.join(20)
        _require(not worker.is_alive() and outcomes == [{'detail':'PAPER_ONBOARDING_ACTION_UNAVAILABLE'}] and
            state['requests'] == state['closed'] == 5, 'HTTP_PAPER_REVOKED_RESULT')
        credentials._vault = original_vault  # Status must work with the actual denied vault again.
        before = path.read_bytes()
        pending = post('pacing-status', dict(expected_account_id=account_id))
        _require(pending['pending_read'] is True and pending['sessions'] == 2 and
            pending['next_allowed_at'] == 1060 and all(pending[flag] is False for flag in STATUS_FLAGS) and
            path.read_bytes() == before, 'HTTP_PAPER_PENDING')
        expression = 'import json,sys; from nexus.release_trials import _pacing_restart_review; print(json.dumps(_pacing_restart_review(sys.argv[1])))'
        child = subprocess.run([sys.executable,'-c',expression,str(path)],capture_output=True,text=True,timeout=25)
        _require(child.returncode == 0, 'HTTP_PAPER_RESTART_FAILED')
        restarted = json.loads(child.stdout)
        _require(restarted == dict(preserved=True,sessions=2,external_connections=0,credential_operations=0) and
            path.read_bytes() == before, 'HTTP_PAPER_RESTART_RESULT')
        return dict(**{flag:True for flag in PAPER_HTTP_CHECKS}, injected_get_requests=state['requests'],
            actual_vault_access=False, actual_broker_access=False, ready_for_routing=False,
            live_orders_enabled=False, money_movement_enabled=False, release_approved=False,
            scope='REAL_LOOPBACK_HTTP_INJECTED_VAULT_AND_PAPER_TRANSPORT_TEMPORARY_JOURNAL')
    finally:
        resume.set()
        for worker in workers: worker.join(20)
        portal._managed_path, credentials._vault, portal.inspect_saved = original_path, original_vault, original_inspect


FIRST_RUN_HTTP_CHECKS=('new_install_preset_applied','only_settings_storage_created',
    'no_trade_autostart','checklist_purposes_present','optional_connections_not_required','saved_off_preserved')


def _http_defaults_trial(base,root):
    """Actual default-configuration HTTP path; no engine, feed or order start."""
    from urllib.request import build_opener,ProxyHandler,Request
    opener=build_opener(ProxyHandler({}))
    def call(path,data=None):
        request=Request(base+path,data=None if data is None else json.dumps(data).encode(),
            headers={'Origin':base,'Content-Type':'application/json'},method='GET' if data is None else 'PUT')
        with opener.open(request,timeout=5) as response:return json.loads(response.read(256*1024))
    settings=call('/api/paper/settings');tiers=call('/api/tiers/permissions')['tiers']
    _require(settings['paper_budget']==10000 and {t['tier'] for t in tiers if t['enabled']}=={'terrestrial'},'HTTP_FIRST_RUN_PRESET')
    _require(sorted(p.name for p in root.iterdir())==['portal_settings.sqlite3'],'HTTP_FIRST_RUN_ACCOUNT_CREATED')
    items={row['id']:row for row in call('/api/onboarding/tasks')['items']}
    _require(items['budget']['checked'] and items['tier']['checked'] and
        items['portfolio']['status']=='ON_DEMAND' and not items['portfolio']['checked'],'HTTP_FIRST_RUN_CHECKS')
    _require(all(type(row['why']) is str and len(row['why'])>30 and row['setup_scope'] for row in items.values()),'HTTP_FIRST_RUN_PURPOSES')
    _require(all(items[k]['setup_scope'] in ('Optional','Future feature') for k in
        ('external_ai','paper_read','payment','broker','cloud')),'HTTP_FIRST_RUN_OPTIONAL')
    call('/api/tiers/terrestrial/permission',{'enabled':False});call('/api/paper/settings')
    _require(not any(t['enabled'] for t in call('/api/tiers/permissions')['tiers']),'HTTP_FIRST_RUN_OFF_OVERWRITTEN')
    _require(not call('/api/status')['portal_enabled'] and not call('/api/paper/forward')['enabled'], 'HTTP_FIRST_RUN_STARTED')
    return {key:True for key in FIRST_RUN_HTTP_CHECKS}


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
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, proxy_headers=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start(); started = time.monotonic(); responses = {}
    try:
        while not server.started:
            _require(thread.is_alive() and time.monotonic() - started < 15, 'HTTP_STARTUP')
            time.sleep(.01)
        opener = build_opener(ProxyHandler({}))
        base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        for path in ('/api/status', '/api/roadmap', '/api/onboarding/tasks', '/api/onboarding/readiness', '/api/paper/ai-provider/status', '/api/paper/portfolio', '/api/paper/forward', '/api/paper/forward/sessions', '/api/execution/recovery', '/api/paper/forward/recovery', '/', '/portal'):
            with opener.open(base + path, timeout=5) as response:
                body = response.read(2 * 1024 * 1024)
                _require(response.status == 200, 'HTTP_STATUS')
                responses[path] = json.loads(body) if path.startswith('/api/') else body.decode('utf-8')
        from urllib.request import Request
        from urllib.error import HTTPError
        for headers in ({'Host':'rebinding.example'},{'Origin':'https://foreign.example'}):
            try:
                with opener.open(Request(base+'/api/status',headers=headers),timeout=5):
                    _require(False,'HTTP_UNTRUSTED_ADMITTED')
            except HTTPError as error:
                _require(error.code==403 and error.headers.get('Cache-Control')=='no-store','HTTP_BOUNDARY')
        with opener.open(base+'/api/status',timeout=5) as response:
            _require(response.headers.get('Cache-Control')=='no-store','HTTP_PRIVATE_CACHE')
        status = responses['/api/status']
        _require(status['portal_enabled'] is False, 'ENGINE_AUTOSTART')
        for name in ('broker_connected', 'live_orders_enabled', 'money_movement_enabled'):
            _require(status[name] is False, 'AUTHORITY_ENABLED')
        _require(responses['/api/roadmap']['read_only'] is True, 'ROADMAP_MUTABLE')
        _require(responses['/api/execution/recovery']['journal_present'] is False and
            responses['/api/execution/recovery']['status']=='NO_RETAINED_HISTORY' and
            responses['/api/paper/forward/recovery']['order_history_present'] is False,
            'PASSIVE_ORDER_RECOVERY_STARTUP')
        forward=responses['/api/paper/forward']
        _require(forward['enabled'] is False and forward['history']==[] and
            forward['storage_capacity']['sessions_retained']==forward['storage_capacity']['quote_observations_retained']==0 and
            responses['/api/paper/forward/sessions']['sessions']==[], 'PASSIVE_FORWARD_STARTUP')
        _require('nexus' in responses['/portal'].lower() and 'url=/portal' in responses['/'], 'PORTAL_CONTENT')
        _require(not list(root.rglob('*')), 'READ_ONLY_STARTUP_WROTE_DATA')
        _require(not any(attempts.values()), 'UNEXPECTED_EXTERNAL_ACCESS')
        first_run_defaults = _http_defaults_trial(base,root)
        _require(not any(attempts.values()),'HTTP_FIRST_RUN_EXTERNAL_ACCESS')
        paper_pacing = _http_pacing_trial(base, root)
        _require(not any(attempts.values()), 'HTTP_PAPER_EXTERNAL_ACCESS')
        result = dict(passed=True, transport='REAL_LOOPBACK_TCP_HTTP', startup_engine_stopped=True,
            endpoints_checked=list(responses), startup_data_files=0, passive_forward_reads_preserved_missing_storage=True, **attempts,
            paper_pacing=paper_pacing,first_run_defaults=first_run_defaults,
            credential_policy_branch='INJECTED_WINDOWS_ONLY_GATE',untrusted_host_rejected=True,cross_origin_rejected=True,api_no_store=True,proxy_headers_trusted=False,
            limitations='Real local source-process HTTP only; paper credential and provider seams are injected fixtures with temporary retained pacing. No rendered browser, Windows executable, actual provider, order or sustained market test.')
    finally:
        server.should_exit = True; thread.join(timeout=10); listener.close()
    _require(not thread.is_alive(), 'HTTP_SHUTDOWN')
    result['graceful_shutdown'] = True
    return result


def _pacing_trial():
    """Fixed local reservation/cooldown fixture; no HTTP or credential access."""
    from nexus.paper_read_pacing import PaperReadPacing
    account = '01234567-89ab-cdef-0123-456789abcdef'
    def denied(operation, reason):
        try: operation()
        except ValueError as error:
            _require(str(error) == 'PAPER_READ_PACING_' + reason, 'PACING_DENIAL_REASON')
            return True
        raise ValueError('RELEASE_TRIAL_PACING_DENIAL_MISSING')
    with tempfile.TemporaryDirectory(prefix='nexus-pacing-trial-') as directory:
        path = Path(directory)/'pacing.sqlite3'
        gate = PaperReadPacing(path, account)
        token = gate.begin(admission=lambda:1000)
        gate.finish(token, admission=lambda:1000, status_code=200)
        gate = PaperReadPacing(path, account)
        cooldown = denied(lambda:gate.begin(admission=lambda:1059), 'COOLDOWN')
        token = gate.begin(admission=lambda:1060)
        gate.finish(token, admission=lambda:1060, status_code=429, retry_after='120')
        rate_limit = denied(lambda:PaperReadPacing(path,account).begin(admission=lambda:1179), 'COOLDOWN')
        token = gate.begin(admission=lambda:1180)
        gate.finish(token, admission=lambda:1180, status_code=401)
        auth = denied(lambda:PaperReadPacing(path,account).begin(admission=lambda:100000), 'BLOCKED_REVIEW')
        pending = PaperReadPacing(Path(directory)/'pending.sqlite3', account)
        pending.begin(admission=lambda:1000)
        interrupted = denied(lambda:PaperReadPacing(pending.path,account).begin(admission=lambda:100000), 'PENDING_REVIEW')
        owned = PaperReadPacing(Path(directory)/'owned.sqlite3', account)
        with owned.ownership() as owner:
            owned.begin(admission=lambda:1000, ownership=owner)
        snapshot = owned.inspect()
        owned.interrupt(snapshot['pending_token'], expected_head=snapshot['head_sha256'],
            expected_account_id=account, confirm_interrupted=True, admission=lambda:1001)
        interrupted_state = PaperReadPacing(owned.path,account).inspect()
        explicit_review = interrupted_state['last_outcome'] == 'INTERRUPTED_UNKNOWN' and interrupted_state['sessions'] == 1
        long_wait = denied(lambda:owned.begin(admission=lambda:87400), 'COOLDOWN')
        state = PaperReadPacing(path,account).inspect()
        wal = PaperReadPacing(Path(directory)/'wal.sqlite3', account)
        token = wal.begin(admission=lambda:1000)
        wal.finish(token,admission=lambda:1000,status_code=200)
        db = sqlite3.connect(wal.path)
        try: _require(db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal', 'PACING_WAL_FIXTURE')
        finally: db.close()
        before = {p.name:p.read_bytes() for p in Path(directory).iterdir() if p.is_file()}
        wal_read = denied(wal.inspect,'JOURNAL_MODE_REVIEW_REQUIRED')
        wal_write = denied(lambda:wal.begin(admission=lambda:1060),'JOURNAL_MODE_REVIEW_REQUIRED')
        no_sidecars = before == {p.name:p.read_bytes() for p in Path(directory).iterdir() if p.is_file()}
        return dict(passed=True, source='FIXED_LOCAL_INJECTED_CLOCK_FIXTURE', completed_sessions=state['sessions'],
            replayed_events=state['events'], cooldown_enforced=cooldown, retry_after_retained=rate_limit,
            authentication_block_retained=auth, pending_read_not_auto_released=interrupted,
            explicit_owned_interruption_retained=explicit_review, interrupted_full_day_cooldown=long_wait,
            wal_read_rejected_before_sqlite=wal_read, wal_write_rejected_before_conversion=wal_write,
            wal_rejection_preserved_files=no_sidecars,
            broker_wide_quota_verified=False, ready_for_routing=False,
            limitations='Disposable local journal policy only; no provider requests, actual rate-limit acceptance, distributed ownership or runtime default integration.')


def _inspection_inventory_trial():
    """Actual reader, injected objects/keys only; no provider or vault access."""
    import io
    from nexus.alpaca_paper_inspection import inspect_account
    account = dict(id='01234567-89ab-cdef-0123-456789abcdef',currency='USD',status='ACTIVE',crypto_status='ACTIVE',
        account_blocked=False,trading_blocked=False,trade_suspended_by_user=False,cash='1000',
        non_marginable_buying_power='900',pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    position = dict(symbol='BTCUSD',asset_class='crypto',side='long',qty='2',qty_available='1.9')
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value): self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self): return self.url
        def read(self,size): return self.body.read(size)
        def __enter__(self): return self
        def __exit__(self,*args): self.body.close()
    class Opener:
        def __init__(self,a,p): self.values=iter([a,[p],[],dict(a)]);self.requests=0
        def open(self,request,timeout): self.requests+=1;return Response(request.full_url,next(self.values))
    reports=[];requests=0
    for a,p in ((account,position),(dict(account,currency='EUR'),position),(account,dict(position,qty_available=None))):
        opener=Opener(a,p)
        reports.append(inspect_account('fixture_key_only_123','fixture_secret_only_123',confirm_read_only_paper=True,
            expected_account_id=account['id'],opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50))
        requests+=opener.requests
    from nexus.portable_paper_observation import loads, validate
    from nexus.alpaca_paper_contract import canonical
    exported = [loads(canonical(r['portable_observation']).encode()) for r in reports]
    tampered = dict(exported[0], manifest_sha256='0'*64)
    tamper_rejected = False
    try: validate(tampered)
    except ValueError: tamper_rejected = True
    return dict(passed=True,source='INJECTED_PAPER_INVENTORY_READER',get_requests=requests,observations=reports, portable_roundtrips=len(exported), portable_tamper_rejected=tamper_rejected,
        broker_connected=False,live_orders_enabled=False,
        limitations='Fixed response objects and clocks; no actual credentials, provider, rendered UI, account reconciliation or spendable funds.')


def _execute(profile):
    if profile == 'activity_acceptance':
        from nexus.paper_activity_acceptance_evidence import fixture_report
        return fixture_report()
    if profile == 'reconciliation_acceptance':
        from nexus.paper_reconciliation_acceptance import fixture_report
        return fixture_report()
    if profile == 'encrypted_observation':
        from nexus.encrypted_observation import fixture_report
        return fixture_report()
    if profile == 'open_order_terms':
        from nexus.paper_open_order_terms import fixture_report
        return fixture_report()
    if profile == 'observation_review':
        from nexus.paper_observation_review import fixture_report
        return fixture_report()
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
    if profile == 'pacing': return _pacing_trial()
    if profile == 'inspection_inventory': return _inspection_inventory_trial()
    if profile == 'retained_balance':
        from nexus.retained_balance_review import fixture_report
        return fixture_report()
    if profile == 'collection':
        from nexus.activity_collection_evidence import fixture_report
        return fixture_report()
    if profile == 'balance':
        from nexus.broker_balance_review import fixture_report
        return fixture_report()
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
        _require(all(result['first_run_defaults'][key] is True for key in FIRST_RUN_HTTP_CHECKS),'HTTP_FIRST_RUN_DEFAULTS')
        _require(result['passive_forward_reads_preserved_missing_storage'] is True,'HTTP_PASSIVE_FORWARD')
        _require(result['transport'] == 'REAL_LOOPBACK_TCP_HTTP' and result['graceful_shutdown'] is True
                 and result['startup_engine_stopped'] is True, 'HTTP_LIFECYCLE')
        _require(result['external_connections'] == result['credential_operations'] == result['startup_data_files'] == 0, 'HTTP_SIDE_EFFECTS')
        _require(all(result[k] is True for k in ('untrusted_host_rejected','cross_origin_rejected','api_no_store')) and result['proxy_headers_trusted'] is False,'HTTP_BOUNDARY_POLICY')
        paper = result['paper_pacing']
        _require(all(paper[k] is True for k in PAPER_HTTP_CHECKS), 'HTTP_PAPER_FENCES')
        _require(type(paper['injected_get_requests']) is int and paper['injected_get_requests'] == 5 and
            paper['scope'] == 'REAL_LOOPBACK_HTTP_INJECTED_VAULT_AND_PAPER_TRANSPORT_TEMPORARY_JOURNAL', 'HTTP_PAPER_SCOPE')
        _require(all(paper[k] is False for k in ('actual_vault_access','actual_broker_access','ready_for_routing',
            'live_orders_enabled','money_movement_enabled','release_approved')), 'HTTP_PAPER_AUTHORITY')
    if profile == 'encrypted_observation':
        _require(all(result[k] is True for k in ('roundtrip_matches','wrong_password_rejected','ciphertext_randomized')), 'ENCRYPTED_OBSERVATION_CRYPTO')
        _require(result['kdf'] == 'SCRYPT_N131072_R8_P1' and result['cipher'] == 'AES256GCM_RANDOM_NONCE12_SALT16', 'ENCRYPTED_OBSERVATION_POLICY')
        _require(all(result[k] is False for k in ('password_saved','vault_accessed','external_connections','original_plaintext_deleted',
            'output_file_writes','ready_for_routing','live_orders_enabled')), 'ENCRYPTED_OBSERVATION_AUTHORITY')
    if profile == 'open_order_terms':
        from nexus.paper_open_order_terms import FLAGS
        observed, unsupported = result['observed'], result['unsupported']
        _require(observed['terms_complete'] is True and observed['reported_rows'] == 3 and
            observed['supported_unique_orders'] == 2 and observed['duplicate_rows_ignored'] == 1 and
            observed['remaining_buy_limit_principal_cap_usd'] == '250' and
            observed['remaining_sell_quantity_btc'] == '0.5', 'OPEN_TERMS_ARITHMETIC')
        _require(unsupported['terms_complete'] is False and unsupported['status'] == 'REVIEW_REQUIRED' and
            unsupported['remaining_buy_limit_principal_cap_usd'] is None and
            unsupported['remaining_sell_quantity_btc'] is None and unsupported['normalized_terms_sha256'] is None,
            'OPEN_TERMS_UNKNOWN')
        _require(all(all(r[k] is False for k in FLAGS) for r in (observed,unsupported)), 'OPEN_TERMS_AUTHORITY')
    if profile == 'observation_review':
        reviewed, boundary = result['review'], result['boundary_review']
        _require(result['encrypted_pair_review_matches'] is True and result['decrypted_file_writes'] is False,
            'OBSERVATION_REVIEW_ENCRYPTED_INPUTS')
        _require(reviewed['arithmetic_evaluated'] is True and reviewed['head_rechecked'] is True and
            result['stale_head_rejected'] is True, 'OBSERVATION_REVIEW_HEAD')
        _require(reviewed['sampled_balance_changes']['cash'] == '9.78011' and
            reviewed['retained_balance_review']['gross_fill_review']['gross_minus_supplied_observed'] ==
                {'USD':'0.10989','BTC':'0.001'}, 'OBSERVATION_REVIEW_ARITHMETIC')
        _require(boundary['arithmetic_evaluated'] is False and boundary['retained_balance_review'] is None and
            boundary['boundary_fill_events'] == 1 and
            'RETAINED_FILL_DURING_SEQUENTIAL_OBSERVATION' in boundary['review_reasons'], 'OBSERVATION_REVIEW_BOUNDARY')
        flags = ('admission_reserved','source_attested','source_coverage_verified','balance_instants_verified',
            'observation_windows_verified_quiet','atomic_snapshot','fee_coverage_complete','fee_execution_links_verified',
            'holds_reconciled','broker_account_reconciled','spendable_funds_verified','ledger_applied',
            'ready_for_routing','broker_connected','live_orders_enabled','automatic_retry','automatic_continuation')
        _require(all(r['status']=='REVIEW_REQUIRED' and all(r[k] is False for k in flags)
            for r in (reviewed,boundary)), 'OBSERVATION_REVIEW_AUTHORITY')
    if profile == 'inspection_inventory':
        _require(result['get_requests'] == 12 and len(result['observations']) == 3, 'INVENTORY_READ_SCOPE')
        regular, foreign, missing = result['observations']
        _require(regular['observed_inventory'] == dict(cash='1000', non_marginable_buying_power='900',
            btc_total='2', btc_available='1.9', btc_unavailable_difference='0.1', scope_supported=True), 'INVENTORY_AMOUNTS')
        _require(foreign['observed_inventory']['cash'] is None and foreign['observed_usd_ceiling'] is None and
            missing['observed_inventory']['btc_available'] is None and missing['observed_inventory']['btc_unavailable_difference'] is None,
            'INVENTORY_UNKNOWN_FIELDS')
        _require(all(r['source'] == 'INJECTED_UNATTESTED' and all(r[k] is False for k in
            ('ready_for_routing','spendable_funds_verified','broker_account_reconciled','broker_connected',
             'ledger_applied','live_orders_enabled','atomic_snapshot')) for r in result['observations']), 'INVENTORY_AUTHORITY')
        _require(result['portable_roundtrips'] == 3 and result['portable_tamper_rejected'] is True, 'INVENTORY_EXPORT_FENCES')
        from nexus.portable_paper_observation import create, validate
        _require(all(validate(r['portable_observation']) == create(r) for r in result['observations']), 'INVENTORY_EXPORT_BINDING')
    if profile == 'retained_balance':
        reviewed = result['review']; gross = reviewed['gross_fill_review']
        _require(reviewed['arithmetic_evaluated'] is True and reviewed['head_rechecked'] is True and
            result['stale_head_rejected'] is True, 'RETAINED_BALANCE_FENCES')
        _require(gross['executions'] == 2 and gross['execution_groups'] == 2 and
            gross['gross_execution_ending_balances'] == {'USD':'1009.89','BTC':'0.001'} and
            gross['residual_equals_all_retained_fee_debits'] == {'USD':True,'BTC':True} and
            gross['residual_equals_exact_interval_fee_debits'] == {'USD':None,'BTC':None} and
            result['date_only_fee_residual_not_promoted'] is True and
            result['exact_timestamped_fee_residual_compared'] is True, 'RETAINED_BALANCE_ARITHMETIC')
        _require(reviewed['status'] == 'REVIEW_REQUIRED' and all(reviewed[k] is False for k in
            ('source_coverage_verified', 'fee_coverage_complete', 'fee_execution_links_verified', 'holds_reconciled',
             'broker_account_reconciled', 'spendable_funds_verified', 'ready_for_routing', 'ledger_applied', 'live_orders_enabled', 'admission_reserved'))
            and all(gross[k] is False for k in ('fee_posting_interval_verified','fee_debits_allocated_to_interval','fee_execution_links_verified',
                'fee_reserved_funding_verified')), 'RETAINED_BALANCE_AUTHORITY')
    if profile == 'collection':
        _require(result['get_requests'] == 4 and result['retained_activities'] == 200 and
            result['retained_observations'] == 1 and result['retained_pages'] == 2, 'COLLECTION_RETENTION')
        _require(result['page_cap_reported'] is True and result['cursor_chain_preserved'] is True, 'COLLECTION_SCOPE')
        _require(all(result[k] is False for k in ('source_attested', 'activity_coverage_verified',
            'ready_for_routing', 'automatic_continuation')), 'COLLECTION_AUTHORITY')
    if profile == 'balance':
        reviewed = result['review']
        _require(reviewed['balance_projection_evaluated'] is True and
            reviewed['linked_history_review']['projection_matches_declared_balances'] is True and
            reviewed['linked_history_review']['cash_path']['declared_path_consistency_ok'] is True, 'BALANCE_ACCOUNTING')
        _require(all(result['negative_checks'].get(k) is True for k in ('missing_available_btc_is_unknown',
            'cash_disagreement_detected')), 'BALANCE_NEGATIVE_FENCES')
        _require(reviewed['status'] == 'REVIEW_REQUIRED' and all(reviewed[k] is False for k in
            ('holds_reconciled', 'broker_account_reconciled', 'spendable_funds_verified', 'ready_for_routing',
             'source_coverage_verified', 'fee_coverage_complete', 'ledger_applied', 'live_orders_enabled')), 'BALANCE_AUTHORITY')
    if profile == 'pacing':
        _require(result['completed_sessions'] == 3 and result['replayed_events'] == 6, 'PACING_HISTORY')
        _require(all(result[name] is True for name in ('cooldown_enforced', 'retry_after_retained',
            'authentication_block_retained', 'pending_read_not_auto_released',
            'explicit_owned_interruption_retained', 'interrupted_full_day_cooldown',
            'wal_read_rejected_before_sqlite','wal_write_rejected_before_conversion','wal_rejection_preserved_files')), 'PACING_FENCES')
        _require(result['broker_wide_quota_verified'] is False and result['ready_for_routing'] is False, 'PACING_AUTHORITY')
    if profile == 'activity_acceptance':
        _require(result['mode']=='DISPOSABLE_CROSS_JOURNAL_ACTIVITY_ACCEPTANCE_RECOVERY' and
            result['source']=='SYNTHETIC_LOCAL_PROCESS_DEATH_FIXTURE' and
            result['accepted_transitions_after_commit']==2, 'ACTIVITY_ACCEPTANCE_SCOPE')
        _require(all(result[name] is True for name in ('missing_status_created_nothing',
            'precommit_death_retained_unbound_transition','postcommit_death_replayed_bound_transition',
            'precommit_rollback_journal_observed','fresh_process_journals_unchanged',
            'fresh_process_vault_silent','postcommit_latest_content_replayed',
            'postcommit_all_content_replayed')), 'ACTIVITY_ACCEPTANCE_RECOVERY')
        _require(all(result[name] is False for name in ('actual_provider_access','actual_vault_access','source_attested',
            'activity_coverage_verified','fee_execution_links_verified','broker_account_reconciled',
            'spendable_funds_verified','ready_for_routing','broker_connected','live_orders_enabled',
            'money_movement_enabled','release_approved')), 'ACTIVITY_ACCEPTANCE_AUTHORITY')
    if profile == 'reconciliation_acceptance':
        _require(result['source']=='SYNTHETIC_LOCAL_ACCEPTED_RECONCILIATION_FIXTURE' and
            result['repeat_review_matches'] is True and result['journals_unchanged'] is True and
            result['unbound_history_rejected'] is True and
            result['continuation_required_history_rejected'] is True and
            result['out_of_window_observations_rejected'] is True and
            result['private_scope_omitted'] is True,
            'RECONCILIATION_ACCEPTANCE_REPLAY')
        _require(result['complete_accepted_history_replayed'] is True and
            result['exact_activity_head_bound'] is True and
            result['legacy_baseline_absent'] is True and
            result['complete_pacing_sessions_accepted'] is True and
            result['complete_retained_batches_accepted'] is True and
            result['complete_collection_observations_accepted'] is True and
            result['all_collection_cursor_sequences_complete'] is True and
            result['declared_windows_cover_observation_envelope'] is True and
            result['observation_arithmetic_evaluated'] is True,
            'RECONCILIATION_ACCEPTANCE_BINDING')
        _require(all(result[name] is False for name in ('actual_provider_access','actual_vault_access','requested_window_boundaries_verified',
            'broker_account_reconciled','spendable_funds_verified','ready_for_routing','broker_connected',
            'live_orders_enabled','money_movement_enabled','release_approved')),
            'RECONCILIATION_ACCEPTANCE_AUTHORITY')
    if profile == 'activity':
        reviewed = result['review']
        _require(reviewed['retained_batches'] == 2 and reviewed['retained_activities'] == 5
                 and reviewed['normalization_evaluated'] is True, 'ACTIVITY_MERGE')
        _require(reviewed['activity_coverage_verified'] is False and reviewed['fee_coverage_complete'] is False
                 and reviewed['ready_for_routing'] is False, 'ACTIVITY_AUTHORITY')
        _require(reviewed['review_reasons'] == ['ACTIVITY_COVERAGE_UNVERIFIED', 'FEE_LINK_AND_TIMING_UNVERIFIED'], 'ACTIVITY_CONSISTENCY')
    if profile == 'forward':
        coverage=result['recovered']['coverage_review']
        from nexus.forward_trial_review import FLAGS as COVERAGE_FLAGS
        _require(coverage['status']=='REVIEW_REQUIRED' and coverage['injected_observations']==result['cycles'] and
            coverage['declared_public_covered_seconds']=='0' and coverage['declared_coverage_target_met'] is False,
            'FORWARD_COVERAGE_SCOPE')
        _require(coverage['retained_quote_recheck_applied'] is True and
            coverage['eligibility_count_scope']=='RECORDED_FLAGS_ONLY_SEE_REVALIDATED_COUNTS' and
            type(coverage['recorded_eligibility_disagreements_in_window']) is int and
            coverage['recorded_eligibility_disagreements_in_window']==0 and
            type(coverage['revalidated_eligible_observations_in_window']) is int and
            coverage['revalidated_eligible_observations_in_window']==coverage['eligible_observations_in_window'] and
            coverage['revalidated_rejected_observations_in_window']==coverage['rejected_observations_in_window'],
            'FORWARD_COVERAGE_REVALIDATION')
        _require(all(coverage[k] is False for k in COVERAGE_FLAGS),'FORWARD_COVERAGE_AUTHORITY')
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
    paths |= set(root.glob('.github/workflows/*.yml')) | set(root.glob('portable/*.py'))
    paths |= {root/'pyproject.toml', root/'dashboard/portal_preview.html', root/'installer/NexusOperative.iss'}
    paths |= {root/name for name in ('.gitignore','.gitleaks.toml','.github/CODEOWNERS','.github/dependabot.yml') if (root/name).is_file()}
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
            try:
                child = subprocess.run([sys.executable, '-c', expression], cwd=root, env=env,
                                       capture_output=True, text=True, timeout=240)
            except subprocess.TimeoutExpired:
                raise ReleaseTrialError('RELEASE_TRIAL_' + profile.upper() + '_CHILD_TIMEOUT') from None
            _require(child.returncode == 0, profile.upper() + '_CHILD_FAILED')
            try: result = json.loads(child.stdout)
            except ValueError: raise ReleaseTrialError('RELEASE_TRIAL_INVALID_CHILD_REPORT') from None
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
        # Only allowlisted fixed codes are emitted. Raw child stderr,
        # exception text, paths, environment and credential values stay hidden.
        parser.exit(1, 'Release trials failed: ' + _safe_failure_code(error) + '\n')


if __name__ == '__main__': main()
