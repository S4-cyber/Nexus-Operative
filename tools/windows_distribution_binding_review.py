"""Bind reviewed installer phases and semantic results to the distribution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


MAX_JSON_BYTES = 256 * 1024
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHA256 = re.compile(r"[0-9a-f]{64}")
INSTALLER_RESULT_REVIEW_NAME = "installer-result-review.json"
RESULT_LIMITATIONS = (
    "Successful CI installer semantic checks with bounded redirected-output drainage only; descendant "
    "containment is not verified; not clean-machine, native-window, ordinary-user, power-loss, broker, "
    "market, funds or release acceptance."
)
RESULT_TRUE = {
    "passed", "first_run_paper_defaults", "saved_setup_survived_reinstall",
    "saved_setup_survived_restart", "setup_preserved_on_uninstall",
    "default_windows_data_recovery", "default_data_preserved_on_uninstall",
    "prior_version_data_recovery", "packaged_backup_recovery", "same_version_reinstall",
    "halt_recovered", "uninstall_preserved_data", "external_process_output_drains_bounded",
    "phase_evidence_recorded", "phase_evidence_bound", "exact_source_bound",
    "installer_digest_bound", "single_session_bound",
}
RESULT_FALSE = {
    "cross_version_upgrade_verified", "process_tree_containment_verified", "native_window_verified", "native_windows_acceptance",
    "actual_broker_acceptance", "live_orders_enabled", "money_movement_enabled", "release_approved",
}
RESULT_KEYS = {
    "mode", "version", "source_commit", "installer_sha256", "session_id",
    "raw_result_bytes", "raw_result_sha256", "maximum_root_process_timeout_seconds",
    "output_drain_timeout_seconds", "limitations",
} | RESULT_TRUE | RESULT_FALSE
PHASE_KEYS = {
    "mode", "passed", "phases_verified", "records_verified", "version", "source_commit",
    "installer_sha256", "session_id", "exact_source_bound", "installer_digest_bound",
    "single_session_bound", "sequence_complete", "timeouts_respected",
    "maximum_root_process_timeout_seconds", "output_drain_timeout_seconds",
    "maximum_observed_duration_seconds", "native_windows_acceptance", "actual_broker_acceptance",
    "live_orders_enabled", "money_movement_enabled", "release_approved", "limitations",
}
MANIFEST_KEYS = {"schema", "version", "source_commit", "signed", "release_approved", "artifacts"}
ENTRY_KEYS = {"name", "bytes", "sha256"}


class DistributionBindingError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise DistributionBindingError("duplicate key")
        result[key] = value
    return result


def _read_json_raw(path):
    path = Path(path)
    if path.is_symlink():
        raise DistributionBindingError("json file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 1 <= before.st_size <= MAX_JSON_BYTES):
            raise DistributionBindingError("json file")
        raw = stream.read(MAX_JSON_BYTES + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    if len(raw) != before.st_size or any(getattr(before, field) != getattr(after, field) for field in fields):
        raise DistributionBindingError("json changed")
    try:
        value = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(DistributionBindingError("number")),
        )
        return value, raw
    except (UnicodeError, json.JSONDecodeError, DistributionBindingError, RecursionError):
        raise DistributionBindingError("json") from None


def _read_json(path):
    return _read_json_raw(path)[0]


def _installer(path):
    path = Path(path)
    if path.is_symlink():
        raise DistributionBindingError("installer file")
    observed = path.lstat()
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or not 1 <= observed.st_size <= MAX_INSTALLER_BYTES:
        raise DistributionBindingError("installer file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                (observed.st_dev,observed.st_ino)!=(before.st_dev,before.st_ino) or
                not 1 <= before.st_size <= MAX_INSTALLER_BYTES):
            raise DistributionBindingError("installer file")
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_INSTALLER_BYTES:
                raise DistributionBindingError("installer size")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    if total != before.st_size or any(getattr(before, field) != getattr(after, field) for field in fields):
        raise DistributionBindingError("installer changed")
    return total, digest.hexdigest()


def verify(phase, installer_result, manifest, *, installer_result_bytes, installer_result_sha256,
           expected_version, expected_source_commit, installer_name, installer_bytes, installer_sha256):
    if type(expected_version) is not str or VERSION.fullmatch(expected_version) is None:
        raise DistributionBindingError("expected version")
    if type(expected_source_commit) is not str or SOURCE_COMMIT.fullmatch(expected_source_commit) is None:
        raise DistributionBindingError("expected source")
    if type(installer_name) is not str or installer_name != f"NexusOperative-Setup-{expected_version}.exe":
        raise DistributionBindingError("installer name")
    if type(installer_bytes) is not int or installer_bytes < 1:
        raise DistributionBindingError("installer bytes")
    if type(installer_sha256) is not str or SHA256.fullmatch(installer_sha256) is None:
        raise DistributionBindingError("installer digest")
    if type(installer_result_bytes) is not int or installer_result_bytes < 1:
        raise DistributionBindingError("result review bytes")
    if type(installer_result_sha256) is not str or SHA256.fullmatch(installer_result_sha256) is None:
        raise DistributionBindingError("result review digest")
    if type(phase) is not dict or set(phase) != PHASE_KEYS:
        raise DistributionBindingError("phase shape")
    if type(installer_result) is not dict or set(installer_result) != RESULT_KEYS:
        raise DistributionBindingError("result review shape")
    if type(manifest) is not dict or set(manifest) != MANIFEST_KEYS:
        raise DistributionBindingError("manifest shape")
    required_true = (
        "passed", "exact_source_bound", "installer_digest_bound", "single_session_bound",
        "sequence_complete", "timeouts_respected",
    )
    required_false = (
        "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
        "money_movement_enabled", "release_approved",
    )
    try:
        session = uuid.UUID(phase["session_id"])
    except (ValueError, TypeError, AttributeError):
        raise DistributionBindingError("phase session") from None
    if (phase["mode"] != "VERIFIED_INSTALLER_PHASE_EVIDENCE"
            or phase["version"] != expected_version
            or phase["source_commit"] != expected_source_commit
            or phase["installer_sha256"] != installer_sha256
            or type(phase["phases_verified"]) is not int or phase["phases_verified"] != 10
            or type(phase["records_verified"]) is not int or phase["records_verified"] != 20
            or type(phase["maximum_root_process_timeout_seconds"]) is not int
            or phase["maximum_root_process_timeout_seconds"] != 300
            or type(phase["output_drain_timeout_seconds"]) is not int
            or phase["output_drain_timeout_seconds"] != 10
            or type(phase["maximum_observed_duration_seconds"]) not in (int, float)
            or not math.isfinite(phase["maximum_observed_duration_seconds"])
            or not 0 <= phase["maximum_observed_duration_seconds"] <= 315
            or session.version != 4 or str(session) != phase["session_id"]
            or type(phase["limitations"]) is not str or not phase["limitations"]
            or any(phase[key] is not True for key in required_true)
            or any(phase[key] is not False for key in required_false)):
        raise DistributionBindingError("phase binding")
    try:
        result_session = uuid.UUID(installer_result["session_id"])
    except (ValueError, TypeError, AttributeError):
        raise DistributionBindingError("result review session") from None
    if (installer_result["mode"] != "VERIFIED_INSTALLER_RESULT_EVIDENCE"
            or installer_result["version"] != expected_version
            or installer_result["source_commit"] != expected_source_commit
            or installer_result["installer_sha256"] != installer_sha256
            or installer_result["session_id"] != phase["session_id"]
            or result_session.version != 4 or str(result_session) != installer_result["session_id"]
            or type(installer_result["raw_result_bytes"]) is not int
            or installer_result["raw_result_bytes"] < 1
            or type(installer_result["raw_result_sha256"]) is not str
            or SHA256.fullmatch(installer_result["raw_result_sha256"]) is None
            or type(installer_result["maximum_root_process_timeout_seconds"]) is not int
            or installer_result["maximum_root_process_timeout_seconds"] != 300
            or type(installer_result["output_drain_timeout_seconds"]) is not int
            or installer_result["output_drain_timeout_seconds"] != 10
            or installer_result["limitations"] != RESULT_LIMITATIONS
            or any(installer_result[key] is not True for key in RESULT_TRUE)
            or any(installer_result[key] is not False for key in RESULT_FALSE)):
        raise DistributionBindingError("result review binding")
    if (manifest["schema"] != "nexus.artifacts.v1"
            or manifest["version"] != expected_version
            or manifest["source_commit"] != expected_source_commit
            or manifest["signed"] is not False
            or manifest["release_approved"] is not False
            or type(manifest["artifacts"]) is not list
            or not 1 <= len(manifest["artifacts"]) <= 16):
        raise DistributionBindingError("manifest binding")
    entries = {}
    names = set()
    for entry in manifest["artifacts"]:
        if (type(entry) is not dict or set(entry) != ENTRY_KEYS
                or type(entry["name"]) is not str or entry["name"] in names
                or entry["name"] in (".", "..") or any(char in entry["name"] for char in "/\\:")
                or type(entry["bytes"]) is not int or entry["bytes"] < 1
                or type(entry["sha256"]) is not str or SHA256.fullmatch(entry["sha256"]) is None):
            raise DistributionBindingError("artifact entry")
        names.add(entry["name"])
        entries[entry["name"]] = entry
    if entries.get(installer_name) != {
            "name": installer_name, "bytes": installer_bytes, "sha256": installer_sha256}:
        raise DistributionBindingError("installer manifest binding")
    if entries.get(INSTALLER_RESULT_REVIEW_NAME) != {
            "name": INSTALLER_RESULT_REVIEW_NAME, "bytes": installer_result_bytes,
            "sha256": installer_result_sha256}:
        raise DistributionBindingError("result review manifest binding")
    return {
        "mode": "VERIFIED_INSTALLER_DISTRIBUTION_BINDING",
        "passed": True,
        "version": expected_version,
        "source_commit": expected_source_commit,
        "installer_name": installer_name,
        "installer_bytes": installer_bytes,
        "installer_sha256": installer_sha256,
        "installer_result_session_id": installer_result["session_id"],
        "installer_result_review_bytes": installer_result_bytes,
        "installer_result_review_sha256": installer_result_sha256,
        "phase_evidence_bound": True,
        "installer_result_review_bound": True,
        "installer_result_semantics_bound": True,
        "artifact_manifest_bound": True,
        "final_installer_rehashed": True,
        "native_windows_acceptance": False,
        "actual_broker_acceptance": False,
        "live_orders_enabled": False,
        "money_movement_enabled": False,
        "release_approved": False,
        "limitations": (
            "Exact CI installer-to-manifest binding only; not clean-machine, native-window, ordinary-user, "
            "broker, market, funds or release acceptance."
        ),
    }


def _write(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise DistributionBindingError("output exists")
    encoded = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".distribution-binding-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise DistributionBindingError("output exists") from None
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
        self.exit(2, "INSTALLER_DISTRIBUTION_BINDING_USAGE_ERROR\n")


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument("--phase-review", required=True)
    parser.add_argument("--installer-result-review", required=True)
    parser.add_argument("--artifact-manifest", required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        installer = Path(args.installer)
        size, digest = _installer(installer)
        installer_result, installer_result_raw = _read_json_raw(args.installer_result_review)
        result = verify(
            _read_json(args.phase_review), installer_result, _read_json(args.artifact_manifest),
            installer_result_bytes=len(installer_result_raw),
            installer_result_sha256=hashlib.sha256(installer_result_raw).hexdigest(),
            expected_version=args.expected_version, expected_source_commit=args.expected_source_commit,
            installer_name=installer.name, installer_bytes=size, installer_sha256=digest,
        )
        _write(args.output, result)
    except (OSError, DistributionBindingError, MemoryError, RecursionError):
        print("INSTALLER_DISTRIBUTION_BINDING_FAILED")
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
