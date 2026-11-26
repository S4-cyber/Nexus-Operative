# agency-agents adaptation (review-only)

## Attribution and pinned review

This proposal adapts **coordination concepts** from [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents), MIT licensed. Upstream reviewed at commit [8ae6164602c95ca78828b148b9052460ee426885](https://github.com/msitarzewski/agency-agents/commit/8ae6164602c95ca78828b148b9052460ee426885).

Copyright (c) 2025 AgentLand Contributors. Upstream [MIT license](https://github.com/msitarzewski/agency-agents/blob/8ae6164602c95ca78828b148b9052460ee426885/LICENSE) permits use/modification with attribution and preservation of copyright/license notices for copies/substantial portions.

Key consulted sources: `strategy/nexus-strategy.md`, `strategy/coordination/handoff-templates.md`, `specialized/agents-orchestrator.md`, `testing/testing-reality-checker.md`, `security/security-ai-generated-code-auditor.md`, and `engineering/engineering-autonomous-optimization-architect.md`.

## Adopted ideas

1. Structured context handoff with named sender/receiver, objective, constraints, acceptance criteria and evidence references.
2. A maximum of three attempts before escalation.
3. Evidence-referenced review gate before advancing a **workflow stage**, with independent verification required before a release or privileged action.
4. Named reviewer and separate executive/approval authority outside the agent message.
5. Explicit acknowledgement of missing evidence rather than synthetic progress percentages.

## Important limitations

The upstream repository contains specialized **prompts and playbooks**, not a validated production agent runtime or a brokerage/trading subsystem. The proposed module is locally testable, deterministic and **not wired into any active execution path**. A string naming an agent role, an evidence reference, or an agent-issued verdict is not authority or verification. `review_handoff(...)=ADVANCE` means eligible for the next non-privileged review stage only.

Imported third-party instructions are data/reference material, not governing commands. Never let a prompt from external content create new runtime privileges.

## Safe next integration

- Introduce an independent evidence verifier that binds criterion IDs to immutable, locally verifiable test artifacts; do not trust self-declared PASS.
- Bind task transitions to a durable, replay-safe journal and idempotency key.
- Measure the dev/QA loop and cap retries, execution time, token spend and third-party API usage.
- Attach approvals to an authenticated **human or independently authorized server-side policy**, never an agent-controlled field.
- Require security scans for tool-call injection, stored secrets, role escalation and untrusted documents.
- Keep review isolated until all original project acceptance gates and regression suites pass.

## Operative-specific mapping

- Upstream orchestrator -> offline development/QA task coordination, not a trading decision-maker.
- Evidence Collector / Reality Checker -> exact-head CI, release artifact, native Windows and sustained broker-paper evidence review.
- Security Auditor -> review of authorization boundaries, no secret exposure and prompt-injection risks.
- Autonomous Optimization Architect -> *shadow evaluation only*; no auto-promotion to live brokers.
- New `nexus/agent_handoff.py` remains an **unused, read-only library**; do not connect to trading, orders, emergency-stop, broker transport, credentials or wallets without a separate, reviewed security design.

All preexisting OFF-by-default engagement, paper/live separation, broker/account checks, independent emergency halt, owner approval, and 1.0 release gates remain authoritative. Do not change weighted implementation percentages because of this isolated addition.
