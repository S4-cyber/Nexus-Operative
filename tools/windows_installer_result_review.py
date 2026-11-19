"""Validate installer semantic results and bind them to reviewed native phases."""
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
SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHA256 = re.compile(r"[0-9a-f]{64}")
RESULT_LIMITATIONS = (
    "Successful CI installer semantic checks only; not clean-machine, native-window, ordinary-user, "
    "power-loss, broker, market, funds or release acceptance."
)
SEMANTIC_TRUE = {
    "first_run_paper_defaults", "saved_setup_survived_reinstall",
    "saved_setup_survived_restart", "setup_preserved_on_uninstall",
    "default_windows_data_recovery", "default_data_preserved_on_uninstall",
    "prior_version_data_recovery", "packaged_backup_recovery",
    "same_version_reinstall", "halt_recovered", "uninstall_preserved_data",
    "all_external_processes_bounded", "phase_evidence_recorded",
}
SEMANTIC_FALSE = {
    "cross_version_upgrade_verified", "native_window_verified",
    "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
    "money_movement_enabled", "release_approved",
}
RAW_KEYS = {
    "mode", "version", "source_commit", "installer_sha256", "session_id",
    "maximum_phase_timeout_seconds", "limitations",
} | SEMANTIC_TRUE | SEMANTIC_FALSE
PHASE_KEYS = {
    "mode", "passed", "phases_verified", "records_verified", "version", "source_commit",
    "installer_sha256", "session_id", "exact_source_bound", "installer_digest_bound",
    "single_session_bound", "sequence_complete", "timeouts_respected", "maximum_timeout_seconds",
    "maximum_observed_duration_seconds", "native_windows_acceptance", "actual_broker_acceptance",
    "live_orders_enabled", "money_movement_enabled", "release_approved", "limitations",
}
REVIEW_KEYS = {
    "mode", "passed", "version", "source_commit", "installer_sha256", "session_id",
    "raw_result_bytes", "raw_result_sha256", "maximum_phase_timeout_seconds",
    "phase_evidence_bound", "exact_source_bound", "installer_digest_bound",
    "single_session_bound", "limitations",
} | SEMANTIC_TRUE | SEMANTIC_FALSE


class InstallerResultReviewError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InstallerResultReviewError("duplicate key")
        result[key] = value
    return result


def _read_bytes(path):
    path = Path(path)
    if path.is_symlink():
        raise InstallerResultReviewError("file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 1 <= before.st_size <= MAX_JSON_BYTES):
            raise InstallerResultReviewError("file")
        raw = stream.read(MAX_JSON_BYTES + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    if len(raw) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
        raise InstallerResultReviewError("file changed")
    return raw


def _load_json_raw(path):
    raw = _read_bytes(path)
    try:
        value = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(InstallerResultReviewError("number")),
        )
    except (UnicodeError, json.JSONDecodeError, InstallerResultReviewError, RecursionError):
        raise InstallerResultReviewError("json") from None
    return value, raw


def _session(value):
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise InstallerResultReviewError("session") from None
    if parsed.version != 4 or str(parsed) != value:
        raise InstallerResultReviewError("session")
    return value


def verify(result, phase, *, raw_result_bytes, raw_result_sha256,
           expected_version, expected_source_commit):
    if type(expected_version) is not str or VERSION.fullmatch(expected_version) is None:
        raise InstallerResultReviewError("expected version")
    if type(expected_source_commit) is not str or SOURCE_COMMIT.fullmatch(expected_source_commit) is None:
        raise InstallerResultReviewError("expected source")
    if type(raw_result_bytes) is not int or raw_result_bytes < 1:
        raise InstallerResultReviewError("result bytes")
    if type(raw_result_sha256) is not str or SHA256.fullmatch(raw_result_sha256) is None:
        raise InstallerResultReviewError("result digest")
    if type(result) is not dict or set(result) != RAW_KEYS:
        raise InstallerResultReviewError("result shape")
    if type(phase) is not dict or set(phase) != PHASE_KEYS:
        raise InstallerResultReviewError("phase shape")
    session_id = _session(result["session_id"])
    phase_session_id = _session(phase["session_id"])
    if (result["mode"] != "INSTALLER_SMOKE_RESULT"
            or result["version"] != expected_version
            or result["source_commit"] != expected_source_commit
            or type(result["installer_sha256"]) is not str
            or SHA256.fullmatch(result["installer_sha256"]) is None
            or result["maximum_phase_timeout_seconds"] != 300
            or type(result["maximum_phase_timeout_seconds"]) is not int
            or result["limitations"] != RESULT_LIMITATIONS
            or any(result[key] is not True for key in SEMANTIC_TRUE)
            or any(result[key] is not False for key in SEMANTIC_FALSE)):
        raise InstallerResultReviewError("result binding")
    if (phase["mode"] != "VERIFIED_INSTALLER_PHASE_EVIDENCE"
            or phase["passed"] is not True
            or phase["version"] != expected_version
            or phase["source_commit"] != expected_source_commit
            or phase["installer_sha256"] != result["installer_sha256"]
            or phase_session_id != session_id
            or type(phase["phases_verified"]) is not int
            or phase["phases_verified"] != 10
            or type(phase["records_verified"]) is not int
            or phase["records_verified"] != 20
            or type(phase["maximum_timeout_seconds"]) is not int
            or phase["maximum_timeout_seconds"] != 300
            or type(phase["maximum_observed_duration_seconds"]) not in (int, float)
            or not math.isfinite(phase["maximum_observed_duration_seconds"])
            or not 0 <= phase["maximum_observed_duration_seconds"] <= 315
            or type(phase["limitations"]) is not str or not phase["limitations"]
            or any(phase[key] is not True for key in (
                "exact_source_bound", "installer_digest_bound", "single_session_bound",
                "sequence_complete", "timeouts_respected"))
            or any(phase[key] is not False for key in (
                "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
                "money_movement_enabled", "release_approved"))):
        raise InstallerResultReviewError("phase binding")
    return {
        "mode": "VERIFIED_INSTALLER_RESULT_EVIDENCE",
        "passed": True,
        "version": expected_version,
        "source_commit": expected_source_commit,
        "installer_sha256": result["installer_sha256"],
        "session_id": session_id,
        "raw_result_bytes": raw_result_bytes,
        "raw_result_sha256": raw_result_sha256,
        "maximum_phase_timeout_seconds": 300,
        **{key: True for key in sorted(SEMANTIC_TRUE)},
        **{key: False for key in sorted(SEMANTIC_FALSE)},
        "phase_evidence_bound": True,
        "exact_source_bound": True,
        "installer_digest_bound": True,
        "single_session_bound": True,
        "limitations": RESULT_LIMITATIONS,
    }


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise InstallerResultReviewError("output exists")
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".installer-result-review-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise InstallerResultReviewError("output exists") from None
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
        self.exit(2, "INSTALLER_RESULT_REVIEW_USAGE_ERROR\n")


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--phase-review", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result, raw = _load_json_raw(args.input)
        phase, _ = _load_json_raw(args.phase_review)
        review = verify(
            result, phase, raw_result_bytes=len(raw),
            raw_result_sha256=hashlib.sha256(raw).hexdigest(),
            expected_version=args.expected_version,
            expected_source_commit=args.expected_source_commit,
        )
        _write(args.output, review)
    except (OSError, InstallerResultReviewError, MemoryError, RecursionError):
        print("INSTALLER_RESULT_REVIEW_FAILED")
        return 1
    print(json.dumps(review, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
