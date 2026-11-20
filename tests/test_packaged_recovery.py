import copy
import pytest
from nexus.recovery_drill import _child
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from tools.installed_smoke import verify_recovery


def fixture(tmp_path,monkeypatch):
    expected=_child(tmp_path)
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    g=DurableOrderGateway(tmp_path/'order_lifecycle.sqlite3');r=PaperForwardRunner(g)
    def fetch(base,path):
        if path=='/api/execution/recovery':return g.recovery_report()
        if path.endswith('/evidence'):return r.evidence_report(expected['session'])
        if path.startswith('/api/execution/intent/'):return g.order_report(path.rsplit('/',1)[1])
        if path=='/api/paper/forward':return r.status()
        raise AssertionError(path)
    return expected,fetch


def test_installed_recovery_checker_validates_complete_fixture(tmp_path,monkeypatch):
    expected,fetch=fixture(tmp_path,monkeypatch)
    report=verify_recovery('local',expected,fetch)
    assert all(report.values())


@pytest.mark.parametrize('field',['account_sha256','order_sha256','quote_head_sha256','policy_sha256',
    'event_head_sha256','event_reconciliation_sha256'])
def test_installed_checker_rejects_baseline_mismatch(tmp_path,monkeypatch,field):
    expected,fetch=fixture(tmp_path,monkeypatch)
    changed=copy.deepcopy(expected);changed[field]='wrong'
    with pytest.raises(AssertionError):verify_recovery('local',changed,fetch)


def test_installed_checker_reads_legacy_eventless_fixture_as_incomplete(tmp_path,monkeypatch):
    import sqlite3
    expected,fetch=fixture(tmp_path,monkeypatch)
    for key in ('event_records','event_head_sha256','event_reconciliation_sha256',
                'quote_outcome_bijection','durable_order_outcomes_reconciled'):
        expected.pop(key)
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        db.execute('DROP TABLE forward_events');db.execute('DROP TABLE forward_event_heads')
    assert all(verify_recovery('local',expected,fetch).values())
