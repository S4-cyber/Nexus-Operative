"""Release evidence for the installed Activity SSE operator."""

from nexus.paper_activity_sse_acceptance_evidence import (
    failure_fixture_report, fixture_report, retry_fixture_report)


def test_fixture_proves_installed_attempt_restart_without_authority():
    result = fixture_report()
    assert result['passed'] is True
    assert result['one_injected_get_completed'] is True
    assert result['cold_restart_replays_completed_state'] is True
    assert result['completed_state_denies_second_attempt'] is True
    assert result['fresh_process_journals_unchanged'] is True
    assert result['fresh_process_vault_silent'] is True
    assert result['fresh_process_network_silent'] is True
    assert result['actual_provider_access'] is False
    assert result['provider_source_attested'] is False
    assert result['activity_coverage_verified'] is False
    assert result['ready_for_routing'] is False
    assert result['release_approved'] is False


def test_failure_fixture_proves_durable_backoff_without_automatic_retry():
    result = failure_fixture_report()
    assert result['passed'] is True
    assert result['one_injected_failed_get'] is True
    assert result['installed_failure_is_fixed_diagnostic'] is True
    assert result['failed_attempt_retained_for_restart'] is True
    assert result['before_due_restart_denies_attempt'] is True
    assert result['due_restart_requires_explicit_attempt'] is True
    assert result['independent_stop_process_is_local_only'] is True
    assert result['stopped_restart_denies_due_attempt'] is True
    assert result['cold_statuses_are_vault_silent'] is True
    assert result['cold_statuses_are_network_silent'] is True
    assert result['failed_journals_remain_unchanged'] is True
    assert result['no_automatic_retry_or_continuation'] is True
    assert result['actual_provider_access'] is False
    assert result['ready_for_routing'] is False
    assert result['release_approved'] is False


def test_retry_fixture_proves_explicit_due_retry_and_completed_cold_replay():
    result = retry_fixture_report()
    assert result['passed'] is True
    assert result['first_failed_get_retained_once'] is True
    assert result['pre_deadline_restart_denies_retry'] is True
    assert result['due_restart_only_reports_eligibility'] is True
    assert result['explicit_retry_makes_one_get'] is True
    assert result['retry_checkpoint_completed_exactly_once'] is True
    assert result['completed_restart_denies_third_attempt'] is True
    assert result['cold_statuses_are_vault_silent'] is True
    assert result['cold_statuses_are_network_silent'] is True
    assert result['completed_journals_are_cold_read_stable'] is True
    assert result['no_automatic_retry_or_continuation'] is True
    assert result['actual_provider_access'] is False
    assert result['ready_for_routing'] is False
    assert result['release_approved'] is False
