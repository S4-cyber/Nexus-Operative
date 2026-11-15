"""Read-only, evidence-first M1-M9 execution queue. No runtime permissions or scheduling.

Task estimates are coarse engineering effort bands, not calendar dates, percent completion,
profit forecasts, acceptance or instructions to activate any trading/funding/cloud service.
"""
from copy import deepcopy
import json

_PLAN = json.loads(r'''[
  {
    "id": "M1",
    "title": "Windows packaging and recovery",
    "priority": "P0",
    "external_gate": "Windows/owner",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M1.1",
        "title": "Complete exact-source installer regression",
        "required_evidence": "Run full Windows EXE, upgrade, rollback, uninstall, checksum and distribution acceptance",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–3 engineering days",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M1.2",
        "title": "Test ordinary-user installation and ACLs",
        "required_evidence": "Retain a sanitized non-elevated owner-profile probe and native window behavior report",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M1.3",
        "title": "Drill backup, restore and interruption",
        "required_evidence": "Independently compare cold SQLite/hash-chain/position recovery across power-loss-like faults",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "4–7 engineering days",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M1.4",
        "title": "Reconcile stacked review releases",
        "required_evidence": "Verify PR ancestry, CI outcomes and correct package/evidence source identities",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "1–2 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M1.5",
        "title": "Audit versioned distributable",
        "required_evidence": "Check EXE/installer/wheel/version, artifact hash, private data exclusion and reproducible manifest",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–3 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      }
    ]
  },
  {
    "id": "M2",
    "title": "Market data and provenance",
    "priority": "P1",
    "external_gate": "Source/independent",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M2.1",
        "title": "Specify source rights and timestamp policy",
        "required_evidence": "Document venues, revisions, allowed uses and event/arrival/receipt clock domains",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–3 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M2.2",
        "title": "Stress gaps, conflicts and revisions",
        "required_evidence": "Exercise out-of-order, duplicate, missing, stale and incompatible-feed fail-closed fixtures",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M2.3",
        "title": "Retain immutable public-feed provenance",
        "required_evidence": "Use bounded hash-verified receipts, deduplication and restart/outage coverage tests",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M2.4",
        "title": "Collect independently witnessed genuine feeds",
        "required_evidence": "Keep real source evidence and rate/clock/outage diagnostics private",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–7 engineering days; actual source-dependent",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M2.5",
        "title": "Audit listing and survivorship effects",
        "required_evidence": "Independent review of delistings, revisions, licensing and source consistency",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days; independent review",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      }
    ]
  },
  {
    "id": "M3",
    "title": "Decision speed and risk authority",
    "priority": "P0",
    "external_gate": "Offline/Windows",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M3.1",
        "title": "Map all admission authorities",
        "required_evidence": "Prove OFF defaults, tier/account gates, no implicit resumption or learned-policy promotion",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–3 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M3.2",
        "title": "Stress queue, expiration and emergency reserve",
        "required_evidence": "Measure durable p50/p95/p99, overflow and command/ID bounds under bursts",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M3.3",
        "title": "Inject bad clocks, quotes and source loss",
        "required_evidence": "Prove stale, discontinuous, overloaded and missing feeds fail closed",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M3.4",
        "title": "Prove independent emergency stop under contention",
        "required_evidence": "Run separate-process stop against hung workers and locked journals, including native Windows",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–6 engineering days; native Windows required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M3.5",
        "title": "Review worst-case exposure after exits",
        "required_evidence": "Independent audit of risk limits, partial fills and unresolved exposure",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days; independent review",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      }
    ]
  },
  {
    "id": "M4",
    "title": "Order lifecycle and portfolio reconciliation",
    "priority": "P0",
    "external_gate": "Broker-paper/independent",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M4.1",
        "title": "Reproduce idempotent order state machine",
        "required_evidence": "Crash/lost-ack/unknown/duplicate requests, partial fills, cancel and expiry replay",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M4.2",
        "title": "Review fee, hold and balance conservation",
        "required_evidence": "Fixture-backed partial fills, reservations, fees, cash and positions",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M4.3",
        "title": "Capture authorized paper snapshots",
        "required_evidence": "Privately preserve provider identity, source, account and bounded execution/fee receipts",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–7 engineering days; authorized broker required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M4.4",
        "title": "Match broker activity with internal accounting",
        "required_evidence": "Independently reconcile orders, timestamps, fees, holds, open terms and positions",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "4–8 engineering days; broker evidence required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M4.5",
        "title": "Exercise disagreement and safe recovery",
        "required_evidence": "Fail closed without implicit retry, fund movement or trading authority",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days; independent review",
        "mode": "OFFLINE_CANDIDATE"
      }
    ]
  },
  {
    "id": "M5",
    "title": "Durable latency and capacity",
    "priority": "P1",
    "external_gate": "Offline/Windows",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M5.1",
        "title": "Calculate worst-case command and ID reserves",
        "required_evidence": "Keep emergency/cancel/expiry capacity when regular operations reach limits",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M5.2",
        "title": "Run multiepoch stress and checkpoint drills",
        "required_evidence": "Test interruptions, retention, archive and ID reuse over repeated generations",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "4–8 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M5.3",
        "title": "Restore archived state on real Windows",
        "required_evidence": "Verify native markers, backup, rollback, private volumes and interrupted rotation",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–6 engineering days; native Windows required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M5.4",
        "title": "Benchmark full durable operation latency",
        "required_evidence": "Record conservative p50/p95/p99 and overload/stall behavior",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M5.5",
        "title": "Review disk, corruption and retention policy",
        "required_evidence": "Independent fault/retention audit without silent history loss",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days; independent review",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      }
    ]
  },
  {
    "id": "M6",
    "title": "Scientific strategy evidence",
    "priority": "P1",
    "external_gate": "Research/source",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M6.1",
        "title": "Freeze causal features and chronological splits",
        "required_evidence": "No lookahead, purged windows or training/validation leakage",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M6.2",
        "title": "Compare baselines under execution costs",
        "required_evidence": "Model adverse fills, fees, spread, slippage and portfolio drawdowns",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M6.3",
        "title": "Audit calibration, regimes and survivorship",
        "required_evidence": "Independent walk-forward sensitivity and fold-level error assessment",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–6 engineering days; independent review",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M6.4",
        "title": "Record precommitted genuine forward predictions",
        "required_evidence": "Time-stamped policy/quote/result evidence separated from synthetic scores",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "7–14 engineering days plus forward observation",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M6.5",
        "title": "Govern model promotion and rollback",
        "required_evidence": "Independent approval before changing autonomous policy; never auto-promote trained models",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days; independent review",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      }
    ]
  },
  {
    "id": "M7",
    "title": "Unattended paper operation",
    "priority": "P0",
    "external_gate": "Genuine-feed/Windows",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M7.1",
        "title": "Finish cold-runner restart and stop fault matrix",
        "required_evidence": "Exercise independent stop, queue loss, stale state and orphaned tasks",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M7.2",
        "title": "Prove observational coverage semantics",
        "required_evidence": "Use exact session heads and eligible unioned intervals excluding gaps/injection",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M7.3",
        "title": "Conduct supervised 72-hour genuine-feed paper trial",
        "required_evidence": "Record actual source, interruptions and 95% coverage target as diagnostic only",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "At least 72 calendar hours plus 2–4 analysis days",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M7.4",
        "title": "Review incidents and actual cost/execution outcomes",
        "required_evidence": "Independent fee, loss, drawdown, outage and recovery assessment",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–6 engineering days; independent review",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M7.5",
        "title": "Exercise owner native-profile sustained recovery",
        "required_evidence": "Retain private restart/cold-replay and ordinary-user operating evidence",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–6 engineering days; owner Windows required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      }
    ]
  },
  {
    "id": "M8",
    "title": "Broker paper integration and operations",
    "priority": "P0",
    "external_gate": "Authorized paper account",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M8.1",
        "title": "Verify paper-only provider identity and entitlements",
        "required_evidence": "Scoped credentials, hostname, account and quota policy; no live permissions",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–4 engineering days; broker access required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M8.2",
        "title": "Finish single-attempt POST/GET/SSE uncertainty matrix",
        "required_evidence": "Lost acknowledgements, revoked permissions, stream gaps, pacing and fail-closed replays",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "4–7 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M8.3",
        "title": "Supervise bounded actual paper order lifecycle",
        "required_evidence": "Paper-only accepted/unknown order, fill and cancellation receipts",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–7 engineering days; supervised paper account required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M8.4",
        "title": "Validate provider quota, clock and reconnect semantics",
        "required_evidence": "Real headers, activity delivery, ordering, provider-wide pacing and deduplication",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–6 engineering days; broker access required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M8.5",
        "title": "Obtain independent broker-paper account reconciliation",
        "required_evidence": "Account, fees, holds, cash, positions, source and original events reviewed",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–6 engineering days; independent review",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      }
    ]
  },
  {
    "id": "M9",
    "title": "Release acceptance and authorization",
    "priority": "P0",
    "external_gate": "Independent/owner",
    "status": "REVIEW_REQUIRED",
    "tasks": [
      {
        "id": "M9.1",
        "title": "Freeze threat model, release scope and privacy",
        "required_evidence": "Exact source manifest, secret audit, permissions and residual-risk register",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "3–5 engineering days",
        "mode": "OFFLINE_CANDIDATE"
      },
      {
        "id": "M9.2",
        "title": "Complete exact-head security/browser/Windows/package gates",
        "required_evidence": "All required terminal CI, installation, prior-upgrade and saved-evidence checks",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "2–5 engineering days; native Windows required",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M9.3",
        "title": "Obtain separate reviews for all nine stages",
        "required_evidence": "Independently signed evidence, unresolved blockers and residual risk decisions",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "4–10 engineering days; independent reviews",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M9.4",
        "title": "Validate funding/live/cloud readiness without activation",
        "required_evidence": "Secure account/keys, single execution owner, remote stop, billing and recovery dry-run",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "5–10 engineering days; external readiness only",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      },
      {
        "id": "M9.5",
        "title": "Collect explicit owner release decision",
        "required_evidence": "No 1.0, fund movement or real trading before all gates and owner approval",
        "state": "REVIEW_REQUIRED",
        "effort_estimate": "Owner decision; no estimated release date",
        "mode": "EXTERNAL_OR_NATIVE_GATE"
      }
    ]
  }
]''')
_ORDER = ("M8", "M4", "M3", "M7", "M1", "M2", "M5", "M6", "M9")


def execution_plan():
    """Return an independent copy so callers cannot mutate the source plan."""
    result = deepcopy(_PLAN)
    assert len(result) == 9
    assert {group["id"] for group in result} == {"M" + str(i) for i in range(1, 10)}
    identifiers = [task["id"] for group in result for task in group["tasks"]]
    assert len(identifiers) == len(set(identifiers)) == 45
    assert all(group["status"] == "REVIEW_REQUIRED" for group in result)
    return result


def next_work_batches():
    """Candidates only; never auto-start tasks or grant external authority."""
    groups = {entry["id"]: entry for entry in execution_plan()}
    return [
        dict(milestone=identity, priority=groups[identity]["priority"],
             offline_candidates=[task["id"] for task in groups[identity]["tasks"]
                                 if task["mode"] == "OFFLINE_CANDIDATE"],
             external_or_native_gates=[task["id"] for task in groups[identity]["tasks"]
                                       if task["mode"] != "OFFLINE_CANDIDATE"])
        for identity in _ORDER
    ]


def markdown_lines():
    lines = [
        "## Engineering milestone execution queue (read-only; not acceptance)",
        "",
        "Every task remains REVIEW_REQUIRED until separately evidenced and independently reviewed.",
        "Effort bands are rough engineering estimates; external wait and observation windows are separate.",
        "Suggested priority: M8 broker paper, M4 reconciliation, M3 risk, M7 sustained operation,",
        "M1 Windows, M2 data, M5 capacity, M6 research, M9 final acceptance.",
        "Do offline work in parallel when account/Windows/feed access blocks external gates;",
        "serialize shared branch writes, pin expected heads, and never bypass fail-closed controls.",
        "No percentage, acceptance or 1.0 approval is inferred from checklist size.",
    ]
    for group in execution_plan():
        lines.extend(("", "### " + group["id"] + " — " + group["title"],
                      "", "Priority: " + group["priority"] + "; external dependency: " +
                      group["external_gate"] + ".", ""))
        for task in group["tasks"]:
            lines.append("- [ ] " + task["id"] + " " + task["title"] +
                         " (" + task["mode"] + "; " + task["effort_estimate"] + "). " +
                         "Acceptance evidence: " + task["required_evidence"] + ".")
    lines.extend(("", "### Per-run execution discipline", "",
        "1. Read current development head, active PR/sprint, source roadmap, AGENTS,",
        "   LAUNCH_DRIVE, VERSIONING and exact-head CI failures before selecting work.",
        "2. Choose the largest coherent safe batch from highest-priority unaccepted tasks;",
        "   group implementation, regression, saved evidence, packaging and documentation.",
        "3. Parallelize read-only inspections and independent tests; serialize shared writes",
        "   using isolated branches and expected-head leases; preserve remote-only changes.",
        "4. Execute exact-source offline profiles, saved-evidence verification, security",
        "   and platform/browser/installer checks that are actually available.",
        "5. Record verified commits, failed/pending gates, follow-on dependencies and",
        "   the next batch. Do not raise 38%/30–45%/0-of-9 without meaningful acceptance.",
        "6. Keep broker/funding/live/cloud permissions OFF; private evidence never goes to Git.",
    ))
    return lines
