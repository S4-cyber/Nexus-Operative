import hashlib
import json
import os

import pytest

from tools import windows_installer_result_review as review


VERSION = "0.340.364"
SOURCE = "a" * 40
DIGEST = "b" * 64
EXECUTABLE_DIGEST = "d" * 64
SESSION = "12345678-1234-4234-8234-123456789abc"


def fixture():
    result = {
        "mode": "INSTALLER_SMOKE_RESULT", "version": VERSION, "source_commit": SOURCE,
        "installer_sha256": DIGEST, "session_id": SESSION,
        "standalone_executable_sha256": EXECUTABLE_DIGEST,
        "installed_executable_sha256": EXECUTABLE_DIGEST,
        "reinstalled_executable_sha256": EXECUTABLE_DIGEST,
        "maximum_root_process_timeout_seconds": 300, "output_drain_timeout_seconds": 10,
        "limitations": review.RESULT_LIMITATIONS,
        **{key: True for key in review.SEMANTIC_TRUE},
        **{key: False for key in review.SEMANTIC_FALSE},
    }
    phase = {
        "mode": "VERIFIED_INSTALLER_PHASE_EVIDENCE", "passed": True,
        "phases_verified": 10, "records_verified": 20, "version": VERSION,
        "source_commit": SOURCE, "installer_sha256": DIGEST, "session_id": SESSION,
        "exact_source_bound": True, "installer_digest_bound": True,
        "single_session_bound": True, "sequence_complete": True, "timeouts_respected": True,
        "maximum_root_process_timeout_seconds": 300, "output_drain_timeout_seconds": 10,
        "maximum_observed_duration_seconds": 1.25,
        "native_windows_acceptance": False, "actual_broker_acceptance": False,
        "live_orders_enabled": False, "money_movement_enabled": False,
        "release_approved": False, "limitations": "fixture",
    }
    return result, phase


def verify(result, phase):
    raw = json.dumps(result, sort_keys=True).encode()
    return review.verify(result, phase, raw_result_bytes=len(raw),
        raw_result_sha256=hashlib.sha256(raw).hexdigest(),
        expected_version=VERSION, expected_source_commit=SOURCE)


def test_exact_semantic_result_is_bound_to_phase_without_authority():
    assert "external_process_output_drains_bounded" in review.SEMANTIC_TRUE
    assert "process_tree_containment_verified" in review.SEMANTIC_FALSE
    assert "all_external_processes_bounded" not in review.RAW_KEYS
    assert "descendant containment" in review.RESULT_LIMITATIONS
    result = verify(*fixture())
    assert result["passed"] and result["phase_evidence_bound"]
    assert result["exact_source_bound"] and result["installer_digest_bound"]
    assert result["single_session_bound"] and result["session_id"] == SESSION
    assert result["standalone_executable_sha256"] == EXECUTABLE_DIGEST
    assert result["installed_executable_sha256"] == EXECUTABLE_DIGEST
    assert result["reinstalled_executable_sha256"] == EXECUTABLE_DIGEST
    assert result["installed_executable_matches_standalone"] is True
    assert result["reinstalled_executable_matches_standalone"] is True
    assert all(result[key] is True for key in review.SEMANTIC_TRUE)
    assert all(result[key] is False for key in review.SEMANTIC_FALSE)


@pytest.mark.parametrize("key", sorted(review.SEMANTIC_TRUE))
def test_every_required_true_semantic_is_enforced(key):
    result, phase = fixture()
    result[key] = False
    with pytest.raises(review.InstallerResultReviewError, match="result binding"):
        verify(result, phase)


@pytest.mark.parametrize("key", sorted(review.SEMANTIC_FALSE))
def test_every_required_false_semantic_is_enforced(key):
    result, phase = fixture()
    result[key] = True
    with pytest.raises(review.InstallerResultReviewError, match="result binding"):
        verify(result, phase)


@pytest.mark.parametrize(("target", "key", "value"), (
    ("result", "version", "0.340.1"), ("result", "source_commit", "c" * 40),
    ("result", "installer_sha256", "c" * 64), ("result", "session_id", "not-a-session"),
    ("result", "standalone_executable_sha256", "c" * 64),
    ("result", "installed_executable_sha256", "c" * 64),
    ("result", "reinstalled_executable_sha256", "c" * 64),
    ("result", "maximum_root_process_timeout_seconds", True),
    ("result", "output_drain_timeout_seconds", True), ("result", "limitations", "promoted"),
    ("phase", "version", "0.340.1"), ("phase", "source_commit", "c" * 40),
    ("phase", "installer_sha256", "c" * 64),
    ("phase", "session_id", "87654321-4321-4321-8321-cba987654321"),
    ("phase", "sequence_complete", False), ("phase", "release_approved", True),
    ("phase", "phases_verified", 10.0),
    ("phase", "records_verified", 20.0),
    ("phase", "maximum_root_process_timeout_seconds", 300.0),
    ("phase", "output_drain_timeout_seconds", 10.0),
    ("phase", "maximum_observed_duration_seconds", True),
    ("phase", "maximum_observed_duration_seconds", float("nan")),
    ("phase", "limitations", ""),
))
def test_stale_mismatched_or_promoted_result_and_phase_are_rejected(target, key, value):
    result, phase = fixture()
    {"result": result, "phase": phase}[target][key] = value
    with pytest.raises(review.InstallerResultReviewError):
        verify(result, phase)


def test_result_and_phase_shapes_are_closed():
    result, phase = fixture()
    result["private_path"] = "C:/private"
    with pytest.raises(review.InstallerResultReviewError, match="result shape"):
        verify(result, phase)
    result, phase = fixture()
    del phase["limitations"]
    with pytest.raises(review.InstallerResultReviewError, match="phase shape"):
        verify(result, phase)


def test_cli_binds_raw_bytes_rejects_duplicate_json_and_output_overwrite(tmp_path, monkeypatch, capsys):
    result, phase = fixture()
    result_path = tmp_path / "installer-result.json"
    phase_path = tmp_path / "installer-phase-review.json"
    output = tmp_path / "installer-result-review.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    phase_path.write_text(json.dumps(phase), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["result-review", "--input", str(result_path),
        "--phase-review", str(phase_path), "--expected-version", VERSION,
        "--expected-source-commit", SOURCE, "--output", str(output)])
    assert review.main() == 0
    parsed = json.loads(output.read_text())
    assert parsed["raw_result_bytes"] == len(result_path.read_bytes())
    assert parsed["raw_result_sha256"] == hashlib.sha256(result_path.read_bytes()).hexdigest()
    assert review.main() == 1
    assert "INSTALLER_RESULT_REVIEW_FAILED" in capsys.readouterr().out

    output.unlink()
    result_path.write_bytes(b'{"mode":1,"mode":2}')
    assert review.main() == 1 and not output.exists()


@pytest.mark.parametrize("name", ["installer-result.json", "installer-phase-review.json"])
def test_input_hardlinks_are_rejected(tmp_path, name):
    path = tmp_path / name
    path.write_text("{}")
    os.link(path, tmp_path / ("alias-" + name))
    with pytest.raises(review.InstallerResultReviewError, match="file"):
        review._load_json_raw(path)
