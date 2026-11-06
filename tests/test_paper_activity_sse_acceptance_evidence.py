"""Release evidence for the installed Activity SSE operator."""

from nexus.paper_activity_sse_acceptance_evidence import (
    failure_fixture_report, fixture_report, retry_fixture_report,
    revoked_retry_fixture_report, rotated_inflight_fixture_report)


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


def test_revoked_retry_fixture_preserves_due_state_until_explicit_restore():
    result = revoked_retry_fixture_report()
    assert result['passed'] is True
    assert result['failed_attempt_reaches_due_state'] is True
    assert result['credential_removal_is_explicit_and_non_authoritative'] is True
    assert result['revoked_retry_fails_before_get'] is True
    assert result['revoked_retry_preserves_exact_durable_state'] is True
    assert result['revoked_cold_status_is_vault_and_network_silent'] is True
    assert result['credential_restore_is_explicit_and_non_authoritative'] is True
    assert result['restored_retry_makes_one_get'] is True
    assert result['restored_retry_checkpoint_completed'] is True
    assert result['completed_restart_denies_third_attempt'] is True
    assert result['no_automatic_retry_or_continuation'] is True
    assert result['actual_provider_access'] is False
    assert result['ready_for_routing'] is False
    assert result['release_approved'] is False


def test_rotated_inflight_fixture_requires_review_without_another_get():
    result = rotated_inflight_fixture_report()
    assert result['passed'] is True
    assert result['rotation_is_explicit_and_non_authoritative'] is True
    assert result['rotated_pair_replaces_original_record'] is True
    assert result['inflight_rotation_fails_after_one_get'] is True
    assert result['inflight_rotation_withholds_checkpoint'] is True
    assert result['inflight_rotation_requires_durable_review'] is True
    assert result['independent_stop_process_is_local_only'] is True
    assert result['independent_stop_preserves_rotation_review'] is True
    assert result['rotated_retry_denied_before_get'] is True
    assert result['denied_retry_preserves_exact_durable_state'] is True
    assert result['cold_status_is_vault_network_and_write_silent'] is True
    assert result['no_order_journal_created_before_explicit_stop'] is True
    assert result['no_automatic_retry_or_continuation'] is True
    assert result['actual_provider_access'] is False
    assert result['ready_for_routing'] is False
    assert result['release_approved'] is False
