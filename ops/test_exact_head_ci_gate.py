"""No-network regression cases for the exact-head CI inventory reviewer."""
from copy import deepcopy
import unittest

from exact_head_ci_gate import REQUIRED, review


COMMIT = "a" * 40


def bundle():
    return {"expected_commit": COMMIT, "runs": [
        {"run": {"name": name, "head_sha": COMMIT,
                 "status": "completed", "conclusion": "success"},
         "jobs": [{"name": job, "status": "completed", "conclusion": "success"}
                  for job in sorted(jobs)]}
        for name, jobs in REQUIRED.items()]}


class TestGate(unittest.TestCase):
    def test_complete_inventory_is_not_release_approval(self):
        outcome = review(bundle())
        self.assertTrue(outcome["ci_inventory_consistent"])
        self.assertFalse(outcome["release_approved"])
        self.assertFalse(outcome["live_trading_enabled"])
        self.assertFalse(outcome["native_windows_accepted"])

    def test_missing_workflow(self):
        bad = bundle()
        bad["runs"].pop()
        with self.assertRaisesRegex(ValueError, "WORKFLOW_MISSING"):
            review(bad)

    def test_cancelled_installer(self):
        bad = bundle()
        bad["runs"][-1]["run"]["conclusion"] = "cancelled"
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"):
            review(bad)

    def test_incomplete_job(self):
        bad = bundle()
        bad["runs"][1]["jobs"][0]["status"] = "in_progress"
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"):
            review(bad)

    def test_different_head(self):
        bad = bundle()
        bad["runs"][0]["run"]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "NOT_GREEN"):
            review(bad)

    def test_missing_evidence_job(self):
        bad = bundle()
        bad["runs"][1]["jobs"] = [j for j in bad["runs"][1]["jobs"]
                                   if j["name"] != "offline-release-evidence"]
        with self.assertRaisesRegex(ValueError, "JOB_MISSING"):
            review(bad)

    def test_duplicate_workflow(self):
        bad = bundle()
        bad["runs"].append(deepcopy(bad["runs"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE"):
            review(bad)

    def test_duplicate_job(self):
        bad = bundle()
        bad["runs"][1]["jobs"].append(deepcopy(bad["runs"][1]["jobs"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE"):
            review(bad)

    def test_unknown_workflow(self):
        bad = bundle()
        bad["runs"][0]["run"]["name"] = "Other"
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            review(bad)

    def test_malformed_expected_commit(self):
        bad = bundle()
        bad["expected_commit"] = "main"
        with self.assertRaisesRegex(ValueError, "COMMIT"):
            review(bad)


if __name__ == "__main__":
    unittest.main()
