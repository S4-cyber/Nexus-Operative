"""Disposable Git histories; no actual credentials or repository changes."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_audit',ROOT/'tools/public_repo_audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def git(root,*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout

def repository(tmp_path):
    git(tmp_path,'init');git(tmp_path,'config','user.name','Fixture');git(tmp_path,'config','user.email','fixture@example.invalid')
    return tmp_path


def commit(root,name,raw):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    git(root,'add','--',name);git(root,'commit','-m','fixture')
    return path


@pytest.mark.parametrize('name',['.env','.env.local','config.env','secrets/token.txt','storage/private.json','data/account.db','account.sqlite-wal','saved.db-journal','backup.zip','vault.key','saved.nexusenc','Nexus-Paper-Observation.json','nexus-backup-manifest.json'])
def test_sensitive_path_policy(name):assert audit.path_rule(name)


@pytest.mark.parametrize('name',['nexus/app.py','storage/README.txt','storage/.gitkeep','.env.example','validation/release_trials_0_110_0.json','SECURITY.md'])
def test_allowed_public_source_names(name):assert audit.path_rule(name) is None


def test_clean_full_history_and_no_content_echo(tmp_path):
    root=repository(tmp_path);commit(root,'source.py',b'print("public")\n')
    report=audit.audit(root,history=True)
    assert report['passed'] and report['history_scanned'] and report['blobs_checked']==1
    assert report['paths_checked']==1


def test_removed_private_path_is_still_blocked(tmp_path):
    root=repository(tmp_path);path=commit(root,'.env.local',b'not-a-real-secret')
    path.unlink();git(root,'add','-u');git(root,'commit','-m','removed fixture')
    report=audit.audit(root,history=True)
    assert not report['passed'] and any(x['rule']=='ENVIRONMENT_FILE' for x in report['findings'])
    assert 'not-a-real-secret' not in json.dumps(report)


def test_private_blob_renamed_as_text_is_blocked(tmp_path):
    root=repository(tmp_path);commit(root,'ordinary.txt',b'SQLite format 3\x00' + b'fixture')
    report=audit.audit(root,history=True)
    assert not report['passed'] and report['findings'][0]['rule']=='PRIVATE_BINARY_OR_KEY_CONTENT'


def test_export_renamed_as_json_is_blocked_but_offline_fixture_report_is_allowed(tmp_path):
    root=repository(tmp_path);commit(root,'ordinary.json',json.dumps({'profile':'nexus.portable-paper-observation.v1','private':'fixture'}).encode())
    assert any(x['rule']=='PRIVATE_FINANCIAL_REPORT' for x in audit.audit(root,history=True)['findings'])
    commit(root,'validation/report.json',json.dumps({'mode':'BOUNDED_OFFLINE_RELEASE_TRIALS','fixture':True}).encode())
    assert not audit.audit(root,history=True)['passed']


def test_required_ref_must_be_fetched_and_shallow_clone_is_not_a_pass(tmp_path):
    root=repository(tmp_path);commit(root,'source.py',b'public')
    with pytest.raises(ValueError,match='GIT_AUDIT_INCOMPLETE'):audit.audit(root,history=True,required_refs=['refs/remotes/origin/missing'])
    head=git(root,'rev-parse','HEAD').strip();(root/'.git/shallow').write_bytes(head+b'\n')
    with pytest.raises(ValueError,match='FULL_HISTORY_REQUIRED'):audit.audit(root,history=True)


def test_renaming_same_blob_does_not_hide_a_private_historical_path(tmp_path):
    root=repository(tmp_path);path=commit(root,'safe.txt',b'public')
    path.rename(root/'.env');git(root,'add','-A');git(root,'commit','-m','fixture rename')
    assert not audit.audit(root,history=True)['passed']


def test_current_workflows_have_immutable_actions_and_no_privileged_secret_contexts():
    import re
    workflows=list((ROOT/'.github/workflows').glob('*.yml'))
    assert workflows
    for path in workflows:
        text=path.read_text()
        assert 'pull_request_target' not in text and 'secrets.' not in text
        assert 'contents: read' in text
        uses=re.findall(r'uses:\s*([^\s#]+)',text)
        assert uses and all(re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}',item) for item in uses)
        blocks=re.split(r'\n      - ',text)
        for block in blocks:
            if 'uses: actions/checkout@' in block:assert 'persist-credentials: false' in block


def test_pr_security_uses_trusted_policy_full_history_and_checksum_pinned_scanner():
    text=(ROOT/'.github/workflows/public-repo-security.yml').read_text()
    assert 'fetch-depth: 0' in text and 'github.event.pull_request.base.sha || github.sha' in text
    assert 'python3 policy/tools/public_repo_audit.py --repository scan --history' in text
    assert 'sha256sum --check --strict' in text and "--log-opts='--all'" in text
    assert '--redact=100' in text and '--log-level fatal' in text
    assert '--ignore-gitleaks-allow' in text and '--gitleaks-ignore-path /dev/null' in text
    assert 'upload-artifact' not in text and "'Secret'" not in text and "'Match'" not in text


@pytest.mark.parametrize('mode',['RETAINED_FORWARD_OPERATIONAL_COVERAGE_REVIEW','PAPER_ACCOUNT_ACCEPTANCE_PROBE','PAPER_ACCEPTANCE_PACING_STATUS',
    'PAPER_ACTIVITY_ACCEPTANCE_PROBE','PAPER_ACTIVITY_ACCEPTANCE_STATUS','PAPER_ACTIVITY_ACCEPTANCE_HISTORY',
    'PAPER_RECONCILIATION_ACCEPTANCE_REVIEW','BROKER_REQUEST_ACTIVITY_REVIEW',
    'READ_ONLY_REQUEST_OBSERVATION_PREFIXES','PAPER_REQUEST_ACTIVITY_ACCEPTANCE_REVIEW','PAPER_ACCOUNT_EVIDENCE_BUNDLE_REVIEW','READ_ONLY_PAPER_ASSET_RULES',
    'PAPER_RECOVERY_SELECTION','PAPER_RECOVERY_STEP_REVIEW','EXPLICIT_SINGLE_PAPER_RECOVERY_STEP',
    'SUPPLIED_EXECUTION_QUALITY_REVIEW','PAPER_EXECUTION_QUALITY_REVIEW','READ_ONLY_PAPER_BTC_QUOTE_OBSERVATION','PAPER_REQUEST_QUOTE_HISTORY','PAPER_REQUEST_QUOTE_RECEIPT'])
def test_standalone_operational_trial_report_is_private_even_when_renamed(tmp_path,mode):
    root=repository(tmp_path)
    commit(root,'ordinary.txt',json.dumps({'mode':mode,'fixture':True}).encode())
    result=audit.audit(root,history=True)
    assert not result['passed'] and any(f['rule']=='PRIVATE_FINANCIAL_REPORT' for f in result['findings'])
