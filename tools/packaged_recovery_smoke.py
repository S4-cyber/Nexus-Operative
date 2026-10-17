"""Restore a disposable paper fixture and verify it through a packaged application."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

# Running a file under tools/ must also work before editable installation.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nexus.recovery_bundle import backup,restore,verify
from nexus.recovery_drill import _child


def run(exe,version,evidence):
    evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nexus-packaged-recovery-') as temporary:
        root=Path(temporary);data=root/'source';data.mkdir()
        expected=_child(data)
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
                    scope='Same-source disposable injected fixture through installed API; no cross-version upgrade, native-window or real-feed acceptance.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.exe,args.version,args.evidence)
    (args.evidence/'packaged-recovery-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
