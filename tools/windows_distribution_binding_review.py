"""Bind reviewed installer smoke evidence to the final distribution manifest."""
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
PHASE_KEYS = {
    "mode", "passed", "phases_verified", "records_verified", "version", "source_commit",
    "installer_sha256", "session_id", "exact_source_bound", "installer_digest_bound",
    "single_session_bound", "sequence_complete", "timeouts_respected", "maximum_timeout_seconds",
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


def _read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise DistributionBindingError("json file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_JSON_BYTES:
            raise DistributionBindingError("json file")
        raw = stream.read(MAX_JSON_BYTES + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if len(raw) != before.st_size or any(getattr(before, field) != getattr(after, field) for field in fields):
        raise DistributionBindingError("json changed")
    try:
        return json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(DistributionBindingError("number")),
        )
    except (UnicodeError, json.JSONDecodeError, DistributionBindingError, RecursionError):
        raise DistributionBindingError("json") from None


def _installer(path):
    path = Path(path)
    if path.is_symlink():
        raise DistributionBindingError("installer file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_INSTALLER_BYTES:
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
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if total != before.st_size or any(getattr(before, field) != getattr(after, field) for field in fields):
        raise DistributionBindingError("installer changed")
    return total, digest.hexdigest()


def verify(phase, manifest, *, expected_version, expected_source_commit, installer_name, installer_bytes,
           installer_sha256):
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
    if type(phase) is not dict or set(phase) != PHASE_KEYS:
        raise DistributionBindingError("phase shape")
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
            or phase["phases_verified"] != 10 or phase["records_verified"] != 20
            or phase["maximum_timeout_seconds"] != 300
            or type(phase["maximum_observed_duration_seconds"]) not in (int, float)
            or not math.isfinite(phase["maximum_observed_duration_seconds"])
            or not 0 <= phase["maximum_observed_duration_seconds"] <= 315
            or session.version != 4 or str(session) != phase["session_id"]
            or type(phase["limitations"]) is not str or not phase["limitations"]
            or any(phase[key] is not True for key in required_true)
            or any(phase[key] is not False for key in required_false)):
        raise DistributionBindingError("phase binding")
    if (manifest["schema"] != "nexus.artifacts.v1"
            or manifest["version"] != expected_version
            or manifest["source_commit"] != expected_source_commit
            or manifest["signed"] is not False
            or manifest["release_approved"] is not False
            or type(manifest["artifacts"]) is not list
            or not 1 <= len(manifest["artifacts"]) <= 16):
        raise DistributionBindingError("manifest binding")
    entries = []
    names = set()
    for entry in manifest["artifacts"]:
        if (type(entry) is not dict or set(entry) != ENTRY_KEYS
                or type(entry["name"]) is not str or entry["name"] in names
                or entry["name"] in (".", "..") or any(char in entry["name"] for char in "/\\:")
                or type(entry["bytes"]) is not int or entry["bytes"] < 1
                or type(entry["sha256"]) is not str or SHA256.fullmatch(entry["sha256"]) is None):
            raise DistributionBindingError("artifact entry")
        names.add(entry["name"])
        if entry["name"] == installer_name:
            entries.append(entry)
    if entries != [{"name": installer_name, "bytes": installer_bytes, "sha256": installer_sha256}]:
        raise DistributionBindingError("installer manifest binding")
    return {
        "mode": "VERIFIED_INSTALLER_DISTRIBUTION_BINDING",
        "passed": True,
        "version": expected_version,
        "source_commit": expected_source_commit,
        "installer_name": installer_name,
        "installer_bytes": installer_bytes,
        "installer_sha256": installer_sha256,
        "phase_evidence_bound": True,
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
    parser.add_argument("--artifact-manifest", required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        installer = Path(args.installer)
        size, digest = _installer(installer)
        result = verify(
            _read_json(args.phase_review), _read_json(args.artifact_manifest),
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
