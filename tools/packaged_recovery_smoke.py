"""Restore a disposable paper fixture and verify it through a packaged application."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

# Running a file under tools/ must also work before editable installation.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nexus.recovery_bundle import backup,restore,verify
from nexus.recovery_drill import CHILD_TIMEOUT_SECONDS,_child


def prior_fixture(source,commit,data):
    source=Path(source).resolve()
    if not isinstance(commit,str) or not re.fullmatch(r'[a-f0-9]{40}',commit):
        raise ValueError('PINNED_FIXTURE_COMMIT_REQUIRED')
    actual=subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
    if actual!=commit:raise ValueError('FIXTURE_SOURCE_COMMIT_DISAGREEMENT')
    dirty=subprocess.run(['git','-C',str(source),'status','--porcelain','--untracked-files=no'],check=True,capture_output=True,text=True).stdout
    if dirty:raise ValueError('FIXTURE_SOURCE_HAS_TRACKED_CHANGES')
    code="""import json,os,nexus
from pathlib import Path
assert Path(nexus.__file__).resolve().is_relative_to(Path.cwd())
from nexus.recovery_drill import _create
from nexus.durable_orders import DurableOrderGateway
from nexus.release_roadmap import REVISION
expected=_create()
gateway=DurableOrderGateway(Path(os.environ['NEXUS_PORTABLE_DATA'])/'order_lifecycle.sqlite3')
expected['session_order_ids']=[item['order']['id'] for item in gateway.session_orders(expected['session'])]
expected['fixture_version']=REVISION
print(json.dumps(expected))
"""
    result=subprocess.run([sys.executable,'-c',code],cwd=source,
                          env={**os.environ,'PYTHONPATH':str(source),'NEXUS_PORTABLE_DATA':str(Path(data).resolve())},
                          check=True,capture_output=True,text=True,timeout=CHILD_TIMEOUT_SECONDS)
    expected=json.loads(result.stdout);expected['fixture_source_commit']=commit
    return expected


def run(exe,version,evidence,fixture_source=None,fixture_commit=None):
    evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nexus-packaged-recovery-') as temporary:
        root=Path(temporary);data=root/'source';data.mkdir()
        if (fixture_source is None)!=(fixture_commit is None):raise ValueError('FIXTURE_SOURCE_AND_COMMIT_REQUIRED_TOGETHER')
        expected=prior_fixture(fixture_source,fixture_commit,data) if fixture_source else _child(data)
        if fixture_source and tuple(map(int,expected['fixture_version'].split('.')))>=tuple(map(int,version.split('.'))):
            raise ValueError('FIXTURE_VERSION_MUST_PRECEDE_TARGET')
        baseline=evidence/'recovery-baseline.json'
        baseline.write_text(json.dumps(expected,sort_keys=True,indent=2),encoding='utf-8')
        backup(data,root/'backup',application_closed=True)
        restore(root/'backup',root/'restored',application_closed=True)
        subprocess.run([sys.executable,str(Path(__file__).with_name('installed_smoke.py')),
                        '--exe',str(Path(exe).resolve()),'--data',str(root/'restored'),
                        '--version',version,'--expect-halted','--expected-recovery',str(baseline.resolve()),
                        '--output',str((evidence/'packaged-recovery.json').resolve())],check=True,timeout=100)
        verify(root/'backup')
        return dict(passed=True,version=version,backup_unchanged=True,
                    fixture_version=expected.get('fixture_version',version),fixture_source_commit=fixture_commit,
                    prior_version_data_recovery=fixture_source is not None,
                    scope='Disposable injected data recovery through installed API; pinned prior source when specified. No prior-installer upgrade, native-window or real-feed acceptance.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--fixture-source',type=Path)
    parser.add_argument('--fixture-commit')
    args=parser.parse_args()
    result=run(args.exe,args.version,args.evidence,args.fixture_source,args.fixture_commit)
    (args.evidence/'packaged-recovery-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
