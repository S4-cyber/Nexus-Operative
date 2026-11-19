"""Fail-closed admission-authority inventory acceptance tests."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.authority_inventory import AUTHORITY_FLAGS, AUTHORITIES, snapshot


def test_inventory_binds_every_declared_route_without_storage(tmp_path, monkeypatch):
    root = tmp_path / "unused"
    monkeypatch.setenv("NEXUS_PORTABLE_DATA", str(root))
    report = snapshot(app)
    assert report["route_bindings_verified"] is True
    assert report["source_declaration_complete"] is True
    assert report["authority_count"] == len(AUTHORITIES) == 12
    assert len(report["authority_ids"]) == len(set(report["authority_ids"]))
    assert report["runtime_engagement_default"] == "OFF"
    assert report["external_ai_default"] == "OFF"
    assert not root.exists()
    assert all(report[flag] is False for flag in AUTHORITY_FLAGS)
    assert report["broker_order_method_present"] is False
    assert report["bank_transfer_method_present"] is False
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


def test_missing_declared_route_fails_closed():
    partial = SimpleNamespace(routes=[SimpleNamespace(
        path="/api/funding/preview", methods={"POST"})])
    with pytest.raises(ValueError, match="ADMISSION_AUTHORITY_ROUTE_INVENTORY_MISMATCH"):
        snapshot(partial)


def test_api_is_no_store_and_never_promotes_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA", str(tmp_path / "unused"))
    response = TestClient(app).get("/api/authority/inventory")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert report["route_bindings_verified"] is True
    assert all(report[flag] is False for flag in AUTHORITY_FLAGS)
    assert not (tmp_path / "unused").exists()
