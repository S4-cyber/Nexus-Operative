"""Actual local HTTP acceptance and rejection of falsely successful evidence."""
import copy
import os
import subprocess
import sys
import json

import pytest
from nexus import release_trials as trials


def test_real_http_process_starts_stopped_without_data_credentials_or_external_io(tmp_path):
    env = {**os.environ, 'NEXUS_PORTABLE_DATA': str(tmp_path)}
    code = 'import json; from nexus.release_trials import _http_trial; print(json.dumps(_http_trial()))'
    child = subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=35)
    assert child.returncode == 0, child.stderr
    report = json.loads(child.stdout)
    assert report['passed'] and report['graceful_shutdown'] and report['startup_engine_stopped']
    assert report['external_connections'] == report['credential_operations'] == report['startup_data_files'] == 0
    assert report['transport'] == 'REAL_LOOPBACK_TCP_HTTP'


@pytest.mark.parametrize('profile', trials.PROFILES)
def test_zero_exit_or_generic_success_cannot_certify_a_trial(profile):
    # Incomplete profile results must not turn a green child exit into acceptance.
    with pytest.raises((ValueError, KeyError)): trials._validate(profile, {})


@pytest.mark.parametrize('name', ['broker_connected','live_orders_enabled','release_approved','application_accounts_changed'])
def test_declared_authority_or_user_data_mutation_rejects_report(name):
    with pytest.raises(ValueError,match='UNEXPECTED_AUTHORITY'):
        trials._validate('operations', {'passed':True,'cycles':24,'buy_fills':24,'sell_fills':24,'tier_vetoes':24,
            'history_deleted':False,'old_id_deduplicated':True,name:True})


def test_source_fingerprint_tracks_source_test_and_workflow_changes(tmp_path):
    for name in ('nexus','tests','tools','.github/workflows','dashboard','installer'):
        (tmp_path/name).mkdir(parents=True)
    for name in ('pyproject.toml','dashboard/portal_preview.html','installer/NexusOperative.iss','nexus/core.py',
                 'tests/test_core.py','.github/workflows/test.yml'):
        (tmp_path/name).write_text('original')
    original = trials.source_digest(tmp_path)
    (tmp_path/'report.json').write_text('generated output')
    assert trials.source_digest(tmp_path) == original
    for name in ('nexus/core.py','tests/test_core.py','.github/workflows/test.yml'):
        (tmp_path/name).write_text('changed')
        assert trials.source_digest(tmp_path) != original
        (tmp_path/name).write_text('original')


def test_nonzero_child_failure_is_sanitized_and_never_reported_as_passed(monkeypatch):
    monkeypatch.setattr(trials.subprocess,'run',lambda *a,**k: subprocess.CompletedProcess(a,1,'','SECRET_MUST_NOT_ESCAPE'))
    with pytest.raises(ValueError,match='FORWARD_CHILD_FAILED') as error: trials.run()
    assert 'SECRET' not in str(error.value)


@pytest.mark.parametrize('has_intent', [False, True])
def test_missing_order_history_is_inspected_without_creating_it(tmp_path, monkeypatch, has_intent):
    from nexus import app as api, emergency_intent
    from nexus.durable_orders import DurableOrderGateway
    target = DurableOrderGateway(tmp_path/'orders.sqlite3'); cuts = []
    class Runner:
        def request_cut(self): cuts.append(True)
    monkeypatch.setattr(api, 'research_order_gateway', target)
    monkeypatch.setattr(api, 'paper_forward_runner', Runner())
    monkeypatch.setattr(target, 'recovery_report', lambda: pytest.fail('missing history must not initialize accounting'))
    if has_intent: emergency_intent.persist(target._path())
    assert api._recover_emergency_cut() == (has_intent, False)
    assert cuts == ([True] if has_intent else [])
    assert not target._path().exists() and not (tmp_path/'paper_portfolio.sqlite3').exists()


def test_unreadable_order_history_fails_closed_without_repair(tmp_path, monkeypatch):
    from nexus import app as api
    from nexus.durable_orders import DurableOrderGateway
    target = DurableOrderGateway(tmp_path/'orders.sqlite3'); target._path().write_bytes(b'private-invalid-db')
    before = target._path().read_bytes(); cuts = []
    class Runner:
        def request_cut(self): cuts.append(True)
    monkeypatch.setattr(api, 'research_order_gateway', target)
    monkeypatch.setattr(api, 'paper_forward_runner', Runner())
    assert api._recover_emergency_cut() == (True, False)
    assert cuts == [True] and target._path().read_bytes() == before


def test_pacing_fixture_reports_actual_retained_fences_without_provider_authority():
    result = trials._pacing_trial()
    trials._validate('pacing', result)
    for field in ('cooldown_enforced', 'retry_after_retained', 'authentication_block_retained', 'pending_read_not_auto_released', 'explicit_owned_interruption_retained', 'interrupted_full_day_cooldown'):
        changed = dict(result, **{field:False})
        with pytest.raises(ValueError, match='PACING_FENCES'): trials._validate('pacing', changed)
    for field in ('broker_wide_quota_verified', 'ready_for_routing'):
        with pytest.raises(ValueError, match='PACING_AUTHORITY'): trials._validate('pacing', dict(result, **{field:True}))


def test_balance_trial_requires_both_accounting_and_negative_fences_without_spending_authority():
    from nexus.broker_balance_review import fixture_report
    result=fixture_report();trials._validate('balance',result)
    for field in ('holds_reconciled','broker_account_reconciled','spendable_funds_verified','ready_for_routing',
                  'source_coverage_verified','fee_coverage_complete','ledger_applied','live_orders_enabled'):
        altered=copy.deepcopy(result);altered['review'][field]=True
        with pytest.raises(ValueError,match='BALANCE_AUTHORITY'): trials._validate('balance',altered)
    for field in ('missing_available_btc_is_unknown','cash_disagreement_detected'):
        altered=copy.deepcopy(result);altered['negative_checks'][field]=False
        with pytest.raises(ValueError,match='BALANCE_NEGATIVE_FENCES'): trials._validate('balance',altered)
