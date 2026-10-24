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
    'precredential_denial_reset_scoped',
    'inspection_inventory_validated', 'cooldown_prevented_transport',
    'removal_completed_during_read', 'revoked_read_returned_no_observation',
    'pending_history_preserved', 'fresh_process_preserved_pending_history')
SAFE_FAILURE_CODE = re.compile(r'RELEASE_TRIAL_[A-Z0-9_]+\Z')
CHILD_FAILURE_LINE = re.compile(
    r'^nexus\.release_trials\.ReleaseTrialError: (RELEASE_TRIAL_[A-Z0-9_]+)$',
    re.MULTILINE)


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


def _child_failure(profile, child):
    """Preserve only our typed fixed code; discard every other child byte."""
    for value in reversed(CHILD_FAILURE_LINE.findall(child.stderr or '')):
        if SAFE_FAILURE_CODE.fullmatch(value):
            raise ReleaseTrialError(value)
    raise ReleaseTrialError('RELEASE_TRIAL_' + profile.upper() + '_CHILD_FAILED')


def _http_trial_response_body(response, *, tolerate_precredential_denial_reset=False):
    """Bounded trial-only read; a rejected pre-credential request may close early.

    Windows can surface an unread denied POST body as a TCP reset after the
    response status and headers have arrived. Only callers that have already
    verified a pre-credential denial may tolerate that close. Successful or
    credential-bearing responses still require a complete JSON body.
    """
    try:
        return response.read(2 * 1024 * 1024)
    except (ConnectionAbortedError, ConnectionResetError):
        if not tolerate_precredential_denial_reset:
            raise
        return b'{}'


def _http_trial_open(opener, request, *, timeout, precredential_denial_probe=None):
    """Open one trial request, with a strictly scoped denied-request re-probe.

    Windows can abort a connection before urllib exposes the response when the
    loopback boundary rejects a POST without consuming its body.  Only the
    foreign-origin, pre-credential fixture supplies a bodyless probe.  That
    second request must still return the exact expected denial in the caller;
    ordinary, same-origin and credential-bearing requests never retry here.
    """
    from urllib.error import HTTPError
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as error:
        return error
    except (ConnectionAbortedError, ConnectionResetError):
        if precredential_denial_probe is None:
            raise
        try:
            return opener.open(precredential_denial_probe, timeout=timeout)
        except HTTPError as error:
            return error


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
    def post(action, payload, *, origin=base, expected=200, tolerate_precredential_denial_reset=False):
        request = Request(base+'/api/execution/alpaca-paper/local-credentials/'+action,
            data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','Origin':origin}, method='POST')
        probe = None
        if tolerate_precredential_denial_reset:
            _require(expected == 403 and origin != base, 'HTTP_PAPER_RESET_SCOPE')
            probe = Request(request.full_url, data=b'',
                headers={'Content-Type':'application/json','Origin':origin}, method='POST')
        response = _http_trial_open(build_opener(ProxyHandler({})), request, timeout=15,
            precredential_denial_probe=probe)
        with response:
            _require(response.status == expected and response.headers.get('Cache-Control') == 'no-store', 'HTTP_PAPER_STATUS')
            _require(not tolerate_precredential_denial_reset or expected == 403, 'HTTP_PAPER_RESET_SCOPE')
            raw = _http_trial_response_body(response,
                tolerate_precredential_denial_reset=tolerate_precredential_denial_reset)
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
        post('asset-rules', dict(expected_account_id=account_id,confirm_read_only_paper=False), expected=503)
        post('asset-rules', dict(expected_account_id=account_id,confirm_read_only_paper=True,url='https://foreign.invalid'), expected=422)
        post('asset-rules', dict(expected_account_id=account_id,confirm_read_only_paper=True),
            origin='https://foreign.invalid',expected=403,tolerate_precredential_denial_reset=True)
        post('paced-inspect', dict(expected_account_id=account_id,confirm_read_only_paper=True,pacing_path='forbidden'), expected=422)
        post('paced-inspect', dict(confirm_read_only_paper=True), expected=422)
        post('paced-inspect', dict(expected_account_id=account_id,confirm_read_only_paper=True),
            origin='https://foreign.invalid',expected=403,tolerate_precredential_denial_reset=True)
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
    'no_trade_autostart','checklist_purposes_present','optional_connections_not_required','saved_off_preserved',
    'corrupt_settings_read_rejected','corrupt_settings_enable_rejected','corrupt_settings_disengage_available','explicit_repair_kept_off')


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
    _require(all(type(row['why']) is str and len(row['why'])>30 and row['setup_scope'] and len(row['trading_role'])>5 for row in items.values()),'HTTP_FIRST_RUN_PURPOSES')
    _require(items['found_markets']['necessity']=='OPTIONAL' and not items['found_markets']['required_for_baseline'] and all(items[k]['required_for_baseline'] for k in ('portal','storage','budget','tier','portfolio','quotes')), 'HTTP_CHECKLIST_SECTIONS')
    _require(all(items[k]['setup_scope'] in ('Optional','Future feature') for k in
        ('external_ai','paper_read','payment','broker','cloud')),'HTTP_FIRST_RUN_OPTIONAL')
    call('/api/tiers/terrestrial/permission',{'enabled':False});call('/api/paper/settings')
    _require(not any(t['enabled'] for t in call('/api/tiers/permissions')['tiers']),'HTTP_FIRST_RUN_OFF_OVERWRITTEN')
    _require(not call('/api/status')['portal_enabled'] and not call('/api/paper/forward')['enabled'], 'HTTP_FIRST_RUN_STARTED')
    # Disposable corruption/recovery fixture: never a user's configuration.
    with sqlite3.connect(root/'portal_settings.sqlite3') as db:
        db.execute("UPDATE configuration SET value='BROKEN_LOCAL_SETTINGS_FIXTURE' WHERE key='paper_budget'")
    from urllib.error import HTTPError
    before=(root/'portal_settings.sqlite3').read_bytes()
    for path,payload,code in [('/api/paper/settings',None,503),('/api/tiers/solar/permission',{'enabled':True},422)]:
        try:call(path,payload);_require(False,'HTTP_CORRUPT_SETTINGS_ACCEPTED')
        except HTTPError as error:
            _require(error.code==code and json.loads(error.read(4096))=={'detail':'PAPER_SETTINGS_REVIEW_REQUIRED'},'HTTP_CORRUPT_SETTINGS_SCOPE')
    _require((root/'portal_settings.sqlite3').read_bytes()==before,'HTTP_CORRUPT_SETTINGS_REPAIRED')
    off=call('/api/tiers/terrestrial/permission',{'enabled':False})
    _require(off['enabled'] is False and off['settings_review_required'] is True,'HTTP_CORRUPT_SETTINGS_DISENGAGE')
    call('/api/paper/settings',{key:settings[key] for key in ('paper_budget','terrestrial_allocation','solar_allocation','universal_allocation')})
    _require(not any(t['enabled'] for t in call('/api/tiers/permissions')['tiers']),'HTTP_SETTINGS_REPAIR_REENGAGED')
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
        for path in ('/api/status', '/api/roadmap', '/api/onboarding/tasks', '/api/onboarding/readiness', '/api/paper/ai-provider/status', '/api/paper/portfolio', '/api/paper/forward', '/api/paper/forward/sessions', '/api/paper/forward/preflight', '/api/research/found-markets', '/api/paper/forward/trial-readiness', '/api/execution/recovery', '/api/paper/forward/recovery', '/', '/portal'):
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
        progress=responses['/api/roadmap']['progress_estimate']
        _require(progress['implementation_estimate_percent']==40 and progress['fully_accepted_stages']==0 and sum(row['weight_percent'] for row in progress['stages'])==100 and all(progress[k] is False for k in ('release_ready','release_approved','live_orders_enabled','money_movement_enabled')),'HTTP_PROGRESS_SCOPE')
        _require(responses['/api/execution/recovery']['journal_present'] is False and
            responses['/api/execution/recovery']['status']=='NO_RETAINED_HISTORY' and
            responses['/api/paper/forward/recovery']['order_history_present'] is False,
            'PASSIVE_ORDER_RECOVERY_STARTUP')
        trial=responses['/api/paper/forward/trial-readiness']
        _require(trial['read_only'] is True and trial['status']=='NO_RETAINED_HISTORY' and trial['review'] is None and trial['quote_capacity']['estimated_additional_observations']==51841 and all(trial[k] is False for k in ('automatic_start','strategy_validated','sustained_forward_accepted','release_approved','live_orders_enabled','money_movement_enabled')), 'HTTP_TRIAL_READINESS_PASSIVE')
        found=responses['/api/research/found-markets']
        _require(found['enabled'] is False and found['selected']==[] and found['requests_attempted']==0 and found['policy_changed'] is False and found['request_budget_scope']=='SHARED_COOPERATING_LOCAL_DATA_ROOT' and found['provider_or_ip_wide_budget_verified'] is False,'HTTP_FOUND_MARKETS_PASSIVE')
        preflight=responses['/api/paper/forward/preflight']
        _require(preflight['read_only'] is True and preflight['blockers']==['PORTAL_STOPPED'] and
            preflight['local_checks_clear'] is False and all(preflight[k] is False for k in
            ('automatic_start','automatic_repair','running_permission_granted','provider_contacted','live_orders_enabled','money_movement_enabled')), 'HTTP_PREFLIGHT_AUTHORITY')
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
            paper_pacing=paper_pacing,first_run_defaults=first_run_defaults,read_only_preflight_preserved_stopped_gate=True,found_markets_passive_read=True,found_markets_shared_budget_scope_checked=True,trial_readiness_passive_read=True,progress_estimate_scope_checked=True,
            credential_policy_branch='INJECTED_WINDOWS_ONLY_GATE',untrusted_host_rejected=True,cross_origin_rejected=True,api_no_store=True,proxy_headers_trusted=False,
            limitations='Real local source-process HTTP only; paper credential and provider seams are injected fixtures with temporary retained pacing. No rendered browser, Windows executable, actual provider, order or sustained market test.')
    finally:
        server.should_exit = True; thread.join(timeout=10); listener.close()
    _require(not thread.is_alive(), 'HTTP_SHUTDOWN')
    result['graceful_shutdown'] = True
    from tools.packaged_paper_tools_smoke import run as paper_tools_trial
    result['paper_tools_source']=paper_tools_trial([sys.executable,str(Path(__file__).resolve().parents[1]/'portable'/'desktop.py')])
    return result


def _pacing_trial():
    """Fixed local reservation/cooldown fixture; no HTTP or credential access."""
    from nexus.paper_read_pacing import PaperReadPacing,typed_fixture_report
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
        explicit_review = (interrupted_state['last_outcome'] == 'INTERRUPTED_UNKNOWN' and
            interrupted_state['sessions'] == 1 and interrupted_state['review_required'] is True)
        with owned.ownership() as owner:
            owned.recover_review_block(expected_head=interrupted_state['head_sha256'],
                expected_account_id=account,confirm_block_reviewed=True,
                confirm_uncertainty_preserved=True,admission=lambda:1002,ownership=owner)
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
        from nexus.broker_request_journal import submission_admission_fixture
        submission=submission_admission_fixture()
        from nexus.alpaca_paper_submission import fixture_report as submission_transport_fixture
        submission_transport=submission_transport_fixture()
        from nexus.paper_provider_post_recovery import fixture_report as post_recovery_fixture
        post_recovery=post_recovery_fixture()
        from nexus.paper_provider_post_review import fixture_report as post_review_fixture
        post_review=post_review_fixture()
        from nexus.paper_provider_post_activity_review import fixture_report as post_activity_fixture
        post_activity=post_activity_fixture()
        from nexus.paper_recovery_step import fixture_report as recovery_step_fixture
        bounded_step=recovery_step_fixture()
        from nexus.paper_recovery_drill import fixture_report as crash_fixture
        crash=crash_fixture()
        from nexus.sqlite_journal_guard import path_fixture_report
        from nexus.alpaca_paper_contract import timestamp_fixture_report
        from nexus.paper_read_pacing import completion_fixture_report
        return dict(read_completion=completion_fixture_report(),snapshot_time=timestamp_fixture_report(),path_preflight=path_fixture_report(),typed_operations=typed_fixture_report(),bounded_recovery_crash_drill=crash,bounded_recovery_step=bounded_step,submission_admission=submission,single_attempt_submission_transport=submission_transport,resolved_unknown_post_recovery=post_recovery,provider_post_history_review=post_review,provider_post_activity_review=post_activity,passed=True, source='FIXED_LOCAL_INJECTED_CLOCK_FIXTURE', completed_sessions=state['sessions'],
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
    from nexus.paper_asset_rules import inspect_rules
    from nexus.paper_read_pacing import PaperReadPacing
    asset=dict(id='11234567-89ab-cdef-0123-456789abcdef',symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,min_order_size='0.0001',min_trade_increment='0.0001',price_increment='1')
    with tempfile.TemporaryDirectory(prefix='nexus-asset-trial-') as directory:
        gate=PaperReadPacing(Path(directory)/'pacing.sqlite3',account['id']);reader=Opener(account,position);reader.values=iter([account,asset,dict(account)])
        asset_report=inspect_rules('fixture_key_only_123','fixture_secret_only_123',expected_account_id=account['id'],confirm_read_only_paper=True,opener=reader,wall_clock=lambda:1000,elapsed_clock=lambda:50,pacing=gate)
        next_reader=Opener(account,position);before=gate.path.read_bytes();asset_cooldown=False
        try:inspect_account('fixture_key_only_123','fixture_secret_only_123',expected_account_id=account['id'],confirm_read_only_paper=True,opener=next_reader,wall_clock=lambda:1000,elapsed_clock=lambda:50,pacing=gate)
        except ValueError:asset_cooldown=next_reader.requests==0 and gate.path.read_bytes()==before
    from nexus.paper_quote_observation import fixture_report as quote_fixture
    from nexus.paper_quote_store import fixture_report as quote_retention_fixture
    from nexus.paper_quote_capture import fixture_report as quote_capture_fixture
    from nexus.paper_quote_bundle import fixture_report as quote_bundle_fixture
    return dict(passed=True,source='INJECTED_PAPER_INVENTORY_READER',btc_quote_observation=quote_fixture(),btc_quote_retention=quote_retention_fixture(),btc_quote_capture=quote_capture_fixture(),btc_quote_bundle=quote_bundle_fixture(),asset_rules_observation=asset_report,asset_get_requests=reader.requests,asset_shared_cooldown_checked=asset_cooldown,get_requests=requests,observations=reports, portable_roundtrips=len(exported), portable_tamper_rejected=tamper_rejected,
        broker_connected=False,live_orders_enabled=False,
        limitations='Fixed response objects and clocks; no actual credentials, provider, rendered UI, account reconciliation or spendable funds.')



def _request_collection_trial():
    """Release child only: disposable journals plus injected OS/HTTPS seams."""
    import io
    from nexus import paper_credentials as credentials,paper_activity_acceptance_probe as probe
    from nexus.paper_request_activity_acceptance import _fixture
    from nexus.broker_activity_store import _raw
    from nexus.broker_request_journal import BrokerRequestJournal
    account='01234567-89ab-cdef-0123-456789abcdef'
    saved=None;vault_reads=0;gets=0
    class Vault:
        def get_password(self,*args):
            nonlocal vault_reads
            vault_reads+=1;return saved
        def set_password(self,*args):
            nonlocal saved
            saved=args[-1]
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',
        account_blocked=False,trading_blocked=False,trade_suspended_by_user=False,
        cash='1000',non_marginable_buying_power='900',pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value):self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    original_vault=credentials._vault;epoch=credentials._epoch;blocked=credentials._blocked
    try:
        credentials._vault=lambda:Vault()
        credentials.install('fixture_key_only_123','fixture_secret_only_123',confirm_store=True)
        with tempfile.TemporaryDirectory(prefix='nexus-request-collection-') as directory:
            root=Path(directory);source=root/'source';source.mkdir();options,activity=_fixture(source)
            rows=[_raw(row) for row in activity.inspect()['normalized_activities']]
            from nexus.alpaca_paper_inspection import URLS
            class Reader:
                def open(self,request,timeout):
                    nonlocal gets
                    _require(request.get_method()=='GET' and request.data is None,'REQUEST_COLLECTION_METHOD')
                    gets+=1
                    return Response(request.full_url,raw_account if request.full_url==URLS[0] else rows)
            dest=root/'collected';args=dict(expected_account_id=account,
                request_journal_path=options['request_journal_path'],expected_request_head=options['expected_request_head'],
                pacing_path=dest/'pacing.sqlite3',activity_store_path=dest/'activity.sqlite3',acceptance_store_path=dest/'acceptance.sqlite3',
                after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',confirm_read_only_paper=True,
                opener=Reader(),wall_clock=lambda:1792368000,elapsed_clock=lambda:50)
            before=Path(options['request_journal_path']).read_bytes();result=probe.probe(**args)
            request_unchanged=Path(options['request_journal_path']).read_bytes()==before
            status=probe.status(request_journal_path=options['request_journal_path'],
                **{k:args[k] for k in ('expected_account_id','pacing_path','activity_store_path','acceptance_store_path')})
            journal=BrokerRequestJournal(options['request_journal_path'],account)
            plan=journal.lookup_request('nx_accept_fixture')['plan'];plan['payload']['client_order_id']='new_prepared'
            from nexus.alpaca_paper_contract import sha
            plan['payload_sha256']=sha(plan['payload']);journal.prepare(plan)
            after_append=journal.path.read_bytes()
            from nexus.paper_request_activity_acceptance import review as origin_review
            origin_options=dict(expected_account_id=account,request_journal_path=journal.path,
                pacing_path=args['pacing_path'],activity_store_path=args['activity_store_path'],
                acceptance_store_path=args['acceptance_store_path'],
                expected_request_head=journal.recovery()['head_sha256'],
                expected_activity_head=result['local_retention']['after_head_sha256'])
            origin=origin_review(**origin_options)
            expression="""
import json,sys
from nexus import paper_credentials
from nexus.paper_request_activity_acceptance import review
from nexus.paper_activity_acceptance_probe import status
def audit(event,args):
 if event=='socket.connect':raise RuntimeError('NETWORK_DENIED')
def denied():raise RuntimeError('VAULT_DENIED')
sys.addaudithook(audit);paper_credentials._vault=denied
r=review(**json.loads(sys.argv[1]))
o=json.loads(sys.argv[1])
s=status(expected_account_id=o['expected_account_id'],request_journal_path=o['request_journal_path'],
 pacing_path=o['pacing_path'],activity_store_path=o['activity_store_path'],acceptance_store_path=o['acceptance_store_path'])
print(json.dumps({'origins':r['request_origin_bound_transitions'],'prefixes':r['retained_request_origin_prefixes_replayed'],'bound':r['all_retained_request_origins_bound'],'status':s['request_origin_history_verified'],'unchanged':s['request_journal_rechecked_unchanged']}))
"""
            try:
                child=subprocess.run([sys.executable,'-c',expression,json.dumps({k:str(v) for k,v in origin_options.items()})],capture_output=True,text=True,timeout=20)
            except (OSError,subprocess.SubprocessError):
                raise ReleaseTrialError('RELEASE_TRIAL_REQUEST_ORIGIN_RESTART_CHILD') from None
            _require(child.returncode==0,'REQUEST_ORIGIN_RESTART_CHILD')
            cold=json.loads(child.stdout)
            origin_status=probe.status(expected_account_id=account,request_journal_path=journal.path,
                pacing_path=args['pacing_path'],activity_store_path=args['activity_store_path'],
                acceptance_store_path=args['acceptance_store_path'])
            reads_before=vault_reads;gets_before=gets;stale=False
            try:probe.probe(**args)
            except ValueError:stale=vault_reads==reads_before and gets==gets_before
            checks=dict(request_origin_head_persisted=result['order_input']['request_head_persisted_in_acceptance_record'],
                historical_request_prefix_after_append_replayed=origin['all_retained_request_origins_bound'] and origin['retained_request_origin_prefixes_replayed']==1,
                request_status_origin_replayed=status['request_origin_history_verified'] and status['request_journal_rechecked_unchanged'],
                request_status_after_append_replayed=origin_status['request_origin_history_verified'] and origin_status['request_origin_current_head_sha256']==journal.recovery()['head_sha256'],
                fresh_process_origin_replayed=cold==dict(origins=1,prefixes=1,bound=True,status=True,unchanged=True),
                request_status_journal_unchanged=journal.path.read_bytes()==after_append,
                request_head_bound=result['order_input']['request_head_sha256']==options['expected_request_head'] and result['order_input']['request_head_rechecked'],
                original_orders_derived=result['collection']['counts']['order_snapshots']==1,
                two_explicit_fills_retained=result['collection']['counts']['executions']==2,
                acceptance_replayed=status['all_accepted_transitions_replayed'] and status['accepted_transitions']==1,
                accepted_operation_typed=status['all_pacing_operations_typed'],
                accepted_result_receipt_bound=status['all_pacing_result_receipts_bound'],
                request_journal_unchanged=request_unchanged,stale_head_denied_before_vault_and_io=stale,
                manual_order_file_not_created=not list(root.rglob('orders.json')))
            return dict(source='SYNTHETIC_RETAINED_REQUEST_ACTIVITY_COLLECTION_FIXTURE',passed=all(checks.values()),
                **checks,injected_get_requests=gets,actual_provider_access=False,actual_vault_access=False,
                **{flag:False for flag in probe.FLAGS})
    finally:credentials._vault=original_vault;credentials._epoch=epoch;credentials._blocked=blocked


def _execute(profile):
    if profile == 'activity_acceptance':
        from nexus.paper_activity_acceptance_evidence import fixture_report
        return fixture_report()
    if profile == 'reconciliation_acceptance':
        from nexus.paper_reconciliation_acceptance import fixture_report
        from nexus.paper_account_evidence_review import fixture_report as bundle_fixture
        from nexus.paper_response_bundle_review import fixture_report as response_fixture
        from nexus.paper_provider_post_reconciliation_review import fixture_report as post_reconciliation_fixture
        from nexus.paper_provider_post_fee_reference_review import fixture_report as post_fee_fixture
        from nexus.paper_provider_post_known_fee_reserve_review import fixture_report as post_fee_reserve_fixture
        from nexus.alpaca_activity_sse_review import fixture_report as activity_sse_fixture
        from nexus.alpaca_activity_sse_reconnect_review import fixture_report as activity_sse_reconnect_fixture
        from nexus.alpaca_activity_sse_cursor_store import fixture_report as activity_sse_cursor_fixture
        from nexus.alpaca_activity_sse_wire_review import fixture_report as activity_sse_wire_fixture
        from nexus.alpaca_activity_sse_transport import fixture_report as activity_sse_transport_fixture
        from nexus.alpaca_activity_sse_checkpoint import fixture_report as activity_sse_checkpoint_fixture
        from nexus.alpaca_activity_sse_reconnect_budget import fixture_report as activity_sse_reconnect_budget_fixture
        result=fixture_report();result['account_evidence_bundle']=bundle_fixture()
        result['exact_response_bundle']=response_fixture()
        result['provider_post_reconciliation']=post_reconciliation_fixture()
        result['provider_post_fee_references']=post_fee_fixture()
        result['provider_post_known_fee_reserve']=post_fee_reserve_fixture()
        result['supplied_activity_sse_links']=activity_sse_fixture()
        result['supplied_activity_sse_reconnect']=activity_sse_reconnect_fixture()
        result['private_activity_sse_cursor_store']=activity_sse_cursor_fixture()
        result['supplied_activity_sse_wire_session']=activity_sse_wire_fixture()
        result['explicit_activity_sse_transport']=activity_sse_transport_fixture()
        result['explicit_activity_sse_checkpoint']=activity_sse_checkpoint_fixture()
        result['explicit_activity_sse_reconnect_budget']=activity_sse_reconnect_budget_fixture();return result
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
        from nexus.order_offload import fixture_report as offload_fixture
        result=drill(24);result['order_physical_offload']=offload_fixture();return result
    if profile == 'recovery':
        from nexus.recovery_drill import run
        return run()
    if profile == 'capacity':
        from nexus.durable_benchmark import benchmark
        from nexus.execution_quality import fixture_report
        from nexus.paper_execution_quality_review import fixture_report as paper_quality_fixture
        result=benchmark(30);result['execution_quality']=fixture_report()
        result['paper_execution_quality']=paper_quality_fixture();return result
    if profile == 'http': return _http_trial()
    if profile == 'pacing': return _pacing_trial()
    if profile == 'inspection_inventory': return _inspection_inventory_trial()
    if profile == 'retained_balance':
        from nexus.retained_balance_review import fixture_report
        return fixture_report()
    if profile == 'collection':
        from nexus.activity_collection_evidence import fixture_report
        result=fixture_report();result['request_derived_collection']=_request_collection_trial();return result
    if profile == 'balance':
        from nexus.broker_balance_review import fixture_report
        return fixture_report()
    if profile == 'activity':
        from nexus.broker_activity_review import fixture_report
        from nexus.broker_request_activity_review import fixture_report as link_fixture
        from nexus.paper_request_activity_acceptance import fixture_report as accepted_link_fixture
        result=fixture_report();result['request_activity_link']=link_fixture()
        result['request_activity_acceptance']=accepted_link_fixture();return result
    raise ValueError('RELEASE_TRIAL_UNKNOWN_PROFILE')


def _validate(profile, result):
    _require(profile in PROFILES and isinstance(result, dict), 'RESULT_SHAPE')
    # Each profile has explicit acceptance assertions; process exit 0 alone is insufficient.
    if profile == 'capacity':
        from nexus.execution_quality import CHECKS as QUALITY_CHECKS, FLAGS as QUALITY_FLAGS
        quality=result['execution_quality']
        _require(quality['source']=='SYNTHETIC_EXECUTION_QUALITY_FIXTURE' and quality['passed'] is True and
            all(quality[k] is True for k in QUALITY_CHECKS),'EXECUTION_QUALITY')
        _require(all(quality[k] is False for k in QUALITY_FLAGS),'EXECUTION_QUALITY_AUTHORITY')
        from nexus.paper_execution_quality_review import CHECKS as PAPER_QUALITY_CHECKS, FLAGS as PAPER_QUALITY_FLAGS
        paper=result['paper_execution_quality']
        _require(paper['source']=='SYNTHETIC_ACCEPTED_PAPER_EXECUTION_QUALITY_FIXTURE' and paper['passed'] is True and
            all(paper[k] is True for k in PAPER_QUALITY_CHECKS),'PAPER_EXECUTION_QUALITY')
        _require(all(paper[k] is False for k in PAPER_QUALITY_FLAGS),'PAPER_EXECUTION_QUALITY_AUTHORITY')
        _require(len(result['treatments']) == 4, 'CAPACITY_TREATMENTS')
        _require(all(t['accounting_consistent'] is True and t['broker_orders'] == 0 and t['cycles'] == 30
                     for t in result['treatments']), 'CAPACITY_ACCOUNTING')
        for name in ('burst_treatment', 'atomic_burst_treatment'):
            _require(result[name]['accounting_consistent'] is True, 'BURST_ACCOUNTING')
        _require(result['target_validated'] is False, 'UNSUPPORTED_MARKET_SPEED_CLAIM')
    else: _require(result.get('passed') is True, 'PROFILE_FAILED')
    if profile == 'http':
        from tools.packaged_paper_tools_smoke import CHECKS as PAPER_TOOL_CHECKS
        tools=result['paper_tools_source']
        native_windows_required=tools['host_os']=='WINDOWS_NT'
        _require(tools['mode']=='PACKAGED_PAPER_OPERATOR_TOOLS_SMOKE' and tools['passed'] is True and
            tools['host_os'] in ('WINDOWS_NT','NON_WINDOWS') and
            tools['tool_invocations']==31+int(native_windows_required) and
            tools['native_windows_storage_fault_checked'] is native_windows_required and
            all(tools[k] is True for k in PAPER_TOOL_CHECKS),'HTTP_PAPER_TOOLS')
        _require(all(tools[k] is False for k in ('provider_reads_requested','credential_reads_requested','live_orders_enabled','money_movement_enabled','release_approved','native_windows_acceptance')),'HTTP_PAPER_TOOLS_AUTHORITY')
        _require(result['progress_estimate_scope_checked'] is True,'HTTP_PROGRESS_ASSESSMENT')
        _require(result['trial_readiness_passive_read'] is True,'HTTP_TRIAL_READINESS_READ')
        _require(result['found_markets_passive_read'] is True and result['found_markets_shared_budget_scope_checked'] is True,'HTTP_FOUND_MARKETS_READ')
        _require(result['read_only_preflight_preserved_stopped_gate'] is True,'HTTP_PREFLIGHT_GATE')
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
        from nexus.paper_quote_observation import CHECKS as QUOTE_CHECKS,FLAGS as QUOTE_FLAGS
        quote=result['btc_quote_observation']
        _require(quote['source']=='INJECTED_ALPACA_BTC_QUOTE_OBSERVATION_FIXTURE' and quote['passed'] is True and
            all(quote[k] is True for k in QUOTE_CHECKS),'BTC_QUOTE_OBSERVATION')
        _require(all(quote[k] is False for k in (*QUOTE_FLAGS,'actual_provider_access','actual_vault_access')),'BTC_QUOTE_AUTHORITY')
        from nexus.paper_quote_store import CHECKS as RETENTION_CHECKS
        retention=result['btc_quote_retention']
        _require(retention['source']=='SYNTHETIC_ORIGINAL_REQUEST_QUOTE_RETENTION_FIXTURE' and retention['passed'] is True and
            all(retention[k] is True for k in RETENTION_CHECKS),'BTC_QUOTE_RETENTION')
        _require(all(retention[k] is False for k in (*QUOTE_FLAGS,'actual_provider_access','actual_vault_access')),'BTC_QUOTE_RETENTION_AUTHORITY')
        from nexus.paper_quote_capture import CHECKS as CAPTURE_CHECKS,FLAGS as CAPTURE_FLAGS
        capture=result['btc_quote_capture']
        _require(capture['source']=='INJECTED_PAPER_QUOTE_CAPTURE_FIXTURE' and capture['passed'] is True and
            all(capture[k] is True for k in CAPTURE_CHECKS),'BTC_QUOTE_CAPTURE')
        _require(all(capture[k] is False for k in (*CAPTURE_FLAGS,'actual_provider_access','actual_vault_access')),'BTC_QUOTE_CAPTURE_AUTHORITY')
        from nexus.paper_quote_bundle import CHECKS as BUNDLE_CHECKS,FLAGS as BUNDLE_FLAGS
        bundle=result['btc_quote_bundle']
        _require(bundle['source']=='SYNTHETIC_ENCRYPTED_QUOTE_BUNDLE_FIXTURE' and bundle['passed'] is True and
            all(bundle[k] is True for k in BUNDLE_CHECKS),'BTC_QUOTE_BUNDLE')
        _require(all(bundle[k] is False for k in (*BUNDLE_FLAGS,'actual_provider_access','actual_vault_access')),'BTC_QUOTE_BUNDLE_AUTHORITY')
        from nexus.paper_asset_rules import FLAGS
        asset=result['asset_rules_observation']
        _require(result['asset_get_requests']==3 and result['asset_shared_cooldown_checked'] is True and asset['asset']['min_order_size']=='0.0001' and asset['reported_fields_observed'] is True and asset['account_fields_double_checked'] is True,'ASSET_RULES_READER')
        _require(all(asset[k] is False for k in FLAGS),'ASSET_RULES_AUTHORITY')
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
        from nexus.paper_activity_acceptance_probe import FLAGS as REQUEST_COLLECTION_FLAGS
        linked=result['request_derived_collection']
        _require(linked['source']=='SYNTHETIC_RETAINED_REQUEST_ACTIVITY_COLLECTION_FIXTURE' and linked['injected_get_requests']==3 and all(linked[k] is True for k in ('passed','request_origin_head_persisted','historical_request_prefix_after_append_replayed','request_status_origin_replayed','request_status_after_append_replayed','fresh_process_origin_replayed','request_status_journal_unchanged','request_head_bound','original_orders_derived','two_explicit_fills_retained','acceptance_replayed','accepted_operation_typed','accepted_result_receipt_bound','request_journal_unchanged','stale_head_denied_before_vault_and_io','manual_order_file_not_created')),'REQUEST_DERIVED_COLLECTION')
        _require(all(linked[k] is False for k in (*REQUEST_COLLECTION_FLAGS,'actual_provider_access','actual_vault_access')),'REQUEST_DERIVED_COLLECTION_AUTHORITY')
        _require(result['get_requests'] == 4 and result['retained_activities'] == 200 and
            result['retained_observations'] == 1 and result['retained_pages'] == 2, 'COLLECTION_RETENTION')
        _require(result['page_cap_reported'] is True and result['cursor_chain_preserved'] is True, 'COLLECTION_SCOPE')
        _require(all(result[k] is False for k in ('source_attested', 'activity_coverage_verified',
            'ready_for_routing', 'automatic_continuation')), 'COLLECTION_AUTHORITY')
    if profile == 'balance':
        from nexus.broker_fee_projection import TIMESTAMP_CHECKS,TIMESTAMP_FLAGS
        timing=result['timestamp_profile']
        _require(timing['source']=='SUPPLIED_TIMESTAMP_PROFILE_FIXTURE' and timing['passed'] is True and all(timing[k] is True for k in TIMESTAMP_CHECKS),'BALANCE_TIMESTAMP_PROFILE')
        _require(all(timing[k] is False for k in TIMESTAMP_FLAGS),'BALANCE_TIMESTAMP_AUTHORITY')
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
        from nexus.paper_read_pacing import OPERATION_CHECKS,OPERATION_FLAGS
        typed=result['typed_operations']
        _require(typed.get('source')=='INJECTED_TYPED_PAPER_READ_BUDGET_FIXTURE',
            'TYPED_READ_OPERATIONS_ENVELOPE')
        # Keep CI diagnostics fixed and content-free while identifying the
        # precise bounded invariant.  This never emits fixture values, paths,
        # account identifiers or arbitrary exception text.
        for name in OPERATION_CHECKS:
            _require(typed.get(name) is True,'TYPED_READ_OPERATIONS_'+name.upper())
        # The summary is derived from the fixed checks above.  Validate it
        # after them so an intermittent failure reports the exact allowlisted
        # invariant instead of collapsing back to the aggregate envelope.
        _require(typed.get('passed') is True,'TYPED_READ_OPERATIONS_SUMMARY')
        _require(all(typed[k] is False for k in OPERATION_FLAGS),'TYPED_READ_AUTHORITY')
        from nexus.sqlite_journal_guard import PATH_CHECKS,PATH_FLAGS
        paths=result['path_preflight']
        _require(paths['source']=='TEMPORARY_MISSING_JOURNAL_PATH_FIXTURE' and paths['passed'] is True and all(paths[k] is True for k in PATH_CHECKS),'JOURNAL_PATH_PREFLIGHT')
        _require(all(paths[k] is False for k in PATH_FLAGS),'JOURNAL_PATH_AUTHORITY')
        from nexus.alpaca_paper_contract import TIME_CHECKS,TIME_FLAGS
        timing=result['snapshot_time']
        _require(timing['source']=='SUPPLIED_EXACT_SNAPSHOT_TIME_FIXTURE' and timing['passed'] is True and all(timing[k] is True for k in TIME_CHECKS),'SNAPSHOT_TIME_ORDERING')
        _require(all(timing[k] is False for k in TIME_FLAGS),'SNAPSHOT_TIME_AUTHORITY')
        from nexus.paper_read_pacing import COMPLETION_CHECKS
        completion=result['read_completion']
        _require(completion['source']=='TEMPORARY_ORIGINAL_READ_LEASE_FIXTURE' and completion['passed'] is True and all(completion[k] is True for k in COMPLETION_CHECKS),'READ_COMPLETION_LEASE')
        _require(all(completion[k] is False for k in OPERATION_FLAGS),'READ_COMPLETION_AUTHORITY')
        from nexus.paper_recovery_drill import PHASES as CRASH_PHASES,CHECKS as CRASH_CHECKS
        from nexus.paper_recovery_step import FLAGS as RECOVERY_FLAGS
        crash=result['bounded_recovery_crash_drill']
        _require(crash['mode']=='DISPOSABLE_BOUNDED_RECOVERY_CRASH_DRILL' and crash['source']=='SYNTHETIC_LOCAL_PROCESS_DEATH_FIXTURE' and crash['passed'] is True and crash['forced_process_exits']==5 and crash['fresh_process_reviews']==10 and crash['precommit_rollback_sidecars_observed'] is True,'RECOVERY_CRASH_SCOPE')
        _require([case['phase'] for case in crash['cases']]==list(CRASH_PHASES) and [case['injected_get_requests'] for case in crash['cases']]==[0,0,3,3,3] and all(all(case[k] is True for k in CRASH_CHECKS) for case in crash['cases']),'RECOVERY_CRASH_BOUNDARIES')
        _require(all(crash[k] is False for k in (*RECOVERY_FLAGS,'actual_provider_access','actual_vault_access')),'RECOVERY_CRASH_AUTHORITY')
        step=result['bounded_recovery_step']
        _require(step['source']=='TEMPORARY_BOUNDED_RECOVERY_STEP_FIXTURE' and step['steps_performed']==2 and step['get_requests']==4 and all(step[k] is True for k in ('passed','first_request_selected','next_request_selected','cooldown_review_unchanged','stale_head_prevented_io')),'BOUNDED_RECOVERY_STEP')
        _require(all(step[k] is False for k in (*RECOVERY_FLAGS,'actual_provider_access','actual_vault_access')),'BOUNDED_RECOVERY_STEP_AUTHORITY')
        submission=result['submission_admission']
        _require(all(submission[k] is True for k in ('admission_marker_replayed','admission_clock_floor_retained','legacy_marker_untimed')),'SUBMISSION_ADMISSION_HISTORY')
        _require(submission['source']=='TEMPORARY_LOCAL_SUBMISSION_ADMISSION_FIXTURE' and all(submission[k] is True for k in ('preflight_passive_no_authority','preflight_marked_requires_recovery','lookup_begin_clock_rollback','lookup_result_clock_rollback','retained_clock_rejected','rate_cooldown_rejected','rate_deadline_allows_marker_only','account_review_blocks_begin','marker_commit_observed','recovery_pair_reserved','duplicate_begin_rejected','revoked_admission_unchanged','pending_lookup_completion_reserved','terminal_lookup_releases_reserve','reopened_terminal_observation')),'SUBMISSION_ADMISSION_FENCES')
        _require(all(submission[k] is False for k in ('transport_invoked','routing_authorized','broker_connected','ledger_applied','broker_account_reconciled','live_orders_enabled','money_movement_enabled','release_approved')),'SUBMISSION_ADMISSION_AUTHORITY')
        transport=result['single_attempt_submission_transport']
        _require(transport['source']=='INJECTED_SINGLE_ATTEMPT_PAPER_SUBMISSION_FIXTURE' and
            transport['passed'] is True and all(transport[k] is True for k in
            ('marker_committed_before_post','exactly_one_post','response_retained',
             'transport_loss_preserved_for_original_id_lookup','repeat_submission_blocked',
             'output_never_sendable')),'SUBMISSION_TRANSPORT_FENCES')
        _require(all(transport[k] is False for k in ('actual_provider_access','actual_vault_access',
            'broker_account_reconciled','spendable_funds_verified','ready_for_routing',
            'live_orders_enabled','money_movement_enabled','release_approved')),
            'SUBMISSION_TRANSPORT_AUTHORITY')
        post_recovery=result['resolved_unknown_post_recovery']
        _require(post_recovery['source']=='TEMPORARY_RESOLVED_UNKNOWN_POST_RECOVERY_FIXTURE' and
            post_recovery['passed'] is True and all(post_recovery[k] is True for k in
            ('unresolved_request_denied','exact_terminal_resolution_bound',
             'original_request_not_rearmed','unknown_history_preserved','cooldown_preserved')),
            'POST_RECOVERY_FENCES')
        _require(all(post_recovery[k] is False for k in ('actual_provider_access',
            'actual_vault_access','broker_account_reconciled','spendable_funds_verified',
            'ready_for_routing','live_orders_enabled','money_movement_enabled','release_approved')),
            'POST_RECOVERY_AUTHORITY')
        post_review=result['provider_post_history_review']
        _require(post_review['source']=='TEMPORARY_LOCAL_PROVIDER_POST_HISTORY_REVIEW_FIXTURE' and
            post_review['passed'] is True and all(post_review[k] is True for k in
            ('exact_multi_account_history','every_attempt_bound','every_outcome_bound',
             'exact_terminal_recovery_history',
             'outcome_disagreement_requires_review','changed_head_denied',
             'omitted_account_requires_review','output_never_authoritative')),
            'POST_HISTORY_REVIEW_FENCES')
        from nexus.paper_provider_post_review import FLAGS as POST_REVIEW_FLAGS
        _require(all(post_review[k] is False for k in POST_REVIEW_FLAGS),
            'POST_HISTORY_REVIEW_AUTHORITY')
        post_activity=result['provider_post_activity_review']
        _require(post_activity['source']=='SYNTHETIC_LOCAL_PROVIDER_POST_ACTIVITY_FIXTURE' and
            post_activity['passed'] is True and all(post_activity[k] is True for k in
            ('recovered_unknown_bound_to_accepted_activity',
             'not_sent_bound_without_activity','unresolved_unknown_requires_review',
             'journals_unchanged','output_never_authoritative')),
            'POST_ACTIVITY_REVIEW_FENCES')
        from nexus.paper_provider_post_activity_review import FLAGS as POST_ACTIVITY_FLAGS
        _require(all(post_activity[k] is False for k in POST_ACTIVITY_FLAGS),
            'POST_ACTIVITY_REVIEW_AUTHORITY')
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
            'postcommit_all_content_replayed','postcommit_all_pacing_operations_typed',
            'postcommit_all_pacing_result_receipts_bound','postcommit_all_pacing_request_targets_bound',
            'postcommit_all_normalized_results_bound','legacy_normalized_result_not_invented','legacy_normalized_history_preserved',
            'changed_creation_window_targets_refused','changed_cursor_target_refused',
            'request_target_disagreement_history_preserved')), 'ACTIVITY_ACCEPTANCE_RECOVERY')
        _require(all(result[name] is False for name in ('actual_provider_access','actual_vault_access','source_attested',
            'activity_coverage_verified','fee_execution_links_verified','broker_account_reconciled',
            'spendable_funds_verified','ready_for_routing','broker_connected','live_orders_enabled',
            'money_movement_enabled','release_approved')), 'ACTIVITY_ACCEPTANCE_AUTHORITY')
    if profile == 'reconciliation_acceptance':
        from nexus.paper_account_evidence_review import FLAGS as BUNDLE_FLAGS
        from nexus.paper_provider_post_reconciliation_review import FLAGS as POST_RECONCILIATION_FLAGS
        from nexus.paper_provider_post_fee_reference_review import FLAGS as POST_FEE_FLAGS
        from nexus.paper_provider_post_known_fee_reserve_review import FLAGS as POST_FEE_RESERVE_FLAGS
        from nexus.alpaca_activity_sse_review import FLAGS as ACTIVITY_SSE_FLAGS
        from nexus.alpaca_activity_sse_reconnect_review import FLAGS as ACTIVITY_SSE_RECONNECT_FLAGS
        from nexus.alpaca_activity_sse_cursor_store import FLAGS as ACTIVITY_SSE_CURSOR_FLAGS
        from nexus.alpaca_activity_sse_wire_review import FLAGS as ACTIVITY_SSE_WIRE_FLAGS
        from nexus.alpaca_activity_sse_checkpoint import FLAGS as ACTIVITY_SSE_CHECKPOINT_FLAGS
        from nexus.alpaca_activity_sse_reconnect_budget import FLAGS as ACTIVITY_SSE_RECONNECT_BUDGET_FLAGS
        from nexus.paper_response_bundle_review import FLAGS as RESPONSE_FLAGS
        bundle=result['account_evidence_bundle']
        _require(bundle['source']=='SYNTHETIC_LOCAL_ACCOUNT_EVIDENCE_BUNDLE_FIXTURE' and all(bundle[k] is True for k in ('passed','component_heads_bound','original_terms_and_two_fills_checked','observation_arithmetic_evaluated','gross_only_zero_difference_observed','changed_balance_difference_preserved','zero_fee_records_not_promoted','repeat_review_matches','current_head_review_matches','public_summary_checked','stability_guard_bound','guarded_repeat_review_matches','guarded_current_head_review_matches','guarded_public_summary_checked','guarded_declared_source_summary_checked','guarded_collection_timing_summary_checked','journals_unchanged')),'ACCOUNT_EVIDENCE_BUNDLE')
        _require(all(bundle[k] is False for k in (*BUNDLE_FLAGS,'actual_provider_access','actual_vault_access')),'ACCOUNT_EVIDENCE_BUNDLE_AUTHORITY')
        post_reconciliation=result['provider_post_reconciliation']
        _require(post_reconciliation['source']==
            'SYNTHETIC_LOCAL_PROVIDER_POST_RECONCILIATION_FIXTURE' and
            all(post_reconciliation[k] is True for k in ('passed',
                'exact_post_activity_bound','guarded_account_evidence_bound',
                'accepted_history_replayed','gross_only_residual_checked',
                'unresolved_unknown_rejected','repeat_review_matches',
                'journals_unchanged','output_never_authoritative')),
            'PROVIDER_POST_RECONCILIATION')
        _require(all(post_reconciliation[k] is False for k in
            (*POST_RECONCILIATION_FLAGS,'actual_provider_access',
             'actual_vault_access')),
            'PROVIDER_POST_RECONCILIATION_AUTHORITY')
        post_fees=result['provider_post_fee_references']
        _require(post_fees['source']==
            'SYNTHETIC_LOCAL_PROVIDER_POST_FEE_REFERENCE_FIXTURE' and
            all(post_fees[k] is True for k in ('passed',
                'post_reconciliation_bound',
                'complete_accepted_history_replayed',
                'accepted_receipt_partition_complete',
                'all_retained_fees_explicitly_referenced',
                'references_resolve_to_accepted_original_fills',
                'date_only_scope_preserved',
                'missing_fee_history_rejected',
                'unreferenced_fee_rejected',
                'unknown_execution_reference_rejected',
                'repeat_review_matches','journals_unchanged',
                'output_never_authoritative')),
            'PROVIDER_POST_FEE_REFERENCES')
        _require(all(post_fees[k] is False for k in
            (*POST_FEE_FLAGS,'actual_provider_access','actual_vault_access')),
            'PROVIDER_POST_FEE_REFERENCES_AUTHORITY')
        post_fee_reserve=result['provider_post_known_fee_reserve']
        _require(post_fee_reserve['source']==
            'SYNTHETIC_LOCAL_PROVIDER_POST_KNOWN_FEE_RESERVE_FIXTURE' and
            all(post_fee_reserve[k] is True for k in ('passed',
                'fee_reference_component_bound',
                'retained_known_fee_reservations_applied',
                'retained_known_fee_debits_covered',
                'known_fee_adjusted_closing_balances_match',
                'balance_disagreement_rejected',
                'insufficient_known_fee_funding_rejected',
                'missing_fee_history_rejected',
                'repeat_review_matches','journals_unchanged',
                'output_never_authoritative')),
            'PROVIDER_POST_KNOWN_FEE_RESERVE')
        _require(all(post_fee_reserve[k] is False for k in
            (*POST_FEE_RESERVE_FLAGS,'actual_provider_access',
             'actual_vault_access')),
            'PROVIDER_POST_KNOWN_FEE_RESERVE_AUTHORITY')
        activity_sse=result['supplied_activity_sse_links']
        _require(activity_sse['source']==
            'SYNTHETIC_SUPPLIED_ACTIVITY_SSE_LINK_FIXTURE' and
            all(activity_sse[k] is True for k in ('passed',
                'strict_cursor_and_identity','exact_parent_link_bound',
                'unsupported_corrections_and_bulk_fees_refused',
                'unknown_parent_refused','cursor_regression_refused',
                'output_never_authoritative')),
            'SUPPLIED_ACTIVITY_SSE_LINKS')
        _require(all(activity_sse[k] is False for k in
            (*ACTIVITY_SSE_FLAGS,'actual_provider_access',
             'actual_vault_access')),
            'SUPPLIED_ACTIVITY_SSE_LINKS_AUTHORITY')
        reconnect=result['supplied_activity_sse_reconnect']
        _require(reconnect['source']==
            'SYNTHETIC_SUPPLIED_ACTIVITY_SSE_RECONNECT_FIXTURE' and
            all(reconnect[k] is True for k in ('passed',
                'exact_since_id_reused','duplicate_redelivery_idempotent',
                'forward_cursor_advanced','fee_links_rechecked',
                'output_never_authoritative')),
            'SUPPLIED_ACTIVITY_SSE_RECONNECT')
        _require(all(reconnect[k] is False for k in
            (*ACTIVITY_SSE_RECONNECT_FLAGS,'actual_provider_access',
             'actual_vault_access','actual_storage_access')),
            'SUPPLIED_ACTIVITY_SSE_RECONNECT_AUTHORITY')
        cursor=result['private_activity_sse_cursor_store']
        _require(cursor['source']==
            'TEMPORARY_PRIVATE_ACTIVITY_SSE_CURSOR_STORE_FIXTURE' and
            all(cursor[k] is True for k in ('passed',
                'review_fixture_passed','checkpoint_committed',
                'cold_restart_recovers_cursor',
                'exact_stale_head_replay_idempotent',
                'output_never_authoritative')) and
            cursor['actual_storage_retained'] is False,
            'PRIVATE_ACTIVITY_SSE_CURSOR_STORE')
        _require(all(cursor[k] is False for k in ACTIVITY_SSE_CURSOR_FLAGS),
            'PRIVATE_ACTIVITY_SSE_CURSOR_STORE_AUTHORITY')
        wire=result['supplied_activity_sse_wire_session']
        _require(wire['source']==
            'SYNTHETIC_SUPPLIED_ACTIVITY_SSE_WIRE_FIXTURE' and
            all(wire[k] is True for k in ('passed',
                'exact_since_id_target_bound',
                'arbitrary_chunk_boundaries_reassembled',
                'heartbeat_and_linked_events_reviewed',
                'slow_consumer_requires_reconnect',
                'internal_error_requires_reconnect',
                'output_never_authoritative')) and
            wire['actual_storage_access'] is False,
            'SUPPLIED_ACTIVITY_SSE_WIRE_SESSION')
        _require(all(wire[k] is False for k in ACTIVITY_SSE_WIRE_FLAGS),
            'SUPPLIED_ACTIVITY_SSE_WIRE_SESSION_AUTHORITY')
        transport=result['explicit_activity_sse_transport']
        _require(transport['source']==
            'SYNTHETIC_INJECTED_ACTIVITY_SSE_TRANSPORT_FIXTURE' and
            all(transport[k] is True for k in ('passed','exact_single_get',
                'fixed_activity_target','fixed_stream_headers',
                'bounded_segment_reviewed','injected_response_closed',
                'no_identifier_or_credential_echo','no_automatic_continuation',
                'output_never_authoritative')) and
            transport['injected_get_requests']==1,
            'EXPLICIT_ACTIVITY_SSE_TRANSPORT')
        _require(all(transport[k] is False for k in
            ('actual_provider_access','actual_vault_access','ready_for_routing',
             'live_orders_enabled','money_movement_enabled','release_approved')),
            'EXPLICIT_ACTIVITY_SSE_TRANSPORT_AUTHORITY')
        checkpoint=result['explicit_activity_sse_checkpoint']
        _require(checkpoint['source']==
            'TEMPORARY_INJECTED_ACTIVITY_SSE_CHECKPOINT_FIXTURE' and
            all(checkpoint[k] is True for k in ('passed',
                'single_injected_get','duplicate_redelivery_and_advance',
                'exact_head_checkpoint_committed',
                'no_private_identifier_or_credential_echo',
                'no_automatic_or_financial_authority')) and
            checkpoint['actual_storage_retained'] is False,
            'EXPLICIT_ACTIVITY_SSE_CHECKPOINT')
        _require(all(checkpoint[k] is False for k in
            (*ACTIVITY_SSE_CHECKPOINT_FLAGS,'actual_provider_access',
             'actual_stream_connection','actual_vault_access')),
            'EXPLICIT_ACTIVITY_SSE_CHECKPOINT_AUTHORITY')
        reconnect_budget=result['explicit_activity_sse_reconnect_budget']
        _require(reconnect_budget['source']==
            'TEMPORARY_INJECTED_ACTIVITY_SSE_RECONNECT_BUDGET_FIXTURE' and
            all(reconnect_budget[k] is True for k in ('passed',
                'first_failure_retained_without_retry',
                'lost_reservation_ack_replays_without_provider_request',
                'lost_retry_result_ack_replays_without_new_attempt',
                'emergency_stop_blocks_before_reconnect_storage',
                'due_second_attempt_explicit_and_single',
                'checkpoint_and_budget_recover_together',
                'pending_checkpoint_result_recovers_without_second_get',
                'emergency_stop_retires_pending_without_external_access',
                'emergency_stop_closes_retry_wait_without_new_attempt',
                'emergency_stop_blocks_retry_schedule_during_result',
                'provider_retry_after_extends_durable_backoff',
                'no_sleep_loop_or_financial_authority',
                'no_private_identifier_or_credential_echo')) and
            reconnect_budget['actual_storage_retained'] is False,
            'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET')
        _require(all(reconnect_budget[k] is False for k in
            (*ACTIVITY_SSE_RECONNECT_BUDGET_FLAGS,'actual_provider_access',
             'actual_stream_connection','actual_vault_access')),
            'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET_AUTHORITY')
        response=result['exact_response_bundle']
        _require(response['source']=='SYNTHETIC_EXACT_RESPONSE_BUNDLE_FIXTURE' and
            all(response[k] is True for k in ('passed','exact_adjacent_sessions_bound',
                'exact_account_response_replayed','exact_activity_response_replayed',
                'bounded_adjacent_session_start_gap','delayed_adjacent_session_rejected',
                'component_session_timing_envelopes_match','outside_session_timing_rejected',
                'adjacent_account_fields_match','adjacent_account_discontinuity_rejected',
                'overlapping_order_identities_match','shared_order_identity_bound',
                'cross_response_order_identity_conflict_rejected',
                'overlapping_order_progress_monotonic','shared_order_progress_bound',
                'cross_response_order_time_regression_rejected',
                'cross_response_order_fill_regression_rejected',
                'cross_response_order_status_regression_rejected',
                'overlapping_order_fill_deltas_bound','shared_order_fill_delta_bound',
                'retained_fill_limit_prices_checked',
                'activity_only_order_average_limit_prices_checked',
                'complete_activity_only_fill_averages_checked',
                'unobserved_activity_fill_average_limits_checked',
                'activity_only_fill_order_update_bounds_checked',
                'retained_fill_type_remainders_checked',
                'retained_activity_fill_deltas_checked',
                'cross_response_order_fill_activity_missing_rejected',
                'cross_response_order_fill_activity_gap_rejected',
                'cross_response_order_fill_activity_time_rejected',
                'cross_response_order_fill_average_disagreement_rejected',
                'overlapping_order_fill_activities_partitioned',
                'shared_order_fill_partition_bound',
                'late_historical_fill_activity_rejected',
                'repeated_cumulative_fill_state_rejected',
                'cumulative_fill_time_regression_rejected',
                'cumulative_fill_remaining_conflict_rejected',
                'fill_requested_quantity_disagreement_rejected',
                'activity_requested_quantity_changed_rejected',
                'activity_limit_price_changed_rejected',
                'fill_limit_price_disagreement_rejected',
                'partial_fill_without_remainder_rejected',
                'terminal_fill_with_remainder_rejected',
                'activity_only_fill_chain_gap_rejected',
                'activity_only_fill_after_order_update_rejected',
                'activity_only_average_limit_disagreement_rejected',
                'activity_only_complete_fill_average_disagreement_rejected',
                'activity_only_unobserved_average_limit_disagreement_rejected',
                'activity_only_unobserved_average_invalid_rejected',
                'filled_status_quantity_disagreement_rejected',
                'completion_status_disagreement_rejected',
                'reported_fee_references_consistent','reported_fee_reference_bound',
                'reported_fee_exact_timestamp_chronology_checked',
                'reported_fee_exact_timestamp_capture_upper_bound_checked',
                'retained_fee_exact_timestamp_capture_upper_bound_checked',
                'retained_order_update_collection_start_upper_bound_checked',
                'reported_account_order_update_capture_upper_bound_checked',
                'retained_fill_timestamp_page_receipt_upper_bound_checked',
                'retained_fee_exact_timestamp_page_receipt_upper_bound_checked',
                'retained_exact_activity_page_receipt_upper_bound_checked',
                'page_receipt_exact_activity_scope_bound',
                'unreferenced_fee_capture_scope_bound',
                'reported_fee_date_only_scope_preserved',
                'fee_execution_reference_unknown_rejected',
                'fee_reference_disagreement_rejected','fee_reference_partial_rejected',
                'fee_timestamp_before_fill_rejected',
                'fee_timestamp_after_capture_rejected',
                'order_update_after_collection_start_rejected',
                'account_order_update_after_capture_rejected',
                'unreferenced_fee_timestamp_after_capture_rejected',
                'fill_timestamp_after_page_receipt_rejected',
                'fee_timestamp_after_page_receipt_rejected',
                'repeat_review_matches','changed_activity_response_rejected',
                'session_tail_required','journals_unchanged')),'EXACT_RESPONSE_BUNDLE')
        _require(all(response[k] is False for k in (*RESPONSE_FLAGS,
            'actual_provider_access','actual_vault_access')),'EXACT_RESPONSE_BUNDLE_AUTHORITY')
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
            result['accepted_pacing_operations_typed'] is True and
            result['accepted_pacing_result_receipts_bound'] is True and
            result['complete_retained_batches_accepted'] is True and
            result['complete_collection_observations_accepted'] is True and
            result['all_collection_cursor_sequences_complete'] is True and
            result['declared_windows_cover_observation_envelope'] is True and
            result['observation_arithmetic_evaluated'] is True,
            'RECONCILIATION_ACCEPTANCE_BINDING')
        _require(all(result[name] is True for name in ('stability_guard_bound',
            'stability_guard_repeat_matches','changed_guard_balance_rejected',
            'unordered_guard_windows_rejected','outer_guard_window_coverage_rejected',
            'guard_declared_sources_agree','declared_source_mismatch_rejected',
            'guard_retained_fill_events_absent','guard_collection_observed_fill_events_absent',
            'late_collected_historical_fill_rejected','guard_collection_observed_fee_events_absent',
            'late_collected_historical_fee_rejected','guard_retained_fee_date_overlaps_absent',
            'guard_fee_date_overlap_detected')),
            'RECONCILIATION_STABILITY_GUARD')
        _require(all(result[name] is False for name in ('actual_provider_access','actual_vault_access','requested_window_boundaries_verified',
            'guard_balance_instants_verified','guard_observation_windows_verified_quiet',
            'broker_account_reconciled','spendable_funds_verified','ready_for_routing','broker_connected',
            'live_orders_enabled','money_movement_enabled','release_approved')),
            'RECONCILIATION_ACCEPTANCE_AUTHORITY')
    if profile == 'activity':
        from nexus.broker_request_activity_review import FLAGS
        linked=result['request_activity_link']
        _require(linked['source']=='TEMPORARY_REQUEST_ACTIVITY_LINK_FIXTURE' and all(linked[k] is True for k in ('passed','original_request_linked','explicit_fills_checked','offsetting_bad_price_detected','journals_unchanged')), 'REQUEST_ACTIVITY_LINK')
        _require(all(linked[k] is False for k in FLAGS),'REQUEST_ACTIVITY_AUTHORITY')
        accepted=result['request_activity_acceptance']
        from nexus.paper_request_activity_acceptance import FLAGS as ACCEPTED_FLAGS
        _require(accepted['source']=='SYNTHETIC_LOCAL_ACCEPTED_REQUEST_FILL_FIXTURE' and
            all(accepted[k] is True for k in ('passed','accepted_history_bound','original_request_linked',
                'accepted_pacing_operations_typed','accepted_pacing_result_receipts_bound','explicit_fills_checked','journals_unchanged','unbound_activity_rejected')),
            'REQUEST_ACTIVITY_ACCEPTANCE')
        _require(all(accepted[k] is False for k in ACCEPTED_FLAGS) and
            accepted['actual_provider_access'] is accepted['actual_vault_access'] is False,
            'REQUEST_ACTIVITY_ACCEPTANCE_AUTHORITY')
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
        # Reject an authority claim before reading the new nested evidence so
        # malformed legacy-shaped results still fail on the safety invariant.
        for name in ('broker_connected', 'live_orders_enabled', 'release_approved', 'application_accounts_changed'):
            if name in result: _require(result[name] is False, 'UNEXPECTED_AUTHORITY')
        from nexus.order_offload import (CHECKS as OFFLOAD_CHECKS,AUTHORITY_FLAGS as OFFLOAD_FLAGS,
            MULTI_EPOCH_FIXTURE_GENERATIONS,MULTI_EPOCH_FIXTURE_ORDERS)
        offload=result['order_physical_offload']
        _require(offload['source']=='TEMPORARY_OFFLINE_ORDER_OFFLOAD_FIXTURE' and offload['passed'] is True and
                 all(offload[key] is True for key in OFFLOAD_CHECKS),'ORDER_OFFLOAD')
        _require(offload['multi_epoch_generations']==MULTI_EPOCH_FIXTURE_GENERATIONS and
                 offload['multi_epoch_orders']==MULTI_EPOCH_FIXTURE_GENERATIONS*MULTI_EPOCH_FIXTURE_ORDERS and
                 offload['multi_epoch_retired_id_fingerprint_count']==offload['multi_epoch_orders'] and
                 offload['multi_epoch_max_live_commands']==4 and
                 type(offload['multi_epoch_max_source_commands']) is int and
                 4<offload['multi_epoch_max_source_commands']<100,
                 'ORDER_OFFLOAD_MULTI_EPOCH')
        _require(all(offload[key] is False for key in OFFLOAD_FLAGS),'ORDER_OFFLOAD_AUTHORITY')
    if profile == 'recovery':
        _require(result['recovered']['halted'] is True and result['recovered']['portfolio_reconciled'] is True, 'RESTORE_ACCOUNTING')
    for name in ('broker_connected', 'live_orders_enabled', 'release_approved', 'application_accounts_changed'):
        if name in result: _require(result[name] is False, 'UNEXPECTED_AUTHORITY')


def source_digest(root):
    """Fingerprint executed text inputs independent of checkout line endings.

    Git's ordinary Windows checkout may materialize text as CRLF while Linux
    materializes the same tree as LF.  These inputs are all text source or
    configuration, so normalize only CRLF pairs before hashing.  Lone CR bytes
    and every other byte remain significant.
    """
    root = Path(root); entries = []
    paths = set(root.glob('nexus/*.py')) | set(root.glob('tests/*.py')) | set(root.glob('tools/*.py')) | set(root.glob('tools/*.cjs')) | set(root.glob('tools/*.ps1'))
    paths |= set(root.glob('.github/workflows/*.yml')) | set(root.glob('portable/*.py'))
    paths |= {root/'pyproject.toml', root/'dashboard/portal_preview.html', root/'installer/NexusOperative.iss'}
    paths |= {root/name for name in ('.gitignore','.gitleaks.toml','.github/CODEOWNERS','.github/dependabot.yml') if (root/name).is_file()}
    for path in sorted(paths):
        source=path.read_bytes().replace(b'\r\n',b'\n')
        entries.append((path.relative_to(root).as_posix(), hashlib.sha256(source).hexdigest()))
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
            if child.returncode != 0:
                _child_failure(profile, child)
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
