import hashlib
import json
from pathlib import Path

import pytest

from nexus.artifact_manifest import manifest
from tools import windows_owner_profile_review as review


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.340.62"
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
    binding = {
        "mode": "VERIFIED_INSTALLER_DISTRIBUTION_BINDING", "passed": True,
        "version": VERSION, "source_commit": SOURCE, "installer_name": installer.name,
        "installer_bytes": installer_info[0], "installer_sha256": installer_info[1],
        "phase_evidence_bound": True, "artifact_manifest_bound": True,
        "final_installer_rehashed": True, "native_windows_acceptance": False,
        "actual_broker_acceptance": False, "live_orders_enabled": False,
        "money_movement_enabled": False, "release_approved": False, "limitations": "fixture",
    }
    artifact = manifest([installer, executable, probe_tool, reviewer_tool], VERSION, SOURCE)
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
    return probe, binding, artifact, probe_info, reviewer_info, probe_tool, reviewer_tool


def verify(parts):
    probe, binding, artifact, probe_info, reviewer_info, _, _ = parts
    return review.verify(probe, binding, artifact, expected_version=VERSION,
        expected_source_commit=SOURCE, probe_tool=probe_info, reviewer_tool=reviewer_info)


def test_exact_owner_profile_probe_is_bound_without_promoting_authority(tmp_path):
    result = verify(fixture(tmp_path))
    assert result["passed"] and result["ordinary_user_profile_checked"]
    assert result["effective_acl_operations_checked"] and result["exact_distribution_bound"]
    assert result["installed_executable_bound"] and result["probe_tool_manifest_bound"]
    assert result["review_tool_manifest_bound"] and result["private_paths_retained"] is False
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
        review.verify(parts[0], parts[1], parts[2], expected_version=VERSION,
            expected_source_commit=SOURCE, probe_tool=tuple(changed), reviewer_tool=parts[4])


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
    probe, binding, artifact, _, _, probe_tool, reviewer_tool = parts
    probe_path = tmp_path / "probe.json"
    binding_path = tmp_path / "binding.json"
    manifest_path = tmp_path / "manifest.json"
    output = tmp_path / "review.json"
    probe_path.write_text(json.dumps(probe), encoding="utf-8")
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    manifest_path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["owner-review", "--probe", str(probe_path),
        "--distribution-binding", str(binding_path), "--artifact-manifest", str(manifest_path),
        "--probe-tool", str(probe_tool), "--review-tool", str(reviewer_tool), "--expected-version", VERSION,
        "--expected-source-commit", SOURCE, "--output", str(output)])
    assert review.main() == 0
    assert json.loads(output.read_text())["mode"] == "VERIFIED_OWNER_WINDOWS_PROFILE_REVIEW"
    assert review.main() == 1
    assert "OWNER_WINDOWS_PROFILE_REVIEW_FAILED" in capsys.readouterr().out

    output.unlink()
    probe_path.write_bytes(b'{"profile":1,"profile":2}')
    assert review.main() == 1


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
