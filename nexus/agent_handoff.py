"""Read-only structured agent coordination; never grants external authority.

Concept adapted from msitarzewski/agency-agents (MIT).
Pinned upstream: 8ae6164602c95ca78828b148b9052460ee426885.
"""
from dataclasses import dataclass
from enum import Enum
import re

MAX_ATTEMPTS = 3
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ALLOWED_VERDICTS = frozenset({"PASS", "FAIL"})


def _nonblank(value: object, limit: int = 2048) -> str:
    if type(value) is not str or not 0 < len(value.strip()) <= limit:
        raise ValueError("expected bounded nonblank string")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("control characters forbidden")
    return value.strip()


def _items(value: object, *, limit: int = 16) -> tuple[str, ...]:
    if type(value) not in (tuple, list) or not 1 <= len(value) <= limit:
        raise ValueError("expected nonempty bounded sequence")
    result = tuple(_nonblank(v, 512) for v in value)
    if len(set(result)) != len(result):
        raise ValueError("duplicated sequence members")
    return result


@dataclass(frozen=True, slots=True)
class AgentHandoff:
    task_id: str
    from_role: str
    to_role: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    source_references: tuple[str, ...]
    constraints: tuple[str, ...]
    attempt: int


def parse_handoff(raw: object) -> AgentHandoff:
    """Strictly validate a proposed handoff; never infer execution authority."""
    required = {"task_id", "from_role", "to_role", "objective",
                "acceptance_criteria", "source_references", "constraints", "attempt"}
    if type(raw) is not dict or set(raw) != required:
        raise ValueError("invalid handoff schema")
    task_id = _nonblank(raw["task_id"], 128)
    if not _ID.fullmatch(task_id):
        raise ValueError("invalid task identifier")
    attempt = raw["attempt"]
    if type(attempt) is not int or not 1 <= attempt <= MAX_ATTEMPTS:
        raise ValueError("attempt must be an integer from 1 to 3")
    return AgentHandoff(
        task_id=task_id,
        from_role=_nonblank(raw["from_role"], 64),
        to_role=_nonblank(raw["to_role"], 64),
        objective=_nonblank(raw["objective"]),
        acceptance_criteria=_items(raw["acceptance_criteria"]),
        source_references=_items(raw["source_references"]),
        constraints=_items(raw["constraints"]),
        attempt=attempt,
    )


class NextStep(str, Enum):
    ADVANCE = "ADVANCE"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    RETRY = "RETRY"
    ESCALATE = "ESCALATE"


def review_handoff(handoff: AgentHandoff, *, verdict: str,
                   evidence: object) -> NextStep:
    """No claimed evidence => no workflow advance; third failure => escalation.

    This function validates evidence *references*, not their truth. An
    independent verifier must validate referenced evidence before release.
    """
    if type(handoff) is not AgentHandoff or type(verdict) is not str or verdict not in _ALLOWED_VERDICTS:
        raise ValueError("invalid handoff or verdict")
    if type(evidence) not in (list, tuple):
        raise ValueError("invalid evidence collection")
    if len(evidence) > 32:
        raise ValueError("too many evidence references")
    proofs = tuple(_nonblank(item, 512) for item in evidence)
    if verdict == "PASS":
        if not proofs or len(proofs) < len(handoff.acceptance_criteria):
            return NextStep.NEEDS_EVIDENCE
        return NextStep.ADVANCE
    return NextStep.ESCALATE if handoff.attempt == MAX_ATTEMPTS else NextStep.RETRY


def action_authorized_by_handoff(action: object) -> bool:
    """Agent handoffs are never credentials or grants of side-effect authority."""
    _nonblank(action, 128)
    return False
