from pathlib import Path
import pytest
from tools.installed_smoke import launch_environment


def test_default_layout_clears_overrides_without_mutating_parent(tmp_path):
    inherited={key:'external' for key in ('NEXUS_PORTABLE_DATA','NEXUS_AUDIT_PATH','NEXUS_RESERVE_PATH','NEXUS_STRATEGY_MEMORY_PATH')}
    inherited['LOCALAPPDATA']='original';inherited['KEEP']='value';before=dict(inherited)
    child,expected=launch_environment(default_data_root=tmp_path,environ=inherited)
    assert inherited==before and child['KEEP']=='value'
    assert child['LOCALAPPDATA']==str(tmp_path.resolve())
    assert expected==tmp_path.resolve()/'NexusOperative'/'storage'
    assert all(key not in child for key in before if key.startswith('NEXUS_'))
    assert not expected.exists()


def test_portable_layout_isolates_auxiliary_databases(tmp_path):
    inherited={'NEXUS_AUDIT_PATH':'external','NEXUS_PORTABLE_DATA':'original'}
    child,expected=launch_environment(data=tmp_path,environ=inherited)
    assert child['NEXUS_PORTABLE_DATA']==str(tmp_path.resolve())
    assert 'NEXUS_AUDIT_PATH' not in child
    assert inherited['NEXUS_AUDIT_PATH']=='external'
    assert expected==tmp_path.resolve()


@pytest.mark.parametrize('both',[False,True])
def test_layout_selection_is_unambiguous(tmp_path,both):
    with pytest.raises(ValueError,match='CHOOSE_ONE_DATA_LAYOUT'):
        launch_environment(data=tmp_path if both else None,default_data_root=tmp_path if both else None)


@pytest.mark.parametrize('case',['wrong_budget','tier_off','missing_purpose','auto_start','promoted_funds','reset_off','empty_checklist'])
def test_packaged_setup_acceptance_rejects_wrong_defaults_or_permission(case):
    from tools.installed_smoke import verify_setup
    budget=10000;on=True;writes=[]
    def fetch(base,path,method='GET',data=None):
        nonlocal budget,on
        if method=='PUT':
            writes.append((path,data))
            if path=='/api/paper/settings':budget=data['paper_budget']
            else:on=False
        if path=='/api/paper/settings':return dict(simulation_only=True,paper_budget=99 if case=='wrong_budget' else budget,
            terrestrial_allocation=50,solar_allocation=35,universal_allocation=15)
        if path=='/api/tiers/permissions':return dict(tiers=[dict(tier=t,enabled=(on if case!='tier_off' else False) if t=='terrestrial' else False)
            for t in ('terrestrial','solar','universal')]) if case!='reset_off' or not writes else dict(tiers=[dict(tier='terrestrial',enabled=True),dict(tier='solar',enabled=False),dict(tier='universal',enabled=False)])
        if path=='/api/status':return dict(portal_enabled=case=='auto_start',live_orders_enabled=False,broker_connected=False,money_movement_enabled=case=='promoted_funds')
        if path=='/api/paper/forward':return dict(enabled=False)
        if path=='/api/onboarding/tasks':return dict(items=[] if case=='empty_checklist' else [dict(why='' if case=='missing_purpose' else 'Built-in tools need no external paid account.',setup_scope='Included')])
        return {}
    with pytest.raises(AssertionError):verify_setup('http://127.0.0.1', 'first-run',fetch)
    assert all(path in ('/api/paper/settings','/api/tiers/terrestrial/permission') for path,_ in writes)


def test_saved_setup_acceptance_is_read_only():
    from tools.installed_smoke import verify_setup
    def fetch(base,path,method='GET',data=None):
        assert method=='GET' and data is None
        if path=='/api/paper/settings':return dict(simulation_only=True,paper_budget=2500,terrestrial_allocation=50,solar_allocation=35,universal_allocation=15)
        if path=='/api/tiers/permissions':return dict(tiers=[dict(tier=t,enabled=False) for t in ('terrestrial','solar','universal')])
        if path=='/api/status':return dict(portal_enabled=False,live_orders_enabled=False,broker_connected=False,money_movement_enabled=False)
        if path=='/api/paper/forward':return dict(enabled=False)
        return dict(items=[dict(why='Built-in tools need no external paid account.',setup_scope='Included')])
    result=verify_setup('http://127.0.0.1','saved',fetch)
    assert result['saved_choices_verified'] and result['trading_stayed_stopped']


def test_installed_api_smoke_explicitly_uses_headless_launch(tmp_path):
    child,_=launch_environment(data=tmp_path/'data',environ={})
    assert child['NEXUS_HEADLESS_MODE']=='1'


def test_failed_halt_records_allowlisted_pending_status_without_retry(tmp_path):
    import json
    from tools.installed_smoke import verify_halt
    calls=[];samples=iter([10,13]);out=tmp_path/'halt.json'
    def fetch(base,path,method='GET'):
        calls.append((path,method))
        if method=='POST':raise TimeoutError('PRIVATE_TRANSPORT_DETAILS')
        return dict(halted=True,emergency_cut_pending=True,emergency_cut_state='PENDING',live_orders_enabled=False,money_movement_enabled=False,private='PRIVATE_ACCOUNT')
    with pytest.raises(TimeoutError):verify_halt('local',out,fetch,lambda:next(samples))
    result=json.loads(out.read_text())
    assert result['elapsed_seconds']==3 and not result['halt_acknowledged'] and result['emergency_cut_pending']
    assert calls==[('/api/simulate/halt','POST'),('/api/status','GET')]
    assert not result['retry_attempted'] and 'PRIVATE' not in out.read_text()


def test_successful_halt_records_duration_without_claiming_latency_acceptance(tmp_path):
    import json
    from tools.installed_smoke import verify_halt
    out=tmp_path/'halt.json';samples=iter([10,10.25])
    result=verify_halt('local',out,lambda *args:dict(halted=True,emergency_cut_pending=False,emergency_cut_state='VERIFIED'),lambda:next(samples))
    assert result['halt_acknowledged'] and result['elapsed_seconds']==0.25 and result['latency_acceptance'] is False
    assert json.loads(out.read_text())==result
