"""Strict offline POST/GET/SSE uncertainty coverage matrix.

This module composes existing disposable fixtures.  It performs no provider,
vault, retained-storage, routing, or financial operation and grants no runtime
authority.  The matrix is deliberately compact: component details stay in
their separately validated release-profile reports.
"""

MODE = 'OFFLINE_PAPER_TRANSPORT_UNCERTAINTY_MATRIX'
SOURCE = 'SYNTHETIC_LOCAL_POST_GET_SSE_UNCERTAINTY_MATRIX'
LIMITATIONS = (
    'Exact-schema composition of disposable injected POST, GET and Activity '
    'SSE fixtures only. It proves that the reviewed local treatments cover '
    'single-attempt admission, unknown outcomes, lost acknowledgements, '
    'explicit recovery, bounded pacing, emergency-stop revocation and cold '
    'replay together. It does not attest provider identity, an authorized '
    'paper account, credentials, source delivery, broker-wide quotas, native '
    'durability, fees, funds, holdings, routing, sustained operation, '
    'performance, profitability or release acceptance.')

AUTHORITY_FLAGS = (
    'actual_provider_access', 'actual_vault_access',
    'actual_stream_connection', 'actual_storage_retained',
    'provider_source_attested', 'stream_delivery_verified',
    'broker_wide_quota_verified', 'distributed_ownership_verified',
    'native_power_loss_durability_verified', 'broker_account_reconciled',
    'spendable_funds_verified', 'fee_coverage_complete',
    'historical_replay_complete', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved')

CHECKS = (
    'get_auth_denial_review_bound', 'get_lost_acknowledgement_replayed',
    'get_cooldown_and_capacity_preserved',
    'post_single_attempt_and_safety_windows_bound',
    'post_unknown_outcome_retained_for_original_id',
    'post_terminal_recovery_lost_acknowledgement_replayed',
    'post_original_request_never_rearmed',
    'post_replaced_owner_interruption_review_bound',
    'sse_failure_retained_without_automatic_retry',
    'sse_due_retry_explicit_and_single',
    'sse_emergency_stop_independent',
    'sse_lost_acknowledgements_replayed_without_extra_get',
    'sse_reconciliation_replay_byte_stable',
    'all_component_schemas_exact', 'all_external_authority_denied')

GET_KEYS = frozenset((
    'source', 'passed', 'exact_review_block_bound',
    'recovery_acknowledgement_cold_replayed',
    'provider_budget_file_identity_rechecked',
    'provider_budget_file_fingerprint_rechecked',
    'provider_budget_file_set_rechecked_on_return',
    'provider_budget_ownership_rechecked_on_exit',
    'blocked_history_preserved', 'cooldown_preserved',
    'session_capacity_not_reclaimed', 'actual_provider_access',
    'actual_vault_access', 'broker_account_reconciled',
    'spendable_funds_verified', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved'))
GET_FALSE = frozenset((
    'actual_provider_access', 'actual_vault_access',
    'broker_account_reconciled', 'spendable_funds_verified',
    'ready_for_routing', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved'))

POST_TRANSPORT_KEYS = frozenset((
    'source', 'passed', 'exactly_one_post', 'marker_committed_before_post',
    'account_and_position_capacity_resampled',
    'post_reservation_risk_resampled', 'post_reservation_asset_resampled',
    'open_order_reservations_absent_at_three_samples',
    'post_transport_credentials_rechecked',
    'post_transport_controls_rechecked',
    'typed_local_transport_authorization_consumed',
    'pre_post_journal_writer_exclusion',
    'explicit_emergency_stop_binding',
    'trusted_transport_deadline_enforced',
    'terminal_store_bindings_rechecked',
    'request_and_post_budget_store_bindings_pinned',
    'request_and_post_budget_file_identities_pinned',
    'post_reservation_durable_heads_rechecked',
    'post_budget_reservation_cold_replayed',
    'reservation_admission_window_bound', 'request_marker_cold_replayed',
    'request_marker_admission_window_bound',
    'response_observation_cold_replayed',
    'response_observation_head_bound_to_post_budget',
    'response_budget_completion_cold_replayed',
    'post_response_persistence_stop_independent',
    'post_response_local_completion_stop_independent',
    'response_observation_completion_bracketed',
    'response_completion_window_bound', 'not_sent_completion_window_bound',
    'outcome_unknown_completion_window_bound',
    'safety_closure_request_head_bracketed',
    'alpaca_post_budget_scope_required',
    'late_open_order_blocked_before_reservation',
    'post_reservation_open_order_blocked_before_post',
    'late_risk_change_blocked_before_reservation',
    'post_reservation_risk_change_blocked_before_post',
    'post_reservation_asset_change_blocked_before_post',
    'exact_asset_flag_types_required',
    'final_provider_owner_reproved_before_post',
    'safety_closure_cold_replayed', 'response_retained',
    'transport_loss_preserved_for_original_id_lookup',
    'local_provider_post_budget_retained', 'repeat_submission_blocked',
    'output_never_sendable', 'actual_provider_access',
    'actual_vault_access', 'broker_account_reconciled',
    'spendable_funds_verified', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved'))
POST_TRANSPORT_FALSE = GET_FALSE

POST_RECOVERY_KEYS = frozenset((
    'source', 'passed', 'exact_terminal_resolution_bound',
    'recovery_acknowledgement_cold_replayed',
    'request_path_identity_rechecked', 'request_file_fingerprint_rechecked',
    'request_replay_fingerprint_rechecked_after_load',
    'request_proof_final_before_budget_commit',
    'request_proof_rechecked_after_budget_receipts',
    'request_snapshot_rechecked_on_exit',
    'post_budget_file_identity_rechecked',
    'post_budget_file_fingerprint_rechecked',
    'post_budget_file_set_rechecked_on_return',
    'post_budget_ownership_rechecked_on_exit',
    'request_writer_excluded_through_commit', 'unknown_history_preserved',
    'cooldown_preserved', 'original_request_not_rearmed',
    'unresolved_request_denied', 'actual_provider_access',
    'actual_vault_access', 'broker_account_reconciled',
    'spendable_funds_verified', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved'))
POST_RECOVERY_FALSE = GET_FALSE

POST_INTERRUPTION_KEYS = frozenset((
    'source', 'passed', 'ordinary_interrupt_refused_replaced_owner',
    'replaced_owner_interrupted_exactly_once',
    'lost_acknowledgement_cold_replayed',
    'unknown_outcome_and_cooldown_preserved',
    'actual_provider_access', 'actual_vault_access', 'owner_repaired',
    'attempt_rearmed', 'automatic_retry', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved'))
POST_INTERRUPTION_FALSE = frozenset((
    'actual_provider_access', 'actual_vault_access', 'owner_repaired',
    'attempt_rearmed', 'automatic_retry', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved'))

SSE_KEYS = frozenset((
    'source', 'passed', 'first_failure_retained_without_retry',
    'lost_reservation_ack_replays_without_provider_request',
    'lost_retry_result_ack_replays_without_new_attempt',
    'reservation_commit_durability_replays_exactly',
    'commit_sync_preserves_process_control',
    'emergency_stop_blocks_before_reconnect_storage',
    'control_callback_mutation_blocks_before_provider_request',
    'control_callback_cursor_advance_blocks_before_provider_request',
    'preopen_cross_store_cursor_advance_blocks_provider_request',
    'provider_open_cursor_owner_blocks_competing_checkpoint',
    'provider_open_owner_release_failure_closes_response',
    'due_second_attempt_explicit_and_single',
    'checkpoint_and_budget_recover_together',
    'success_exception_rechecks_settled_files',
    'failure_exception_rechecks_settled_files',
    'pending_checkpoint_result_recovers_without_second_get',
    'checkpoint_reconciliation_lost_result_ack_recovers_exactly_once',
    'reconciliation_pins_budget_before_cursor_decision',
    'reconciliation_exception_rechecks_entry_files',
    'pending_checkpoint_exception_rechecks_settled_files',
    'reconciliation_exception_rechecks_settled_files',
    'checkpoint_reconciliation_replay_is_byte_stable',
    'type_confused_replays_rejected',
    'emergency_stop_retires_pending_without_external_access',
    'emergency_stop_closes_retry_wait_without_new_attempt',
    'emergency_stop_blocks_retry_schedule_during_result',
    'provider_retry_after_extends_durable_backoff',
    'provider_service_retry_after_extends_durable_backoff',
    'attempt_completion_anchors_reconnect_cooldown',
    'failed_clock_uncertainty_closes_from_trusted_admission',
    'checkpoint_uncertainty_closes_without_retry_or_external_access',
    'uncertain_outcome_closes_without_retry_or_external_access',
    'uncertain_outcome_close_replay_is_byte_stable',
    'no_private_identifier_or_credential_echo',
    'no_sleep_loop_or_financial_authority', 'actual_provider_access',
    'actual_vault_access', 'actual_stream_connection',
    'actual_storage_retained', 'provider_source_attested',
    'stream_delivery_verified', 'fee_coverage_complete',
    'historical_replay_complete', 'native_power_loss_durability_verified',
    'broker_account_reconciled', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved'))
SSE_FALSE = frozenset((
    'actual_provider_access', 'actual_vault_access',
    'actual_stream_connection', 'actual_storage_retained',
    'provider_source_attested', 'stream_delivery_verified',
    'fee_coverage_complete', 'historical_replay_complete',
    'native_power_loss_durability_verified', 'broker_account_reconciled',
    'ready_for_routing', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved'))

PUBLIC_FIXTURE_KEYS = frozenset((
    'mode', 'source', 'passed', 'surfaces_verified', *CHECKS,
    *AUTHORITY_FLAGS, 'limitations'))


def _component(value, *, keys, source, false_fields):
    if type(value) is not dict or frozenset(value) != keys:
        raise ValueError('PAPER_TRANSPORT_UNCERTAINTY_MATRIX_SCHEMA')
    if type(value.get('source')) is not str or value['source'] != source:
        raise ValueError('PAPER_TRANSPORT_UNCERTAINTY_MATRIX_SOURCE')
    for key in keys - {'source'}:
        if type(value[key]) is not bool:
            raise ValueError('PAPER_TRANSPORT_UNCERTAINTY_MATRIX_TYPE')
        expected = key not in false_fields
        if value[key] is not expected:
            raise ValueError('PAPER_TRANSPORT_UNCERTAINTY_MATRIX_COMPONENT')


def review(get_recovery, post_transport, post_recovery, post_interruption,
        sse_reconnect):
    """Validate exact disposable component reports and return no raw evidence."""
    _component(get_recovery, keys=GET_KEYS,
        source='TEMPORARY_PROVIDER_READ_BUDGET_RECOVERY_FIXTURE',
        false_fields=GET_FALSE)
    _component(post_transport, keys=POST_TRANSPORT_KEYS,
        source='INJECTED_SINGLE_ATTEMPT_PAPER_SUBMISSION_FIXTURE',
        false_fields=POST_TRANSPORT_FALSE)
    _component(post_recovery, keys=POST_RECOVERY_KEYS,
        source='TEMPORARY_RESOLVED_UNKNOWN_POST_RECOVERY_FIXTURE',
        false_fields=POST_RECOVERY_FALSE)
    _component(post_interruption, keys=POST_INTERRUPTION_KEYS,
        source='TEMPORARY_REPLACED_OWNER_INTERRUPTION_FIXTURE',
        false_fields=POST_INTERRUPTION_FALSE)
    _component(sse_reconnect, keys=SSE_KEYS,
        source='TEMPORARY_INJECTED_ACTIVITY_SSE_RECONNECT_BUDGET_FIXTURE',
        false_fields=SSE_FALSE)
    checks = dict(
        get_auth_denial_review_bound=True,
        get_lost_acknowledgement_replayed=True,
        get_cooldown_and_capacity_preserved=True,
        post_single_attempt_and_safety_windows_bound=True,
        post_unknown_outcome_retained_for_original_id=True,
        post_terminal_recovery_lost_acknowledgement_replayed=True,
        post_original_request_never_rearmed=True,
        post_replaced_owner_interruption_review_bound=True,
        sse_failure_retained_without_automatic_retry=True,
        sse_due_retry_explicit_and_single=True,
        sse_emergency_stop_independent=True,
        sse_lost_acknowledgements_replayed_without_extra_get=True,
        sse_reconciliation_replay_byte_stable=True,
        all_component_schemas_exact=True,
        all_external_authority_denied=True)
    return dict(mode=MODE, source=SOURCE, passed=all(checks.values()),
        surfaces_verified=3, **checks,
        **{flag: False for flag in AUTHORITY_FLAGS},
        limitations=LIMITATIONS)


def fixture_report():
    """Run the four existing disposable fixtures; never use real credentials."""
    from nexus.alpaca_activity_sse_reconnect_budget import (
        fixture_report as sse_fixture)
    from nexus.alpaca_paper_submission import fixture_report as post_fixture
    from nexus.paper_provider_budget_recovery import (
        fixture_report as get_fixture)
    from nexus.paper_provider_post_recovery import (
        fixture_report as post_recovery_fixture)
    from nexus.paper_provider_post_interruption import (
        fixture_report as post_interruption_fixture)
    return review(get_fixture(), post_fixture(), post_recovery_fixture(),
        post_interruption_fixture(), sse_fixture())
