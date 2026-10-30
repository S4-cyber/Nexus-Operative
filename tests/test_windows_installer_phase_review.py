from datetime import datetime, timedelta, timezone
import json

import pytest

from tools import windows_installer_phase_review as review


VERSION = "0.340.47"
SOURCE_COMMIT = "a" * 40
INSTALLER_SHA256 = "b" * 64
SESSION_ID = "12345678-1234-4234-8234-123456789abc"


def evidence(*, status="completed", duration=1, timeout_delta=0):
    current = datetime(2026, 10, 30, tzinfo=timezone.utc)
    records = []
    for label, timeout in review.EXPECTED_PHASES:
        records.append(dict(mode="INSTALLER_SMOKE_PHASE", label=label, status="started",
            timeout_seconds=timeout, timestamp_utc=current.isoformat().replace("+00:00", "Z"),
            session_id=SESSION_ID, source_commit=SOURCE_COMMIT, version=VERSION,
            installer_sha256=INSTALLER_SHA256))
        current += timedelta(seconds=duration)
        records.append(dict(mode="INSTALLER_SMOKE_PHASE", label=label, status=status,
            timeout_seconds=timeout + timeout_delta, timestamp_utc=current.isoformat().replace("+00:00", "Z"),
            session_id=SESSION_ID, source_commit=SOURCE_COMMIT, version=VERSION,
            installer_sha256=INSTALLER_SHA256))
        current += timedelta(seconds=1)
    return ("\n".join(json.dumps(row, separators=(",", ":")) for row in records) + "\n").encode()


def test_successful_phase_evidence_requires_exact_complete_sequence():
    result = verify(evidence())
    assert result["passed"] is result["sequence_complete"] is result["timeouts_respected"] is True
    assert result["phases_verified"] == 10 and result["records_verified"] == 20
    assert result["maximum_timeout_seconds"] == 300
    assert result["maximum_observed_duration_seconds"] == 1
    assert result["version"] == VERSION and result["source_commit"] == SOURCE_COMMIT
    assert result["installer_sha256"] == INSTALLER_SHA256 and result["session_id"] == SESSION_ID
    assert result["exact_source_bound"] is result["installer_digest_bound"] is result["single_session_bound"] is True
    assert all(result[key] is False for key in (
        "native_windows_acceptance", "actual_broker_acceptance", "live_orders_enabled",
        "money_movement_enabled", "release_approved"))


@pytest.mark.parametrize("mutation", (
    lambda rows: rows[:-1],
    lambda rows: rows + rows[-1:],
    lambda rows: [*rows[:3], rows[2], *rows[4:]],
    lambda rows: [{**rows[0], "label": "unknown"}, *rows[1:]],
    lambda rows: [rows[1], rows[0], *rows[2:]],
    lambda rows: [rows[0], {**rows[1], "status": "failed"}, *rows[2:]],
    lambda rows: [rows[0], {**rows[1], "timeout_seconds": 299}, *rows[2:]],
    lambda rows: [{**rows[0], "extra": True}, *rows[1:]],
))
def test_phase_evidence_rejects_missing_duplicate_or_changed_records(mutation):
    rows = [json.loads(line) for line in evidence().splitlines()]
    raw = ("\n".join(json.dumps(row) for row in mutation(rows)) + "\n").encode()
    with pytest.raises(review.PhaseEvidenceError):
        verify(raw)


def verify(raw):
    return review.verify(raw, expected_version=VERSION, expected_source_commit=SOURCE_COMMIT,
        expected_installer_sha256=INSTALLER_SHA256)


@pytest.mark.parametrize(("field", "value"), (
    ("version", "0.340.45"),
    ("source_commit", "c" * 40),
    ("installer_sha256", "d" * 64),
    ("session_id", "22345678-1234-4234-8234-123456789abc"),
))
def test_phase_evidence_rejects_stale_source_version_installer_or_session(field, value):
    rows = [json.loads(line) for line in evidence().splitlines()]
    rows[-1][field] = value
    with pytest.raises(review.PhaseEvidenceError):
        verify(("\n".join(json.dumps(row) for row in rows) + "\n").encode())


def test_phase_evidence_rejects_non_utc_clock_regression_and_overrun():
    rows = [json.loads(line) for line in evidence().splitlines()]
    rows[0]["timestamp_utc"] = "2026-10-30T01:00:00+01:00"
    with pytest.raises(review.PhaseEvidenceError):
        verify(("\n".join(json.dumps(row) for row in rows) + "\n").encode())

    rows = [json.loads(line) for line in evidence().splitlines()]
    rows[1]["timestamp_utc"] = "2026-10-30T00:03:30Z"
    with pytest.raises(review.PhaseEvidenceError):
        verify(("\n".join(json.dumps(row) for row in rows) + "\n").encode())


def test_phase_review_cli_writes_only_verified_summary(tmp_path, capsys, monkeypatch):
    source = tmp_path / "phases.jsonl"
    installer = tmp_path / "installer.exe"
    output = tmp_path / "review.json"
    installer.write_bytes(b"installer fixture")
    digest = review._installer_digest(installer)
    raw = evidence().replace(INSTALLER_SHA256.encode(), digest.encode())
    source.write_bytes(raw)
    monkeypatch.setattr("sys.argv", ["phase-review", "--input", str(source), "--installer", str(installer),
        "--expected-version", VERSION, "--expected-source-commit", SOURCE_COMMIT, "--output", str(output)])
    assert review.main() == 0
    assert json.loads(output.read_text())["mode"] == "VERIFIED_INSTALLER_PHASE_EVIDENCE"
    result = verify(evidence())
    with pytest.raises(review.PhaseEvidenceError):
        review._write(output, result)
