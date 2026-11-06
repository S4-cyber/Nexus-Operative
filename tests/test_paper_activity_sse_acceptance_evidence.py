"""Release evidence for the installed Activity SSE operator."""

from nexus.paper_activity_sse_acceptance_evidence import fixture_report


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
