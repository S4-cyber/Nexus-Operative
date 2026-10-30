"""Validate successful native-installer phase evidence without granting authority."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


MAX_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
MAX_RECORDS = 64
TERMINAL_GRACE_SECONDS = 15
EXPECTED_PHASES = (
    ("install", 180),
    ("installed-paper-tools", 300),
    ("first-installed-start", 120),
    ("reinstall", 180),
    ("reinstalled-start", 120),
    ("packaged-recovery", 150),
    ("prior-version-recovery", 150),
    ("default-data-first-start", 120),
    ("default-data-restart", 120),
    ("uninstall", 180),
)
RECORD_KEYS = {
    "mode", "label", "status", "timeout_seconds", "timestamp_utc",
    "session_id", "source_commit", "version", "installer_sha256",
}
SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")
INSTALLER_SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


class PhaseEvidenceError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise PhaseEvidenceError("duplicate key")
        result[key] = value
    return result


def _timestamp(value):
    if type(value) is not str or not 20 <= len(value) <= 40:
        raise PhaseEvidenceError("timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PhaseEvidenceError("timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PhaseEvidenceError("timestamp")
    return parsed


def _canonical_session(value):
    if type(value) is not str:
        raise PhaseEvidenceError("session")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise PhaseEvidenceError("session") from None
    if parsed.version != 4 or str(parsed) != value:
        raise PhaseEvidenceError("session")
    return value


def verify(raw, *, expected_version, expected_source_commit, expected_installer_sha256):
    if type(expected_version) is not str or VERSION.fullmatch(expected_version) is None:
        raise PhaseEvidenceError("expected version")
    if type(expected_source_commit) is not str or SOURCE_COMMIT.fullmatch(expected_source_commit) is None:
        raise PhaseEvidenceError("expected source")
    if type(expected_installer_sha256) is not str or INSTALLER_SHA256.fullmatch(expected_installer_sha256) is None:
        raise PhaseEvidenceError("expected installer")
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES:
        raise PhaseEvidenceError("size")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise PhaseEvidenceError("encoding") from None
    lines = text.splitlines()
    if len(lines) != len(EXPECTED_PHASES) * 2 or len(lines) > MAX_RECORDS:
        raise PhaseEvidenceError("record count")
    records = []
    for line in lines:
        if not line or len(line) > 2048:
            raise PhaseEvidenceError("record size")
        try:
            record = json.loads(
                line,
                object_pairs_hook=_pairs,
                parse_float=lambda _: (_ for _ in ()).throw(PhaseEvidenceError("number")),
                parse_constant=lambda _: (_ for _ in ()).throw(PhaseEvidenceError("number")),
            )
        except (json.JSONDecodeError, PhaseEvidenceError, RecursionError):
            raise PhaseEvidenceError("json") from None
        if type(record) is not dict or set(record) != RECORD_KEYS:
            raise PhaseEvidenceError("shape")
        records.append(record)

    previous = None
    session_id = None
    maximum_duration = 0.0
    for index, (label, timeout_seconds) in enumerate(EXPECTED_PHASES):
        started, completed = records[index * 2 : index * 2 + 2]
        for record, status in ((started, "started"), (completed, "completed")):
            if (
                record["mode"] != "INSTALLER_SMOKE_PHASE"
                or record["label"] != label
                or record["status"] != status
                or type(record["timeout_seconds"]) is not int
                or record["timeout_seconds"] != timeout_seconds
                or record["version"] != expected_version
                or record["source_commit"] != expected_source_commit
                or record["installer_sha256"] != expected_installer_sha256
            ):
                raise PhaseEvidenceError("sequence")
            current_session = _canonical_session(record["session_id"])
            if session_id is None:
                session_id = current_session
            elif current_session != session_id:
                raise PhaseEvidenceError("session")
            instant = _timestamp(record["timestamp_utc"])
            if previous is not None and instant < previous:
                raise PhaseEvidenceError("clock order")
            previous = instant
        start_time = _timestamp(started["timestamp_utc"])
        completed_time = _timestamp(completed["timestamp_utc"])
        duration = (completed_time - start_time).total_seconds()
        if not math.isfinite(duration) or not 0 <= duration <= timeout_seconds + TERMINAL_GRACE_SECONDS:
            raise PhaseEvidenceError("duration")
        maximum_duration = max(maximum_duration, duration)

    return {
        "mode": "VERIFIED_INSTALLER_PHASE_EVIDENCE",
        "passed": True,
        "phases_verified": len(EXPECTED_PHASES),
        "records_verified": len(records),
        "version": expected_version,
        "source_commit": expected_source_commit,
        "installer_sha256": expected_installer_sha256,
        "session_id": session_id,
        "exact_source_bound": True,
        "installer_digest_bound": True,
        "single_session_bound": True,
        "sequence_complete": True,
        "timeouts_respected": True,
        "maximum_timeout_seconds": max(timeout for _, timeout in EXPECTED_PHASES),
        "maximum_observed_duration_seconds": round(maximum_duration, 3),
        "native_windows_acceptance": False,
        "actual_broker_acceptance": False,
        "live_orders_enabled": False,
        "money_movement_enabled": False,
        "release_approved": False,
        "limitations": (
            "Successful CI phase-sequence review only. Timestamps and process results are runner-generated "
            "evidence, not clean-machine, native-window, ordinary-user, broker, market, funds or release acceptance."
        ),
    }


def _read(path):
    path = Path(path)
    if path.is_symlink():
        raise PhaseEvidenceError("file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise PhaseEvidenceError("file")
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise PhaseEvidenceError("file changed")
    return raw


def _installer_digest(path):
    path = Path(path)
    if path.is_symlink():
        raise PhaseEvidenceError("installer file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_INSTALLER_BYTES:
            raise PhaseEvidenceError("installer file")
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_INSTALLER_BYTES:
                raise PhaseEvidenceError("installer size")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if total != before.st_size or any(getattr(before, field) != getattr(after, field) for field in fields):
        raise PhaseEvidenceError("installer changed")
    return digest.hexdigest()


def _write(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise PhaseEvidenceError("output exists")
    encoded = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".installer-phase-review-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise PhaseEvidenceError("output exists") from None
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
        self.exit(2, "INSTALLER_PHASE_REVIEW_USAGE_ERROR\n")


def main():
    parser = _Parser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = verify(
            _read(args.input),
            expected_version=args.expected_version,
            expected_source_commit=args.expected_source_commit,
            expected_installer_sha256=_installer_digest(args.installer),
        )
        _write(args.output, result)
    except (OSError, PhaseEvidenceError, MemoryError, RecursionError):
        print("INSTALLER_PHASE_REVIEW_FAILED")
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
