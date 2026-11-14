"""Offline, fail-closed review of exported GitHub Actions run/job summaries.

This is a reviewer convenience, NOT CI attestation or permission to release.
It performs no network operations and prints no source payloads or secrets.
"""
import argparse
import json
import re
from pathlib import Path

MAX_BYTES = 512 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED = {
    "Public repository security": frozenset({"security"}),
    "Nexus research safety regression": frozenset({
        "offline-regression", "offline-release-evidence", "portal-browser-help"}),
    "Windows simulated-research build": frozenset({"test-and-build"}),
}


def review(bundle):
    """Never return release authorization, only exact-head CI inventory checks."""
    if not isinstance(bundle, dict) or set(bundle) != {"expected_commit", "runs"}:
        raise ValueError("CI_GATE_SHAPE")
    expected, runs = bundle["expected_commit"], bundle["runs"]
    if not isinstance(expected, str) or not SHA.fullmatch(expected):
        raise ValueError("CI_GATE_COMMIT")
    if not isinstance(runs, list) or not 1 <= len(runs) <= 12:
        raise ValueError("CI_GATE_RUNS")
    matched = set()
    for record in runs:
        if not isinstance(record, dict) or set(record) != {"run", "jobs"}:
            raise ValueError("CI_GATE_RECORD")
        run, jobs = record["run"], record["jobs"]
        if not isinstance(run, dict) or not isinstance(jobs, list):
            raise ValueError("CI_GATE_RECORD")
        name = run.get("name")
        if name not in REQUIRED:
            raise ValueError("CI_GATE_WORKFLOW_UNKNOWN")
        if name in matched:
            raise ValueError("CI_GATE_WORKFLOW_DUPLICATE")
        matched.add(name)
        if (run.get("head_sha") != expected or run.get("status") != "completed"
                or run.get("conclusion") != "success"):
            raise ValueError("CI_GATE_WORKFLOW_NOT_GREEN")
        if not 1 <= len(jobs) <= 16:
            raise ValueError("CI_GATE_JOBS")
        names = set()
        for job in jobs:
            if not isinstance(job, dict) or not isinstance(job.get("name"), str):
                raise ValueError("CI_GATE_JOB_SHAPE")
            if job["name"] in names:
                raise ValueError("CI_GATE_JOB_DUPLICATE")
            names.add(job["name"])
            if job.get("status") != "completed" or job.get("conclusion") != "success":
                raise ValueError("CI_GATE_JOB_NOT_GREEN")
        if not REQUIRED[name] <= names:
            raise ValueError("CI_GATE_REQUIRED_JOB_MISSING")
    if set(REQUIRED) != matched:
        raise ValueError("CI_GATE_REQUIRED_WORKFLOW_MISSING")
    return {
        "status": "EXACT_HEAD_CI_INVENTORY_ONLY",
        "expected_commit": expected,
        "workflows_checked": len(matched),
        "ci_inventory_consistent": True,
        "release_approved": False,
        "broker_accepted": False,
        "native_windows_accepted": False,
        "live_trading_enabled": False,
        "limitations": "Exported JSON is untrusted; this does not attest GitHub, job steps,"
                       " dispatch checkout, source provenance, installer bytes,"
                       " independent acceptance or owner approval.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.bundle.is_symlink() or not args.bundle.is_file():
            raise ValueError("CI_GATE_FILE")
        with args.bundle.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError("CI_GATE_SIZE")
        result = review(json.loads(raw.decode("utf-8")))
    except (ValueError, OSError, UnicodeError, TypeError):
        print("CI_GATE_REVIEW_REQUIRED")
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
