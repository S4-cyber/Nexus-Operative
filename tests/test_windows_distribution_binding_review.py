import json

import pytest

from nexus.artifact_manifest import manifest
from tools import windows_distribution_binding_review as review


VERSION = "0.340.47"
SOURCE = "a" * 40


def fixture(tmp_path):
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
    artifact = manifest([installer], VERSION, SOURCE)
    return installer, size, digest, phase, artifact


def verify(installer, size, digest, phase, artifact):
    return review.verify(phase, artifact, expected_version=VERSION, expected_source_commit=SOURCE,
        installer_name=installer.name, installer_bytes=size, installer_sha256=digest)


def test_exact_phase_installer_and_manifest_binding_passes(tmp_path):
    installer, size, digest, phase, artifact = fixture(tmp_path)
    result = verify(installer, size, digest, phase, artifact)
    assert result["passed"] and result["phase_evidence_bound"] and result["artifact_manifest_bound"]
    assert result["final_installer_rehashed"] and result["installer_sha256"] == digest
    assert all(result[key] is False for key in (
        "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
        "money_movement_enabled", "release_approved"))


@pytest.mark.parametrize("mutation", (
    lambda phase, artifact: phase.update(version="0.340.46"),
    lambda phase, artifact: phase.update(source_commit="b" * 40),
    lambda phase, artifact: phase.update(installer_sha256="b" * 64),
    lambda phase, artifact: phase.update(session_id="not-a-session"),
    lambda phase, artifact: phase.update(passed=False),
    lambda phase, artifact: phase.update(release_approved=True),
    lambda phase, artifact: artifact.update(version="0.340.46"),
    lambda phase, artifact: artifact.update(source_commit="b" * 40),
    lambda phase, artifact: artifact.update(release_approved=True),
    lambda phase, artifact: artifact["artifacts"][0].update(bytes=1),
    lambda phase, artifact: artifact["artifacts"][0].update(sha256="b" * 64),
    lambda phase, artifact: artifact["artifacts"].append({"name": "../escape", "bytes": 1, "sha256": "b" * 64}),
))
def test_stale_or_promoted_phase_and_manifest_are_rejected(tmp_path, mutation):
    installer, size, digest, phase, artifact = fixture(tmp_path)
    mutation(phase, artifact)
    with pytest.raises(review.DistributionBindingError):
        verify(installer, size, digest, phase, artifact)


def test_cli_rehashes_final_installer_and_refuses_output_overwrite(tmp_path, monkeypatch):
    installer, _, digest, phase, artifact = fixture(tmp_path)
    phase_path = tmp_path / "phase.json"
    manifest_path = tmp_path / "manifest.json"
    output = tmp_path / "binding.json"
    phase_path.write_text(json.dumps(phase))
    manifest_path.write_text(json.dumps(artifact))
    monkeypatch.setattr("sys.argv", ["binding-review", "--phase-review", str(phase_path),
        "--artifact-manifest", str(manifest_path), "--installer", str(installer),
        "--expected-version", VERSION, "--expected-source-commit", SOURCE, "--output", str(output)])
    assert review.main() == 0
    assert json.loads(output.read_text())["installer_sha256"] == digest
    assert review.main() == 1

    installer.write_bytes(b"changed after smoke")
    output.unlink()
    assert review.main() == 1
