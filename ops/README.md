# Offline exact-checkout CI inventory reviewer

Run from a trusted local checkout: `python ops/exact_head_ci_gate.py /path/to/sanitized-ci-bundle.json`.

The input is a manually constructed, UTF-8 JSON file no larger than 512 KiB. It must be a regular single-link file and is read through a stable no-follow descriptor. Each run uses this exact schema:

```json
{
  "expected_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "runs": [
    {
      "run": {
        "id": 1001,
        "name": "Public repository security",
        "workflow_path": ".github/workflows/public-repo-security.yml",
        "event": "pull_request",
        "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "checked_out_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1
      },
      "jobs": [
        {
          "id": 2001,
          "run_id": 1001,
          "run_attempt": 1,
          "name": "reachable-history-and-secrets",
          "status": "completed",
          "conclusion": "success"
        }
      ]
    }
  ]
}
```

Add the remaining two required workflows with their exact workflow paths, run/job identities, attempts and checked-out SHA:

- `Nexus research safety regression` must contain exactly green
  `offline-regression-0-of-2`, `offline-regression-1-of-2`,
  `offline-release-evidence` and `portal-browser-help` jobs. Both deterministic
  regression shards collect the complete suite and divide execution by exact
  pytest node ID; neither shard alone is a complete regression result.
- `Windows simulated-research build` must contain exactly one green `test-and-build` job.

The example above is intentionally incomplete and returns `CI_GATE_REVIEW_REQUIRED` (exit 2). A complete internally consistent inventory returns an allowlisted `EXACT_CHECKOUT_CI_INVENTORY_ONLY` response with all approval and acceptance flags false. Unknown fields, workflows, jobs, paths, events, duplicated identifiers, cross-run jobs, changed input files and non-green state fail closed.

Ordinary push and pull-request inventories require both the event head and verified checkout to equal `expected_commit`. An exact-review `workflow_dispatch` of the Windows distribution lane may have a different event head only when its retained checkout-binding step proves `checked_out_sha` equals the expected immutable commit. The reviewer does not establish that proof itself: the exported JSON remains untrusted, and the checkout step, ancestry, source manifest and retained artifacts still require separate inspection.

This helper does not fetch GitHub data, read credentials, operate a broker, approve software or authorize trading. Use only sanitized run/job status fields; never place market/account records, credentials or private artifacts in the bundle.

Local scope test: `cd ops && python -m unittest -v test_exact_head_ci_gate`.
