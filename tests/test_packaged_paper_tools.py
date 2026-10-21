"""Tool dispatch cannot initialize a desktop or acquire provider authority."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from portable import desktop
from nexus import paper_activity_acceptance_probe as probe

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('paper_tools_smoke',ROOT/'tools/packaged_paper_tools_smoke.py')
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)


def test_actual_source_child_smoke_covers_all_tools_without_default_data():
    r=smoke.run([sys.executable,str(ROOT/'portable/desktop.py')])
    assert r['passed'] and r['tool_invocations']==30+int(os.name=='nt') and all(r[k] is True for k in smoke.CHECKS)
    assert r['host_os'] == ('WINDOWS_NT' if os.name=='nt' else 'NON_WINDOWS')
    assert r['native_windows_storage_fault_checked'] is (os.name=='nt')
    assert not r['native_windows_acceptance'] and not r['provider_reads_requested'] and not r['credential_reads_requested']
    assert all(value not in json.dumps(r) for value in ('01234567-89ab-cdef-0123-456789abcdef','opening.json','private.sqlite3','802'))


def test_only_encrypted_evidence_smokes_may_use_wider_bounded_child_timeout(monkeypatch):
    seen=[]
    class Process:
        returncode=0
        def communicate(self,timeout):seen.append(timeout);return ('{}','')
    monkeypatch.setattr(smoke.subprocess,'Popen',lambda *args,**kwargs:Process())
    assert smoke._json_call(['python'],['ordinary'],{},timeout=30)=={}
    assert smoke._json_call(['python'],['crypto'],{},timeout=60)=={}
    assert seen==[30,60]
    with pytest.raises(ValueError,match='PACKAGED_PAPER_TOOLS_TIMEOUT'):
        smoke._json_call(['python'],['invalid'],{},timeout=61)


@pytest.mark.parametrize('args',[['--paper-tool'],['--paper-tool','unknown','PRIVATE_VALUE'],['--api-secret','PRIVATE_VALUE'],['--paper-tool','list','PRIVATE_VALUE']])
def test_bad_dispatch_never_configures_or_echoes_arguments(monkeypatch,capsys,args):
    monkeypatch.setattr(desktop,'configure',lambda:pytest.fail('desktop initialized'))
    assert desktop.main(args)==2
    result=capsys.readouterr();assert result.err=='NEXUS_PAPER_TOOL_USAGE_ERROR\n' and not result.out


@pytest.mark.parametrize('args',[['--help'],['--paper-tool','list']])
def test_catalog_is_available_without_configure(monkeypatch,capsys,args):
    monkeypatch.setattr(desktop,'configure',lambda:pytest.fail('desktop initialized'))
    assert desktop.main(args)==0
    r=json.loads(capsys.readouterr().out);assert set(r['commands'])=={'account-probe','account-recovery','account-response-review','activity','activity-response-review','account-review','observation-crypto','order-offload','quality-review','provider-recovery','provider-results','quote-capture','quote-evidence','recovery'}
    assert r['emergency_stop_command']=='--emergency-stop'
    assert not r['desktop_started'] and not r['default_data_created']


@pytest.mark.parametrize('behavior',['return','help','error','unexpected'])
def test_allowlisted_dispatch_restores_argv_and_preserves_fixed_errors(monkeypatch,capsys,behavior):
    import importlib
    original=sys.argv;seen=[]
    def target():
        seen.append(list(sys.argv))
        if behavior=='help':raise SystemExit(0)
        if behavior=='error':raise SystemExit(2)
        if behavior=='unexpected':raise RuntimeError('PRIVATE_PATH_OR_TOKEN')
        return 0
    monkeypatch.setattr(importlib,'import_module',lambda name:SimpleNamespace(main=target))
    monkeypatch.setattr(desktop,'configure',lambda:pytest.fail('desktop initialized'))
    result=desktop.main(['--paper-tool','recovery','--review-only'])
    assert result==(0 if behavior in ('return','help') else 2) and sys.argv is original
    assert seen==[[original[0],'--review-only']] and 'PRIVATE_PATH_OR_TOKEN' not in str(capsys.readouterr())


def test_frozen_private_inputs_reject_installation_folder_and_resolved_alias(tmp_path,monkeypatch):
    app=tmp_path/'app';app.mkdir();monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(app/'NexusOperative.exe'))
    for path in (app/'private.sqlite3',tmp_path/'other'/'..'/'app'/'private.sqlite3'):
        with pytest.raises(ValueError):probe._private_path(path)
    outside=tmp_path/'user-data'/'private.sqlite3';assert probe._private_path(outside)[1]==outside
    assert not outside.parent.exists()


def test_dispatch_real_review_denies_vault_and_socket_access_without_creating_default_storage(tmp_path):
    script='''
import sys
from nexus import paper_credentials
from portable.desktop import main
def denied():raise RuntimeError('VAULT_DENIED')
def audit(event,args):
 if event in ('socket.connect','socket.bind'):raise RuntimeError('NETWORK_DENIED')
paper_credentials._vault=denied;sys.addaudithook(audit)
raise SystemExit(main(sys.argv[1:]))
'''
    account='01234567-89ab-cdef-0123-456789abcdef';missing=tmp_path/'missing'
    child=subprocess.run([sys.executable,'-c',script,'--paper-tool','recovery','--expected-account',account,
        '--journal',str(missing/'r.sqlite3'),'--pacing',str(missing/'p.sqlite3'),'--review-only'],capture_output=True,text=True,timeout=10,env=dict(os.environ,NEXUS_PORTABLE_DATA=str(tmp_path/'unexpected-default')))
    assert child.returncode==0 and json.loads(child.stdout)['read_only'] and not missing.exists() and not (tmp_path/'unexpected-default').exists()


def test_smoke_errors_suppress_private_child_output(monkeypatch):
    monkeypatch.setattr(smoke,'_call',lambda *a:(2,'PRIVATE_VALUE','PRIVATE_PATH'))
    with pytest.raises(ValueError) as error:smoke._json_call(['fixture'],[],{})
    assert str(error.value)=='PACKAGED_PAPER_TOOLS_CHILD_FAILED'


def test_smoke_failure_report_preserves_only_allowlisted_code(tmp_path,monkeypatch,capsys):
    target=tmp_path/'evidence.json'
    monkeypatch.setattr(sys,'argv',['fixture','--executable',sys.executable,'--output',str(target)])
    def failed(*a):raise ValueError('PRIVATE_INPUT_AND_ERROR')
    monkeypatch.setattr(smoke,'run',failed)
    assert smoke.main()==2 and capsys.readouterr().out=='PACKAGED_PAPER_TOOLS_FAILED\n'
    r=json.loads(target.read_text());assert r['passed'] is False and r['failure_code']=='PACKAGED_PAPER_TOOLS_INTERNAL_ERROR'
    assert 'PRIVATE_INPUT_AND_ERROR' not in target.read_text()
