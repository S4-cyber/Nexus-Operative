"""Offline, fail-closed review of exported GitHub Actions run/job summaries.

This is a reviewer convenience, NOT CI attestation or permission to release.
It performs no network operations and prints no source payloads or secrets.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat

MAX_BYTES = 512 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
RUN_FIELDS = frozenset({
    "id", "name", "workflow_path", "event", "head_sha",
    "checked_out_sha", "status", "conclusion", "run_attempt",
})
JOB_FIELDS = frozenset({
    "id", "run_id", "run_attempt", "name", "status", "conclusion",
})
REQUIRED = {
    "Public repository security": {
        "workflow_path": ".github/workflows/public-repo-security.yml",
        "events": frozenset({"pull_request", "push", "workflow_dispatch"}),
        "jobs": frozenset({"reachable-history-and-secrets"}),
    },
    "Nexus research safety regression": {
        "workflow_path": ".github/workflows/research-safety-windows.yml",
        "events": frozenset({"pull_request", "push", "workflow_dispatch"}),
        "jobs": frozenset({
            "offline-regression-0-of-2", "offline-regression-1-of-2",
            "offline-release-evidence", "portal-browser-help",
        }),
    },
    "Windows simulated-research build": {
        "workflow_path": ".github/workflows/windows-release.yml",
        "events": frozenset({"push", "workflow_dispatch"}),
        "jobs": frozenset({"test-and-build"}),
    },
}


def _positive_integer(value):
    return type(value) is int and value > 0


def review(bundle):
    """Never return release authorization, only exact-checkout CI checks."""
    if type(bundle) is not dict or set(bundle) != {"expected_commit", "runs"}:
        raise ValueError("CI_GATE_SHAPE")
    expected, runs = bundle["expected_commit"], bundle["runs"]
    if type(expected) is not str or not SHA.fullmatch(expected):
        raise ValueError("CI_GATE_COMMIT")
    if type(runs) is not list or not 1 <= len(runs) <= 12:
        raise ValueError("CI_GATE_RUNS")
    matched = set()
    run_ids = set()
    job_ids = set()
    jobs_checked = 0
    for record in runs:
        if type(record) is not dict or set(record) != {"run", "jobs"}:
            raise ValueError("CI_GATE_RECORD")
        run, jobs = record["run"], record["jobs"]
        if type(run) is not dict or set(run) != RUN_FIELDS or type(jobs) is not list:
            raise ValueError("CI_GATE_RECORD")
        name = run["name"]
        if name not in REQUIRED:
            raise ValueError("CI_GATE_WORKFLOW_UNKNOWN")
        if name in matched:
            raise ValueError("CI_GATE_WORKFLOW_DUPLICATE")
        matched.add(name)
        policy = REQUIRED[name]
        if (not _positive_integer(run["id"]) or run["id"] in run_ids or
                not _positive_integer(run["run_attempt"])):
            raise ValueError("CI_GATE_RUN_IDENTITY")
        run_ids.add(run["id"])
        if (type(run["workflow_path"]) is not str or
                run["workflow_path"] != policy["workflow_path"] or
                type(run["event"]) is not str or run["event"] not in policy["events"]):
            raise ValueError("CI_GATE_WORKFLOW_IDENTITY")
        if (type(run["head_sha"]) is not str or not SHA.fullmatch(run["head_sha"]) or
                run["checked_out_sha"] != expected or
                run["status"] != "completed" or run["conclusion"] != "success"):
            raise ValueError("CI_GATE_WORKFLOW_NOT_GREEN")
        dispatching_exact_review = (
            name == "Windows simulated-research build" and
            run["event"] == "workflow_dispatch")
        if not dispatching_exact_review and run["head_sha"] != expected:
            raise ValueError("CI_GATE_WORKFLOW_NOT_GREEN")
        if not 1 <= len(jobs) <= 16:
            raise ValueError("CI_GATE_JOBS")
        names = set()
        for job in jobs:
            if type(job) is not dict or set(job) != JOB_FIELDS:
                raise ValueError("CI_GATE_JOB_SHAPE")
            if (not _positive_integer(job["id"]) or job["id"] in job_ids or
                    job["run_id"] != run["id"] or
                    job["run_attempt"] != run["run_attempt"]):
                raise ValueError("CI_GATE_JOB_IDENTITY")
            job_ids.add(job["id"])
            if type(job["name"]) is not str or job["name"] in names:
                raise ValueError("CI_GATE_JOB_DUPLICATE")
            names.add(job["name"])
            if job["status"] != "completed" or job["conclusion"] != "success":
                raise ValueError("CI_GATE_JOB_NOT_GREEN")
        if names != policy["jobs"]:
            raise ValueError("CI_GATE_REQUIRED_JOB_MISMATCH")
        jobs_checked += len(jobs)
    if set(REQUIRED) != matched:
        raise ValueError("CI_GATE_REQUIRED_WORKFLOW_MISSING")
    return {
        "status": "EXACT_CHECKOUT_CI_INVENTORY_ONLY",
        "expected_commit": expected,
        "workflows_checked": len(matched),
        "jobs_checked": jobs_checked,
        "ci_inventory_consistent": True,
        "exact_checkout_bound": True,
        "release_approved": False,
        "broker_accepted": False,
        "native_windows_accepted": False,
        "live_trading_enabled": False,
        "limitations": "Exported JSON is untrusted; this does not attest GitHub, job steps,"
                       " ancestry, source manifests, installer bytes, independent acceptance"
                       " or owner approval.",
    }


def _fingerprint(observed):
    return (observed.st_dev, observed.st_ino, stat.S_IFMT(observed.st_mode),
            observed.st_nlink, observed.st_size, observed.st_mtime_ns)


def _read_stable_file(path):
    """Read one bounded regular single-link file without following replacement."""
    before = path.lstat()
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1 or not 0 < before.st_size <= MAX_BYTES):
        raise ValueError("CI_GATE_FILE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                not os.path.samestat(before, opened) or _fingerprint(before) != _fingerprint(opened)):
            raise ValueError("CI_GATE_FILE_CHANGED")
        chunks = []
        remaining = MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = path.lstat()
    if (not raw or len(raw) > MAX_BYTES or not os.path.samestat(before, final) or
            _fingerprint(before) != _fingerprint(after) or
            _fingerprint(before) != _fingerprint(final)):
        raise ValueError("CI_GATE_FILE_CHANGED")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        result = review(json.loads(_read_stable_file(args.bundle).decode("utf-8")))
    except (ValueError, OSError, UnicodeError, TypeError):
        print("CI_GATE_REVIEW_REQUIRED")
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
