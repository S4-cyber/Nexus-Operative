from datetime import datetime, timedelta, timezone
import json

import pytest

from tools import windows_installer_phase_review as review


def evidence(*, status="completed", duration=1, timeout_delta=0):
    current = datetime(2026, 10, 30, tzinfo=timezone.utc)
    records = []
    for label, timeout in review.EXPECTED_PHASES:
        records.append(dict(mode="INSTALLER_SMOKE_PHASE", label=label, status="started",
            timeout_seconds=timeout, timestamp_utc=current.isoformat().replace("+00:00", "Z")))
        current += timedelta(seconds=duration)
        records.append(dict(mode="INSTALLER_SMOKE_PHASE", label=label, status=status,
            timeout_seconds=timeout + timeout_delta, timestamp_utc=current.isoformat().replace("+00:00", "Z")))
        current += timedelta(seconds=1)
    return ("\n".join(json.dumps(row, separators=(",", ":")) for row in records) + "\n").encode()


def test_successful_phase_evidence_requires_exact_complete_sequence():
    result = review.verify(evidence())
    assert result["passed"] is result["sequence_complete"] is result["timeouts_respected"] is True
    assert result["phases_verified"] == 10 and result["records_verified"] == 20
    assert result["maximum_timeout_seconds"] == 300
    assert result["maximum_observed_duration_seconds"] == 1
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
        review.verify(raw)


def test_phase_evidence_rejects_non_utc_clock_regression_and_overrun():
    rows = [json.loads(line) for line in evidence().splitlines()]
    rows[0]["timestamp_utc"] = "2026-10-30T01:00:00+01:00"
    with pytest.raises(review.PhaseEvidenceError):
        review.verify(("\n".join(json.dumps(row) for row in rows) + "\n").encode())

    rows = [json.loads(line) for line in evidence().splitlines()]
    rows[1]["timestamp_utc"] = "2026-10-30T00:03:30Z"
    with pytest.raises(review.PhaseEvidenceError):
        review.verify(("\n".join(json.dumps(row) for row in rows) + "\n").encode())


def test_phase_review_cli_writes_only_verified_summary(tmp_path, capsys):
    source = tmp_path / "phases.jsonl"
    output = tmp_path / "review.json"
    source.write_bytes(evidence())
    assert review.main.__module__ == "tools.windows_installer_phase_review"
    result = review.verify(source.read_bytes())
    review._write(output, result)
    assert json.loads(output.read_text())["mode"] == "VERIFIED_INSTALLER_PHASE_EVIDENCE"
    with pytest.raises(review.PhaseEvidenceError):
        review._write(output, result)
