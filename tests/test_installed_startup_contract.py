"""Exercise the installer checker over real source-process HTTP on disposable data.

Native Windows runs the actual EXE in the installer workflow; the local shim is
not a Windows/package acceptance claim.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

pytestmark=pytest.mark.skipif(os.name=='nt',reason='Native Windows workflow uses the actual packaged EXE')


def launcher(tmp_path):
    shim=tmp_path/'source-launcher'
    shim.write_text('#!'+sys.executable+'\nimport os,uvicorn\nuvicorn.run("nexus.app:app",host="127.0.0.1",port=int(os.environ["NEXUS_PORT"]),log_level="error")\n')
    shim.chmod(0o700)
    return shim


def run(shim,data,output,*extra):
    from nexus.app import app
    return subprocess.run([sys.executable,'tools/installed_smoke.py','--exe',str(shim),'--data',str(data),
        '--version',app.version,'--output',str(output),*extra],capture_output=True,text=True,timeout=40)


def test_fresh_start_then_halted_restart_validate_actual_write_destination(tmp_path):
    shim=launcher(tmp_path);data=tmp_path/'data';first=tmp_path/'fresh.json';second=tmp_path/'restart.json'
    result=run(shim,data,first)
    assert result.returncode==0,result.stderr
    initial=json.loads(first.read_text())
    assert initial['fresh_startup_preserved_missing_accounting']
    assert initial['expected_data_layout_verified_after_explicit_halt'] and initial['halt_persisted']
    assert (data/'order_lifecycle.sqlite3').is_file()
    result=run(shim,data,second,'--expect-halted')
    assert result.returncode==0,result.stderr
    restored=json.loads(second.read_text())
    assert restored['initial_halted'] and not restored['fresh_startup_preserved_missing_accounting']
    assert restored['expected_data_layout_verified_after_explicit_halt']


def test_missing_expected_history_is_not_silently_recreated(tmp_path):
    shim=launcher(tmp_path);data=tmp_path/'missing';output=tmp_path/'unexpected.json'
    result=run(shim,data,output,'--expect-halted')
    assert result.returncode!=0 and 'Expected retained order journal is missing' in result.stderr
    assert not output.exists() and not (data/'order_lifecycle.sqlite3').exists()
