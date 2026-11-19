import json
import os
import hashlib

import pytest

from nexus.artifact_manifest import manifest
from tools import windows_distribution_binding_review as review


VERSION = "0.340.108"
SOURCE = "a" * 40


def fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    installer = tmp_path / f"NexusOperative-Setup-{VERSION}.exe"
    installer.write_bytes(b"installer distribution fixture")
    size, digest = review._installer(installer)
    phase = {
        "mode": "VERIFIED_INSTALLER_PHASE_EVIDENCE", "passed": True,
        "phases_verified": 10, "records_verified": 20, "version": VERSION,
        "source_commit": SOURCE, "installer_sha256": digest,
        "session_id": "12345678-1234-4234-8234-123456789abc",
        "exact_source_bound": True, "installer_digest_bound": True, "single_session_bound": True,
        "sequence_complete": True, "timeouts_respected": True, "maximum_timeout_seconds": 300,
        "maximum_observed_duration_seconds": 1, "native_windows_acceptance": False,
        "actual_broker_acceptance": False, "live_orders_enabled": False,
        "money_movement_enabled": False, "release_approved": False, "limitations": "fixture",
    }
    result_review = {
        "mode": "VERIFIED_INSTALLER_RESULT_EVIDENCE", "version": VERSION,
        "source_commit": SOURCE, "installer_sha256": digest,
        "session_id": phase["session_id"], "raw_result_bytes": 123,
        "raw_result_sha256": "c" * 64, "maximum_phase_timeout_seconds": 300,
        "limitations": review.RESULT_LIMITATIONS,
        **{key: True for key in review.RESULT_TRUE},
        **{key: False for key in review.RESULT_FALSE},
    }
    result_path = tmp_path / review.INSTALLER_RESULT_REVIEW_NAME
    result_path.write_text(json.dumps(result_review, sort_keys=True), encoding="utf-8")
    result_raw = result_path.read_bytes()
    artifact = manifest([installer, result_path], VERSION, SOURCE)
    return installer, size, digest, phase, result_review, result_raw, artifact


def verify(installer, size, digest, phase, result_review, result_raw, artifact):
    return review.verify(phase, result_review, artifact,
        installer_result_bytes=len(result_raw),
        installer_result_sha256=hashlib.sha256(result_raw).hexdigest(),
        expected_version=VERSION, expected_source_commit=SOURCE,
        installer_name=installer.name, installer_bytes=size, installer_sha256=digest)


def test_exact_phase_installer_and_manifest_binding_passes(tmp_path):
    parts = fixture(tmp_path)
    result = verify(*parts)
    assert result["passed"] and result["phase_evidence_bound"] and result["artifact_manifest_bound"]
    assert result["final_installer_rehashed"] and result["installer_sha256"] == parts[2]
    assert result["installer_result_review_bound"] and result["installer_result_semantics_bound"]
    assert result["installer_result_session_id"] == parts[3]["session_id"]
    assert all(result[key] is False for key in (
        "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
        "money_movement_enabled", "release_approved"))


@pytest.mark.parametrize("mutation", (
    lambda phase, result, artifact: phase.update(version="0.340.46"),
    lambda phase, result, artifact: phase.update(source_commit="b" * 40),
    lambda phase, result, artifact: phase.update(installer_sha256="b" * 64),
    lambda phase, result, artifact: phase.update(session_id="not-a-session"),
    lambda phase, result, artifact: phase.update(passed=False),
    lambda phase, result, artifact: phase.update(phases_verified=10.0),
    lambda phase, result, artifact: phase.update(records_verified=20.0),
    lambda phase, result, artifact: phase.update(maximum_timeout_seconds=300.0),
    lambda phase, result, artifact: phase.update(release_approved=True),
    lambda phase, result, artifact: result.update(version="0.340.46"),
    lambda phase, result, artifact: result.update(source_commit="b" * 40),
    lambda phase, result, artifact: result.update(installer_sha256="b" * 64),
    lambda phase, result, artifact: result.update(session_id="not-a-session"),
    lambda phase, result, artifact: result.update(first_run_paper_defaults=False),
    lambda phase, result, artifact: result.update(release_approved=True),
    lambda phase, result, artifact: artifact.update(version="0.340.46"),
    lambda phase, result, artifact: artifact.update(source_commit="b" * 40),
    lambda phase, result, artifact: artifact.update(release_approved=True),
    lambda phase, result, artifact: artifact["artifacts"][0].update(bytes=1),
    lambda phase, result, artifact: artifact["artifacts"][0].update(sha256="b" * 64),
    lambda phase, result, artifact: artifact["artifacts"].append({"name": "../escape", "bytes": 1, "sha256": "b" * 64}),
))
def test_stale_or_promoted_phase_and_manifest_are_rejected(tmp_path, mutation):
    parts = fixture(tmp_path)
    mutation(parts[3], parts[4], parts[6])
    with pytest.raises(review.DistributionBindingError):
        verify(*parts)


def test_missing_or_changed_result_review_manifest_entry_is_rejected(tmp_path):
    parts = fixture(tmp_path)
    parts[6]["artifacts"] = [entry for entry in parts[6]["artifacts"]
        if entry["name"] != review.INSTALLER_RESULT_REVIEW_NAME]
    with pytest.raises(review.DistributionBindingError, match="result review manifest binding"):
        verify(*parts)

    parts = fixture(tmp_path / "changed")
    entry = next(row for row in parts[6]["artifacts"]
        if row["name"] == review.INSTALLER_RESULT_REVIEW_NAME)
    entry["sha256"] = "d" * 64
    with pytest.raises(review.DistributionBindingError, match="result review manifest binding"):
        verify(*parts)


def test_cli_rehashes_final_installer_and_refuses_output_overwrite(tmp_path, monkeypatch):
    installer, _, digest, phase, result_review, result_raw, artifact = fixture(tmp_path)
    phase_path = tmp_path / "phase.json"
    result_path = tmp_path / review.INSTALLER_RESULT_REVIEW_NAME
    manifest_path = tmp_path / "manifest.json"
    output = tmp_path / "binding.json"
    phase_path.write_text(json.dumps(phase))
    result_path.write_bytes(result_raw)
    manifest_path.write_text(json.dumps(artifact))
    monkeypatch.setattr("sys.argv", ["binding-review", "--phase-review", str(phase_path),
        "--installer-result-review", str(result_path),
        "--artifact-manifest", str(manifest_path), "--installer", str(installer),
        "--expected-version", VERSION, "--expected-source-commit", SOURCE, "--output", str(output)])
    assert review.main() == 0
    assert json.loads(output.read_text())["installer_sha256"] == digest
    assert review.main() == 1

    installer.write_bytes(b"changed after smoke")
    output.unlink()
    assert review.main() == 1


def test_final_installer_rehash_rejects_hardlinked_alias(tmp_path):
    installer=tmp_path/'installer.exe';installer.write_bytes(b'installer fixture')
    os.link(installer,tmp_path/'retained-alias.exe')
    with pytest.raises(review.DistributionBindingError,match='installer file'):
        review._installer(installer)


@pytest.mark.parametrize('name',[
    'phase-review.json', review.INSTALLER_RESULT_REVIEW_NAME, 'artifact-manifest.json'])
def test_binding_json_hardlink_is_rejected(tmp_path,name):
    path=tmp_path/name;path.write_text('{}');os.link(path,tmp_path/('alias-'+name))
    with pytest.raises(review.DistributionBindingError,match='json file'):
        review._read_json(path)
