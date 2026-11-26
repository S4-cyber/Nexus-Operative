"""Cross-surface POST/GET/SSE uncertainty coverage remains fail closed."""
import copy

import pytest

from nexus import paper_transport_uncertainty_matrix as matrix


@pytest.fixture(scope='module')
def components():
    from nexus.alpaca_activity_sse_reconnect_budget import (
        fixture_report as sse_fixture)
    from nexus.alpaca_paper_submission import fixture_report as post_fixture
    from nexus.paper_provider_budget_recovery import (
        fixture_report as get_fixture)
    from nexus.paper_provider_post_recovery import (
        fixture_report as post_recovery_fixture)
    return (get_fixture(), post_fixture(), post_recovery_fixture(),
        sse_fixture())


def test_complete_matrix_is_compact_exact_and_non_authoritative(components):
    before=copy.deepcopy(components)
    result=matrix.review(*components)
    assert frozenset(result)==matrix.PUBLIC_FIXTURE_KEYS
    assert result['mode']==matrix.MODE and result['source']==matrix.SOURCE
    assert result['passed'] is True and result['surfaces_verified']==3
    assert all(result[key] is True for key in matrix.CHECKS)
    assert all(result[key] is False for key in matrix.AUTHORITY_FLAGS)
    assert components==before
    assert matrix.review(*components)==result
    assert all(source not in result.values() for source in (
        components[0]['source'],components[1]['source'],components[2]['source'],
        components[3]['source']))


@pytest.mark.parametrize(('component','key'),(
    (0,'exact_review_block_bound'),
    (1,'exactly_one_post'),
    (1,'post_transport_controls_rechecked'),
    (1,'typed_local_transport_authorization_consumed'),
    (1,'pre_post_journal_writer_exclusion'),
    (1,'explicit_emergency_stop_binding'),
    (1,'trusted_transport_deadline_enforced'),
    (1,'terminal_store_bindings_rechecked'),
    (1,'safety_closure_cold_replayed'),
    (1,'transport_loss_preserved_for_original_id_lookup'),
    (2,'recovery_acknowledgement_cold_replayed'),
    (2,'original_request_not_rearmed'),
    (3,'first_failure_retained_without_retry'),
    (3,'emergency_stop_retires_pending_without_external_access'),
    (3,'checkpoint_reconciliation_replay_is_byte_stable')))
def test_matrix_rejects_missing_required_treatment(components,component,key):
    changed=copy.deepcopy(components);changed[component][key]=False
    with pytest.raises(ValueError,match='MATRIX_COMPONENT'):
        matrix.review(*changed)


@pytest.mark.parametrize(('component','key'),(
    (0,'actual_provider_access'),
    (1,'ready_for_routing'),
    (2,'broker_account_reconciled'),
    (3,'stream_delivery_verified'),
    (3,'release_approved')))
def test_matrix_rejects_promoted_authority(components,component,key):
    changed=copy.deepcopy(components);changed[component][key]=True
    with pytest.raises(ValueError,match='MATRIX_COMPONENT'):
        matrix.review(*changed)


@pytest.mark.parametrize('component',(0,1,2,3))
def test_matrix_rejects_schema_extensions(components,component):
    changed=copy.deepcopy(components);changed[component]['private_extension']=True
    with pytest.raises(ValueError,match='MATRIX_SCHEMA'):
        matrix.review(*changed)


def test_matrix_rejects_type_confusion_and_forged_source(components):
    changed=copy.deepcopy(components);changed[0]['passed']=1
    with pytest.raises(ValueError,match='MATRIX_TYPE'):
        matrix.review(*changed)
    class EqualSource(str):
        pass
    changed=copy.deepcopy(components)
    changed[1]['source']=EqualSource(changed[1]['source'])
    with pytest.raises(ValueError,match='MATRIX_SOURCE'):
        matrix.review(*changed)


def test_disposable_fixture_report_matches_pure_review(components):
    result=matrix.fixture_report()
    assert result==matrix.review(*components)
