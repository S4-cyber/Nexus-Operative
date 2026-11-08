"""Actual local HTTP acceptance and rejection of falsely successful evidence."""
import copy
import io
import os
import subprocess
import sys
import json
from urllib.error import HTTPError

import pytest
from nexus import release_trials as trials

HTTP_TRIAL_SUBPROCESS_TIMEOUT_SECONDS = 120


class _ResettingBody:
    error = ConnectionResetError

    def read(self, size):
        raise self.error('fixture reset after response headers')


@pytest.mark.parametrize('error', [ConnectionAbortedError, ConnectionResetError])
def test_http_trial_tolerates_reset_only_for_precredential_denial(error):
    body = _ResettingBody(); body.error = error
    assert trials._http_trial_response_body(
        body,tolerate_precredential_denial_reset=True) == b'{}'
    body = _ResettingBody(); body.error = error
    with pytest.raises(error):
        trials._http_trial_response_body(body)


class _AbortThenDeny:
    def __init__(self, error=ConnectionAbortedError):
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if len(self.requests) == 1:
            raise self.error('fixture aborted before response headers')
        raise HTTPError(request.full_url, 403, 'denied',
            {'Cache-Control':'no-store'}, io.BytesIO(b'{"detail":"LOCAL_PAPER_PORTAL_REQUIRED"}'))


@pytest.mark.parametrize('error', [ConnectionAbortedError, ConnectionResetError])
def test_http_trial_reprobes_only_scoped_precredential_denial(error):
    from urllib.request import Request
    request = Request('http://127.0.0.1:1/private', data=b'secretless-fixture', method='POST')
    probe = Request(request.full_url, data=b'', method='POST')
    opener = _AbortThenDeny(error)
    response = trials._http_trial_open(opener, request, timeout=15,
        precredential_denial_probe=probe)
    assert response.code == 403 and response.headers['Cache-Control'] == 'no-store'
    assert [row[0].data for row in opener.requests] == [b'secretless-fixture', b'']
    assert [row[1] for row in opener.requests] == [15, 15]


@pytest.mark.parametrize('error', [ConnectionAbortedError, ConnectionResetError])
def test_http_trial_never_reprobes_unscoped_request(error):
    from urllib.request import Request
    opener = _AbortThenDeny(error)
    request = Request('http://127.0.0.1:1/private', data=b'ordinary-request', method='POST')
    with pytest.raises(error):
        trials._http_trial_open(opener, request, timeout=15)
    assert len(opener.requests) == 1


def test_real_http_process_starts_stopped_without_data_credentials_or_external_io(tmp_path):
    env = {**os.environ, 'NEXUS_PORTABLE_DATA': str(tmp_path)}
    code = 'import json; from nexus.release_trials import _http_trial; print(json.dumps(_http_trial()))'
    # This child runs the complete real-loopback profile and all packaged
    # paper-tool source commands. Keep it bounded but allow loaded Windows CI
    # substantially more process-startup time than the individual operations.
    child = subprocess.run(
        [sys.executable,'-c',code],env=env,capture_output=True,text=True,
        timeout=HTTP_TRIAL_SUBPROCESS_TIMEOUT_SECONDS)
    assert child.returncode == 0, child.stderr
    report = json.loads(child.stdout)
    assert report['passed'] and report['graceful_shutdown'] and report['startup_engine_stopped']
    assert report['private_lifecycle_stop_worker']
    assert report['lifecycle_stop_scope'] == 'DEDICATED_APPLICATION_LIFECYCLE_WORKER'
    assert report['shutdown_fence_order'] == list(trials.APPLICATION_STOP_FENCE_ORDER)
    assert report['external_connections'] == report['credential_operations'] == report['startup_data_files'] == 0
    assert report['transport'] == 'REAL_LOOPBACK_TCP_HTTP'
    assert report['untrusted_host_rejected'] and report['cross_origin_rejected'] and report['api_no_store']
    assert not report['proxy_headers_trusted']
    assert all(report['first_run_defaults'][key] is True for key in trials.FIRST_RUN_HTTP_CHECKS)
    for field in ('default_data_not_created','private_files_unchanged','invalid_command_denied','provider_history_review_checked','provider_history_profile_confusion_denied'):
        altered=copy.deepcopy(report);altered['paper_tools_source'][field]=False
        with pytest.raises(trials.ReleaseTrialError,match='HTTP_PAPER_TOOLS'):trials._validate('http',altered)
    rejected=json.loads(json.dumps(report));rejected['first_run_defaults']['saved_off_preserved']=False
    with pytest.raises(trials.ReleaseTrialError,match='HTTP_FIRST_RUN_DEFAULTS'):trials._validate('http',rejected)
    rejected=copy.deepcopy(report);rejected['private_lifecycle_stop_worker']=False
    with pytest.raises(trials.ReleaseTrialError,match='HTTP_LIFECYCLE'):trials._validate('http',rejected)
    rejected=copy.deepcopy(report)
    rejected['shutdown_fence_order'][0:2]=reversed(rejected['shutdown_fence_order'][0:2])
    with pytest.raises(trials.ReleaseTrialError,match='HTTP_STOP_FENCE'):trials._validate('http',rejected)
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


@pytest.mark.parametrize(('host_os','native_checked','tool_invocations'), [
    ('WINDOWS_NT',False,17),
    ('WINDOWS_NT',True,16),
    ('NON_WINDOWS',True,16),
    ('NON_WINDOWS',False,17),
    ('UNKNOWN',False,16),
])
def test_http_paper_tool_platform_scope_must_be_self_consistent(host_os,native_checked,tool_invocations):
    result=http_report();tools=result['paper_tools_source']
    tools.update(host_os=host_os,native_windows_storage_fault_checked=native_checked,
        tool_invocations=tool_invocations)
    with pytest.raises(ValueError,match='HTTP_PAPER_TOOLS'):trials._validate('http',result)


@pytest.mark.parametrize('profile', trials.PROFILES)
def test_zero_exit_or_generic_success_cannot_certify_a_trial(profile):
    # Incomplete profile results must not turn a green child exit into acceptance.
    with pytest.raises((ValueError, KeyError)): trials._validate(profile, {})


@pytest.mark.parametrize('name', ['broker_connected','live_orders_enabled','release_approved','application_accounts_changed'])
def test_declared_authority_or_user_data_mutation_rejects_report(name):
    with pytest.raises(ValueError,match='UNEXPECTED_AUTHORITY'):
        trials._validate('operations', {'passed':True,'cycles':24,'buy_fills':24,'sell_fills':24,'tier_vetoes':24,
            'history_deleted':False,'old_id_deduplicated':True,name:True})


def test_multi_epoch_order_offload_metrics_are_mandatory():
    result=trials._execute('operations');trials._validate('operations',result)
    offload=result['order_physical_offload']
    for field,value in (('multi_epoch_generations',3),('multi_epoch_orders',47),
                        ('multi_epoch_retired_id_fingerprint_count',47),
                        ('multi_epoch_max_live_commands',5),('multi_epoch_max_source_commands',100)):
        changed={**result,'order_physical_offload':{**offload,field:value}}
        with pytest.raises(ValueError,match='ORDER_OFFLOAD_MULTI_EPOCH'):
            trials._validate('operations',changed)


def test_operations_trial_consumes_exact_durable_scope():
    result=trials._execute('operations');trials._validate('operations',result)
    class ReportMapping(dict):pass
    for changed,code in ((dict(result,unconsumed=True),'OPERATION_PROFILE_SCHEMA'),
        (ReportMapping(result),'OPERATION_PROFILE_SCHEMA')):
        with pytest.raises(ValueError,match=code):trials._validate('operations',changed)
    offload=result['order_physical_offload']
    changed={**result,'order_physical_offload':{**offload,'unconsumed':True}}
    with pytest.raises(ValueError,match='ORDER_OFFLOAD_SCHEMA'):
        trials._validate('operations',changed)
    changed={**result,'order_physical_offload':ReportMapping(offload)}
    with pytest.raises(ValueError,match='ORDER_OFFLOAD_SCHEMA'):
        trials._validate('operations',changed)
    for field,value,code in (('mode','BROKER_OPERATION_DRILL','OPERATION_SCOPE'),
        ('limitations','broader','OPERATION_SCOPE'),('cycles',True,'OPERATION_COUNTS'),
        ('archive_batches',23,'OPERATION_COUNTS'),('archived_orders',47,'OPERATION_COUNTS'),
        ('retained_commands',265,'OPERATION_COUNTS'),
        ('portfolio_reconciled',1,'OPERATION_RECOVERY')):
        changed={**result,field:value}
        with pytest.raises(ValueError,match=code):trials._validate('operations',changed)
    for field,value,code in (('source','PROVIDER_ORDER_OFFLOAD','ORDER_OFFLOAD'),
        ('copy_verified',1,'ORDER_OFFLOAD'),
        ('multi_epoch_generations',True,'ORDER_OFFLOAD_MULTI_EPOCH'),
        ('multi_epoch_max_source_commands',54,'ORDER_OFFLOAD_MULTI_EPOCH'),
        ('legacy_baseline_retired_id_fingerprint_count',1,'ORDER_OFFLOAD_MULTI_EPOCH'),
        ('broker_connected',0,'ORDER_OFFLOAD_AUTHORITY')):
        changed={**result,'order_physical_offload':{**offload,field:value}}
        with pytest.raises(ValueError,match=code):trials._validate('operations',changed)


def test_capacity_trial_consumes_exact_bounded_scope():
    result=trials._execute('capacity');trials._validate('capacity',result)
    class ReportMapping(dict):pass
    for changed,code in ((dict(result,unconsumed=True),'CAPACITY_PROFILE_SCHEMA'),
        (ReportMapping(result),'CAPACITY_PROFILE_SCHEMA')):
        with pytest.raises(ValueError,match=code):trials._validate('capacity',changed)
    cases=[]
    changed=copy.deepcopy(result);changed['treatments']=tuple(changed['treatments'])
    cases.append((changed,'CAPACITY_TREATMENT_SCHEMA'))
    changed=copy.deepcopy(result);changed['treatments'][0]['unconsumed']=True
    cases.append((changed,'CAPACITY_TREATMENT_SCHEMA'))
    changed=copy.deepcopy(result);changed['treatments'][0]=ReportMapping(changed['treatments'][0])
    cases.append((changed,'CAPACITY_TREATMENT_SCHEMA'))
    changed=copy.deepcopy(result);changed['treatments'][0]['cycles']=True
    cases.append((changed,'CAPACITY_TREATMENT_SCOPE'))
    changed=copy.deepcopy(result);changed['treatments'][0]['recovery_diagnostics']['extra']=0
    cases.append((changed,'CAPACITY_RECOVERY_SCOPE'))
    changed=copy.deepcopy(result);changed['treatments'][0]['commit_return_latency']['p50_ms']=0
    cases.append((changed,'CAPACITY_TREATMENT_TIMING'))
    changed=copy.deepcopy(result);changed['burst_treatment']['unconsumed']=True
    cases.append((changed,'CAPACITY_BURST_SCHEMA'))
    changed=copy.deepcopy(result);changed['atomic_burst_treatment']['atomic_order_transactions']=False
    cases.append((changed,'CAPACITY_BURST_SCOPE'))
    changed=copy.deepcopy(result);changed['burst_treatment']['admitted']=True
    cases.append((changed,'CAPACITY_BURST_COUNTS'))
    changed=copy.deepcopy(result);changed['burst_treatment']['queue_wait_latency']['p99_ms']=float('inf')
    cases.append((changed,'CAPACITY_BURST_TIMING'))
    changed=copy.deepcopy(result);changed['execution_quality']['unconsumed']=True
    cases.append((changed,'EXECUTION_QUALITY_SCHEMA'))
    changed=copy.deepcopy(result);changed['execution_quality']['passed']=1
    cases.append((changed,'EXECUTION_QUALITY'))
    changed=copy.deepcopy(result);changed['paper_execution_quality']=ReportMapping(changed['paper_execution_quality'])
    cases.append((changed,'PAPER_EXECUTION_QUALITY_SCHEMA'))
    changed=copy.deepcopy(result);changed['paper_execution_quality']['broker_connected']=0
    cases.append((changed,'PAPER_EXECUTION_QUALITY_AUTHORITY'))
    for changed,code in cases:
        with pytest.raises(ValueError,match=code):trials._validate('capacity',changed)


def test_source_fingerprint_tracks_source_test_and_workflow_changes(tmp_path):
    for name in ('nexus','tests','tools','.github/workflows','dashboard','installer'):
        (tmp_path/name).mkdir(parents=True)
    for name in ('pyproject.toml','dashboard/portal_preview.html','installer/NexusOperative.iss','nexus/core.py',
                 'tests/test_core.py','tools/installer.ps1','.github/workflows/test.yml'):
        (tmp_path/name).write_text('original')
    original = trials.source_digest(tmp_path)
    (tmp_path/'report.json').write_text('generated output')
    assert trials.source_digest(tmp_path) == original
    for name in ('nexus/core.py','tests/test_core.py','tools/installer.ps1','.github/workflows/test.yml'):
        (tmp_path/name).write_text('changed')
        assert trials.source_digest(tmp_path) != original
        (tmp_path/name).write_text('original')


def test_source_fingerprint_is_stable_across_git_checkout_line_endings(tmp_path):
    lf=tmp_path/'lf';crlf=tmp_path/'crlf'
    for root in (lf,crlf):
        for name in ('nexus','tests','tools','.github/workflows','dashboard','installer'):
            (root/name).mkdir(parents=True)
    names=('pyproject.toml','dashboard/portal_preview.html','installer/NexusOperative.iss',
        'nexus/core.py','tests/test_core.py','tools/installer.ps1',
        '.github/workflows/test.yml')
    for name in names:
        (lf/name).write_bytes(b'first\nsecond\n')
        (crlf/name).write_bytes(b'first\r\nsecond\r\n')
    assert trials.source_digest(lf)==trials.source_digest(crlf)
    (crlf/'nexus/core.py').write_bytes(b'first\rsecond\r\n')
    assert trials.source_digest(lf)!=trials.source_digest(crlf)


def test_nonzero_child_failure_is_sanitized_and_never_reported_as_passed(monkeypatch):
    monkeypatch.setattr(trials.subprocess,'run',lambda *a,**k: subprocess.CompletedProcess(a,1,'','SECRET_MUST_NOT_ESCAPE'))
    with pytest.raises(ValueError,match='FORWARD_CHILD_FAILED') as error: trials.run()
    assert 'SECRET' not in str(error.value)


def test_nonzero_child_preserves_only_nested_fixed_failure_code(monkeypatch):
    child = subprocess.CompletedProcess([], 1, 'PRIVATE_STDOUT',
        'private path\nnexus.release_trials.ReleaseTrialError: '
        'RELEASE_TRIAL_POST_ACTIVITY_REVIEW_FENCES\n'
        'ValueError: RELEASE_TRIAL_SECRET_MUST_NOT_ESCAPE')
    monkeypatch.setattr(trials.subprocess, 'run', lambda *a, **k: child)
    with pytest.raises(ValueError,
            match='^RELEASE_TRIAL_POST_ACTIVITY_REVIEW_FENCES$') as error:
        trials.run()
    assert 'PRIVATE' not in str(error.value) and 'SECRET' not in str(error.value)


def test_release_trial_failure_diagnostics_allow_only_fixed_codes():
    assert trials._safe_failure_code(trials.ReleaseTrialError('RELEASE_TRIAL_ACTIVITY_ACCEPTANCE_CHILD_FAILED')) == \
        'RELEASE_TRIAL_ACTIVITY_ACCEPTANCE_CHILD_FAILED'
    for error in (ValueError('RELEASE_TRIAL_SECRET_MUST_NOT_ESCAPE'), OSError('C:/private/account.sqlite3'),
                  KeyError('credential-name')):
        assert trials._safe_failure_code(error) == 'RELEASE_TRIAL_INTERNAL_ERROR'


def test_release_trial_timeout_is_profile_specific_and_sanitized(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'], stderr='SECRET_MUST_NOT_ESCAPE')
    monkeypatch.setattr(trials.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='^RELEASE_TRIAL_FORWARD_CHILD_TIMEOUT$') as error:
        trials.run()
    assert 'SECRET' not in str(error.value)


@pytest.mark.parametrize(('error','expected'), [
    (trials.ReleaseTrialError('RELEASE_TRIAL_ACTIVITY_ACCEPTANCE_CHILD_FAILED'),
     'Release trials failed: RELEASE_TRIAL_ACTIVITY_ACCEPTANCE_CHILD_FAILED\n'),
    (OSError('C:/private/account.sqlite3 SECRET_MUST_NOT_ESCAPE'),
     'Release trials failed: RELEASE_TRIAL_INTERNAL_ERROR\n'),
])
def test_release_trial_cli_emits_only_sanitized_failure_codes(monkeypatch, capsys, error, expected):
    monkeypatch.setattr(trials, 'run', lambda: (_ for _ in ()).throw(error))
    monkeypatch.setattr(sys, 'argv', ['release_trials'])
    with pytest.raises(SystemExit) as stopped:
        trials.main()
    assert stopped.value.code == 1 and capsys.readouterr().err == expected


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


def test_pacing_profile_requires_single_attempt_submission_transport_fences():
    result=trials._pacing_trial();trials._validate('pacing',result)
    for field in ('marker_committed_before_post','exactly_one_post','response_retained',
                  'account_and_position_capacity_resampled',
                  'post_reservation_risk_resampled',
                  'post_transport_credentials_rechecked',
                  'request_and_post_budget_store_bindings_pinned',
                  'request_and_post_budget_file_identities_pinned',
                  'post_reservation_durable_heads_rechecked',
                  'response_observation_head_bound_to_post_budget',
                  'post_budget_reservation_cold_replayed',
                  'request_marker_cold_replayed',
                  'reservation_admission_window_bound',
                  'request_marker_admission_window_bound',
                  'response_observation_cold_replayed',
                  'response_budget_completion_cold_replayed',
                  'post_response_persistence_stop_independent',
                  'post_response_local_completion_stop_independent',
                  'response_observation_completion_bracketed',
                  'response_completion_window_bound',
                  'not_sent_completion_window_bound',
                  'outcome_unknown_completion_window_bound',
                  'safety_closure_request_head_bracketed',
                  'safety_closure_cold_replayed',
                  'alpaca_post_budget_scope_required',
                  'late_risk_change_blocked_before_reservation',
                  'post_reservation_risk_change_blocked_before_post',
                  'transport_loss_preserved_for_original_id_lookup','repeat_submission_blocked',
                  'output_never_sendable'):
        changed=copy.deepcopy(result);changed['single_attempt_submission_transport'][field]=False
        with pytest.raises(ValueError,match='SUBMISSION_TRANSPORT_FENCES'):
            trials._validate('pacing',changed)
    for field in ('actual_provider_access','actual_vault_access','broker_account_reconciled',
                  'spendable_funds_verified','ready_for_routing','live_orders_enabled',
                  'money_movement_enabled','release_approved'):
        changed=copy.deepcopy(result);changed['single_attempt_submission_transport'][field]=True
        with pytest.raises(ValueError,match='SUBMISSION_TRANSPORT_AUTHORITY'):
            trials._validate('pacing',changed)


def test_pacing_profile_requires_resolved_unknown_post_recovery_fences():
    result=trials._pacing_trial();trials._validate('pacing',result)
    for field in ('unresolved_request_denied','exact_terminal_resolution_bound',
                  'request_writer_excluded_through_commit',
                  'request_path_identity_rechecked',
                  'request_file_fingerprint_rechecked',
                  'request_replay_fingerprint_rechecked_after_load',
                  'request_proof_final_before_budget_commit',
                  'request_proof_rechecked_after_budget_receipts',
                  'request_snapshot_rechecked_on_exit',
                  'post_budget_ownership_rechecked_on_exit',
                  'recovery_acknowledgement_cold_replayed',
                  'post_budget_file_identity_rechecked',
                  'post_budget_file_fingerprint_rechecked',
                  'post_budget_file_set_rechecked_on_return',
                  'original_request_not_rearmed',
                  'unknown_history_preserved','cooldown_preserved'):
        changed=copy.deepcopy(result);changed['resolved_unknown_post_recovery'][field]=False
        with pytest.raises(ValueError,match='POST_RECOVERY_FENCES'):
            trials._validate('pacing',changed)
    for field in ('actual_provider_access','actual_vault_access','broker_account_reconciled',
                  'spendable_funds_verified','ready_for_routing','live_orders_enabled',
                  'money_movement_enabled','release_approved'):
        changed=copy.deepcopy(result);changed['resolved_unknown_post_recovery'][field]=True
        with pytest.raises(ValueError,match='POST_RECOVERY_AUTHORITY'):
            trials._validate('pacing',changed)


def test_pacing_profile_requires_provider_read_recovery_fences():
    result=trials._pacing_trial();trials._validate('pacing',result)
    for field in ('exact_review_block_bound',
                  'recovery_acknowledgement_cold_replayed',
                  'provider_budget_file_identity_rechecked',
                  'provider_budget_file_fingerprint_rechecked',
                  'provider_budget_file_set_rechecked_on_return',
                  'provider_budget_ownership_rechecked_on_exit',
                  'blocked_history_preserved','cooldown_preserved',
                  'session_capacity_not_reclaimed'):
        changed=copy.deepcopy(result)
        changed['provider_read_budget_recovery'][field]=False
        with pytest.raises(ValueError,match='PROVIDER_READ_RECOVERY_FENCES'):
            trials._validate('pacing',changed)
    for field in ('actual_provider_access','actual_vault_access',
                  'broker_account_reconciled','spendable_funds_verified',
                  'ready_for_routing','live_orders_enabled',
                  'money_movement_enabled','release_approved'):
        changed=copy.deepcopy(result)
        changed['provider_read_budget_recovery'][field]=True
        with pytest.raises(ValueError,match='PROVIDER_READ_RECOVERY_AUTHORITY'):
            trials._validate('pacing',changed)


def test_pacing_profile_requires_account_read_recovery_fences():
    result=trials._pacing_trial();trials._validate('pacing',result)
    from nexus.paper_read_pacing_recovery import RECOVERY_CHECKS,RECOVERY_FLAGS
    for field in RECOVERY_CHECKS:
        changed=copy.deepcopy(result)
        changed['account_read_pacing_recovery'][field]=False
        with pytest.raises(ValueError,match='ACCOUNT_READ_RECOVERY_FENCES'):
            trials._validate('pacing',changed)
    for field in RECOVERY_FLAGS:
        changed=copy.deepcopy(result)
        changed['account_read_pacing_recovery'][field]=True
        with pytest.raises(ValueError,match='ACCOUNT_READ_RECOVERY_AUTHORITY'):
            trials._validate('pacing',changed)


def test_pacing_profile_requires_provider_post_history_review_fences():
    from nexus.paper_provider_post_review import FLAGS
    result=trials._pacing_trial();trials._validate('pacing',result)
    for field in ('exact_multi_account_history','every_attempt_bound','every_outcome_bound',
                  'exact_terminal_recovery_history',
                  'journal_file_fingerprints_rechecked',
                  'semantic_reader_failures_rechecked',
                  'outcome_disagreement_requires_review','changed_head_denied',
                  'omitted_account_requires_review','output_never_authoritative'):
        changed=copy.deepcopy(result);changed['provider_post_history_review'][field]=False
        with pytest.raises(ValueError,match='POST_HISTORY_REVIEW_FENCES'):
            trials._validate('pacing',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result);changed['provider_post_history_review'][field]=True
        with pytest.raises(ValueError,match='POST_HISTORY_REVIEW_AUTHORITY'):
            trials._validate('pacing',changed)


def test_pacing_profile_requires_provider_read_history_review_fences():
    from nexus.paper_provider_read_review import FLAGS
    result=trials._pacing_trial();trials._validate('pacing',result)
    for field in ('exact_provider_account_history','every_session_matched',
                  'all_five_purposes_observed','changed_head_denied',
                  'journals_unchanged','split_recovery_requires_both_sides',
                  'split_recovery_order_independent',
                  'pending_histories_never_promote',
                  'pending_account_cannot_hide_behind_complete_peer',
                  'incomplete_account_cannot_hide_behind_complete_peer',
                  'multi_account_reader_failure_rechecks_peer',
                  'retained_receipt_source_mutation_denied',
                  'retained_receipt_acknowledgement_replayed',
                  'retained_receipt_recovery_inspection_preserves_process_control',
                  'retained_receipt_read_finalization_preserves_process_control',
                  'retained_receipt_write_finalization_preserves_process_control',
                  'retained_receipt_fresh_failure_recoverable',
                  'retained_receipt_initialization_failure_recoverable',
                  'retained_receipt_commit_flush_failure_recoverable',
                  'retained_receipt_commit_sync_preserves_process_control',
                  'retained_receipt_commit_fingerprint_change_recoverable',
                  'retained_receipt_commit_descriptor_tamper_recoverable',
                  'retained_receipt_inspection_binding_pinned',
                  'retained_receipt_connection_binding_pinned',
                  'retained_receipt_connection_content_pinned',
                  'retained_receipt_connection_content_continuity_pinned',
                  'retained_receipt_write_connection_pinned',
                  'retained_receipt_write_connection_content_pinned',
                  'retained_receipt_write_connection_content_continuity_pinned',
                  'retained_receipt_initialization_connection_pinned',
                  'retained_receipt_write_setup_content_pinned',
                  'retained_receipt_connection_close_content_pinned',
                  'retained_receipt_close_error_requires_explicit_recovery',
                  'retained_receipt_read_close_error_requires_explicit_retry',
                  'retained_receipt_parent_descriptor_boundary_respected',
                  'journal_file_fingerprints_rechecked',
                  'semantic_reader_failures_rechecked','output_never_authoritative'):
        changed=copy.deepcopy(result);changed['provider_read_history_review'][field]=False
        with pytest.raises(ValueError,match='PROVIDER_READ_HISTORY_FENCES'):
            trials._validate('pacing',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result);changed['provider_read_history_review'][field]=True
        with pytest.raises(ValueError,match='PROVIDER_READ_HISTORY_AUTHORITY'):
            trials._validate('pacing',changed)


def test_pacing_profile_requires_account_read_history_review_fences():
    from nexus.paper_read_review import CHECKS,FLAGS
    result=trials._pacing_trial();trials._validate('pacing',result)
    for field in CHECKS:
        changed=copy.deepcopy(result)
        changed['account_read_history_review'][field]=False
        with pytest.raises(ValueError,match='ACCOUNT_READ_HISTORY_FENCES'):
            trials._validate('pacing',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result)
        changed['account_read_history_review'][field]=True
        with pytest.raises(ValueError,match='ACCOUNT_READ_HISTORY_AUTHORITY'):
            trials._validate('pacing',changed)


def test_pacing_profile_requires_provider_post_activity_review_fences():
    from nexus.paper_provider_post_activity_review import FLAGS
    result=trials._pacing_trial();trials._validate('pacing',result)
    for field in ('recovered_unknown_bound_to_accepted_activity',
                  'not_sent_bound_without_activity',
                  'unresolved_unknown_requires_review','journals_unchanged',
                  'journal_file_fingerprints_rechecked',
                  'semantic_reader_failures_rechecked',
                  'output_never_authoritative'):
        changed=copy.deepcopy(result);changed['provider_post_activity_review'][field]=False
        with pytest.raises(ValueError,match='POST_ACTIVITY_REVIEW_FENCES'):
            trials._validate('pacing',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result);changed['provider_post_activity_review'][field]=True
        with pytest.raises(ValueError,match='POST_ACTIVITY_REVIEW_AUTHORITY'):
            trials._validate('pacing',changed)


def test_activity_acceptance_process_death_profile_is_mandatory_and_cannot_claim_authority():
    from nexus.paper_activity_acceptance_evidence import (
        PUBLIC_FIXTURE_KEYS, fixture_report)
    result=fixture_report();trials._validate('activity_acceptance',result)
    assert set(result)==PUBLIC_FIXTURE_KEYS
    assert result['postcommit_latest_content_replayed'] is True
    assert result['postcommit_all_content_replayed'] is True
    assert result['accepted_transitions_after_commit']==2
    for field in ('missing_status_created_nothing','precommit_death_retained_unbound_transition',
                  'postcommit_death_replayed_bound_transition','precommit_rollback_journal_observed',
                  'fresh_process_journals_unchanged','fresh_process_vault_silent',
                  'postcommit_all_content_replayed','postcommit_all_pacing_request_targets_bound',
                  'postcommit_all_normalized_results_bound','acceptance_store_binding_pinned',
                  'acceptance_object_scope_pinned','acceptance_cleanup_path_pinned',
                  'acceptance_connection_binding_pinned','acceptance_connection_content_pinned',
                  'acceptance_read_close_requires_explicit_retry',
                  'acceptance_connection_content_continuity_pinned',
                  'acceptance_write_binding_pinned','acceptance_write_ack_replayed',
                  'acceptance_write_connection_pinned',
                  'acceptance_write_connection_content_pinned',
                  'acceptance_write_connection_content_continuity_pinned',
                  'acceptance_write_file_identity_pinned',
                  'acceptance_write_committed_content_pinned',
                  'acceptance_commit_durability_replayed',
                  'acceptance_commit_sync_preserves_process_control',
                  'acceptance_read_finalization_preserves_process_control',
                  'acceptance_write_finalization_preserves_process_control',
                  'acceptance_write_close_requires_explicit_recovery',
                  'acceptance_initialization_identity_continuity_pinned',
                  'acceptance_uncommitted_initialization_replayed',
                  'acceptance_rebind_boundary_pinned',
                  'legacy_normalized_result_not_invented','legacy_normalized_history_preserved',
                  'changed_creation_window_targets_refused','changed_cursor_target_refused',
                  'request_target_disagreement_history_preserved'):
        changed=dict(result,**{field:False})
        with pytest.raises(ValueError,match='ACTIVITY_ACCEPTANCE_RECOVERY'):trials._validate('activity_acceptance',changed)
    for field in ('actual_provider_access','actual_vault_access','source_attested','activity_coverage_verified',
                  'fee_execution_links_verified','broker_account_reconciled','spendable_funds_verified',
                  'ready_for_routing','broker_connected','live_orders_enabled','money_movement_enabled','release_approved'):
        changed=dict(result,**{field:True})
        with pytest.raises(ValueError,match='ACTIVITY_ACCEPTANCE_AUTHORITY'):trials._validate('activity_acceptance',changed)
    changed=dict(result,private_extension=True)
    with pytest.raises(ValueError,match='ACTIVITY_ACCEPTANCE_SCHEMA'):
        trials._validate('activity_acceptance',changed)
    class ActivityAcceptanceMapping(dict):
        pass
    changed=ActivityAcceptanceMapping(result)
    with pytest.raises(ValueError,match='ACTIVITY_ACCEPTANCE_SCHEMA'):
        trials._validate('activity_acceptance',changed)
    changed=dict(result,limitations='synthetic evidence promoted')
    with pytest.raises(ValueError,match='ACTIVITY_ACCEPTANCE_SCOPE'):
        trials._validate('activity_acceptance',changed)


def test_request_activity_acceptance_is_mandatory_in_activity_profile():
    from nexus.paper_request_activity_acceptance import PUBLIC_FIXTURE_KEYS
    result=trials._execute('activity');trials._validate('activity',result)
    accepted=result['request_activity_acceptance']
    assert type(accepted) is dict
    assert frozenset(accepted)==PUBLIC_FIXTURE_KEYS
    assert accepted['accepted_history_bound'] and accepted['unbound_activity_rejected']
    for key in ('accepted_history_bound','unbound_activity_rejected',
                'semantic_reader_failures_rechecked',
                'accepted_pacing_request_targets_bound'):
        changed=copy.deepcopy(result);changed['request_activity_acceptance'][key]=False
        with pytest.raises(ValueError,match='REQUEST_ACTIVITY_ACCEPTANCE'):trials._validate('activity',changed)
    changed=copy.deepcopy(result);changed['request_activity_acceptance']['ready_for_routing']=True
    with pytest.raises(ValueError,match='REQUEST_ACTIVITY_ACCEPTANCE_AUTHORITY'):trials._validate('activity',changed)
    changed=copy.deepcopy(result);changed['request_activity_acceptance']['private_extension']=True
    with pytest.raises(ValueError,match='REQUEST_ACTIVITY_ACCEPTANCE_SCHEMA'):
        trials._validate('activity',changed)
    class RequestActivityAcceptanceMapping(dict):
        pass
    changed=copy.deepcopy(result)
    changed['request_activity_acceptance']=RequestActivityAcceptanceMapping(accepted)
    with pytest.raises(ValueError,match='REQUEST_ACTIVITY_ACCEPTANCE_SCHEMA'):
        trials._validate('activity',changed)


def test_activity_profile_requires_exact_public_schemas():
    from nexus.broker_activity_review import (PUBLIC_FIXTURE_KEYS,
        REVIEW_PUBLIC_KEYS)
    from nexus.broker_request_activity_review import (
        PUBLIC_FIXTURE_KEYS as LINK_PUBLIC_FIXTURE_KEYS)
    result=trials._execute('activity');trials._validate('activity',result)
    assert type(result) is dict
    assert frozenset(result)==PUBLIC_FIXTURE_KEYS|frozenset((
        'request_activity_link','request_activity_acceptance'))
    assert type(result['request_activity_link']) is dict
    assert frozenset(result['request_activity_link'])==LINK_PUBLIC_FIXTURE_KEYS
    assert type(result['review']) is dict
    assert frozenset(result['review'])==REVIEW_PUBLIC_KEYS
    changed=copy.deepcopy(result);changed['private_extension']=True
    with pytest.raises(ValueError,match='ACTIVITY_PROFILE_SCHEMA'):
        trials._validate('activity',changed)
    class ActivityProfileMapping(dict):
        pass
    with pytest.raises(ValueError,match='ACTIVITY_PROFILE_SCHEMA'):
        trials._validate('activity',ActivityProfileMapping(result))
    changed=copy.deepcopy(result);changed['request_activity_link']['private_extension']=True
    with pytest.raises(ValueError,match='REQUEST_ACTIVITY_LINK_SCHEMA'):
        trials._validate('activity',changed)
    class RequestActivityLinkMapping(dict):
        pass
    changed=copy.deepcopy(result)
    changed['request_activity_link']=RequestActivityLinkMapping(
        result['request_activity_link'])
    with pytest.raises(ValueError,match='REQUEST_ACTIVITY_LINK_SCHEMA'):
        trials._validate('activity',changed)
    changed=copy.deepcopy(result);changed['review']['private_extension']=True
    with pytest.raises(ValueError,match='ACTIVITY_REVIEW_SCHEMA'):
        trials._validate('activity',changed)
    class ActivityReviewMapping(dict):
        pass
    changed=copy.deepcopy(result)
    changed['review']=ActivityReviewMapping(result['review'])
    with pytest.raises(ValueError,match='ACTIVITY_REVIEW_SCHEMA'):
        trials._validate('activity',changed)
    changed=copy.deepcopy(result)
    changed['review']['limitations']='synthetic evidence promoted'
    with pytest.raises(ValueError,match='ACTIVITY_REVIEW_SCOPE'):
        trials._validate('activity',changed)


def test_reconciliation_acceptance_profile_requires_exact_binding_and_no_authority():
    from nexus.paper_reconciliation_acceptance import fixture_report
    result=trials._execute('reconciliation_acceptance');trials._validate('reconciliation_acceptance',result)
    for path,value,code in (
        (('account_evidence_bundle','changed_balance_difference_preserved'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','zero_fee_records_not_promoted'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','current_head_review_matches'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','public_summary_checked'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','stability_guard_bound'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','guarded_current_head_review_matches'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','guarded_public_summary_checked'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','guarded_declared_source_summary_checked'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','guarded_collection_timing_summary_checked'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','journal_file_fingerprints_rechecked'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','semantic_reader_failures_rechecked'),False,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','private_extension'),True,'ACCOUNT_EVIDENCE_BUNDLE'),
        (('account_evidence_bundle','ready_for_routing'),True,'ACCOUNT_EVIDENCE_BUNDLE_AUTHORITY'),
        (('exact_account_response','private_extension'),True,'EXACT_ACCOUNT_RESPONSE'),
        (('exact_activity_response','private_extension'),True,'EXACT_ACTIVITY_RESPONSE'),
        (('provider_post_reconciliation','exact_post_activity_bound'),False,'PROVIDER_POST_RECONCILIATION'),
        (('provider_post_reconciliation','guarded_account_evidence_bound'),False,'PROVIDER_POST_RECONCILIATION'),
        (('provider_post_reconciliation','gross_only_residual_checked'),False,'PROVIDER_POST_RECONCILIATION'),
        (('provider_post_reconciliation','journal_file_fingerprints_rechecked'),False,'PROVIDER_POST_RECONCILIATION'),
        (('provider_post_reconciliation','semantic_reader_failures_rechecked'),False,'PROVIDER_POST_RECONCILIATION'),
        (('provider_post_reconciliation','unresolved_unknown_rejected'),False,'PROVIDER_POST_RECONCILIATION'),
        (('provider_post_reconciliation','private_extension'),True,'PROVIDER_POST_RECONCILIATION'),
        (('provider_post_reconciliation','ready_for_routing'),True,'PROVIDER_POST_RECONCILIATION_AUTHORITY'),
        (('provider_post_fee_references','accepted_receipt_partition_complete'),False,'PROVIDER_POST_FEE_REFERENCES'),
        (('provider_post_fee_references','all_retained_fees_explicitly_referenced'),False,'PROVIDER_POST_FEE_REFERENCES'),
        (('provider_post_fee_references','references_resolve_to_accepted_original_fills'),False,'PROVIDER_POST_FEE_REFERENCES'),
        (('provider_post_fee_references','journal_file_fingerprints_rechecked'),False,'PROVIDER_POST_FEE_REFERENCES'),
        (('provider_post_fee_references','semantic_reader_failures_rechecked'),False,'PROVIDER_POST_FEE_REFERENCES'),
        (('provider_post_fee_references','unreferenced_fee_rejected'),False,'PROVIDER_POST_FEE_REFERENCES'),
        (('provider_post_fee_references','private_extension'),True,'PROVIDER_POST_FEE_REFERENCES'),
        (('provider_post_fee_references','fee_execution_links_verified'),True,'PROVIDER_POST_FEE_REFERENCES_AUTHORITY'),
        (('provider_post_known_fee_reserve','retained_known_fee_debits_covered'),False,'PROVIDER_POST_KNOWN_FEE_RESERVE'),
        (('provider_post_known_fee_reserve','known_fee_adjusted_closing_balances_match'),False,'PROVIDER_POST_KNOWN_FEE_RESERVE'),
        (('provider_post_known_fee_reserve','journal_file_fingerprints_rechecked'),False,'PROVIDER_POST_KNOWN_FEE_RESERVE'),
        (('provider_post_known_fee_reserve','semantic_reader_failures_rechecked'),False,'PROVIDER_POST_KNOWN_FEE_RESERVE'),
        (('provider_post_known_fee_reserve','primary_replay_matches'),False,'PROVIDER_POST_KNOWN_FEE_RESERVE'),
        (('provider_post_known_fee_reserve','private_extension'),True,'PROVIDER_POST_KNOWN_FEE_RESERVE'),
        (('provider_post_known_fee_reserve','spendable_funds_verified'),True,'PROVIDER_POST_KNOWN_FEE_RESERVE_AUTHORITY'),
        (('supplied_activity_sse_links','exact_parent_link_bound'),False,'SUPPLIED_ACTIVITY_SSE_LINKS'),
        (('supplied_activity_sse_links','provider_source_attested'),True,'SUPPLIED_ACTIVITY_SSE_LINKS_AUTHORITY'),
        (('supplied_activity_sse_reconnect','duplicate_redelivery_idempotent'),False,'SUPPLIED_ACTIVITY_SSE_RECONNECT'),
        (('supplied_activity_sse_reconnect','cursor_persisted_durably'),True,'SUPPLIED_ACTIVITY_SSE_RECONNECT_AUTHORITY'),
        (('private_activity_sse_cursor_store','cold_restart_recovers_cursor'),False,'PRIVATE_ACTIVITY_SSE_CURSOR_STORE'),
        (('private_activity_sse_cursor_store','ready_for_routing'),True,'PRIVATE_ACTIVITY_SSE_CURSOR_STORE_AUTHORITY'),
        (('supplied_activity_sse_wire_session','heartbeat_and_linked_events_reviewed'),False,'SUPPLIED_ACTIVITY_SSE_WIRE_SESSION'),
        (('supplied_activity_sse_wire_session','actual_stream_connection'),True,'SUPPLIED_ACTIVITY_SSE_WIRE_SESSION_AUTHORITY'),
        (('explicit_activity_sse_transport','bounded_segment_reviewed'),False,'EXPLICIT_ACTIVITY_SSE_TRANSPORT'),
        (('explicit_activity_sse_transport','ready_for_routing'),True,'EXPLICIT_ACTIVITY_SSE_TRANSPORT_AUTHORITY'),
        (('explicit_activity_sse_checkpoint','exact_head_checkpoint_committed'),False,'EXPLICIT_ACTIVITY_SSE_CHECKPOINT'),
        (('explicit_activity_sse_checkpoint','checkpoint_store_scope_pinned'),False,'EXPLICIT_ACTIVITY_SSE_CHECKPOINT'),
        (('explicit_activity_sse_checkpoint','checkpoint_exception_scope_pinned'),False,'EXPLICIT_ACTIVITY_SSE_CHECKPOINT'),
        (('explicit_activity_sse_checkpoint','semantic_review_exception_scope_pinned'),False,'EXPLICIT_ACTIVITY_SSE_CHECKPOINT'),
        (('explicit_activity_sse_checkpoint','malformed_checkpoint_acknowledgement_retained'),False,'EXPLICIT_ACTIVITY_SSE_CHECKPOINT'),
        (('explicit_activity_sse_checkpoint','ready_for_routing'),True,'EXPLICIT_ACTIVITY_SSE_CHECKPOINT_AUTHORITY'),
        (('explicit_activity_sse_reconnect_budget','first_failure_retained_without_retry'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','lost_reservation_ack_replays_without_provider_request'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','lost_retry_result_ack_replays_without_new_attempt'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','emergency_stop_blocks_before_reconnect_storage'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','control_callback_mutation_blocks_before_provider_request'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','control_callback_cursor_advance_blocks_before_provider_request'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','preopen_cross_store_cursor_advance_blocks_provider_request'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','provider_open_cursor_owner_blocks_competing_checkpoint'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','provider_open_owner_release_failure_closes_response'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','success_exception_rechecks_settled_files'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','failure_exception_rechecks_settled_files'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','pending_checkpoint_result_recovers_without_second_get'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','checkpoint_reconciliation_lost_result_ack_recovers_exactly_once'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','reconciliation_pins_budget_before_cursor_decision'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','reconciliation_exception_rechecks_entry_files'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','pending_checkpoint_exception_rechecks_settled_files'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','reconciliation_exception_rechecks_settled_files'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','checkpoint_reconciliation_replay_is_byte_stable'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','emergency_stop_retires_pending_without_external_access'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','emergency_stop_closes_retry_wait_without_new_attempt'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','emergency_stop_blocks_retry_schedule_during_result'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','provider_retry_after_extends_durable_backoff'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','provider_service_retry_after_extends_durable_backoff'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','attempt_completion_anchors_reconnect_cooldown'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','failed_clock_uncertainty_closes_from_trusted_admission'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','checkpoint_uncertainty_closes_without_retry_or_external_access'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','uncertain_outcome_closes_without_retry_or_external_access'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','uncertain_outcome_close_replay_is_byte_stable'),False,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET'),
        (('explicit_activity_sse_reconnect_budget','ready_for_routing'),True,'EXPLICIT_ACTIVITY_SSE_RECONNECT_BUDGET_AUTHORITY'),
        (('installed_activity_sse_acceptance','cold_restart_replays_completed_state'),False,'INSTALLED_ACTIVITY_SSE_ACCEPTANCE'),
        (('installed_activity_sse_acceptance','provider_source_attested'),True,'INSTALLED_ACTIVITY_SSE_ACCEPTANCE_AUTHORITY'),
        (('installed_activity_sse_failure_recovery','before_due_restart_denies_attempt'),False,'INSTALLED_ACTIVITY_SSE_FAILURE_RECOVERY'),
        (('installed_activity_sse_failure_recovery','stopped_restart_denies_due_attempt'),False,'INSTALLED_ACTIVITY_SSE_FAILURE_RECOVERY'),
        (('installed_activity_sse_failure_recovery','ready_for_routing'),True,'INSTALLED_ACTIVITY_SSE_FAILURE_RECOVERY_AUTHORITY'),
        (('installed_activity_sse_explicit_retry','explicit_retry_makes_one_get'),False,'INSTALLED_ACTIVITY_SSE_EXPLICIT_RETRY'),
        (('installed_activity_sse_explicit_retry','completed_restart_denies_third_attempt'),False,'INSTALLED_ACTIVITY_SSE_EXPLICIT_RETRY'),
        (('installed_activity_sse_explicit_retry','ready_for_routing'),True,'INSTALLED_ACTIVITY_SSE_EXPLICIT_RETRY_AUTHORITY'),
        (('installed_activity_sse_revoked_retry','revoked_retry_fails_before_get'),False,'INSTALLED_ACTIVITY_SSE_REVOKED_RETRY'),
        (('installed_activity_sse_revoked_retry','restored_retry_makes_one_get'),False,'INSTALLED_ACTIVITY_SSE_REVOKED_RETRY'),
        (('installed_activity_sse_revoked_retry','ready_for_routing'),True,'INSTALLED_ACTIVITY_SSE_REVOKED_RETRY_AUTHORITY'),
        (('installed_activity_sse_rotated_inflight','inflight_rotation_requires_durable_review'),False,'INSTALLED_ACTIVITY_SSE_ROTATED_INFLIGHT'),
        (('installed_activity_sse_rotated_inflight','independent_stop_process_is_local_only'),False,'INSTALLED_ACTIVITY_SSE_ROTATED_INFLIGHT'),
        (('installed_activity_sse_rotated_inflight','independent_stop_preserves_rotation_review'),False,'INSTALLED_ACTIVITY_SSE_ROTATED_INFLIGHT'),
        (('installed_activity_sse_rotated_inflight','rotated_retry_denied_before_get'),False,'INSTALLED_ACTIVITY_SSE_ROTATED_INFLIGHT'),
        (('installed_activity_sse_rotated_inflight','ready_for_routing'),True,'INSTALLED_ACTIVITY_SSE_ROTATED_INFLIGHT_AUTHORITY'),
        (('exact_response_bundle','session_tail_required'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','full_private_file_set_rechecked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','semantic_reader_failures_rechecked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','semantic_reader_mutation_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','bounded_adjacent_session_start_gap'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','delayed_adjacent_session_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','component_session_timing_envelopes_match'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','outside_session_timing_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','adjacent_account_fields_match'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','adjacent_account_discontinuity_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','overlapping_order_identities_match'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','shared_order_identity_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_identity_conflict_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','overlapping_order_progress_monotonic'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','shared_order_progress_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_time_regression_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_fill_regression_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_status_regression_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','overlapping_order_fill_deltas_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_fill_limit_prices_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_order_average_limit_prices_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','complete_activity_only_fill_averages_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','unobserved_activity_fill_average_limits_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_fill_order_update_bounds_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_fill_type_remainders_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_activity_fill_deltas_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','shared_order_fill_delta_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_fill_activity_missing_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_fill_activity_gap_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_fill_activity_time_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cross_response_order_fill_average_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','overlapping_order_fill_activities_partitioned'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','shared_order_fill_partition_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','late_historical_fill_activity_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','repeated_cumulative_fill_state_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cumulative_fill_time_regression_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','cumulative_fill_remaining_conflict_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fill_requested_quantity_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_requested_quantity_changed_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_limit_price_changed_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fill_limit_price_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','partial_fill_without_remainder_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','terminal_fill_with_remainder_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_fill_chain_gap_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_fill_after_order_update_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_average_limit_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_complete_fill_average_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_unobserved_average_limit_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_unobserved_average_invalid_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','filled_status_quantity_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','completion_status_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','reported_fee_references_consistent'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','reported_fee_reference_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','reported_fee_exact_timestamp_chronology_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','reported_fee_exact_timestamp_capture_upper_bound_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_fee_exact_timestamp_capture_upper_bound_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_order_update_collection_start_upper_bound_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','reported_account_order_update_capture_upper_bound_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_fill_timestamp_page_receipt_upper_bound_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_fee_exact_timestamp_page_receipt_upper_bound_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_exact_activity_page_receipt_upper_bound_checked'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','page_receipt_exact_activity_scope_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','unreferenced_fee_capture_scope_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','reported_fee_date_only_scope_preserved'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fee_execution_reference_unknown_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fee_reference_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fee_reference_partial_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fee_timestamp_before_fill_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fee_timestamp_after_capture_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','order_update_after_collection_start_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','account_order_update_after_capture_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','unreferenced_fee_timestamp_after_capture_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fill_timestamp_after_page_receipt_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','fee_timestamp_after_page_receipt_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','retained_activity_order_fill_semantics_bound'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','activity_only_order_fill_semantic_disagreement_rejected'),False,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','private_extension'),True,'EXACT_RESPONSE_BUNDLE'),
        (('exact_response_bundle','ready_for_routing'),True,'EXACT_RESPONSE_BUNDLE_AUTHORITY'),
        (('journals_unchanged',),False,'RECONCILIATION_ACCEPTANCE_REPLAY'),
        (('out_of_window_observations_rejected',),False,'RECONCILIATION_ACCEPTANCE_REPLAY'),
        (('complete_accepted_history_replayed',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('accepted_pacing_request_targets_bound',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('declared_windows_cover_observation_envelope',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('journal_file_fingerprints_rechecked',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('journal_file_identities_rechecked',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('journal_store_bindings_rechecked',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('semantic_reader_failures_rechecked',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('identity_replacement_failure_rejected',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('store_retarget_rejected',),False,'RECONCILIATION_ACCEPTANCE_BINDING'),
        (('stability_guard_bound',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('guard_declared_sources_agree',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('declared_source_mismatch_rejected',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('guard_retained_fill_events_absent',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('guard_collection_observed_fill_events_absent',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('late_collected_historical_fill_rejected',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('guard_collection_observed_fee_events_absent',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('late_collected_historical_fee_rejected',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('guard_retained_fee_date_overlaps_absent',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('guard_fee_date_overlap_detected',),False,'RECONCILIATION_STABILITY_GUARD'),
        (('guard_observation_windows_verified_quiet',),True,'RECONCILIATION_ACCEPTANCE_AUTHORITY'),
        (('requested_window_boundaries_verified',),True,'RECONCILIATION_ACCEPTANCE_AUTHORITY'),
        (('broker_account_reconciled',),True,'RECONCILIATION_ACCEPTANCE_AUTHORITY')):
        changed=copy.deepcopy(result);target=changed
        for key in path[:-1]:target=target[key]
        target[path[-1]]=value
        with pytest.raises(ValueError,match=code):
            trials._validate('reconciliation_acceptance',changed)
    class ResponseBundleMapping(dict):
        pass
    changed=copy.deepcopy(result)
    changed['exact_response_bundle']=ResponseBundleMapping(
        changed['exact_response_bundle'])
    with pytest.raises(ValueError,match='EXACT_RESPONSE_BUNDLE'):
        trials._validate('reconciliation_acceptance',changed)
    changed=copy.deepcopy(result);changed['private_extension']=True
    with pytest.raises(ValueError,match='RECONCILIATION_ACCEPTANCE_SCHEMA'):
        trials._validate('reconciliation_acceptance',changed)
    class ReconciliationMapping(dict):
        pass
    changed=ReconciliationMapping(copy.deepcopy(result))
    with pytest.raises(ValueError,match='RECONCILIATION_ACCEPTANCE_SCHEMA'):
        trials._validate('reconciliation_acceptance',changed)


def test_activity_acceptance_snapshot_covers_main_rollback_wal_and_shm_files(tmp_path):
    from nexus.paper_activity_acceptance_evidence import _sqlite_files_snapshot
    expected=[]
    for name in ('pacing.sqlite3','activity.sqlite3','acceptance.sqlite3'):
        for suffix in ('','-journal','-wal','-shm'):
            path=tmp_path/(name+suffix);path.write_bytes((name+suffix).encode())
            expected.append(name+suffix)
    before=_sqlite_files_snapshot(tmp_path)
    assert [entry[0] for entry in before]==expected
    (tmp_path/'acceptance.sqlite3-journal').write_bytes(b'changed')
    assert _sqlite_files_snapshot(tmp_path)!=before
    (tmp_path/'acceptance.sqlite3-journal').unlink()
    assert _sqlite_files_snapshot(tmp_path)!=before


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


def test_balance_trial_requires_exact_public_schemas():
    from nexus.broker_balance_review import (NEGATIVE_CHECKS,
        PUBLIC_FIXTURE_KEYS, REVIEW_PUBLIC_KEYS)
    from nexus.broker_fee_projection import TIMESTAMP_PUBLIC_KEYS
    result=trials._execute('balance');trials._validate('balance',result)
    assert type(result) is dict and frozenset(result)==PUBLIC_FIXTURE_KEYS
    assert type(result['review']) is dict
    assert frozenset(result['review'])==REVIEW_PUBLIC_KEYS
    assert type(result['negative_checks']) is dict
    assert frozenset(result['negative_checks'])==frozenset(NEGATIVE_CHECKS)
    assert type(result['timestamp_profile']) is dict
    assert frozenset(result['timestamp_profile'])==TIMESTAMP_PUBLIC_KEYS
    changed=copy.deepcopy(result);changed['private_extension']=True
    with pytest.raises(ValueError,match='BALANCE_PROFILE_SCHEMA'):
        trials._validate('balance',changed)
    class BalanceProfileMapping(dict):
        pass
    with pytest.raises(ValueError,match='BALANCE_PROFILE_SCHEMA'):
        trials._validate('balance',BalanceProfileMapping(result))
    changed=copy.deepcopy(result);changed['review']['private_extension']=True
    with pytest.raises(ValueError,match='BALANCE_REVIEW_SCHEMA'):
        trials._validate('balance',changed)
    class BalanceReviewMapping(dict):
        pass
    changed=copy.deepcopy(result);changed['review']=BalanceReviewMapping(result['review'])
    with pytest.raises(ValueError,match='BALANCE_REVIEW_SCHEMA'):
        trials._validate('balance',changed)
    changed=copy.deepcopy(result);changed['review']['limitations']='spendable funds verified'
    with pytest.raises(ValueError,match='BALANCE_REVIEW_SCOPE'):
        trials._validate('balance',changed)
    changed=copy.deepcopy(result);changed['negative_checks']['private_extension']=True
    with pytest.raises(ValueError,match='BALANCE_NEGATIVE_SCHEMA'):
        trials._validate('balance',changed)
    class BalanceNegativeMapping(dict):
        pass
    changed=copy.deepcopy(result)
    changed['negative_checks']=BalanceNegativeMapping(result['negative_checks'])
    with pytest.raises(ValueError,match='BALANCE_NEGATIVE_SCHEMA'):
        trials._validate('balance',changed)
    changed=copy.deepcopy(result);changed['timestamp_profile']['private_extension']=True
    with pytest.raises(ValueError,match='BALANCE_TIMESTAMP_SCHEMA'):
        trials._validate('balance',changed)
    class BalanceTimestampMapping(dict):
        pass
    changed=copy.deepcopy(result)
    changed['timestamp_profile']=BalanceTimestampMapping(result['timestamp_profile'])
    with pytest.raises(ValueError,match='BALANCE_TIMESTAMP_SCHEMA'):
        trials._validate('balance',changed)
    for field in ('atomic_snapshot','broker_connected','automatic_retry'):
        changed=copy.deepcopy(result);changed['review'][field]=True
        with pytest.raises(ValueError,match='BALANCE_AUTHORITY'):
            trials._validate('balance',changed)


def test_pacing_trial_requires_exact_root_and_typed_operation_schemas():
    from nexus.paper_read_pacing import OPERATION_PUBLIC_KEYS
    result=trials._execute('pacing');trials._validate('pacing',result)
    assert type(result) is dict
    assert frozenset(result)==trials.PACING_PUBLIC_KEYS
    assert type(result['typed_operations']) is dict
    assert frozenset(result['typed_operations'])==OPERATION_PUBLIC_KEYS
    changed=copy.deepcopy(result);changed['private_extension']=True
    with pytest.raises(ValueError,match='PACING_PROFILE_SCHEMA'):
        trials._validate('pacing',changed)
    class PacingProfileMapping(dict):
        pass
    with pytest.raises(ValueError,match='PACING_PROFILE_SCHEMA'):
        trials._validate('pacing',PacingProfileMapping(result))
    changed=copy.deepcopy(result)
    changed['limitations']='broker-wide distributed quota verified'
    with pytest.raises(ValueError,match='PACING_PROFILE_SCOPE'):
        trials._validate('pacing',changed)
    changed=copy.deepcopy(result)
    changed['typed_operations']['private_extension']=True
    with pytest.raises(ValueError,match='TYPED_READ_OPERATIONS_SCHEMA'):
        trials._validate('pacing',changed)
    class TypedOperationsMapping(dict):
        pass
    changed=copy.deepcopy(result)
    changed['typed_operations']=TypedOperationsMapping(
        result['typed_operations'])
    with pytest.raises(ValueError,match='TYPED_READ_OPERATIONS_SCHEMA'):
        trials._validate('pacing',changed)


def test_collection_trial_keeps_page_cap_and_cursor_evidence_without_coverage_authority():
    from nexus.activity_collection_evidence import (LIMITATIONS,
        PUBLIC_FIXTURE_KEYS)
    result=trials._execute('collection');trials._validate('collection',result)
    assert type(result) is dict
    assert frozenset(result)==PUBLIC_FIXTURE_KEYS|{'request_derived_collection'}
    assert result['limitations']==LIMITATIONS
    assert type(result['request_derived_collection']) is dict
    assert frozenset(result['request_derived_collection'])==trials.REQUEST_COLLECTION_PUBLIC_KEYS
    for field in ('source_attested','activity_coverage_verified','ready_for_routing',
                  'automatic_continuation','broker_connected','live_orders_enabled'):
        with pytest.raises(ValueError,match='COLLECTION_AUTHORITY'): trials._validate('collection',dict(result,**{field:True}))
    with pytest.raises(ValueError,match='PROFILE_FAILED'):
        trials._validate('collection',dict(result,passed=False))
    with pytest.raises(ValueError,match='COLLECTION_PROFILE'):
        trials._validate('collection',dict(result,source='BROADER_SOURCE'))
    with pytest.raises(ValueError,match='COLLECTION_SCOPE'): trials._validate('collection',dict(result,cursor_chain_preserved=False))
    with pytest.raises(ValueError,match='COLLECTION_RETENTION'): trials._validate('collection',dict(result,retained_pages=1))
    for field in trials.REQUEST_COLLECTION_CHECKS:
        altered=copy.deepcopy(result);altered['request_derived_collection'][field]=False
        with pytest.raises(ValueError,match='REQUEST_DERIVED_COLLECTION'):trials._validate('collection',altered)
    from nexus.paper_activity_acceptance_probe import FLAGS
    for field in (*FLAGS,'actual_provider_access','actual_vault_access'):
        altered=copy.deepcopy(result);altered['request_derived_collection'][field]=True
        with pytest.raises(ValueError,match='REQUEST_DERIVED_COLLECTION_AUTHORITY'):
            trials._validate('collection',altered)


def test_collection_trial_rejects_extensions_mapping_subclasses_and_altered_scope():
    result=trials._execute('collection')
    changed=copy.deepcopy(result);del changed['request_derived_collection']
    with pytest.raises(ValueError,match='COLLECTION_PROFILE_SCOPE'):
        trials._validate('collection',changed)
    changed=copy.deepcopy(result);changed['private_extension']=True
    with pytest.raises(ValueError,match='COLLECTION_PROFILE_SCOPE'):
        trials._validate('collection',changed)
    class CollectionMapping(dict):
        pass
    with pytest.raises(ValueError,match='COLLECTION_PROFILE_SCOPE'):
        trials._validate('collection',CollectionMapping(result))
    changed=copy.deepcopy(result);changed['limitations']='Broader scope.'
    with pytest.raises(ValueError,match='COLLECTION_PROFILE_SCOPE'):
        trials._validate('collection',changed)
    changed=copy.deepcopy(result)
    changed['request_derived_collection']['private_extension']=True
    with pytest.raises(ValueError,match='REQUEST_DERIVED_COLLECTION_SCHEMA'):
        trials._validate('collection',changed)
    changed=copy.deepcopy(result)
    changed['request_derived_collection']=CollectionMapping(
        changed['request_derived_collection'])
    with pytest.raises(ValueError,match='REQUEST_DERIVED_COLLECTION_SCHEMA'):
        trials._validate('collection',changed)


def test_retained_balance_trial_rejects_stale_or_authorized_gross_fee_inference():
    from nexus.retained_balance_review import fixture_report
    result=fixture_report();trials._validate('retained_balance',result)
    changed=copy.deepcopy(result);changed['stale_head_rejected']=False
    with pytest.raises(ValueError,match='RETAINED_BALANCE_FENCES'): trials._validate('retained_balance',changed)
    for field in ('date_only_fee_residual_not_promoted','exact_timestamped_fee_residual_compared'):
        changed=copy.deepcopy(result);changed[field]=False
        with pytest.raises(ValueError,match='RETAINED_BALANCE_ARITHMETIC'): trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result);changed['review']['gross_fill_review']['residual_equals_exact_interval_fee_debits']={'USD':True,'BTC':True}
    with pytest.raises(ValueError,match='RETAINED_BALANCE_ARITHMETIC'): trials._validate('retained_balance',changed)
    for field in ('source_coverage_verified','fee_coverage_complete','fee_execution_links_verified','holds_reconciled',
                  'broker_account_reconciled','spendable_funds_verified','ready_for_routing','ledger_applied','live_orders_enabled','admission_reserved'):
        changed=copy.deepcopy(result);changed['review'][field]=True
        with pytest.raises(ValueError,match='RETAINED_BALANCE_AUTHORITY'): trials._validate('retained_balance',changed)
    for field in ('fee_posting_interval_verified','fee_debits_allocated_to_interval','fee_execution_links_verified','fee_reserved_funding_verified'):
        changed=copy.deepcopy(result);changed['review']['gross_fill_review'][field]=True
        with pytest.raises(ValueError,match='RETAINED_BALANCE_AUTHORITY'): trials._validate('retained_balance',changed)


def test_retained_balance_trial_requires_exact_bound_reconciliation_schemas():
    from nexus.broker_activity_review import (REVIEW_PUBLIC_KEYS as
        ACTIVITY_REVIEW_PUBLIC_KEYS)
    from nexus.retained_balance_review import (GROSS_PUBLIC_KEYS,LIMITATIONS,
        PUBLIC_FIXTURE_KEYS,REVIEW_PUBLIC_KEYS)
    result=trials._execute('retained_balance')
    trials._validate('retained_balance',result)
    assert type(result) is dict and frozenset(result)==PUBLIC_FIXTURE_KEYS
    assert type(result['review']) is dict
    assert frozenset(result['review'])==REVIEW_PUBLIC_KEYS
    assert result['review']['limitations']==LIMITATIONS
    assert type(result['review']['gross_fill_review']) is dict
    assert frozenset(result['review']['gross_fill_review'])==GROSS_PUBLIC_KEYS
    assert type(result['review']['retained_activity_review']) is dict
    assert frozenset(result['review']['retained_activity_review'])==ACTIVITY_REVIEW_PUBLIC_KEYS
    class BalanceMapping(dict):
        pass
    changed=copy.deepcopy(result);changed['private_extension']=True
    with pytest.raises(ValueError,match='RETAINED_BALANCE_PROFILE_SCHEMA'):
        trials._validate('retained_balance',changed)
    with pytest.raises(ValueError,match='RETAINED_BALANCE_PROFILE_SCHEMA'):
        trials._validate('retained_balance',BalanceMapping(result))
    changed=copy.deepcopy(result);changed['review']['private_extension']=True
    with pytest.raises(ValueError,match='RETAINED_BALANCE_REVIEW_SCHEMA'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result);changed['review']=BalanceMapping(changed['review'])
    with pytest.raises(ValueError,match='RETAINED_BALANCE_REVIEW_SCHEMA'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result);changed['review']['limitations']='Broader scope.'
    with pytest.raises(ValueError,match='RETAINED_BALANCE_REVIEW_SCOPE'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result)
    changed['review']['gross_fill_review']['private_extension']=True
    with pytest.raises(ValueError,match='RETAINED_BALANCE_GROSS_SCHEMA'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result)
    changed['review']['gross_fill_review']=BalanceMapping(
        changed['review']['gross_fill_review'])
    with pytest.raises(ValueError,match='RETAINED_BALANCE_GROSS_SCHEMA'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result)
    changed['review']['retained_activity_review']['private_extension']=True
    with pytest.raises(ValueError,match='RETAINED_BALANCE_ACTIVITY_SCHEMA'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result)
    changed['review']['retained_activity_review']=BalanceMapping(
        changed['review']['retained_activity_review'])
    with pytest.raises(ValueError,match='RETAINED_BALANCE_ACTIVITY_SCHEMA'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result)
    changed['review']['retained_activity_review']['limitations']='Broader scope.'
    with pytest.raises(ValueError,match='RETAINED_BALANCE_ACTIVITY_SCOPE'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result);changed['review']['input_sha256']='0'*64
    with pytest.raises(ValueError,match='RETAINED_BALANCE_REVIEW_BINDING'):
        trials._validate('retained_balance',changed)
    changed=copy.deepcopy(result)
    changed['review']['gross_fill_review']['gross_only_minimum_group_end_balances']['USD']='901'
    with pytest.raises(ValueError,match='RETAINED_BALANCE_REPLAY'):
        trials._validate('retained_balance',changed)


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


def test_inventory_trial_consumes_exact_broker_paper_scope():
    result=trials._inspection_inventory_trial();trials._validate('inspection_inventory',result)
    class InventoryMapping(dict):pass
    cases=[]
    cases.append((dict(result,unconsumed=True),'INSPECTION_PROFILE_SCHEMA'))
    cases.append((InventoryMapping(result),'INSPECTION_PROFILE_SCHEMA'))
    changed=copy.deepcopy(result);changed['btc_quote_observation']['unconsumed']=True
    cases.append((changed,'BTC_QUOTE_OBSERVATION_SCHEMA'))
    changed=copy.deepcopy(result);changed['btc_quote_observation']=InventoryMapping(changed['btc_quote_observation'])
    cases.append((changed,'BTC_QUOTE_OBSERVATION_SCHEMA'))
    changed=copy.deepcopy(result);changed['btc_quote_bundle']['cipher']='PLAINTEXT'
    cases.append((changed,'BTC_QUOTE_BUNDLE_POLICY'))
    changed=copy.deepcopy(result);changed['asset_rules_observation']['unconsumed']=True
    cases.append((changed,'ASSET_RULES_SCHEMA'))
    changed=copy.deepcopy(result);changed['asset_rules_observation']['asset']=InventoryMapping(changed['asset_rules_observation']['asset'])
    cases.append((changed,'ASSET_RULES_SCHEMA'))
    changed=copy.deepcopy(result);changed['asset_rules_observation']['read_timing']['extra']=0
    cases.append((changed,'ASSET_RULES_SCHEMA'))
    changed=copy.deepcopy(result);changed['asset_rules_observation']['requests_completed']=True
    cases.append((changed,'ASSET_RULES_READER'))
    changed=copy.deepcopy(result);changed['asset_rules_observation']['broker_connected']=0
    cases.append((changed,'ASSET_RULES_AUTHORITY'))
    changed=copy.deepcopy(result);changed['observations']=tuple(changed['observations'])
    cases.append((changed,'INVENTORY_REPORT_SCHEMA'))
    changed=copy.deepcopy(result);changed['observations'][0]['unconsumed']=True
    cases.append((changed,'INVENTORY_REPORT_SCHEMA'))
    changed=copy.deepcopy(result);changed['observations'][0]=InventoryMapping(changed['observations'][0])
    cases.append((changed,'INVENTORY_REPORT_SCHEMA'))
    changed=copy.deepcopy(result);changed['observations'][0]['observed_inventory']['extra']=None
    cases.append((changed,'INVENTORY_NESTED_SCHEMA'))
    changed=copy.deepcopy(result);changed['observations'][0]['read_timing']['started_at']=True
    cases.append((changed,'INVENTORY_REPORT_SCOPE'))
    changed=copy.deepcopy(result);changed['observations'][0]['open_order_terms']['extra']=False
    cases.append((changed,'INVENTORY_NESTED_SCHEMA'))
    changed=copy.deepcopy(result);changed['observations'][0]['open_order_terms']['reported_rows']=True
    cases.append((changed,'INVENTORY_OPEN_TERMS'))
    changed=copy.deepcopy(result);changed['get_requests']=True
    cases.append((changed,'INVENTORY_READ_SCOPE'))
    changed=copy.deepcopy(result);changed['limitations']='Broader scope.'
    cases.append((changed,'INSPECTION_PROFILE_SCOPE'))
    for changed,code in cases:
        with pytest.raises(ValueError,match=code):
            trials._validate('inspection_inventory',changed)


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


def test_portable_observation_review_trial_requires_exact_bound_schemas():
    from nexus.broker_activity_review import REVIEW_PUBLIC_KEYS as ACTIVITY_PUBLIC_KEYS
    from nexus.paper_observation_review import (OBSERVATION_BALANCE_REVIEW_PUBLIC_KEYS,
        PUBLIC_FIXTURE_KEYS, REVIEW_PUBLIC_KEYS)
    from nexus.retained_balance_review import GROSS_PUBLIC_KEYS
    result=trials._execute('observation_review')
    trials._validate('observation_review',result)
    assert type(result) is dict and frozenset(result)==PUBLIC_FIXTURE_KEYS
    for name in ('review','boundary_review'):
        assert type(result[name]) is dict
        assert frozenset(result[name])==REVIEW_PUBLIC_KEYS
        assert type(result[name]['retained_activity_review']) is dict
        assert frozenset(result[name]['retained_activity_review'])==ACTIVITY_PUBLIC_KEYS
    assert type(result['review']['retained_balance_review']) is dict
    assert frozenset(result['review']['retained_balance_review'])==OBSERVATION_BALANCE_REVIEW_PUBLIC_KEYS
    assert type(result['review']['retained_balance_review']['gross_fill_review']) is dict
    assert frozenset(result['review']['retained_balance_review']['gross_fill_review'])==GROSS_PUBLIC_KEYS
    class ObservationMapping(dict): pass
    cases=(
        (('private_extension',),True,'OBSERVATION_REVIEW_PROFILE_SCHEMA'),
        (('review','private_extension'),True,'OBSERVATION_REVIEW_SCHEMA'),
        (('boundary_review','private_extension'),True,'OBSERVATION_REVIEW_SCHEMA'),
        (('review','retained_activity_review','private_extension'),True,
            'OBSERVATION_REVIEW_ACTIVITY_SCHEMA'),
        (('review','retained_balance_review','private_extension'),True,
            'OBSERVATION_REVIEW_BALANCE_SCHEMA'),
        (('review','retained_balance_review','gross_fill_review','private_extension'),True,
            'OBSERVATION_REVIEW_GROSS_SCHEMA'),
        (('review','limitations'),'Broader scope.','OBSERVATION_REVIEW_SCOPE'),
        (('review','input_sha256'),'0'*64,'OBSERVATION_REVIEW_BINDING'),
        (('review','account_sha256'),'0'*64,'OBSERVATION_REVIEW_BINDING'),
        (('review','retained_activity_review','head_sha256'),'0'*64,
            'OBSERVATION_REVIEW_ACTIVITY_BINDING'),
        (('review','retained_balance_review','input_sha256'),'0'*64,
            'OBSERVATION_REVIEW_BALANCE_BINDING'),
        (('review','retained_balance_review','gross_fill_review',
            'gross_only_minimum_group_end_balances','USD'),'901',
            'OBSERVATION_REVIEW_GROSS_REPLAY'),
    )
    for path,value,code in cases:
        changed=copy.deepcopy(result);target=changed
        for key in path[:-1]: target=target[key]
        target[path[-1]]=value
        with pytest.raises(ValueError,match=code):
            trials._validate('observation_review',changed)
    with pytest.raises(ValueError,match='OBSERVATION_REVIEW_PROFILE_SCHEMA'):
        trials._validate('observation_review',ObservationMapping(result))
    changed=copy.deepcopy(result)
    changed['review']=ObservationMapping(changed['review'])
    with pytest.raises(ValueError,match='OBSERVATION_REVIEW_SCHEMA'):
        trials._validate('observation_review',changed)


def test_open_terms_trial_rejects_partial_totals_or_promoted_hold_authority():
    from nexus.paper_open_order_terms import fixture_report, FLAGS
    result=fixture_report();trials._validate('open_order_terms',result)
    changed=copy.deepcopy(result);changed['unsupported']['remaining_buy_limit_principal_cap_usd']='250'
    with pytest.raises(ValueError,match='OPEN_TERMS_UNKNOWN'):trials._validate('open_order_terms',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result);changed['observed'][field]=True
        with pytest.raises(ValueError,match='OPEN_TERMS_AUTHORITY'):trials._validate('open_order_terms',changed)


def test_open_terms_trial_requires_exact_public_schemas():
    from nexus.paper_open_order_terms import fixture_report
    class OpenTermsMapping(dict): pass
    result=fixture_report()
    for path in (('private_extension',),('observed','private_extension'),
            ('unsupported','private_extension')):
        changed=copy.deepcopy(result);target=changed
        for key in path[:-1]: target=target[key]
        target[path[-1]]=True
        code='OPEN_TERMS_PROFILE_SCHEMA' if len(path)==1 else 'OPEN_TERMS_REPORT_SCHEMA'
        with pytest.raises(ValueError,match=code):
            trials._validate('open_order_terms',changed)
    with pytest.raises(ValueError,match='OPEN_TERMS_PROFILE_SCHEMA'):
        trials._validate('open_order_terms',OpenTermsMapping(result))
    changed=copy.deepcopy(result);changed['observed']=OpenTermsMapping(changed['observed'])
    with pytest.raises(ValueError,match='OPEN_TERMS_REPORT_SCHEMA'):
        trials._validate('open_order_terms',changed)


@pytest.mark.parametrize(('path','value','code'),(
    (('source',),'BROADER_FIXTURE','OPEN_TERMS_PROFILE'),
    (('broker_connected',),True,'OPEN_TERMS_PROFILE_AUTHORITY'),
    (('observed','scope'),'BROADER_SCOPE','OPEN_TERMS_SCOPE'),
    (('observed','status'),'REVIEW_REQUIRED','OPEN_TERMS_OBSERVED'),
    (('observed','unsupported_rows'),1,'OPEN_TERMS_OBSERVED'),
    (('observed','review_reasons'),['PRIVATE_REASON'],'OPEN_TERMS_OBSERVED'),
    (('observed','normalized_terms_sha256'),'0'*64,'OPEN_TERMS_OBSERVED'),
    (('observed','summary_sha256'),'0'*64,'OPEN_TERMS_SUMMARY'),
    (('unsupported','reported_rows'),3,'OPEN_TERMS_UNSUPPORTED'),
    (('unsupported','supported_unique_orders'),2,'OPEN_TERMS_UNSUPPORTED'),
    (('unsupported','duplicate_rows_ignored'),1,'OPEN_TERMS_UNSUPPORTED'),
    (('unsupported','review_reasons'),[],'OPEN_TERMS_UNSUPPORTED'),
    (('unsupported','summary_sha256'),'0'*64,'OPEN_TERMS_SUMMARY'),
))
def test_open_terms_trial_consumes_all_fixed_evidence(path,value,code):
    from nexus.paper_open_order_terms import fixture_report
    changed=copy.deepcopy(fixture_report());target=changed
    for key in path[:-1]: target=target[key]
    target[path[-1]]=value
    with pytest.raises(ValueError,match=code):
        trials._validate('open_order_terms',changed)


def test_encrypted_observation_profile_cannot_claim_password_storage_or_replaced_originals():
    from nexus.encrypted_observation import AUTHORITY_FLAGS, fixture_report
    result=fixture_report();trials._validate('encrypted_observation',result)
    for field in AUTHORITY_FLAGS:
        changed=copy.deepcopy(result);changed[field]=True
        with pytest.raises(ValueError,match='ENCRYPTED_OBSERVATION_AUTHORITY'):trials._validate('encrypted_observation',changed)


def test_encrypted_observation_trial_requires_exact_public_schema():
    from nexus.encrypted_observation import fixture_report
    class EncryptedObservationMapping(dict): pass
    result=fixture_report();changed=copy.deepcopy(result);changed['private_extension']=True
    with pytest.raises(ValueError,match='ENCRYPTED_OBSERVATION_SCHEMA'):
        trials._validate('encrypted_observation',changed)
    with pytest.raises(ValueError,match='ENCRYPTED_OBSERVATION_SCHEMA'):
        trials._validate('encrypted_observation',EncryptedObservationMapping(result))


@pytest.mark.parametrize(('field','value','code'),(
    ('source','BROADER_SOURCE','ENCRYPTED_OBSERVATION_SCOPE'),
    ('limitations','Broader acceptance.','ENCRYPTED_OBSERVATION_SCOPE'),
    ('kdf','SCRYPT_PROVIDER_SELECTED','ENCRYPTED_OBSERVATION_POLICY'),
    ('cipher','AES256GCM_REUSED_NONCE','ENCRYPTED_OBSERVATION_POLICY'),
    ('roundtrip_matches',1,'ENCRYPTED_OBSERVATION_CRYPTO'),
))
def test_encrypted_observation_trial_consumes_fixed_scope(field,value,code):
    from nexus.encrypted_observation import fixture_report
    changed=copy.deepcopy(fixture_report());changed[field]=value
    with pytest.raises(ValueError,match=code):
        trials._validate('encrypted_observation',changed)


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
    for target,field,code in ((result,'unconsumed','FORWARD_PROFILE_SCHEMA'),
        (result['recovered'],'unconsumed','FORWARD_RECOVERY_SCHEMA'),
        (result['recovered']['sessions'][0],'unconsumed','FORWARD_SESSION_SCHEMA'),
        (result['cycle_latency'],'unconsumed','FORWARD_LATENCY_SCHEMA'),
        (result['recovered']['coverage_review'],'unconsumed','FORWARD_COVERAGE_SCHEMA')):
        changed=copy.deepcopy(result)
        if target is result:changed[field]=True
        elif target is result['recovered']:changed['recovered'][field]=True
        elif target is result['recovered']['sessions'][0]:changed['recovered']['sessions'][0][field]=True
        elif target is result['cycle_latency']:changed['cycle_latency'][field]=True
        else:changed['recovered']['coverage_review'][field]=True
        with pytest.raises(ValueError,match=code):trials._validate('forward',changed)
    changed=copy.deepcopy(result);changed['passed']=1
    with pytest.raises(ValueError,match='FORWARD_SCOPE'):trials._validate('forward',changed)
    changed=copy.deepcopy(result);changed['broker_connected']=1
    with pytest.raises(ValueError,match='FORWARD_AUTHORITY'):trials._validate('forward',changed)
    changed=copy.deepcopy(result);changed['recovered']['sessions'][0]['session']=result['recovered']['sessions'][1]['session']
    with pytest.raises(ValueError,match='FORWARD_SESSION_BINDING'):trials._validate('forward',changed)
    changed=copy.deepcopy(result);changed['cycle_latency']['p50_ms']=changed['cycle_latency']['p99_ms']+1
    with pytest.raises(ValueError,match='FORWARD_LATENCY_SCHEMA'):trials._validate('forward',changed)
    for field,value in (('retained_observations',279),('after','1970-01-01T00:16:41Z'),
        ('review_reasons',[]),('evidence_snapshot_sha256','0'*63)):
        changed=copy.deepcopy(result);changed['recovered']['coverage_review'][field]=value
        with pytest.raises(ValueError,match='FORWARD_COVERAGE_EVIDENCE'):trials._validate('forward',changed)
    changed=copy.deepcopy(result);changed['recovered']['coverage_review']['declared_public_covered_seconds']='1'
    with pytest.raises(ValueError,match='FORWARD_COVERAGE_SCOPE'):trials._validate('forward',changed)
    for field,value in (('incident_free_coverage_target_met',True),
        ('operational_incidents_present',False)):
        changed=copy.deepcopy(result);changed['recovered']['coverage_review'][field]=value
        with pytest.raises(ValueError,match='FORWARD_COVERAGE_SCOPE'):trials._validate('forward',changed)
    for field,value in (('retained_quote_recheck_applied',False),('recorded_eligibility_disagreements_in_window',1),
        ('recorded_eligibility_disagreements_in_window',False),('revalidated_eligible_observations_in_window',0),
        ('revalidated_rejected_observations_in_window',0),('eligibility_count_scope','VERIFIED_MARKET_COVERAGE')):
        changed=copy.deepcopy(result);changed['recovered']['coverage_review'][field]=value
        with pytest.raises(ValueError,match='FORWARD_COVERAGE_REVALIDATION'):trials._validate('forward',changed)
    for field in FLAGS:
        changed=copy.deepcopy(result);changed['recovered']['coverage_review'][field]=True
        with pytest.raises(ValueError,match='FORWARD_COVERAGE_AUTHORITY'):trials._validate('forward',changed)


def test_recovery_trial_consumes_exact_restored_scope():
    from pathlib import Path
    from nexus.release_roadmap import REVISION
    path=Path(__file__).resolve().parents[1]/'validation'/('release_trials_'+REVISION.replace('.','_')+'.json')
    result=json.loads(path.read_text())['profiles']['recovery'];trials._validate('recovery',result)
    class RecoveryMapping(dict):pass
    for changed,code in ((dict(result,unconsumed=True),'RECOVERY_PROFILE_SCHEMA'),
        (RecoveryMapping(result),'RECOVERY_PROFILE_SCHEMA')):
        with pytest.raises(ValueError,match=code):trials._validate('recovery',changed)
    changed=copy.deepcopy(result);changed['recovered']['unconsumed']=True
    with pytest.raises(ValueError,match='RECOVERY_RESULT_SCHEMA'):trials._validate('recovery',changed)
    changed=copy.deepcopy(result);changed['recovered']=RecoveryMapping(changed['recovered'])
    with pytest.raises(ValueError,match='RECOVERY_RESULT_SCHEMA'):trials._validate('recovery',changed)
    for field,value,code in (('verified_files',True,'RECOVERY_SCOPE'),
        ('broker_connected',0,'RECOVERY_AUTHORITY')):
        changed=copy.deepcopy(result);changed[field]=value
        with pytest.raises(ValueError,match=code):trials._validate('recovery',changed)
    for field,value,code in (('session','0'*15,'RECOVERY_ORDER_BINDING'),
        ('session_order_ids',[],'RECOVERY_ORDER_BINDING'),
        ('quote_observations',True,'RESTORE_ACCOUNTING'),
        ('source_modes',['PUBLIC_READER_SINGLE_SOURCE_UNATTESTED'],'RESTORE_ACCOUNTING'),
        ('policy_sha256','0'*64,'RECOVERY_EVIDENCE_BINDING'),
        ('account_sha256','0'*63,'RECOVERY_EVIDENCE_BINDING')):
        changed=copy.deepcopy(result);changed['recovered'][field]=value
        with pytest.raises(ValueError,match=code):trials._validate('recovery',changed)


@pytest.mark.parametrize('field',trials.FIRST_RUN_HTTP_CHECKS)
def test_http_first_run_acceptance_requires_each_scope_check(field):
    result=http_report();result['first_run_defaults'][field]=False
    with pytest.raises(trials.ReleaseTrialError,match='HTTP_FIRST_RUN_DEFAULTS'):trials._validate('http',result)
