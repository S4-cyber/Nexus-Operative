# Nexus Operative delivery forecast and milestone coverage

## Current candidate: v0.340.129

Provider read-review stable reads, review scope, precommit callbacks and retained-receipt recovery now preserve process-control signals even when a secondary source recheck or cold inspection would fail. Ordinary failures remain fail-closed, while ordinary lost acknowledgements still require stable exact retained state.

No exact-source v0.340.128 workflow result was visible when this candidate began. Fresh exact-source v0.340.129 hosted workflows and actual authorized-account acceptance remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.128

Account read-review receipts, provider read-review receipts and paper-activity acceptance now preserve `KeyboardInterrupt` and `SystemExit` across explicit post-commit sync. The committed transition remains exactly once and requires explicit cold inspection plus duplicate recovery. Ordinary sync failures still fail closed, and ordinary provider-wrapper lost acknowledgements still recover from retained state.

No exact-source v0.340.127 workflow result was visible when this candidate began. Fresh exact-source v0.340.128 hosted workflows and actual authorized-account acceptance remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.127

The durable paper-activity acceptance journal now reports fixed read-acknowledgement uncertainty for ordinary connection-close failures after complete inspection, preserving retained bytes until an explicit retry. A committed write with the same close uncertainty reports fixed commit-durability uncertainty, retains exactly one accepted transition and requires explicit duplicate recovery.

No exact-source v0.340.126 workflow result was visible when this candidate began. Fresh exact-source v0.340.127 hosted workflows and actual authorized-account acceptance remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.126

The durable paper-activity acceptance journal now retains its exact exclusive-create identity across connection binding and every SQLite setup statement. Existing writers also prove the exact connection path and serialized main content before setup. Injected mid-setup identity loss allows only exact empty-file removal or retention of the same nonempty identity under cleanup uncertainty, with one connection attempt, no sidecars and an explicit clean retry.

No exact-source v0.340.125 workflow result was visible when this candidate began. Fresh exact-source v0.340.126 hosted workflows and actual authorized-account acceptance remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.125

Windows SQLite may materialize bytes after early setup PRAGMAs. Receipt initialization acceptance now distinguishes the two safe fail-closed outcomes: remove only the unchanged empty file and preserve the original identity error, or retain the same original nonempty identity under cleanup uncertainty. Both paths prove one connection attempt, no sidecars and no automatic retry; paired deterministic tests prove exact nonempty files are never deleted.

The exact v0.340.124 public-security workflow and its saved-evidence and Portal browser-help jobs passed. Its research and Windows packaging workflows exposed the corrected eight-case direct-test portability assumption. Fresh exact-source v0.340.125 workflows remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.124

Fresh account-read and provider-read receipt initialization now retains the exact exclusively created file identity across connection binding and every SQLite setup statement. A mid-setup identity loss closes the connection and fails closed, while only the exact unchanged empty sidecar-free file may be removed before an explicit retry.

The exact v0.340.123 parent passed hosted public-security, research safety/browser/evidence and Windows packaging/installer workflows. Fresh exact-source v0.340.124 workflows remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.123

Fresh mixed account-read receipt setup now binds cleanup to the exact file identity captured at exclusive creation. Uncommitted connection or initialization failures restore absence only when that same empty file remains; substituted paths and close uncertainty fail closed without speculative deletion.

The exact v0.340.122 parent passed hosted public-security, research safety/browser/evidence and Windows packaging/installer workflows. Fresh exact-source v0.340.123 workflows remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.122

The mixed account-read receipt now applies an explicit writable-file sync after commit and pins the exact descriptor bytes to the committed pathname fingerprint. New POSIX stores also sync only their captured parent-directory identity. Any flush or descriptor uncertainty fails closed while retaining exactly one receipt for inspection and explicit duplicate recovery.

The exact v0.340.121 parent passed hosted public-security, research safety/browser/evidence and Windows packaging/installer workflows. Fresh exact-source v0.340.122 workflows remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.121

Account-receipt writers now pin the exact committed pathname identity and bounded fingerprint across post-commit callbacks, result construction and successful connection close. Late file-set change is refused while the receipt remains exactly once for explicit duplicate recovery.

The exact v0.340.120 parent passed hosted public-security, research safety/browser/evidence and Windows packaging/installer workflows. Fresh exact-source v0.340.121 workflows remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.120

Account-receipt reads now re-prove the opened SQLite connection's path and serialized content after complete replay, and writes repeat the proof after commit. Late pathname/connection divergence is refused; committed writes require explicit exact-head duplicate recovery rather than automatic retry.

The exact v0.340.119 parent passed hosted public-security, research safety/browser/evidence and Windows packaging/installer workflows. Fresh exact-source v0.340.120 workflows remain required.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.119

Mandatory account-receipt content-binding evidence now closes its disposable foreign SQLite connection before temporary-directory cleanup. This repairs the exact hosted Windows handle-lifecycle failure without suppressing cleanup errors or weakening the v0.340.118 content check.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.118

Existing account-read receipt connections now prove their serialized SQLite `main` bytes match the bounded file at the already verified store path. Pathname-spoofed foreign connections fail closed without changing either store, while exclusive-lock contention retains its bounded busy result.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.117

Retained account-read receipt connections now prove their exact SQLite `main` path before setup or access. Substituted reads and fresh writers fail closed while preserving existing databases and allowing only an explicit clean retry.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.116

Retained account-read receipt writes now report a sanitized fixed durability-uncertain result if SQLite connection close fails after commit. The receipt remains exactly once and only an explicit duplicate replay bound to the observed head can recover acknowledgement.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.115

Retained account-read receipt inspection now reports a sanitized fixed uncertainty result if SQLite connection close fails after a complete read. Receipt history remains unchanged and only a fresh explicit inspection retry can return success, matching the provider-read boundary.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.114

Provider-read receipt inspection now reports a sanitized fixed uncertainty result if SQLite connection close fails after a complete read. Receipt history remains unchanged and only a fresh explicit inspection retry can return success.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.113

Post-commit provider-read receipt exceptions now fail with a fixed durability-uncertain result and require explicit duplicate recovery. This prevents a connection-close error from being reconstructed as success while preserving exactly-once receipt history.

The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. This does not establish a launch date or any new trading, funding, live/cloud or release permission.

## Previous candidate: v0.340.112

The provider-read receipt writer now reasserts the exact committed file identity and bounded fingerprint after SQLite connection close before acknowledging success. Close-time disagreement remains a fixed fail-closed condition with exactly-once retention and explicit duplicate recovery. The separately selected v0.340.111 history-only preparation mode remains available and non-authoritative.

This closes a local acknowledgement boundary without accepting an actual account or external gate. The rough implementation estimate therefore remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. Release approval, trading permission, funding/live/cloud authority and launch date remain false or unset.

## Previous candidate: v0.340.111

The packaged provider-results operator tool now offers an explicitly selected history-only preparation mode with its own strict manifest. It checks retained provider/account pacing agreement without requiring normalized response bindings, performs no provider/vault IO or writes, and cannot authorize routing or funds. Default normalized-result review remains separate. See PROVIDER_RESULT_REVIEW.md for the command and manifest.

This removes one operator-preparation gap, without closing the authorized broker-paper or sustained-market acceptance gates. The rough implementation estimate remains about 40% (30–45%, low confidence), with 0/9 major stages fully accepted. No launch date or new permission is established; exact-source hosted and native-owner acceptance remain required.

## Candidate update on 2026-11-01

v0.340.110 connects the source-controlled 28-step acceptance plan to the existing Portal roadmap view, with bounded validation, exact prerequisite/status checks, literal text rendering and stale-result clearing. Narrow-window testing also prompted shorter broker-recovery and pacing-review help. Source v0.340.108 passed all three hosted workflows; v0.340.109 security and browser jobs passed at the initial check while its remaining jobs were still running. Fresh exact-source v0.340.110 hosted checks remain required.

The implementation estimate remains about 40% (rough range 30–45%, low confidence), with 0/9 major stages fully accepted. This usability work does not close authorized broker-paper, sustained real-feed, funds, live/cloud, independent or owner acceptance. The next operational gate is still supervised connected-paper acceptance alongside sustained local paper trials. Local paper testing needs no deposit; connected-paper acceptance needs an authorized paper account and scoped local credential setup. No launch date is established, and historical conditional planning ranges below are not a delivery commitment.

## Candidate update on 2026-10-30

Current candidate: v0.340.62, based on published v0.340.61 source `aecfa21d3eccf51bd2749944e143f18e344ad69a`. v0.340.62 increases only the hosted Windows/Python 3.11 complete-regression job's finite outer budget from 35 to 60 minutes. Exact compile and full pytest commands, pinned Actions, nonpersistent checkout credentials, separate browser/evidence jobs and application behavior are unchanged. v0.340.61 security 41122236996 and Windows 41122237165 passed. Research 41122237311 passed browser/help and saved evidence but was cancelled at 75% after 3,548 passes and 16 skips when offline regression exhausted the old budget; it is not pre-claimed. Fresh exact-source v0.340.62 results and actual authorized paper-account acceptance remain required.

The rough weighted implementation estimate remains about 40% (30–45%, low confidence), with 0/9 fully accepted major stages. This defensive broker-paper boundary does not change release approval, live-trading permission, money-movement authority or the conditional planning ranges below.

The rough weighted implementation estimate is intentionally unchanged because this CI evidence repair does not integrate a new accepted external gate. Local and hosted v0.340.62 validation must be recorded separately; passing tests cannot substitute for actual-account, sustained-market, independent-review or owner acceptance.

Local pre-publication validation passed 4,668 tests with one expected non-Windows skip and one existing warning; the final workflow/version/roadmap/installer focus passed 89. Sixteen offline profiles and saved evidence verified against 395 source files / `0189adb9a0eec2f2e08c703b8ee2a51186d1c45136f9f9ac466a27580b8008ae`; evidence SHA-256 is `d339fff9d528b4c01d21e4bfedd2a733536c4455d36cc2ebfedd78228fa6cf9c` and wheel SHA-256 is `c511612a280b572a0a10df6b714fe1caf287886d46893191e693f4fd303484c6`. Fresh exact-source hosted checks remain required.

## Prior v0.340.48 candidate record

v0.340.48 made the shared distribution-manifest producer and verifier stable, bounded, exact-schema and no-overwrite. Its exact-source public security workflow passed. Browser-help and saved-evidence jobs passed, while Windows and offline full regression each recorded 4,585 passes, 19 skips and one warning before four exact-output assertions rejected CRLF instead of LF. This is a failed exact-source candidate, not hosted acceptance.

## Prior v0.340.47 candidate record

Current candidate: v0.340.47, based on published v0.340.46 source `97cb861dc11aa338b699c9831b3e8b8db1f01f13`. v0.340.47 re-hashes the final installer after manifest creation and requires exact agreement with the phase review and artifact manifest before upload. No application-runtime or external authority is added. Exact-source v0.340.46 passed security, research/browser/evidence and Windows workflows. Fresh exact-source v0.340.47 results remain required.

The rough weighted implementation estimate remains about 40% (30–45%, low confidence), with 0/9 fully accepted major stages. This integrity fix does not change release approval, live-trading permission, money-movement authority or the conditional planning ranges below.

Local pre-publication validation is complete: 4,587 tests passed with one expected non-Windows skip and one warning, the 225-test installer/distribution/evidence/roadmap/version focus passed, all sixteen release profiles and saved-evidence verification passed, and compilation, dependency, deterministic-roadmap, controller, Portal-syntax and wheel-build checks passed. Fresh exact-source hosted checks remain required. The rough weighted implementation estimate is intentionally unchanged because this acceptance-infrastructure hardening does not integrate a new accepted external gate.

## Prior v0.340.17 candidate record

Current candidate: v0.340.17, based on published v0.340.16 source `5c4b132c64ae437c44eedf23326cc683b11fa833`. All three v0.340.16 exact-source workflows passed. v0.340.17 additionally pins the settled post-result budget bytes and rechecks both journals around every later cursor and budget reader, preventing a same-inode rewrite plus exception from being hidden behind general recovery uncertainty. It adds no runtime retry, provider access, credential access or UI authority. Fresh exact-source hosted results remain required.

The rough weighted implementation estimate remains about 40% (30–45%, low confidence), with 0/9 fully accepted major stages. This integrity fix does not change release approval, live-trading permission, money-movement authority or the conditional planning ranges below.

Final local validation passed 4,400 tests with one expected non-Windows skip and one existing Starlette/httpx warning; the reconnect-budget and release-trial files passed 435 tests. All sixteen bounded offline profiles and saved-evidence verification matched 387 source files / `bc2bab28c76bfa42bf409943c86f0e8eff426e4af1a6e6c9c0b7fbbdaf9462fa`; `validation/release_trials_0_340_17.json` SHA256 is `291c16cc7f36ed6172a4f7cace4232e9ab1cf4e6122541da9bccd7f5473990ca`. Fresh v0.340.17 security, browser/evidence/regression and Windows packaging workflows remain mandatory. The rough weighted implementation estimate is intentionally unchanged because this hardening does not integrate a new accepted external gate.

## Prior v0.340.16 candidate record

Current candidate: v0.340.16, based on published v0.340.15 source `36b87f3d9f6887bdf71d8319d3ac5bff5870bcf0`. All three v0.340.15 exact-source workflows passed. v0.340.16 additionally requires the initial reconciliation fingerprints to remain unchanged on exceptional budget/cursor reader exits, preventing a same-inode rewrite from being hidden behind a general semantic diagnostic. It adds no runtime retry, provider access, credential access or UI authority. Fresh exact-source hosted results remain required.

The rough weighted implementation estimate remains about 40% (30–45%, low confidence), with 0/9 fully accepted major stages. This integrity fix does not change release approval, live-trading permission, money-movement authority or the conditional planning ranges below.

Final local validation passed 4,392 tests with one expected non-Windows skip and one existing Starlette/httpx warning; the reconnect-budget and release-trial files passed 427 tests. All sixteen bounded offline profiles and saved-evidence verification matched 387 source files / `7c1061a85dc6c9202e5a8e4bd87a79115759d4215c0902ff50ec3ab08efcd52f`; `validation/release_trials_0_340_16.json` SHA256 is `ba1dd6d821a3b386309cbcc767486ecc892c4b934e6342ca310d19ac244c4ee7`. Fresh v0.340.16 security, browser/evidence/regression and Windows packaging workflows remain mandatory. The rough weighted implementation estimate is intentionally unchanged because this hardening does not integrate a new accepted external gate.

## Prior v0.340.15 candidate record

Current candidate: v0.340.15, based on published v0.340.14 source `91ca3a31eea7f4727d236938035d459fd6f63f92`. The v0.340.14 lost-result recovery evidence remains mandatory. v0.340.15 additionally content-pins the reconnect budget across the pre-commit cursor-binding decision and requires a same-inode rewrite refusal with preserved pending state. It adds no runtime retry, provider access, credential access or UI authority. Fresh exact-source hosted results remain required.

The rough weighted implementation estimate remains about 40% (30–45%, low confidence), with 0/9 fully accepted major stages. This integrity fix does not change release approval, live-trading permission, money-movement authority or the conditional planning ranges below.

Final local validation passed 4,388 tests with one expected non-Windows skip and one existing Starlette/httpx warning; the reconnect-budget and release-trial files passed 423 tests. All sixteen bounded offline profiles and saved-evidence verification matched 387 source files / `d2e48a29e9076286673fca2acad4071ec2c876a6f7ec87e4dca5277b728790a2`; `validation/release_trials_0_340_15.json` SHA256 is `69c17aa0be2ca7a6f449c377b11072b7faeb54147a8cca01ce43ccf11fc2c0e7`. Exact-source v0.340.14 parent `91ca3a31eea7f4727d236938035d459fd6f63f92` passed public security 40771468125, research safety/browser/evidence 40771468266 and Windows packaging/installer 40771470440. Fresh exact-source v0.340.15 workflows remain mandatory. The rough weighted implementation estimate is intentionally unchanged because this hardening does not integrate a new accepted external gate.

## Reassessment on 2026-10-28

Current candidate: v0.340.9, based on v0.340.8 source ced0884bb08acad99654042ea8a2c7f24a2678b9. The October 28 hosted checks exposed Windows backup/metadata defects and a reached full-history audit budget. Repairs are under fresh validation; the failed source is not a verified installer checkpoint. There is no measured improvement to the 0/9 fully accepted major-stage count. The 40% engineering estimate is a carried-forward low-confidence baseline, not a current operational-readiness score.

Functionality has advanced: an internal explicitly confirmed single-attempt paper POST boundary, shared-local request/outcome recovery and bounded Activity SSE transport/checkpoint/reconnect coordination now exist. These are not yet an accepted saved-write-credential, Portal or autonomous broker-order route. The fixed local BTC virtual experiment and advisory numerical council remain the usable experimental flow. Deposits, live execution and computer-off cloud remain unfinished.

Planning ranges remain conditional rather than deadlines. A connected-paper acceptance candidate may require another 2–6 weeks after the Windows gate is restored and an authorized account/private test environment is available; an additional 1–3 weeks of sustained operational evaluation may follow. The previous January–April 2027 full-scope 1.0 window remains a low-confidence scenario, with later delivery possible. Current patch cadence does not establish accepted-gate velocity, and green CI alone cannot close actual-account, policy, funding, cloud or independent-review gates.

Owner involvement: no setup is needed for ongoing coding. Prepare a paper-only account and a Windows ordinary-user test profile for the supervised acceptance stage; enter secrets only through the documented local credential flow. No bank deposit or paid AI is required for local paper testing. See STARTUP_GUIDE.md for the owner checklist.

## Previous assessment retained for history

Assessment date: 2026-10-28 (America/Los_Angeles).
Parent source reviewed: `8bff74772b0f2c4aa16902f2b45a5fc555fda1fa` (v0.340.7), whose published tree matched the prior locally tested tree. The GitHub connector exposed no workflow or status records for that exact commit, so no v0.340.7 hosted pass is claimed. v0.340.8 is the current candidate and requires fresh exact-source checks.
This is a planning record, not a release authorization or a change to runtime permissions.

Engineering checkpoint note: v0.340.8 closes mixed-version recovery by requiring the transport-bound v6 profile in lost/malformed acknowledgement recovery and explicit no-network reconciliation. Legacy history remains readable but cannot retire a current reservation. This is sampled local consistency, not provider acceptance, delivery completeness, native durability, market realism, profitability or sustained acceptance. The carried-forward percentage and conditional planning windows are unchanged.

Local candidate evidence: final local validation passed 4,376 tests with one expected non-Windows skip and one existing warning; the reconnect-budget suite passed 324, the direct cursor/checkpoint/reconnect set passed 425 and complete Activity SSE passed 523. All sixteen profiles and saved-evidence verification matched 387 source files / `78814f7447e35664f46dbc5d8b4b05a47bf62512535163f3a93173c8a120f0b7`; v0.340.8 evidence SHA256 is `ca09a62b2b2a3f22871332b7167bd1132cab0e857b75590238d22ad14c783d01`. Local Gitleaks is unavailable. Fresh exact-source v0.340.8 hosted workflows and every independent gate remain open.

## Current assessment

- Rough implementation: **about 40%**, with a **30–45%** judgment range and low confidence.
- The bundled weighted model produces an index of 38 before rounding. Its numerical baseline was assessed on 2026-10-18; this review carries that baseline forward rather than inventing an increase from patch volume.
- Fully accepted major production stages: **0/9**. That is 0% of complete stage signoffs, not 0% working software. A calibrated numerical operational-readiness score is not established.
- Local research, the numerical council and an explicitly engaged fixed-BTC virtual trading experiment exist. Learned council signals do not yet direct orders.
- The next major operational gate is connected broker-paper execution and actual-account acceptance. A production broker submission adapter, secure funding, governed live operation and computer-off deployment remain unfinished.
- Exact v0.340.7 parent `8bff74772b0f2c4aa16902f2b45a5fc555fda1fa` had no connector-visible workflow or status records at review. Fresh v0.340.8 hosted results remain required; no parent result transfers to this candidate.

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
| D8: Speed, resilience and independent acceptance | 35% | Durable latency, recovery, security and operational fixtures exist | Full feed-to-broker measurements, sustained load/outage/storage acceptance and independent security/risk review |
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
