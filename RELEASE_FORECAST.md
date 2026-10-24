# Nexus Operative delivery forecast and milestone coverage

Assessment date: 2026-10-23 (America/Los_Angeles).
Parent source reviewed: `413d1de6c84b4ed340bacef48de16d0a2461acda` (v0.238.0); v0.239.0 is the current candidate and requires fresh exact-source checks.
This is a planning record, not a release authorization or a change to runtime permissions.

Engineering checkpoint note: v0.239.0 adds a private account-bound, exact-head Activity SSE cursor journal after the supplied-only reconnect review. It persists the actual next ULID plus hashes/aggregate counts, reopens after process restart and makes the exact last transition idempotent after a lost commit acknowledgment. It deliberately performs no connection, entitlement/authentication check, actual reconnect/backoff, delivery/replay completeness, native power-loss acceptance or provider attestation. Source/coverage acceptance, saved write credentials, explicit operator integration, broker-wide/distributed POST coordination and sustained trials remain absent. The carried-forward percentage and conditional planning windows are unchanged.

## Current assessment

- Rough implementation: **about 40%**, with a **30–45%** judgment range and low confidence.
- The bundled weighted model produces an index of 38 before rounding. Its numerical baseline was assessed on 2026-10-18; this review carries that baseline forward rather than inventing an increase from patch volume.
- Fully accepted major production stages: **0/9**. That is 0% of complete stage signoffs, not 0% working software. A calibrated numerical operational-readiness score is not established.
- Local research, the numerical council and an explicitly engaged fixed-BTC virtual trading experiment exist. Learned council signals do not yet direct orders.
- The next major operational gate is connected broker-paper execution and actual-account acceptance. A production broker submission adapter, secure funding, governed live operation and computer-off deployment remain unfinished.
- Exact v0.238.0 public security/history, saved release evidence, browser-help, complete regression and Windows EXE/installer checks passed on 2026-10-23 Pacific time. Those parent results do not transfer; v0.239.0 requires all fresh exact-source checks.

## Roadmap coverage

These are carried-forward engineering estimates, not acceptance percentages or estimated elapsed time.

| Stage | Rough implementation | Operational position | Work still required |
| --- | --- | --- | --- |
| D1: Usable research application | 85% | Local foundation implemented; automated Windows checks exist | Ordinary-user native-window/profile/ACL, upgrades and owner recovery acceptance |
| D2: Learning models and strategy evidence | 55% | Built-in numerical models and historical cost checks exist | Independent provenance/calibration, reproducible forward evidence, realistic costs and drawdown review; validated policy integration |
| D3: Autonomous local paper trading | 70% | Experimental fixed-BTC virtual engine implemented | Sustained real-feed trial, outages/restarts/emergency drills, incident and accounting review |
| D4: Connected broker paper trading | 35% | Read/recovery/reconciliation foundations plus an internal single-attempt paper POST boundary, local POST/request/activity composition and one-account POST-to-guarded-balance composition; no operator route or account acceptance | Saved write credentials and explicit operator integration, broker-wide/distributed pacing, source/coverage acceptance, actual fills/fees/holds/cash/position reconciliation and broker-account acceptance |
| D5: Secure deposits and available funds | 0% | Not implemented | Selected provider-hosted funding, scoped credentials and pending/settled/failed/reversed/duplicate transfer reconciliation |
| D6: Governed autonomous live trading | 10% | Blocked; foundational controls only | Validated policy, live adapter, accepted funds/risk controls, monitoring and separately authorized limited rollout |
| D7: Computer-off cloud and shared hub | 10% | Local stores/ownership only; no accepted deployment | Hosting choice, authenticated remote control, encryption/key recovery, distributed fencing, quotas and multi-day computer-off acceptance |
| D8: Expansion and opportunity selection | 35% | Bounded research discovery exists | Authorized sources/venues, cost/liquidity/diversification evidence and reviewed discovery-to-policy integration |
| D9: Final 1.0 production release | 0% | Blocked | All agreed stage evidence, independent security/risk review and explicit owner approval |

The highest percentages describe reusable local foundations. They do not mean the remaining integration can be completed by closing a few cosmetic items.

## Pace and what it can predict

The branch API returned 98 commits since 2026-10-19T00:00:00Z through the reviewed source. Recent work is concentrated on evidence consistency, recovery, reconciliation and CI reliability. This demonstrates development activity; it does not measure accepted milestones per day.

No major production acceptance gate has closed in the reviewed roadmap. Therefore there is no defensible linear formula from commit count, version number or test count to a launch date. Keeping exactly the same hardening-heavy work mix leaves the launch date unbounded.

The practical next change is to focus development and acceptance on the connected paper lifecycle and sustained trials, while preserving existing controls.

## Conditional planning windows

The following are low-confidence planning scenarios measured from **2026-10-23**. They assume focused development continues most days, an authorized paper account/private Windows test environment is available when needed, and independent review can be arranged. They are human planning ranges, not measured velocity forecasts or promised deadlines.

| Deliverable | Planning range | Approximate date window | Conditions |
| --- | --- | --- | --- |
| Experimental local paper application | Available now | Current verified test checkpoint | Existing virtual-only functionality; owner-environment and sustained acceptance remain open |
| End-to-end connected broker-paper prototype | 2–6 weeks | **2026-11-06 to 2026-12-04** | Finish paper submission transport, original-ID uncertainty handling and actual-account reconciliation; retain paper-only scope |
| First sustained connected-paper acceptance candidate | 1–3 additional weeks after a stable connected-paper build | **2026-11-13 to 2026-12-25**, if the prototype window holds | A minimum 72-hour declared-coverage diagnostic is only one input; review incidents, accounting, costs, policy, outages and restart evidence independently |
| Full agreed 1.0 candidate, including funding, governed live workflows and computer-off operation | 12–24+ weeks | **2027-01-15 to 2027-04-09 or later** | Provider integrations, validated policy, security/risk/operational acceptance and owner approval all succeed; funding/live/cloud scope is retained |

A 72-hour observation window cannot be replaced by three hours of fast tests. Failed policy evidence, provider changes, private-environment defects or unavailable independent review can move these windows substantially or prevent acceptance. No exact launch timestamp is established. Live-money activation always requires separate authorization.

## Next focused engineering and acceptance sprint

1. **Connected paper order lifecycle:** implement the paper-only provider submission boundary behind explicit permission. Persist original intent before IO; recheck stop/permission after durable admission; never blindly resubmit after a lost acknowledgment. Exercise original-ID recovery, rate/auth/time failures, rejection, partial fills and cancellation.
2. **Actual-account reconciliation:** verify received activity coverage and correction semantics; bind explicit executions and fees; reconcile open-order holds, cash, positions and available amounts. Local supplied-response agreement remains supporting evidence.
3. **Sustained paper operation:** begin the declared real-feed window only after the relevant build is stable. Retain quote/accounting evidence, coverage, incidents, restart and emergency outcomes; investigate gaps instead of suppressing them.
4. **Owner Windows acceptance:** use an exact verified installer, a private ordinary-user profile and backups; test native startup, settings preservation, upgrades, credential recovery and storage-fault behavior.
5. **Then production integrations:** implement reviewed provider-hosted funding, governed live routing and authenticated encrypted cloud operation. Do not infer those capabilities from paper results.

This prioritization is a development plan. It does not start a process, connect an account, request keys in chat, place an order, move money or enroll in cloud hosting.

## Continuity

Read this file with ROADMAP.md, LAUNCH_DRIVE.md and AGENTS.md. ROADMAP.md remains generated from nexus/release_roadmap.py. Preserve the distinction between implementation estimates, automated CI, actual operational evidence and complete independent acceptance. Reforecast after the connected-paper prototype and first sustained trial produce usable evidence.
