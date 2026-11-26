"""Fail-closed admission-authority inventory acceptance tests."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus import authority_inventory
from nexus.authority_inventory import (AUTHORITY_FLAGS, AUTHORITIES,
    EXTERNAL_TRANSPORT_BOUNDARIES, EXTERNAL_TRANSPORT_BOUNDARY_PUBLIC_KEYS,
    MUTATING_ROUTES, snapshot)


def test_inventory_binds_every_declared_route_without_storage(tmp_path, monkeypatch):
    root = tmp_path / "unused"
    monkeypatch.setenv("NEXUS_PORTABLE_DATA", str(root))
    report = snapshot(app)
    assert report["route_bindings_verified"] is True
    assert report["source_declaration_complete"] is True
    assert report["authority_count"] == len(AUTHORITIES) == 12
    assert report["mutating_route_count"] == len(MUTATING_ROUTES) == 52
    assert len({(row["method"], row["path"])
        for row in report["mutating_routes"]}) == 52
    assert len(report["authority_ids"]) == len(set(report["authority_ids"]))
    assert report["runtime_engagement_default"] == "OFF"
    assert report["external_ai_default"] == "OFF"
    assert not root.exists()
    assert all(report[flag] is False for flag in AUTHORITY_FLAGS)
    assert report["broker_order_route_present"] is False
    assert report["bank_transfer_route_present"] is False
    assert report["cloud_control_route_present"] is False
    assert report["broker_order_transport_present"] is True
    assert report["ai_advisory_transport_present"] is True
    assert report["bank_transfer_transport_present"] is False
    assert report["cloud_control_transport_present"] is False
    assert report["automatic_resume_present"] is False
    assert report["learned_policy_promotion_present"] is False


def test_every_authority_denies_external_and_automatic_authority():
    report = snapshot(app)
    for authority in report["authorities"]:
        assert authority["automatic_start"] is False
        assert authority["automatic_resume"] is False
        assert authority["learned_policy_promotion"] is False
        assert all(authority[flag] is False for flag in AUTHORITY_FLAGS)
    by_id = {row["id"]: row for row in report["authorities"]}
    assert by_id["TIER_PERMISSION"]["default"] == "OFF"
    assert by_id["EXPERIMENTAL_FORWARD_SESSION"]["default"] == "STOPPED"
    assert by_id["OPTIONAL_AI_ADVISORY"]["default"] == "OFF"
    assert by_id["BROKER_PAPER_READ"]["default"] == "OFF"
    assert by_id["PRODUCTION_RELEASE"]["routes"] == []
    assert by_id["PRODUCTION_RELEASE"]["default"] == "NOT_APPROVED"
    assert by_id["LOCAL_PAPER_REDUCING_EXIT"]["reducing_exit_available_during_cut"] is True


def test_external_transport_presence_is_exact_but_never_authority():
    report=snapshot(app)
    boundaries=report['external_transport_boundaries']
    assert report['external_transport_boundaries_verified'] is True
    assert report['external_transport_boundary_count']==len(boundaries)==2
    assert all(frozenset(row)==EXTERNAL_TRANSPORT_BOUNDARY_PUBLIC_KEYS
        for row in boundaries)
    by_id={row['id']:row for row in boundaries}
    broker=by_id['ALPACA_PAPER_SINGLE_ATTEMPT_ORDER_POST']
    assert broker['capability']=='BROKER_PAPER_ORDER_POST'
    assert broker['default']=='OFF_UNROUTED' and not broker['app_route_bound']
    advisory=by_id['OPENAI_RESEARCH_ADVISORY_POST']
    assert advisory['capability']=='AI_ADVISORY_POST'
    assert advisory['default']=='OFF' and advisory['app_route_bound']
    for row in boundaries:
        assert row['explicit_confirmation_required'] is True
        assert row['automatic_start'] is row['automatic_resume'] is False
        assert all(row[flag] is False for flag in AUTHORITY_FLAGS)


@pytest.mark.parametrize('mutation,code',[
    (lambda rows:rows[:-1], 'INVENTORY_MISMATCH'),
    (lambda rows:rows+(rows[0],), 'DECLARATION_DUPLICATE'),
    (lambda rows:({**rows[0], 'method':'GET'},*rows[1:]), 'DECLARATION_INVALID'),
    (lambda rows:({**rows[0], 'target':'https://paper-api.alpaca.markets/v2/live-orders'},*rows[1:]), 'DECLARATION_INVALID'),
    (lambda rows:({**rows[0], 'capability':'BROKER_LIVE_ORDER_POST'},*rows[1:]), 'DECLARATION_INVALID'),
    (lambda rows:({**rows[0], 'automatic_resume':True},*rows[1:]), 'DECLARATION_INVALID'),
])
def test_external_transport_declaration_mutations_fail_closed(monkeypatch,mutation,code):
    monkeypatch.setattr(authority_inventory,'EXTERNAL_TRANSPORT_BOUNDARIES',
        tuple(mutation(EXTERNAL_TRANSPORT_BOUNDARIES)))
    with pytest.raises(ValueError,match='ADMISSION_AUTHORITY_EXTERNAL_BOUNDARY_'+code):
        snapshot(app)


def test_missing_declared_route_fails_closed():
    partial = SimpleNamespace(routes=[SimpleNamespace(
        path="/api/funding/preview", methods={"POST"})])
    with pytest.raises(ValueError, match="ADMISSION_AUTHORITY_MUTATING_ROUTE_INVENTORY_MISMATCH"):
        snapshot(partial)


@pytest.mark.parametrize("path", [
    "/api/funding/transfer",
    "/api/execution/live/order",
    "/api/cloud/deploy",
    "/api/cloud/remote-control",
])
def test_unreviewed_external_mutation_route_fails_closed(path):
    unexpected=SimpleNamespace(path=path,methods={"POST"})
    candidate=SimpleNamespace(routes=[*app.routes,unexpected])
    with pytest.raises(ValueError,
            match="ADMISSION_AUTHORITY_MUTATING_ROUTE_INVENTORY_MISMATCH"):
        snapshot(candidate)


def test_removed_mutation_route_fails_closed():
    candidate=SimpleNamespace(routes=[route for route in app.routes
        if getattr(route,"path",None)!="/api/funding/preview"])
    with pytest.raises(ValueError,
            match="ADMISSION_AUTHORITY_MUTATING_ROUTE_INVENTORY_MISMATCH"):
        snapshot(candidate)


def test_duplicate_or_noncanonical_declaration_fails_closed(monkeypatch):
    monkeypatch.setattr(authority_inventory,"MUTATING_ROUTES",
        MUTATING_ROUTES+(MUTATING_ROUTES[0],))
    with pytest.raises(ValueError,
            match="ADMISSION_AUTHORITY_MUTATING_ROUTE_DECLARATION_DUPLICATE"):
        snapshot(app)
    monkeypatch.setattr(authority_inventory,"MUTATING_ROUTES",
        MUTATING_ROUTES+(("POST","/api/fake","FAKE",True),))
    with pytest.raises(ValueError,
            match="ADMISSION_AUTHORITY_MUTATING_ROUTE_DECLARATION_INVALID"):
        snapshot(app)


@pytest.mark.parametrize(('authority_class','capability'),[
    ('BROKER_ORDRE','BROKER_ORDER'),
    ('BROKER_ORDER','BROKER_ORDRE'),
    ('UNREVIEWED_CLASS','LOCAL_ONLY'),
    ('PORTAL_CONTROL','UNREVIEWED_CAPABILITY'),
])
def test_unknown_or_misspelled_classification_fails_closed(
        monkeypatch,authority_class,capability):
    changed=list(MUTATING_ROUTES)
    method,path,_,_=changed[0]
    changed[0]=(method,path,authority_class,capability)
    monkeypatch.setattr(authority_inventory,"MUTATING_ROUTES",tuple(changed))
    with pytest.raises(ValueError,
            match="ADMISSION_AUTHORITY_MUTATING_ROUTE_DECLARATION_INVALID"):
        snapshot(app)


def test_api_is_no_store_and_never_promotes_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA", str(tmp_path / "unused"))
    response = TestClient(app).get("/api/authority/inventory")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert report["route_bindings_verified"] is True
    assert all(report[flag] is False for flag in AUTHORITY_FLAGS)
    assert not (tmp_path / "unused").exists()
