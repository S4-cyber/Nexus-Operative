"""Installed offline evidence input is bounded, explicit and non-authorizing."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from nexus import paper_provider_result_tool as subject
from nexus import paper_provider_result_review as reviewer
from nexus.alpaca_paper_contract import canonical


def files(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}

@pytest.fixture
def manifest(tmp_path):return subject._fixture(tmp_path)


def test_relative_paths_resolve_beside_manifest_without_writes_or_authority(manifest,tmp_path,monkeypatch):
    elsewhere=tmp_path/'elsewhere';elsewhere.mkdir();monkeypatch.chdir(elsewhere);before=files(tmp_path)
    result=subject.review_file(manifest)
    assert result['local_provider_results_consistent'] and result['normalized_sessions_matched']==5
    assert result['status']=='REVIEW_REQUIRED' and all(result[k] is False for k in reviewer.FLAGS)
    assert files(tmp_path)==before


def test_fixture_requires_manifest_identity_and_exceptional_exit_fences():
    result=subject.fixture_report()
    assert result['passed'] and all(result[key] is True for key in subject.CHECKS)
    assert all(result[key] is False for key in reviewer.FLAGS)


def test_path_descriptor_ctime_disagreement_is_not_treated_as_identity_change(manifest,monkeypatch):
    original=Path.lstat
    class StatProxy:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):
            if name=='st_ctime_ns':return self.value.st_ctime_ns+1
            return getattr(self.value,name)
    def shifted(path):
        value=original(path)
        return StatProxy(value) if path==manifest else value
    monkeypatch.setattr(Path,'lstat',shifted)
    assert subject.review_file(manifest)['local_provider_results_consistent']


@pytest.mark.parametrize('change',['result','missing','scope','account','provider_head','account_head'])
def test_changed_evidence_cannot_pass_local_consistency(manifest,change):
    value=json.loads(manifest.read_text());account=value['accounts'][0]
    if change=='result':account['bindings'][0]['normalized_result']['cash']='999'
    if change=='missing':account['bindings'].pop()
    if change=='scope':account['bindings'][0]['request_scope']={}
    if change=='account':account['account_id']='11234567-89ab-cdef-0123-456789abcdef'
    if change=='provider_head':value['expected_provider_head']='0'*64
    if change=='account_head':account['expected_head']='0'*64
    manifest.write_text(canonical(value))
    if change in ('account','provider_head','account_head'):
        with pytest.raises(ValueError):subject.review_file(manifest)
    else:assert not subject.review_file(manifest)['local_provider_results_consistent']


@pytest.mark.parametrize('change',['extra','profile','duplicate_account','extra_account_field','missing_head','tuple_accounts','empty_accounts','too_many','nonstring_path','nul_path'])
def test_manifest_shape_has_no_implicit_accounts_or_defaults(manifest,change):
    value=json.loads(manifest.read_text())
    if change=='extra':value['secret']='never_echo_secret'
    if change=='profile':value['profile']='other'
    if change=='duplicate_account':value['accounts']*=2
    if change=='extra_account_field':value['accounts'][0]['secret']='never_echo_secret'
    if change=='missing_head':value.pop('expected_provider_head')
    if change=='tuple_accounts':value['accounts']={}
    if change=='empty_accounts':value['accounts']=[]
    if change=='too_many':value['accounts']*=7
    if change=='nonstring_path':value['provider_budget']=True
    if change=='nul_path':value['provider_budget']='bad\0path'
    manifest.write_text(canonical(value))
    with pytest.raises(ValueError):subject.review_file(manifest)


@pytest.mark.parametrize('raw',[b'',b'{',b'[]',b'{"profile":1,"profile":2}',b'NaN',b'\xff',b'['*1000+b']'*1000],ids=['empty','malformed','array','duplicate','nonfinite','unicode','deep'])
def test_untrusted_input_always_has_fixed_cli_failure(tmp_path,monkeypatch,capsys,raw):
    path=tmp_path/'private.json';path.write_bytes(raw);monkeypatch.setattr(sys,'argv',['tool','--input',str(path)])
    assert subject.main()==2
    out=capsys.readouterr();assert out.out=='PAPER_PROVIDER_RESULT_TOOL_FAILED\n' and not out.err


def test_serialized_cap_is_enforced_before_parsing(manifest,monkeypatch):
    monkeypatch.setattr(subject,'MAX_BYTES',1)
    with pytest.raises(ValueError):subject.review_file(manifest)


def test_changed_manifest_after_journal_review_loses_acknowledgment(manifest,monkeypatch):
    original=reviewer.review
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);manifest.write_bytes(manifest.read_bytes()+b' ');return result
    monkeypatch.setattr(reviewer,'review',changed)
    with pytest.raises(ValueError,match='PAPER_PROVIDER_RESULT_INPUT_INVALID'):subject.review_file(manifest)


def test_same_byte_manifest_replacement_during_review_is_refused(manifest,monkeypatch):
    original=reviewer.review
    def replaced(*args,**kwargs):
        result=original(*args,**kwargs)
        replacement=manifest.with_suffix('.replacement')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        return result
    monkeypatch.setattr(reviewer,'review',replaced)
    with pytest.raises(ValueError,match='PAPER_PROVIDER_RESULT_INPUT_INVALID'):
        subject.review_file(manifest)


def test_manifest_mutation_hidden_by_reader_failure_is_refused(manifest,monkeypatch):
    def changed_then_failed(*args,**kwargs):
        manifest.write_bytes(manifest.read_bytes()+b' ')
        raise RuntimeError('lower reader failure must not mask manifest mutation')
    monkeypatch.setattr(reviewer,'review',changed_then_failed)
    with pytest.raises(ValueError,match='PAPER_PROVIDER_RESULT_INPUT_INVALID'):
        subject.review_file(manifest)


@pytest.mark.parametrize('suffix',['','-journal','-wal','-shm','.owner.lock'])
def test_manifest_cannot_alias_any_journal_family(manifest,suffix,monkeypatch):
    value=json.loads(manifest.read_text());value['provider_budget']=manifest.name+suffix
    manifest.write_text(canonical(value))
    monkeypatch.setattr(reviewer,'review',lambda *a,**k:pytest.fail('source replay occurred before path validation'))
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):subject.review_file(manifest)


def test_missing_inputs_do_not_initialize_storage(tmp_path):
    path=tmp_path/'missing'/'input.json'
    with pytest.raises(FileNotFoundError):subject.review_file(path)
    assert not path.parent.exists()


def test_leaf_symlink_and_hardlink_manifest_are_refused(manifest,tmp_path):
    hard=tmp_path/'hard.json'
    try:os.link(manifest,hard)
    except OSError:pytest.skip('hardlinks unavailable')
    with pytest.raises(ValueError):subject.review_file(hard)
    hard.unlink()
    link=tmp_path/'link.json'
    try:link.symlink_to(manifest)
    except OSError:pytest.skip('symlinks unavailable')
    with pytest.raises(ValueError):subject.review_file(link)


def test_source_and_frozen_installation_paths_are_refused(tmp_path,monkeypatch):
    from nexus.paper_activity_acceptance_probe import ROOT
    for path in (ROOT/'private.json',):
        with pytest.raises(ValueError):subject.review_file(path)
    app=tmp_path/'app';app.mkdir();monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(app/'NexusOperative.exe'))
    with pytest.raises(ValueError):subject.review_file(app/'private.json')


def test_dispatch_real_command_with_vault_network_and_desktop_denied(manifest,tmp_path):
    before=files(tmp_path)
    script='''
import sys
from portable import desktop
from nexus import paper_credentials
def denied(*a,**k):raise AssertionError('forbidden access')
def audit(event,args):
 if event in ('socket.connect','socket.bind'):denied()
paper_credentials._vault=denied;paper_credentials._capture=denied
desktop.configure=denied;sys.addaudithook(audit)
raise SystemExit(desktop.main(sys.argv[1:]))
'''
    result=subprocess.run([sys.executable,'-c',script,'--paper-tool','provider-results','--input',str(manifest)],
        capture_output=True,text=True,timeout=30,env=dict(os.environ,NEXUS_PORTABLE_DATA=str(tmp_path/'unexpected')))
    assert result.returncode==0 and not result.stderr and json.loads(result.stdout)['local_provider_results_consistent']
    assert files(tmp_path)==before and not (tmp_path/'unexpected').exists()


@pytest.mark.parametrize('args',[[],['--input'],['--secret','never_echo_secret']])
def test_usage_errors_do_not_echo_inputs(monkeypatch,capsys,args):
    monkeypatch.setattr(sys,'argv',['tool',*args])
    with pytest.raises(SystemExit) as error:subject.main()
    assert error.value.code==2
    out=capsys.readouterr();assert not out.out and out.err=='PAPER_PROVIDER_RESULT_TOOL_USAGE_ERROR\n'


def test_nonregular_manifest_is_rejected_without_opening(tmp_path):
    if not hasattr(os,'mkfifo'):pytest.skip('FIFO unavailable')
    path=tmp_path/'private.json';os.mkfifo(path)
    with pytest.raises(ValueError):subject.review_file(path)


@pytest.mark.parametrize('kind',['main','companion','ancestor'])
def test_manifest_path_relationships_are_checked_for_account_journal_too(manifest,kind,monkeypatch):
    value=json.loads(manifest.read_text())
    value['accounts'][0]['pacing']=(manifest.name if kind=='main' else manifest.name+'-journal' if kind=='companion' else manifest.name+'/child')
    manifest.write_text(canonical(value))
    monkeypatch.setattr(reviewer,'review',lambda *a,**k:pytest.fail('unsafe path reached replay'))
    with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):subject.review_file(manifest)
