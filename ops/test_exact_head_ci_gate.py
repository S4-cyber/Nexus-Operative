"""No-network regression cases for the exact-checkout CI reviewer."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from exact_head_ci_gate import REQUIRED, main, review


COMMIT = "a" * 40


def bundle():
    runs = []
    job_id = 100
    for run_id, (name, policy) in enumerate(REQUIRED.items(), start=1):
        jobs = []
        for job in sorted(policy["jobs"]):
            job_id += 1
            jobs.append({"id": job_id, "run_id": run_id, "run_attempt": 1,
                         "name": job, "status": "completed", "conclusion": "success"})
        runs.append({"run": {
            "id": run_id, "name": name, "workflow_path": policy["workflow_path"],
            "event": "push", "head_sha": COMMIT, "checked_out_sha": COMMIT,
            "status": "completed", "conclusion": "success", "run_attempt": 1,
        }, "jobs": jobs})
    return {"expected_commit": COMMIT, "runs": runs}


class TestGate(unittest.TestCase):
    def test_complete_inventory_is_not_release_approval(self):
        outcome = review(bundle())
        self.assertTrue(outcome["ci_inventory_consistent"])
        self.assertTrue(outcome["exact_checkout_bound"])
        self.assertEqual(outcome["jobs_checked"], 5)
        self.assertFalse(outcome["release_approved"])
        self.assertFalse(outcome["live_trading_enabled"])
        self.assertFalse(outcome["native_windows_accepted"])

    def test_missing_workflow(self):
        bad = bundle(); bad["runs"].pop()
        with self.assertRaisesRegex(ValueError, "WORKFLOW_MISSING"): review(bad)

    def test_cancelled_installer(self):
        bad = bundle(); bad["runs"][-1]["run"]["conclusion"] = "cancelled"
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"): review(bad)

    def test_incomplete_job(self):
        bad = bundle(); bad["runs"][1]["jobs"][0]["status"] = "in_progress"
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"): review(bad)

    def test_checked_out_commit_is_mandatory(self):
        bad = bundle(); bad["runs"][0]["run"]["checked_out_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"): review(bad)

    def test_non_dispatch_head_must_match(self):
        bad = bundle(); bad["runs"][0]["run"]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"): review(bad)

    def test_exact_windows_dispatch_allows_different_event_head(self):
        candidate = bundle(); run = candidate["runs"][-1]["run"]
        run["event"] = "workflow_dispatch"; run["head_sha"] = "b" * 40
        self.assertTrue(review(candidate)["exact_checkout_bound"])

    def test_other_dispatch_cannot_claim_different_head(self):
        bad = bundle(); run = bad["runs"][0]["run"]
        run["event"] = "workflow_dispatch"; run["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"): review(bad)

    def test_missing_or_extra_job(self):
        def add_extra(jobs):
            jobs.append({"id": 999, "run_id": 2, "run_attempt": 1,
                         "name": "unreviewed", "status": "completed",
                         "conclusion": "success"})
        for mutate in (lambda jobs: jobs.pop(), add_extra):
            bad = bundle(); mutate(bad["runs"][1]["jobs"])
            with self.assertRaisesRegex(ValueError, "JOB_MISMATCH"): review(bad)

    def test_duplicate_workflow(self):
        bad = bundle(); bad["runs"].append(deepcopy(bad["runs"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE"): review(bad)

    def test_job_must_belong_to_run_and_attempt(self):
        for key, value in (("run_id", 99), ("run_attempt", 2)):
            bad = bundle(); bad["runs"][1]["jobs"][0][key] = value
            with self.assertRaisesRegex(ValueError, "JOB_IDENTITY"): review(bad)

    def test_duplicate_run_and_job_ids(self):
        bad = bundle(); bad["runs"][1]["run"]["id"] = 1
        with self.assertRaisesRegex(ValueError, "RUN_IDENTITY"): review(bad)
        bad = bundle()
        bad["runs"][1]["jobs"][0]["id"] = bad["runs"][0]["jobs"][0]["id"]
        with self.assertRaisesRegex(ValueError, "JOB_IDENTITY"): review(bad)

    def test_exact_shapes_and_workflow_identity(self):
        for target in ("run", "job"):
            bad = bundle()
            record = (bad["runs"][0]["run"] if target == "run"
                      else bad["runs"][0]["jobs"][0])
            record["unexpected"] = "private"
            with self.assertRaisesRegex(ValueError, "RECORD|JOB_SHAPE"): review(bad)
        bad = bundle()
        bad["runs"][0]["run"]["workflow_path"] = ".github/workflows/other.yml"
        with self.assertRaisesRegex(ValueError, "WORKFLOW_IDENTITY"): review(bad)

    def test_cli_rejects_symlink_and_hardlink_without_echoing_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "bundle.json"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            candidates = []
            alias = root / "hardlink.json"; os.link(source, alias); candidates.append(alias)
            if os.name != "nt":
                link = root / "symlink.json"; link.symlink_to(source); candidates.append(link)
            for candidate in candidates:
                output = io.StringIO()
                with redirect_stdout(output): self.assertEqual(main([str(candidate)]), 2)
                self.assertEqual(output.getvalue(), "CI_GATE_REVIEW_REQUIRED\n")


if __name__ == "__main__":
    unittest.main()
