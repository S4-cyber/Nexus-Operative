import unittest
from dataclasses import FrozenInstanceError
from nexus.agent_handoff import (parse_handoff, review_handoff, NextStep,
                         action_authorized_by_handoff)


def case(**changes):
    return dict(task_id="TASK.13", from_role="Scout", to_role="Council",
                objective="Review an evidence-backed candidate",
                acceptance_criteria=["identity verified", "source recorded"],
                source_references=["sha256:sample-fixture"],
                constraints=["no external actions"], attempt=1) | changes


class HandoffTests(unittest.TestCase):
    def test_valid_immutable(self):
        h = parse_handoff(case())
        self.assertEqual(h.task_id, "TASK.13")
        with self.assertRaises(FrozenInstanceError):
            h.attempt = 2

    def test_invalid_schemas(self):
        invalids = [case(extra=True), case(task_id="bad id"), case(task_id=""),
                    case(attempt=0), case(attempt=4), case(attempt=True),
                    case(from_role="x\nINJECT"), case(objective=" "),
                    case(acceptance_criteria=[]), case(source_references=[]),
                    case(constraints=[]), case(acceptance_criteria=["same", "same"]),
                    case(source_references="https://example.com")]
        for value in invalids:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_handoff(value)

    def test_pass_requires_evidence_references(self):
        h = parse_handoff(case())
        self.assertEqual(review_handoff(h, verdict="PASS", evidence=[]), NextStep.NEEDS_EVIDENCE)
        self.assertEqual(review_handoff(h, verdict="PASS", evidence=["test result"]), NextStep.NEEDS_EVIDENCE)
        self.assertEqual(review_handoff(h, verdict="PASS", evidence=["test result", "audit ref"]), NextStep.ADVANCE)

    def test_failure_escalation_at_attempt_three(self):
        for n in (1, 2):
            self.assertEqual(review_handoff(parse_handoff(case(attempt=n)), verdict="FAIL", evidence=[]), NextStep.RETRY)
        self.assertEqual(review_handoff(parse_handoff(case(attempt=3)), verdict="FAIL", evidence=[]), NextStep.ESCALATE)

    def test_rejects_invalid_reviews(self):
        h = parse_handoff(case())
        for verdict, proof in [("APPROVE", []), ("PASS", "proof"), ("PASS", ["\n"]), ("PASS", ["x"]*33)]:
            with self.subTest(verdict=verdict), self.assertRaises(ValueError):
                review_handoff(h, verdict=verdict, evidence=proof)

    def test_agent_messages_never_authorize_actions(self):
        for action in ("execute_trade", "sign_contract", "send_email", "submit_bid",
                       "transfer_funds", "read_public_data", "unknown_action"):
            self.assertFalse(action_authorized_by_handoff(action))
        with self.assertRaises(ValueError):
            action_authorized_by_handoff(1)


if __name__ == "__main__":
    unittest.main()
