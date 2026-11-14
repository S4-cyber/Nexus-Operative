# Offline exact-head CI inventory reviewer (experimental)

Run from a trusted local checkout: `python ops/exact_head_ci_gate.py /path/to/sanitized-ci-bundle.json`.

The input is a manually constructed, UTF-8 JSON file no larger than 512 KiB:

```json
{
  "expected_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "runs": [
    {
      "run": {
        "name": "Public repository security",
        "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "status": "completed",
        "conclusion": "success"
      },
      "jobs": [
        {"name": "reachable-history-and-secrets", "status": "completed", "conclusion": "success"}
      ]
    }
  ]
}
```

Add the remaining two required workflows with the same exact `head_sha`:
- `Nexus research safety regression` must have green `offline-regression`, `offline-release-evidence` and `portal-browser-help` jobs.
- `Windows simulated-research build` must have a green `test-and-build` job.

The example above is intentionally **incomplete** and must return `CI_GATE_REVIEW_REQUIRED` (exit 2). A complete, internally consistent inventory returns an allowlisted `EXACT_HEAD_CI_INVENTORY_ONLY` JSON response with all approval/acceptance flags false. This helper does not fetch GitHub data, read credentials, operate a broker, approve software or authorize trading. Use only sanitized run/job status fields, never upload market/account records.

For a `workflow_dispatch` targeting a commit different from the event `head_sha`, this conservative reviewer cannot establish the checked-out commit and will refuse; separately inspect the immutable checkout-binding step and retained source manifest. Even a green inventory is **not** full step-level CI validation or independent release acceptance.

Local scope test: `cd ops && python -m unittest -v test_exact_head_ci_gate`.

The existing source-fingerprinted v0.340.314 release trial is unaffected by these standalone operator-side files, but this draft still requires review and must not be merged on a stale stack or treated as an accepted release gate.
