import json
import os
import sqlite3
import subprocess
import sys
import pytest
from nexus import recovery_drill as drill


def test_disposable_drill_preserves_caller_data_and_environment(tmp_path,monkeypatch):
    sentinel=tmp_path/'user-data';sentinel.mkdir();(sentinel/'keep.txt').write_text('untouched')
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(sentinel))
    before=dict(os.environ)
    report=drill.run()
    assert dict(os.environ)==before
    assert list(sentinel.iterdir())==[sentinel/'keep.txt']
    assert report['passed'] and not report['release_approved']
    recovered=report['recovered']
    assert recovered['halted'] and recovered['portfolio_reconciled'] and recovered['forward_stopped']
    assert recovered['archived_session_orders']==2
    assert recovered['modeled_buy_orders']==recovered['modeled_sell_orders']==1
    assert recovered['quote_observations']==8
    assert recovered['source_modes']==['INJECTED_TRANSPORT_UNATTESTED']


@pytest.mark.parametrize('kind',['missing','ledger','quote'])
def test_fresh_process_review_rejects_cross_database_damage(tmp_path,kind):
    baseline=drill._child(tmp_path)
    if kind=='missing':(tmp_path/'paper_portfolio.sqlite3').unlink()
    elif kind=='ledger':
        with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
            db.execute("UPDATE meta SET value='1' WHERE key='cash'")
    else:
        with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
            db.execute("UPDATE forward_quote_heads SET head='corrupt'")
    with pytest.raises(ValueError,match='RECOVERY_DRILL_CHILD_FAILED'):
        drill._child(tmp_path,baseline['session'])


def test_drill_rejects_restored_fingerprint_mismatch(monkeypatch):
    original=drill._child
    def altered(path,session=None):
        report=original(path,session)
        if session:report['order_sha256']='wrong'
        return report
    monkeypatch.setattr(drill,'_child',altered)
    with pytest.raises(ValueError,match='RESTORED_STATE_DISAGREEMENT'):drill.run()


def test_recovery_drill_cli():
    result=subprocess.run([sys.executable,'-m','nexus.recovery_drill'],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['passed']
