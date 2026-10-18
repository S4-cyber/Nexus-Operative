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
    assert report['untrusted_host_rejected'] and report['cross_origin_rejected'] and report['api_no_store']
    assert not report['proxy_headers_trusted']
    pacing = report['paper_pacing']
    assert all(pacing[k] is True for k in trials.PAPER_HTTP_CHECKS)
    assert pacing['injected_get_requests'] == 5
    assert pacing['actual_vault_access'] is pacing['actual_broker_access'] is False
    assert pacing['ready_for_routing'] is pacing['live_orders_enabled'] is False
    assert pacing['money_movement_enabled'] is pacing['release_approved'] is False


def http_report():
    from pathlib import Path
    from nexus.release_roadmap import REVISION
    return json.loads((Path(__file__).resolve().parents[1]/'validation'/
        ('release_trials_'+REVISION.replace('.', '_')+'.json')).read_text())['profiles']['http']


@pytest.mark.parametrize('field', trials.PAPER_HTTP_CHECKS)
def test_http_acceptance_requires_every_paper_pacing_fence(field):
    result = http_report()
    result['paper_pacing'][field] = False
    with pytest.raises(ValueError, match='HTTP_PAPER_FENCES'): trials._validate('http', result)


@pytest.mark.parametrize('field', ['actual_vault_access','actual_broker_access','ready_for_routing',
    'live_orders_enabled','money_movement_enabled','release_approved'])
def test_http_paper_fixtures_cannot_claim_actual_provider_or_financial_authority(field):
    result = http_report()
    result['paper_pacing'][field] = True
    with pytest.raises(ValueError, match='HTTP_PAPER_AUTHORITY'): trials._validate('http', result)


@pytest.mark.parametrize('value', [True, 4, 6, '5'])
def test_http_paper_evidence_requires_exact_injected_request_count(value):
    result = http_report()
    result['paper_pacing']['injected_get_requests'] = value
    with pytest.raises(ValueError, match='HTTP_PAPER_SCOPE'): trials._validate('http', result)


def test_http_paper_evidence_cannot_be_missing_or_relabelled_as_broker_io():
    result = http_report(); result.pop('paper_pacing')
    with pytest.raises(KeyError): trials._validate('http', result)
    result = http_report(); result['paper_pacing']['scope'] = 'ACTUAL_BROKER'
    with pytest.raises(ValueError, match='HTTP_PAPER_SCOPE'): trials._validate('http', result)


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
    for field in ('cooldown_enforced', 'retry_after_retained', 'authentication_block_retained', 'pending_read_not_auto_released', 'explicit_owned_interruption_retained', 'interrupted_full_day_cooldown',
                  'wal_read_rejected_before_sqlite','wal_write_rejected_before_conversion','wal_rejection_preserved_files'):
        changed = dict(result, **{field:False})
        with pytest.raises(ValueError, match='PACING_FENCES'): trials._validate('pacing', changed)
    for field in ('broker_wide_quota_verified', 'ready_for_routing'):
        with pytest.raises(ValueError, match='PACING_AUTHORITY'): trials._validate('pacing', dict(result, **{field:True}))


def test_activity_acceptance_process_death_profile_is_mandatory_and_cannot_claim_authority():
    from nexus.paper_activity_acceptance_evidence import fixture_report
    result=fixture_report();trials._validate('activity_acceptance',result)
    for field in ('missing_status_created_nothing','precommit_death_retained_unbound_transition',
                  'postcommit_death_replayed_bound_transition','fresh_process_journals_unchanged','fresh_process_vault_silent'):
        changed=dict(result,**{field:False})
        with pytest.raises(ValueError,match='ACTIVITY_ACCEPTANCE_RECOVERY'):trials._validate('activity_acceptance',changed)
    for field in ('actual_provider_access','actual_vault_access','source_attested','activity_coverage_verified',
                  'fee_execution_links_verified','broker_account_reconciled','spendable_funds_verified',
                  'ready_for_routing','broker_connected','live_orders_enabled','money_movement_enabled','release_approved'):
        changed=dict(result,**{field:True})
        with pytest.raises(ValueError,match='ACTIVITY_ACCEPTANCE_AUTHORITY'):trials._validate('activity_acceptance',changed)


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


def test_collection_trial_keeps_page_cap_and_cursor_evidence_without_coverage_authority():
    from nexus.activity_collection_evidence import fixture_report
    result=fixture_report();trials._validate('collection',result)
    for field in ('source_attested','activity_coverage_verified','ready_for_routing','automatic_continuation'):
        with pytest.raises(ValueError,match='COLLECTION_AUTHORITY'): trials._validate('collection',dict(result,**{field:True}))
    with pytest.raises(ValueError,match='COLLECTION_SCOPE'): trials._validate('collection',dict(result,cursor_chain_preserved=False))
    with pytest.raises(ValueError,match='COLLECTION_RETENTION'): trials._validate('collection',dict(result,retained_pages=1))


def test_retained_balance_trial_rejects_stale_or_authorized_gross_fee_inference():
    from nexus.retained_balance_review import fixture_report
    result=fixture_report();trials._validate('retained_balance',result)
    changed=copy.deepcopy(result);changed['stale_head_rejected']=False
    with pytest.raises(ValueError,match='RETAINED_BALANCE_FENCES'): trials._validate('retained_balance',changed)
    for field in ('source_coverage_verified','fee_coverage_complete','fee_execution_links_verified','holds_reconciled',
                  'broker_account_reconciled','spendable_funds_verified','ready_for_routing','ledger_applied','live_orders_enabled','admission_reserved'):
        changed=copy.deepcopy(result);changed['review'][field]=True
        with pytest.raises(ValueError,match='RETAINED_BALANCE_AUTHORITY'): trials._validate('retained_balance',changed)
    for field in ('fee_debits_allocated_to_interval','fee_execution_links_verified','fee_reserved_funding_verified'):
        changed=copy.deepcopy(result);changed['review']['gross_fill_review'][field]=True
        with pytest.raises(ValueError,match='RETAINED_BALANCE_AUTHORITY'): trials._validate('retained_balance',changed)


def test_inspection_inventory_trial_preserves_unknown_units_and_denies_authority():
    result=trials._inspection_inventory_trial();trials._validate('inspection_inventory',result)
    changed=copy.deepcopy(result);changed['observations'][1]['observed_inventory']['cash']='1000'
    with pytest.raises(ValueError,match='INVENTORY_UNKNOWN_FIELDS'): trials._validate('inspection_inventory',changed)
    changed=copy.deepcopy(result);changed['observations'][2]['observed_inventory']['btc_available']='2'
    with pytest.raises(ValueError,match='INVENTORY_UNKNOWN_FIELDS'): trials._validate('inspection_inventory',changed)
    for field in ('ready_for_routing','spendable_funds_verified','broker_account_reconciled','broker_connected',
                  'ledger_applied','live_orders_enabled','atomic_snapshot'):
        changed=copy.deepcopy(result);changed['observations'][0][field]=True
        with pytest.raises(ValueError,match='INVENTORY_AUTHORITY'): trials._validate('inspection_inventory',changed)


def test_inventory_trial_rejects_missing_or_corrupt_portable_evidence():
    result=trials._inspection_inventory_trial();trials._validate('inspection_inventory',result)
    changed=copy.deepcopy(result);changed['portable_tamper_rejected']=False
    with pytest.raises(ValueError,match='INVENTORY_EXPORT_FENCES'):trials._validate('inspection_inventory',changed)
    changed=copy.deepcopy(result);changed['observations'][0]['portable_observation']['manifest_sha256']='0'*64
    with pytest.raises(ValueError,match='PAPER_OBSERVATION_INVALID'):trials._validate('inspection_inventory',changed)


def test_portable_observation_review_trial_rejects_boundary_or_authority_promotion():
    from nexus.paper_observation_review import fixture_report
    result=fixture_report();trials._validate('observation_review',result)
    changed=copy.deepcopy(result);changed['boundary_review']['arithmetic_evaluated']=True
    with pytest.raises(ValueError,match='OBSERVATION_REVIEW_BOUNDARY'):trials._validate('observation_review',changed)
    for field,value in (('encrypted_pair_review_matches',False),('decrypted_file_writes',True)):
        changed=copy.deepcopy(result);changed[field]=value
        with pytest.raises(ValueError,match='OBSERVATION_REVIEW_ENCRYPTED_INPUTS'):trials._validate('observation_review',changed)
    changed=copy.deepcopy(result);changed['stale_head_rejected']=False
    with pytest.raises(ValueError,match='OBSERVATION_REVIEW_HEAD'):trials._validate('observation_review',changed)
    for field in ('admission_reserved','source_attested','source_coverage_verified','balance_instants_verified',
        'observation_windows_verified_quiet','atomic_snapshot','fee_coverage_complete','fee_execution_links_verified',
        'holds_reconciled','broker_account_reconciled','spendable_funds_verified','ledger_applied',
        'ready_for_routing','broker_connected','live_orders_enabled','automatic_retry','automatic_continuation'):
        changed=copy.deepcopy(result);changed['review'][field]=True
        with pytest.raises(ValueError,match='OBSERVATION_REVIEW_AUTHORITY'):trials._validate('observation_review',changed)


def test_open_terms_trial_rejects_partial_totals_or_promoted_hold_authority():
    from nexus.paper_open_order_terms import fixture_report, FLAGS
    result=fixture_report();trials._validate('open_order_terms',result)
    changed=copy.deepcopy(result);changed['unsupported']['remaining_buy_limit_principal_cap_usd']='250'
    with pytest.raises(ValueError,match='OPEN_TERMS_UNKNOWN'):trials._validate('open_order_terms',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result);changed['observed'][field]=True
        with pytest.raises(ValueError,match='OPEN_TERMS_AUTHORITY'):trials._validate('open_order_terms',changed)


def test_encrypted_observation_profile_cannot_claim_password_storage_or_replaced_originals():
    from nexus.encrypted_observation import fixture_report
    result=fixture_report();trials._validate('encrypted_observation',result)
    for field in ('password_saved','original_plaintext_deleted','vault_accessed','external_connections','ready_for_routing','live_orders_enabled'):
        changed=copy.deepcopy(result);changed[field]=True
        with pytest.raises(ValueError,match='ENCRYPTED_OBSERVATION_AUTHORITY'):trials._validate('encrypted_observation',changed)


def test_security_policy_files_are_bound_to_source_fingerprint(tmp_path):
    for directory in ('nexus','tests','tools','.github/workflows','dashboard','installer'):(tmp_path/directory).mkdir(parents=True,exist_ok=True)
    for name in ('pyproject.toml','dashboard/portal_preview.html','installer/NexusOperative.iss'):(tmp_path/name).write_text('fixture')
    for name in ('.gitignore','.gitleaks.toml','.github/CODEOWNERS','.github/dependabot.yml'):
        before=trials.source_digest(tmp_path);path=tmp_path/name;path.write_text('policy')
        added=trials.source_digest(tmp_path);assert before!=added
        path.write_text('updated policy');assert trials.source_digest(tmp_path)!=added


def test_forward_trial_coverage_cannot_promote_injected_evidence_to_market_acceptance():
    from pathlib import Path
    from nexus.release_roadmap import REVISION
    from nexus.forward_trial_review import FLAGS
    path=Path(__file__).resolve().parents[1]/'validation'/('release_trials_'+REVISION.replace('.','_')+'.json')
    result=json.loads(path.read_text())['profiles']['forward'];trials._validate('forward',result)
    changed=copy.deepcopy(result);changed['recovered']['coverage_review']['declared_public_covered_seconds']='1'
    with pytest.raises(ValueError,match='FORWARD_COVERAGE_SCOPE'):trials._validate('forward',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result);changed['recovered']['coverage_review'][field]=True
        with pytest.raises(ValueError,match='FORWARD_COVERAGE_AUTHORITY'):trials._validate('forward',changed)
