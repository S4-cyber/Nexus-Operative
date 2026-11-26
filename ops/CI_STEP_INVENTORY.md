# Offline CI step inventory (supplemental)

This source-independent Node.js check supplements `ops/exact_head_ci_gate.py`: it checks the **required step names, ordering and success status** for the security, research-safety, and native Windows installer/distribution workflows. The existing checker validates run/job identity. Neither script proves that supplied JSON came from GitHub.

Run locally on a **sanitized exported summary**:

```sh
node ops/ci_step_inventory.mjs /path/to/ci-steps.json
node --test ops/test_ci_step_inventory.mjs
```

Required schema:

```json
{
  "expected_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "runs": [
    {
      "run": {
        "id": 1,
        "name": "Public repository security",
        "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "checked_out_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "event": "pull_request"
      },
      "jobs": [
        {
          "id": 2,
          "run_id": 1,
          "run_attempt": 1,
          "name": "reachable-history-and-secrets",
          "status": "completed",
          "conclusion": "success",
          "steps": [{"name": "Run actions/checkout@<pinned>", "status": "completed", "conclusion": "success"}]
        }
      ]
    }
  ]
}
```

The research-safety workflow contributes both exact regression shard names,
`offline-regression-0-of-2` and `offline-regression-1-of-2`. A green run is
incomplete if either shard is absent, cancelled, skipped or failed. Each shard
must retain the same checkout binding, compilation and complete-suite step
sequence before this supplemental inventory can pass.

This example is intentionally incomplete and **must** be rejected. Add exactly the other two required workflows, all expected jobs, and completed step summaries to form a review candidate. All required steps must occur exactly once and in the documented order. A missing, skipped or cancelled installer-distribution phase is denied even if an earlier application test passed.

**Important:** The file contains only nonsecret run IDs, statuses, names and commit hashes. Do not attach raw logs, credentials, personal paths, broker data or retained market observations. The tool makes no network calls, does not inspect private accounts and produces no release/trading authorization. Duplicate JSON keys are not a provenance guarantee; independently inspect exported GitHub run and job records, checked-out source, signed release artifacts, ordinary-user Windows tests, and owner approval.

This is an optional external reviewer utility; existing source-fingerprinted `nexus/release_trials.py` excludes `ops/*.mjs`, so no application/version/evidence numbers were changed by this isolated draft. A changed workflow policy or native release gate requires updating this allowlist with independent regression and re-review before relying on it.
