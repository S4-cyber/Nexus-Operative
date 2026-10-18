"""Exercise a packaged application against an explicit disposable data directory."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request


def request(base,path,method='GET',data=None):
    headers={'Origin':base,'Content-Type':'application/json'} if data is not None else {}
    body=json.dumps(data).encode('utf-8') if data is not None else None
    with urllib.request.urlopen(urllib.request.Request(base+path,method=method,data=body,headers=headers),timeout=3) as response:
        raw=response.read().decode('utf-8')
        return raw if path=='/portal' else json.loads(raw)


def launch_environment(data=None,default_data_root=None,environ=None):
    if (data is None)==(default_data_root is None):raise ValueError('CHOOSE_ONE_DATA_LAYOUT')
    env=dict(os.environ if environ is None else environ)
    env['NEXUS_BROWSER_MODE']='1'
    if default_data_root is not None:
        root=Path(default_data_root).resolve()
        env['LOCALAPPDATA']=str(root)
        # Inherited overrides would make this a portable-path test instead.
        for key in ('NEXUS_PORTABLE_DATA','NEXUS_AUDIT_PATH','NEXUS_RESERVE_PATH','NEXUS_STRATEGY_MEMORY_PATH'):
            env.pop(key,None)
        expected=root/'NexusOperative'/'storage'
    else:
        expected=Path(data).resolve();env['NEXUS_PORTABLE_DATA']=str(expected)
        for key in ('NEXUS_AUDIT_PATH','NEXUS_RESERVE_PATH','NEXUS_STRATEGY_MEMORY_PATH'):
            env.pop(key,None)
    return env,expected


def verify_recovery(base,expected,fetch=request):
    from nexus.recovery_drill import fingerprint
    recovery=fetch(base,'/api/execution/recovery')
    evidence=fetch(base,'/api/paper/forward/sessions/'+expected['session']+'/evidence')
    orders=[]
    for oid in expected['session_order_ids']:
        detail=fetch(base,'/api/execution/intent/'+oid)
        assert detail['archived'] and detail['read_only']
        orders.append(dict(order=detail['order'],quote_events=[{k:v for k,v in event.items() if k!='event_id'} for event in detail['quote_events']]))
    assert recovery['halted'] and recovery['portfolio_reconciled']
    assert not recovery['orders'] and not recovery['execution_account']['account']['positions']
    assert fingerprint(recovery['execution_account'])==expected['account_sha256']
    assert fingerprint(orders)==expected['order_sha256']
    assert len(orders)==expected['archived_session_orders']
    assert evidence['local_consistency_ok'] and not evidence['provisional_active_session']
    assert evidence['quote_evidence']['head_sha256']==expected['quote_head_sha256']
    assert evidence['quote_evidence']['observations']==expected['quote_observations']
    assert evidence['quote_evidence']['source_modes']==expected['source_modes']
    assert evidence['policy']['policy_sha256']==expected['policy_sha256']
    assert evidence['execution_crosscheck']['modeled_buy_orders']==expected['modeled_buy_orders']
    assert evidence['execution_crosscheck']['modeled_sell_orders']==expected['modeled_sell_orders']
    assert not fetch(base,'/api/paper/forward')['enabled']
    return dict(restored_account_matches=True,archived_orders_match=True,quote_evidence_matches=True,
                policy_matches=True,halted=True,forward_stopped=True)


def verify_setup(base,profile,fetch=request):
    """Fixed disposable preset/roundtrip fixture, never run on user accounting."""
    assert profile in ('first-run','saved')
    settings=fetch(base,'/api/paper/settings')
    tiers=fetch(base,'/api/tiers/permissions')['tiers']
    assert settings['simulation_only'] is True
    assert [settings[k] for k in ('terrestrial_allocation','solar_allocation','universal_allocation')]==[50,35,15]
    assert {row['tier'] for row in tiers}=={'terrestrial','solar','universal'}
    assert all(type(row['enabled']) is bool for row in tiers)
    expected={'terrestrial'} if profile=='first-run' else set()
    assert {row['tier'] for row in tiers if row['enabled']}==expected
    assert settings['paper_budget']==(10000 if profile=='first-run' else 2500)
    assert fetch(base,'/api/status')['portal_enabled'] is False
    assert fetch(base,'/api/paper/forward')['enabled'] is False
    items=fetch(base,'/api/onboarding/tasks')['items']
    assert items and all(type(row['why']) is str and len(row['why'])>30 and row['setup_scope'] for row in items)
    if profile=='first-run':
        # Exercise an intentional customization in this disposable directory.
        saved={k:settings[k] for k in ('paper_budget','terrestrial_allocation','solar_allocation','universal_allocation')}
        saved['paper_budget']=2500
        fetch(base,'/api/paper/settings','PUT',saved)
        fetch(base,'/api/tiers/terrestrial/permission','PUT',{'enabled':False})
    assert fetch(base,'/api/paper/settings')['paper_budget']==2500
    assert all(row['enabled'] is False for row in fetch(base,'/api/tiers/permissions')['tiers'])
    status=fetch(base,'/api/status')
    assert all(status[key] is False for key in ('portal_enabled','live_orders_enabled','broker_connected','money_movement_enabled'))
    assert fetch(base,'/api/paper/forward')['enabled'] is False
    return dict(profile=profile,configuration_verified=True,saved_choices_verified=True,
                trading_stayed_stopped=True,scope='DISPOSABLE_LOCAL_PAPER_CONFIGURATION_ONLY')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',required=True,type=Path)
    layout=parser.add_mutually_exclusive_group(required=True)
    layout.add_argument('--data',type=Path)
    layout.add_argument('--default-data-root',type=Path)
    parser.add_argument('--version',required=True)
    parser.add_argument('--expect-halted',action='store_true')
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--expected-recovery',type=Path)
    parser.add_argument('--setup-check',choices=('first-run','saved'),help='Fixed disposable setup fixture; first-run changes virtual budget and disables Terrestrial for restart verification.')
    args=parser.parse_args()
    if args.setup_check and args.expected_recovery:parser.error('Setup fixtures and retained recovery fixtures require separate directories')
    if args.setup_check=='first-run' and args.expect_halted:parser.error('First-run setup requires an unused directory')
    if args.default_data_root is not None and os.name!='nt':parser.error('Default frozen Windows layout requires Windows')
    env,expected_data=launch_environment(args.data,args.default_data_root)
    accounting_names=('order_lifecycle.sqlite3','paper_portfolio.sqlite3','paper_forward.sqlite3')
    initial_accounting={name:(expected_data/name).is_file() for name in accounting_names}
    if args.setup_check=='first-run':
        retained_names=['emergency_cut.intent']+[base+suffix for base in ('portal_settings.sqlite3',*accounting_names) for suffix in ('','-journal','-wal','-shm')]
        assert not any(os.path.lexists(expected_data/name) for name in retained_names), 'First-run setup requires missing settings and accounting'
    if args.setup_check=='saved':
        assert (expected_data/'portal_settings.sqlite3').is_file(), 'Saved setup fixture is missing'
    if args.expect_halted or args.expected_recovery:
        assert initial_accounting['order_lifecycle.sqlite3'], 'Expected retained order journal is missing'
    if args.data is not None:expected_data.mkdir(parents=True,exist_ok=True)
    else:args.default_data_root.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env['NEXUS_PORT']=str(port)
    # Keep startup evidence in the artifact; avoid pipes that can fill and deadlock.
    with args.output.with_suffix('.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(args.exe.resolve())],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            base=f'http://127.0.0.1:{port}';deadline=time.monotonic()+60
            while True:
                if proc.poll() is not None:raise RuntimeError('INSTALLED_EXE_EXITED_EARLY')
                try:
                    status=request(base,'/api/status');break
                except (OSError,urllib.error.URLError):
                    if time.monotonic()>deadline:raise RuntimeError('INSTALLED_START_TIMEOUT')
                    time.sleep(.25)
            # Passive fresh startup must not initialize accounting. Validate the
            # actual data destination after the explicit halt below instead.
            fresh_accounting=not any(initial_accounting.values())
            if fresh_accounting:
                # The launched browser can race this probe. Exercise the same
                # automatic dashboard read explicitly rather than depend on
                # whether the runner's browser happened to load fast enough.
                assert request(base,'/api/paper/portfolio')['initialized'] is False
                assert not request(base,'/api/paper/forward')['enabled']
                assert request(base,'/api/execution/recovery')['status']=='NO_RETAINED_HISTORY'
                assert request(base,'/api/paper/forward/recovery')['order_history_present'] is False
                assert not any((expected_data/name).exists() for name in accounting_names), 'Fresh startup initialized accounting'
            assert status['version']==args.version, status
            assert status['halted'] is args.expect_halted, status
            assert not status['portal_enabled'] and not status['live_orders_enabled'] and not status['broker_connected']
            assert 'PORTAL' in request(base,'/portal')
            assert not request(base,'/api/paper/forward')['enabled']
            assert request(base,'/api/roadmap')['read_only']
            setup=verify_setup(base,args.setup_check) if args.setup_check else None
            restored=verify_recovery(base,json.loads(args.expected_recovery.read_text(encoding='utf-8'))) if args.expected_recovery else None
            assert request(base,'/api/simulate/halt','POST')['halted']
            assert (expected_data/'order_lifecycle.sqlite3').is_file(), 'Explicit halt did not use the expected data layout'
            assert request(base,'/api/execution/recovery')['halted']
            args.output.write_text(json.dumps(dict(version=args.version,initial_halted=status['halted'],
                portal_served=True,forward_stopped=True,halt_persisted=True,restored_recovery=restored,setup_check=setup,
                fresh_startup_preserved_missing_accounting=fresh_accounting,
                expected_data_layout_verified_after_explicit_halt=True,
                data_layout='DEFAULT_WINDOWS_APPDATA' if args.default_data_root else 'EXPLICIT_PORTABLE_DIRECTORY',
                scope='CI installed-process smoke; no native window, market feed or cross-version upgrade acceptance.'),indent=2),encoding='utf-8')
        finally:
            # PyInstaller onefile has a parent bootloader and application child.
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=log,stderr=log,check=False)
            else:proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


if __name__=='__main__':main()
