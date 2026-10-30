"""Review a privacy-safe ordinary-user Windows profile/ACL probe.

The probe is owner-operated and self-reported.  This reviewer binds it to the
exact installer, installed executable and manifest-shipped probe/reviewer
tools without promoting it to independent native, broker or release
acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


MAX_JSON_BYTES = 256 * 1024
MAX_TOOL_BYTES = 1024 * 1024
SOURCE = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
WINDOWS_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")
ARCHITECTURES = {"X64", "Arm64", "X86"}
PROBE_PROFILE = "nexus.windows-owner-profile-probe.v1"
PROBE_NAME = "windows-owner-profile-probe.ps1"
REVIEWER_NAME = "windows-owner-profile-review.py"
PROBE_LIMITATIONS = (
    "Owner-operated single-profile effective-access probe only; no usernames, SIDs, paths, account data or "
    "credentials retained. Self-reported machine evidence is not independent attestation, native-window, "
    "power-loss, broker, market, funds, full-machine or release acceptance."
)
PROBE_KEYS = {
    "profile", "version", "source_commit", "installer_sha256", "installed_executable_sha256",
    "probe_script_sha256", "session_id", "timestamp_utc", "windows_version", "architecture",
    "ordinary_user_token", "local_appdata_install", "local_appdata_data",
    "current_identity_owns_probe", "directory_acl_inheritance_enabled",
    "file_acl_inheritance_enabled", "create_write_read_verified", "exclusive_lock_verified",
    "replace_verified", "rename_delete_verified", "cleanup_verified", "private_paths_retained",
    "actual_broker_acceptance", "live_orders_enabled", "money_movement_enabled", "release_approved",
    "limitations",
}
PROBE_TRUE = {
    "ordinary_user_token", "local_appdata_install", "local_appdata_data", "current_identity_owns_probe",
    "directory_acl_inheritance_enabled", "file_acl_inheritance_enabled", "create_write_read_verified",
    "exclusive_lock_verified", "replace_verified", "rename_delete_verified", "cleanup_verified",
}
PROBE_FALSE = {
    "private_paths_retained", "actual_broker_acceptance", "live_orders_enabled",
    "money_movement_enabled", "release_approved",
}
BINDING_KEYS = {
    "mode", "passed", "version", "source_commit", "installer_name", "installer_bytes",
    "installer_sha256", "phase_evidence_bound", "artifact_manifest_bound", "final_installer_rehashed",
    "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
    "money_movement_enabled", "release_approved", "limitations",
}
MANIFEST_KEYS = {"schema", "version", "source_commit", "signed", "release_approved", "artifacts"}
ENTRY_KEYS = {"name", "bytes", "sha256"}


class OwnerProfileReviewError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise OwnerProfileReviewError("duplicate key")
        result[key] = value
    return result


def _read_bytes(path, limit):
    path = Path(path)
    if path.is_symlink():
        raise OwnerProfileReviewError("file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= limit:
            raise OwnerProfileReviewError("file")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
    if len(raw) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
        raise OwnerProfileReviewError("file changed")
    return raw


def _load_json(path):
    try:
        raw = _read_bytes(path, MAX_JSON_BYTES)
        return json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
            parse_float=lambda _: (_ for _ in ()).throw(OwnerProfileReviewError("number")),
            parse_constant=lambda _: (_ for _ in ()).throw(OwnerProfileReviewError("number")),
        )
    except (UnicodeError, json.JSONDecodeError, OwnerProfileReviewError, RecursionError):
        raise OwnerProfileReviewError("json") from None


def _tool(path, expected_name):
    path = Path(path)
    if path.name != expected_name:
        raise OwnerProfileReviewError("tool name")
    raw = _read_bytes(path, MAX_TOOL_BYTES)
    return len(raw), hashlib.sha256(raw).hexdigest()


def _utc(value):
    if type(value) is not str or not 20 <= len(value) <= 40:
        raise OwnerProfileReviewError("timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise OwnerProfileReviewError("timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise OwnerProfileReviewError("timestamp")
    return value


def _session(value):
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise OwnerProfileReviewError("session") from None
    if parsed.version != 4 or str(parsed) != value:
        raise OwnerProfileReviewError("session")
    return value


def _manifest_entries(manifest, *, version, source_commit):
    if (type(manifest) is not dict or set(manifest) != MANIFEST_KEYS
            or manifest["schema"] != "nexus.artifacts.v1" or manifest["version"] != version
            or manifest["source_commit"] != source_commit or manifest["signed"] is not False
            or manifest["release_approved"] is not False or type(manifest["artifacts"]) is not list
            or not 1 <= len(manifest["artifacts"]) <= 16):
        raise OwnerProfileReviewError("manifest")
    result = {}
    for entry in manifest["artifacts"]:
        if (type(entry) is not dict or set(entry) != ENTRY_KEYS or type(entry["name"]) is not str
                or entry["name"] in result or entry["name"] in (".", "..")
                or any(char in entry["name"] for char in "/\\:")
                or type(entry["bytes"]) is not int or entry["bytes"] < 1
                or type(entry["sha256"]) is not str or SHA256.fullmatch(entry["sha256"]) is None):
            raise OwnerProfileReviewError("manifest entry")
        result[entry["name"]] = (entry["bytes"], entry["sha256"])
    return result


def verify(probe, binding, manifest, *, expected_version, expected_source_commit,
           probe_tool, reviewer_tool):
    if type(expected_version) is not str or VERSION.fullmatch(expected_version) is None:
        raise OwnerProfileReviewError("expected version")
    if type(expected_source_commit) is not str or SOURCE.fullmatch(expected_source_commit) is None:
        raise OwnerProfileReviewError("expected source")
    for value in (probe_tool, reviewer_tool):
        if (type(value) is not tuple or len(value) != 2 or type(value[0]) is not int or value[0] < 1
                or type(value[1]) is not str or SHA256.fullmatch(value[1]) is None):
            raise OwnerProfileReviewError("tool binding")
    if type(probe) is not dict or set(probe) != PROBE_KEYS:
        raise OwnerProfileReviewError("probe shape")
    if type(binding) is not dict or set(binding) != BINDING_KEYS:
        raise OwnerProfileReviewError("binding shape")
    if (binding["mode"] != "VERIFIED_INSTALLER_DISTRIBUTION_BINDING" or binding["passed"] is not True
            or binding["version"] != expected_version or binding["source_commit"] != expected_source_commit
            or binding["installer_name"] != f"NexusOperative-Setup-{expected_version}.exe"
            or type(binding["installer_bytes"]) is not int or binding["installer_bytes"] < 1
            or type(binding["installer_sha256"]) is not str or SHA256.fullmatch(binding["installer_sha256"]) is None
            or any(binding[key] is not True for key in (
                "phase_evidence_bound", "artifact_manifest_bound", "final_installer_rehashed"))
            or any(binding[key] is not False for key in (
                "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
                "money_movement_enabled", "release_approved"))
            or type(binding["limitations"]) is not str or not binding["limitations"]):
        raise OwnerProfileReviewError("binding")
    entries = _manifest_entries(manifest, version=expected_version, source_commit=expected_source_commit)
    required_entries = {
        binding["installer_name"]: (binding["installer_bytes"], binding["installer_sha256"]),
        "NexusOperative.exe": None,
        PROBE_NAME: probe_tool,
        REVIEWER_NAME: reviewer_tool,
    }
    for name, expected in required_entries.items():
        if name not in entries or (expected is not None and entries[name] != expected):
            raise OwnerProfileReviewError("manifest tool binding")
    executable_bytes, executable_sha256 = entries["NexusOperative.exe"]
    if executable_bytes < 1:
        raise OwnerProfileReviewError("executable")
    if (probe["profile"] != PROBE_PROFILE or probe["version"] != expected_version
            or probe["source_commit"] != expected_source_commit
            or probe["installer_sha256"] != binding["installer_sha256"]
            or probe["installed_executable_sha256"] != executable_sha256
            or probe["probe_script_sha256"] != probe_tool[1]
            or type(probe["windows_version"]) is not str
            or WINDOWS_VERSION.fullmatch(probe["windows_version"]) is None
            or type(probe["architecture"]) is not str or probe["architecture"] not in ARCHITECTURES
            or probe["limitations"] != PROBE_LIMITATIONS
            or any(probe[key] is not True for key in PROBE_TRUE)
            or any(probe[key] is not False for key in PROBE_FALSE)):
        raise OwnerProfileReviewError("probe")
    session_id = _session(probe["session_id"])
    timestamp = _utc(probe["timestamp_utc"])
    return {
        "mode": "VERIFIED_OWNER_WINDOWS_PROFILE_REVIEW",
        "passed": True,
        "version": expected_version,
        "source_commit": expected_source_commit,
        "installer_sha256": binding["installer_sha256"],
        "installed_executable_sha256": executable_sha256,
        "session_id": session_id,
        "timestamp_utc": timestamp,
        "windows_version": probe["windows_version"],
        "architecture": probe["architecture"],
        "exact_distribution_bound": True,
        "installed_executable_bound": True,
        "probe_tool_manifest_bound": True,
        "review_tool_manifest_bound": True,
        "ordinary_user_profile_checked": True,
        "effective_acl_operations_checked": True,
        "private_paths_retained": False,
        "native_windows_acceptance": False,
        "actual_broker_acceptance": False,
        "live_orders_enabled": False,
        "money_movement_enabled": False,
        "release_approved": False,
        "limitations": PROBE_LIMITATIONS,
    }


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise OwnerProfileReviewError("output exists")
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".owner-profile-review-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise OwnerProfileReviewError("output exists") from None
        temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "OWNER_WINDOWS_PROFILE_REVIEW_USAGE_ERROR\n")


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--distribution-binding", required=True)
    parser.add_argument("--artifact-manifest", required=True)
    parser.add_argument("--probe-tool", required=True)
    parser.add_argument("--review-tool", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        probe_tool = _tool(args.probe_tool, PROBE_NAME)
        reviewer_tool = _tool(args.review_tool, REVIEWER_NAME)
        result = verify(
            _load_json(args.probe), _load_json(args.distribution_binding),
            _load_json(args.artifact_manifest), expected_version=args.expected_version,
            expected_source_commit=args.expected_source_commit, probe_tool=probe_tool,
            reviewer_tool=reviewer_tool,
        )
        _write(args.output, result)
    except (OSError, OwnerProfileReviewError, MemoryError, RecursionError):
        print("OWNER_WINDOWS_PROFILE_REVIEW_FAILED")
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
