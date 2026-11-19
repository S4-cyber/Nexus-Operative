import hashlib
import json
from pathlib import Path

import pytest

from nexus.artifact_manifest import manifest
from tools import windows_owner_profile_review as review


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.340.108"
SOURCE = "a" * 40
SESSION = "12345678-1234-4234-8234-123456789abc"


def _sha(path):
    raw = path.read_bytes()
    return len(raw), hashlib.sha256(raw).hexdigest()


def fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    installer = tmp_path / f"NexusOperative-Setup-{VERSION}.exe"
    executable = tmp_path / "NexusOperative.exe"
    probe_tool = tmp_path / review.PROBE_NAME
    reviewer_tool = tmp_path / review.REVIEWER_NAME
    installer.write_bytes(b"installer")
    executable.write_bytes(b"installed executable")
    probe_tool.write_bytes((ROOT / "tools/windows_owner_profile_probe.ps1").read_bytes())
    reviewer_tool.write_bytes((ROOT / "tools/windows_owner_profile_review.py").read_bytes())
    installer_info, executable_info = _sha(installer), _sha(executable)
    probe_info, reviewer_info = _sha(probe_tool), _sha(reviewer_tool)
    installer_result = {
        "mode": "VERIFIED_INSTALLER_RESULT_EVIDENCE", "version": VERSION,
        "source_commit": SOURCE, "installer_sha256": installer_info[1],
        "session_id": SESSION, "raw_result_bytes": 123,
        "raw_result_sha256": "c" * 64, "maximum_phase_timeout_seconds": 300,
        "limitations": review.INSTALLER_RESULT_LIMITATIONS,
        **{key: True for key in review.INSTALLER_RESULT_TRUE},
        **{key: False for key in review.INSTALLER_RESULT_FALSE},
    }
    installer_result_path = tmp_path / review.INSTALLER_RESULT_REVIEW_NAME
    installer_result_path.write_text(json.dumps(installer_result, sort_keys=True), encoding="utf-8")
    installer_result_raw = installer_result_path.read_bytes()
    installer_result_info = (len(installer_result_raw), hashlib.sha256(installer_result_raw).hexdigest())
    binding = {
        "mode": "VERIFIED_INSTALLER_DISTRIBUTION_BINDING", "passed": True,
        "version": VERSION, "source_commit": SOURCE, "installer_name": installer.name,
        "installer_bytes": installer_info[0], "installer_sha256": installer_info[1],
        "installer_result_session_id": SESSION,
        "installer_result_review_bytes": installer_result_info[0],
        "installer_result_review_sha256": installer_result_info[1],
        "phase_evidence_bound": True, "installer_result_review_bound": True,
        "installer_result_semantics_bound": True, "artifact_manifest_bound": True,
        "final_installer_rehashed": True, "native_windows_acceptance": False,
        "actual_broker_acceptance": False, "live_orders_enabled": False,
        "money_movement_enabled": False, "release_approved": False, "limitations": "fixture",
    }
    artifact = manifest(
        [installer, executable, probe_tool, reviewer_tool, installer_result_path], VERSION, SOURCE)
    artifact_raw = json.dumps(artifact, sort_keys=True).encode("utf-8")
    binding_raw = json.dumps(binding, sort_keys=True).encode("utf-8")
    envelope = {
        "schema": "nexus.artifacts.v1", "version": VERSION, "source_commit": SOURCE,
        "signed": False, "release_approved": False, "artifacts": [
            {"name": review.ARTIFACT_MANIFEST_NAME, "bytes": len(artifact_raw),
             "sha256": hashlib.sha256(artifact_raw).hexdigest()},
            {"name": review.DISTRIBUTION_BINDING_NAME, "bytes": len(binding_raw),
             "sha256": hashlib.sha256(binding_raw).hexdigest()},
        ],
    }
    probe = {
        "profile": review.PROBE_PROFILE, "version": VERSION, "source_commit": SOURCE,
        "installer_sha256": installer_info[1], "installed_executable_sha256": executable_info[1],
        "probe_script_sha256": probe_info[1], "session_id": SESSION,
        "timestamp_utc": "2026-10-30T12:34:56.1234567Z", "windows_version": "10.0.26100.1",
        "architecture": "X64", "ordinary_user_token": True, "local_appdata_install": True,
        "local_appdata_data": True, "current_identity_owns_probe": True,
        "directory_acl_inheritance_enabled": True, "file_acl_inheritance_enabled": True,
        "create_write_read_verified": True, "exclusive_lock_verified": True,
        "replace_verified": True, "rename_delete_verified": True, "cleanup_verified": True,
        "private_paths_retained": False, "actual_broker_acceptance": False,
        "live_orders_enabled": False, "money_movement_enabled": False, "release_approved": False,
        "limitations": review.PROBE_LIMITATIONS,
    }
    return (probe, binding, artifact, probe_info, reviewer_info, probe_tool, reviewer_tool,
            envelope, artifact_raw, binding_raw, installer_result, installer_result_raw)


def verify(parts):
    (probe, binding, artifact, probe_info, reviewer_info, _, _, envelope,
     artifact_raw, binding_raw, installer_result, installer_result_raw) = parts
    return review.verify(probe, binding, installer_result, artifact, envelope,
        artifact_manifest_raw=artifact_raw, distribution_binding_raw=binding_raw,
        installer_result_raw=installer_result_raw,
        expected_version=VERSION, expected_source_commit=SOURCE,
        probe_tool=probe_info, reviewer_tool=reviewer_info,
        executing_reviewer_tool=reviewer_info)


def test_exact_owner_profile_probe_is_bound_without_promoting_authority(tmp_path):
    result = verify(fixture(tmp_path))
    assert result["passed"] and result["ordinary_user_profile_checked"]
    assert result["effective_acl_operations_checked"] and result["exact_distribution_bound"]
    assert result["distribution_envelope_bound"] and result["artifact_manifest_file_bound"]
    assert result["distribution_binding_file_bound"]
    assert result["installer_result_review_bound"] and result["installer_result_semantics_bound"]
    assert result["installed_executable_bound"] and result["probe_tool_manifest_bound"]
    assert result["review_tool_manifest_bound"] and result["executing_reviewer_bound"]
    assert result["private_paths_retained"] is False
    assert all(result[key] is False for key in (
        "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
        "money_movement_enabled", "release_approved"))


@pytest.mark.parametrize(("target", "key", "value"), (
    ("probe", "ordinary_user_token", False),
    ("probe", "private_paths_retained", True),
    ("probe", "source_commit", "b" * 40),
    ("probe", "installer_sha256", "b" * 64),
    ("probe", "installed_executable_sha256", "b" * 64),
    ("probe", "probe_script_sha256", "b" * 64),
    ("probe", "architecture", "Unknown"),
    ("probe", "windows_version", "private"),
    ("probe", "limitations", "promoted"),
    ("binding", "passed", False),
    ("binding", "installer_result_semantics_bound", False),
    ("binding", "installer_result_review_sha256", "b" * 64),
    ("binding", "release_approved", True),
    ("binding", "installer_sha256", "b" * 64),
    ("manifest", "release_approved", True),
    ("manifest", "source_commit", "b" * 40),
))
def test_stale_private_or_promoted_evidence_is_rejected(tmp_path, target, key, value):
    parts = fixture(tmp_path)
    subject = {"probe": parts[0], "binding": parts[1], "manifest": parts[2]}[target]
    subject[key] = value
    with pytest.raises(review.OwnerProfileReviewError):
        verify(parts)


def test_missing_or_changed_manifest_tools_are_rejected(tmp_path):
    parts = fixture(tmp_path)
    parts[2]["artifacts"] = [row for row in parts[2]["artifacts"] if row["name"] != review.PROBE_NAME]
    with pytest.raises(review.OwnerProfileReviewError):
        verify(parts)

    parts = fixture(tmp_path / "second")
    changed = list(parts[3])
    changed[1] = "b" * 64
    with pytest.raises(review.OwnerProfileReviewError):
        review.verify(parts[0], parts[1], parts[10], parts[2], parts[7],
            artifact_manifest_raw=parts[8], distribution_binding_raw=parts[9],
            installer_result_raw=parts[11],
            expected_version=VERSION, expected_source_commit=SOURCE,
            probe_tool=tuple(changed), reviewer_tool=parts[4],
            executing_reviewer_tool=parts[4])


def test_installer_result_semantics_and_manifest_binding_are_required(tmp_path):
    parts = fixture(tmp_path)
    parts[10]["first_run_paper_defaults"] = False
    with pytest.raises(review.OwnerProfileReviewError, match="installer result"):
        verify(parts)

    parts = fixture(tmp_path / "promoted")
    parts[10]["release_approved"] = True
    with pytest.raises(review.OwnerProfileReviewError, match="installer result"):
        verify(parts)

    parts = fixture(tmp_path / "missing")
    parts[2]["artifacts"] = [entry for entry in parts[2]["artifacts"]
        if entry["name"] != review.INSTALLER_RESULT_REVIEW_NAME]
    with pytest.raises(review.OwnerProfileReviewError, match="manifest tool binding"):
        verify(parts)

    parts = list(fixture(tmp_path / "changed-bytes"))
    parts[11] = parts[11] + b"\n"
    with pytest.raises(review.OwnerProfileReviewError, match="installer result binding"):
        verify(tuple(parts))


def test_executing_reviewer_must_match_manifest_bound_shipped_reviewer(tmp_path):
    parts=fixture(tmp_path)
    changed=(parts[4][0]+1,'b'*64)
    with pytest.raises(review.OwnerProfileReviewError,match='executing reviewer binding'):
        review.verify(parts[0],parts[1],parts[10],parts[2],parts[7],
            artifact_manifest_raw=parts[8],distribution_binding_raw=parts[9],
            installer_result_raw=parts[11],
            expected_version=VERSION,expected_source_commit=SOURCE,
            probe_tool=parts[3],reviewer_tool=parts[4],
            executing_reviewer_tool=changed)


@pytest.mark.parametrize("target", ["binding", "manifest"])
def test_semantically_equivalent_inner_file_substitution_breaks_outer_envelope(tmp_path, target):
    parts = fixture(tmp_path)
    changed = list(parts)
    index = 9 if target == "binding" else 8
    changed[index] = json.dumps(parts[1 if target == "binding" else 2],
        sort_keys=True, indent=2).encode("utf-8")
    assert json.loads(changed[index]) == (parts[1] if target == "binding" else parts[2])
    with pytest.raises(review.OwnerProfileReviewError, match="distribution envelope"):
        verify(tuple(changed))


def test_probe_timestamp_session_and_exact_shape_are_strict(tmp_path):
    for key, value in (("timestamp_utc", "2026-10-30T13:34:56+01:00"),
                       ("session_id", "not-a-session")):
        parts = fixture(tmp_path / key)
        parts[0][key] = value
        with pytest.raises(review.OwnerProfileReviewError):
            verify(parts)
    parts = fixture(tmp_path / "extra")
    parts[0]["private_path"] = "C:/private"
    with pytest.raises(review.OwnerProfileReviewError):
        verify(parts)


def test_cli_refuses_duplicate_json_and_output_overwrite(tmp_path, monkeypatch, capsys):
    parts = fixture(tmp_path)
    (probe, binding, artifact, _, _, probe_tool, reviewer_tool, envelope,
     artifact_raw, binding_raw, installer_result, installer_result_raw) = parts
    probe_path = tmp_path / "probe.json"
    binding_path = tmp_path / "binding.json"
    manifest_path = tmp_path / "manifest.json"
    envelope_path = tmp_path / "distribution-envelope.json"
    installer_result_path = tmp_path / review.INSTALLER_RESULT_REVIEW_NAME
    output = tmp_path / "review.json"
    probe_path.write_text(json.dumps(probe), encoding="utf-8")
    binding_path.write_bytes(binding_raw)
    manifest_path.write_bytes(artifact_raw)
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    installer_result_path.write_bytes(installer_result_raw)
    monkeypatch.setattr("sys.argv", ["owner-review", "--probe", str(probe_path),
        "--distribution-binding", str(binding_path), "--artifact-manifest", str(manifest_path),
        "--installer-result-review", str(installer_result_path),
        "--distribution-envelope", str(envelope_path),
        "--probe-tool", str(probe_tool), "--review-tool", str(reviewer_tool), "--expected-version", VERSION,
        "--expected-source-commit", SOURCE, "--output", str(output)])
    assert review.main() == 0
    assert json.loads(output.read_text())["mode"] == "VERIFIED_OWNER_WINDOWS_PROFILE_REVIEW"
    assert review.main() == 1
    assert "OWNER_WINDOWS_PROFILE_REVIEW_FAILED" in capsys.readouterr().out

    output.unlink()
    probe_path.write_bytes(b'{"profile":1,"profile":2}')
    assert review.main() == 1


def test_cli_rejects_different_executing_reviewer_copy(tmp_path,monkeypatch,capsys):
    parts=fixture(tmp_path)
    (probe,binding,artifact,_,_,probe_tool,reviewer_tool,envelope,artifact_raw,binding_raw,
     installer_result,installer_result_raw)=parts
    probe_path=tmp_path/'probe.json';probe_path.write_text(json.dumps(probe),encoding='utf-8')
    binding_path=tmp_path/'binding.json';binding_path.write_bytes(binding_raw)
    manifest_path=tmp_path/'manifest.json';manifest_path.write_bytes(artifact_raw)
    envelope_path=tmp_path/'distribution-envelope.json';envelope_path.write_text(json.dumps(envelope),encoding='utf-8')
    installer_result_path=tmp_path/review.INSTALLER_RESULT_REVIEW_NAME
    installer_result_path.write_bytes(installer_result_raw)
    modified_dir=tmp_path/'executing';modified_dir.mkdir()
    modified=modified_dir/review.REVIEWER_NAME
    modified.write_bytes(reviewer_tool.read_bytes()+b'\n# different executing reviewer\n')
    output=tmp_path/'review.json'
    monkeypatch.setattr(review,'__file__',str(modified))
    monkeypatch.setattr('sys.argv',['owner-review','--probe',str(probe_path),
        '--distribution-binding',str(binding_path),'--artifact-manifest',str(manifest_path),
        '--installer-result-review',str(installer_result_path),
        '--distribution-envelope',str(envelope_path),'--probe-tool',str(probe_tool),
        '--review-tool',str(reviewer_tool),'--expected-version',VERSION,
        '--expected-source-commit',SOURCE,'--output',str(output)])
    assert review.main()==1 and not output.exists()
    assert 'OWNER_WINDOWS_PROFILE_REVIEW_FAILED' in capsys.readouterr().out


def test_powershell_probe_retains_no_identity_or_path_fields():
    script = (ROOT / "tools/windows_owner_profile_probe.ps1").read_text(encoding="utf-8")
    assert "IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)" in script
    assert "AreAccessRulesProtected" in script
    assert "[IO.File]::Replace" in script and "[IO.FileShare]::None" in script
    assert "Remove-Item -LiteralPath $probeRoot -Recurse -Force" in script
    assert "private_paths_retained = $false" in script
    assert "actual_broker_acceptance = $false" in script
    assert "live_orders_enabled = $false" in script
    assert "money_movement_enabled = $false" in script
    assert "release_approved = $false" in script
    record = script[script.index("$record = [ordered]@{"):]
    assert "identity.Name" not in record and "identity.User.Value" not in record
    assert "installer_path =" not in record and "executable_path =" not in record and "data_path =" not in record
