import shutil
import subprocess
from pathlib import Path
import pytest
from tools.packaged_recovery_smoke import prior_fixture


def git(source,*args):
    return subprocess.run(['git','-C',str(source),*args],check=True,capture_output=True,text=True).stdout.strip()


def test_pinned_source_is_used_and_modified_checkout_rejected(tmp_path):
    source=tmp_path/'checkout';source.mkdir()
    shutil.copytree(Path(__file__).resolve().parents[1]/'nexus',source/'nexus',ignore=shutil.ignore_patterns('__pycache__'))
    git(source,'init');git(source,'add','nexus')
    git(source,'-c','user.name=Nexus Test','-c','user.email=nexus-test@example.invalid','commit','-m','Fixture source')
    commit=git(source,'rev-parse','HEAD');data=tmp_path/'data';data.mkdir()
    report=prior_fixture(source,commit,data)
    assert report['fixture_source_commit']==commit
    assert report['archived_session_orders']==len(report['session_order_ids'])==2
    assert report['portfolio_reconciled'] and report['forward_stopped']
    with pytest.raises(ValueError,match='COMMIT_DISAGREEMENT'):prior_fixture(source,'0'*40,tmp_path/'other')
    path=source/'nexus'/'recovery_drill.py';path.write_text(path.read_text()+'\n# changed\n')
    with pytest.raises(ValueError,match='TRACKED_CHANGES'):prior_fixture(source,commit,tmp_path/'other')


def test_unpinned_source_rejected_before_execution(tmp_path):
    with pytest.raises(ValueError,match='PINNED_FIXTURE_COMMIT_REQUIRED'):prior_fixture(tmp_path,'main',tmp_path/'data')
