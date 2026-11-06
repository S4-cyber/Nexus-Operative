# Nexus Operative milestone accomplishment report

Report date: November 6, 2026. Development candidate: **v0.340.196**. Reviewed source baseline: `c7958df7dc3a3d1ff05ae95d232aa5ab3bcfc39e` (v0.340.195).

**Overall engineering estimate: about 40% (rough range 30–45%, low confidence). Major operational stages fully accepted: 0/9.** These are the existing weighted scope estimates, whose manual assessment is dated October 18; they are not a new measurement, elapsed-time percentage, probability of profitability or release authorization. Version numbers and passing tests do not automatically raise them. No dependable launch date is established.

## Accomplishments and remaining acceptance

| Milestone | Engineering estimate | Implemented accomplishments | Remaining steps to acceptance |
| --- | ---: | --- | --- |
| D1 — Usable desktop and setup | 85% | Windows packaging/install/reinstall/recovery checks; seven tabs; beginner To Do; separate Portal, Engage Paper and emergency cut; account-based Matrix views; 28-step read-only roadmap and practical hover help. | Ordinary-user clean-profile and upgrade feedback; native-window legibility; saved/OFF settings, restart and recovery acceptance. |
| D2 — Models and strategy evidence | 55% | Bounded, deduplicated local history; numerical council; held-out and modeled-cost checks; saved research evidence. Council remains advisory. | Independent data provenance/calibration; leakage-free evaluation and sustained forward results after fees, spread, slippage and drawdowns; explicit policy review. |
| D3 — Autonomous local paper | 70% | Explicit fixed-BTC virtual-fund experiment; risk gates, durable accounting, restart-stopped behavior and independent emergency cut. | Genuine-feed observation for at least 72 hours; review the 95% declared-coverage diagnostic plus outages, incidents, reconciliation and strategy quality. Coverage alone is insufficient. |
| D4 — Connected broker paper | 35% | Explicit paper credential/read controls; retained pacing and exact-head recovery; offline account/activity/response/history reviews; an installed Activity SSE operator with caller-driven attempt/reconnect, durable cursor/budget and synthetic restart/stop/failure evidence. Internal one-attempt paper submission boundary exists. | Accept credentials, reads, recovery and stream behavior on an authorized paper account/private Windows volume; separately review saved write credentials/operator integration and broker-wide pacing; reconcile actual fills, fees, holds, cash and positions before strategy routing. |
| D5 — Secure funding | 0% | Funding remains preview-only; no operational bank/deposit/withdrawal flow. | Select and implement provider-hosted authorization/funding; protect credentials; reconcile pending/settled/reversed/duplicate transfers and spendable cash; obtain separate acceptance. |
| D6 — Governed live autonomy | 10% | Reusable risk, stop and recovery foundations; real-money execution remains disabled. | Accepted policy/live adapter, verified spendable funds, hard caps, monitoring and reconciliation; independent review and separately authorized limited rollout. |
| D7 — Computer-off cloud and shared hub | 10% | Local capped history/model stores, deduplication and process ownership foundations. | Verify eligible free hosting/quotas; authenticated remote control, encryption/key recovery, durable shared state and desktop/cloud fencing; multi-day desktop-off paper trial and remote-stop acceptance. Cloud is not deployed. |
| D8 — Speed, resilience and assurance | 35% | Bounded process/HTTP, storage, queue, crash/recovery and stop-pressure tests; source-bound offline evidence; Windows and browser CI. | Measure the accepted feed-to-decision-to-provider path, sustained load/storage/outages and restore/rollback; complete independent security/risk and operational review. Fixture throughput is not broker fill speed. |
| D9 — 1.0 release | 0% | Prerelease version gates and persistent acceptance plan. | Accept D1–D8 in the agreed scope, resolve security/reconciliation defects, verify one exact release candidate/installer and obtain independent review plus explicit owner release approval. Release never activates live trading by itself. |

## Progress since the last manual handoff

The earlier v0.340.109–111 work exposed the 28-step acceptance plan in Portal, improved narrow-window help and added an installed history-only provider/account review. Subsequent development added an installed saved-pair Activity SSE operator and substantially expanded durable reconnect, restart, interruption, credential-change, emergency-stop and cleanup-denial coverage. These are useful implementation and resilience accomplishments; their evidence is synthetic or local, not actual-account or sustained-market acceptance.

The v0.340.196 source reports 5,395 local passing tests, one expected non-Windows skip and sixteen source-bound offline profiles. This is 568 more passing tests than the v0.340.111 handoff, without implying a corresponding percentage increase.

Verified prior-source hosted checkpoints:

- v0.340.195 [security checks passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42305251659), [research/browser/evidence passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42305251712), and [Windows/installer passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42305251661).
- v0.340.194 [security checks passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42296516134), [research/browser/evidence passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42296515957), and [Windows/installer passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42296515986).
- v0.340.193 [security checks passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42290610033), and its research and Windows workflows subsequently passed.
- v0.340.190 [Windows installer/distribution checks passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42270833513).
- v0.340.190 [research regression, browser and saved-evidence checks passed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42270833448).
- Its [security workflow failed](https://github.com/S4-cyber/Nexus-Operative/actions/runs/42270833307) on two historical synthetic fixture findings. A passed installer is not an all-green release candidate.

## v0.340.196 fingerprint-bound retained-input parsing

The installed Activity SSE loader now compares the exact parsed bytes to the private pre-attempt SHA-256. A regression supplies identical changed bytes for both reads and restores the original before closing metadata checks; v0.340.195 accepted the substituted parsed event, while this candidate fails closed. The complete 294-test acceptance-probe module passed after the fix.

This is a fail-closed implementation improvement, not authorized paper-account, provider-source, received-coverage, sustained-market, native-storage, fee/hold/funds, live/cloud or release acceptance. It changes no Portal control, editable parameter or authority default. The estimate remains about 40% (30–45%, low confidence), with 0/9 major operational stages accepted.

Final local validation passed 5,395 tests with one expected non-Windows skip and one existing Starlette/httpx warning; the finalized focus passed 454 tests. Sixteen offline profiles match 401 source files / `f334c06d9644369a2dcf2d82b91e7e4b3e65e95df522e5eb2c7949d3f97a5527`; report SHA256 is `5fc846340d6d79c9fc68d611784a457c0a41a684b224af076eeda0e1d6f01ab5`. Compilation, dependency integrity, roadmap equality and fourteen Node controllers passed. The 161-member v0.340.196 wheel SHA256 is `8e591b1f73f6af2f7e11ee6d6e00016397d7cb842a6c78a41d515e3c3ed91ac1`. Final staged artifact and introduced-line high-confidence secret scans found zero findings. These are local/synthetic checks, not broker, genuine-feed, native Windows, funding, live, cloud or release acceptance.

## v0.340.195 byte-consistent retained-input reads

The installed one-attempt Activity SSE loader now requires two byte-identical bounded reads from the already-open retained-input descriptor in addition to the stable metadata/identity fence. The new regression models a Windows creation-time host where same-inode, same-size substituted bytes have restored modification and creation metadata; v0.340.194 accepted those parsed bytes, while this candidate returns the fixed failure. The complete 293-test acceptance-probe module passed after the fix.

This is a fail-closed implementation improvement, not authorized paper-account, provider-source, received-coverage, sustained-market, native-storage, fee/hold/funds, live/cloud or release acceptance. It changes no Portal control, editable parameter or authority default. The estimate remains about 40% (30–45%, low confidence), with 0/9 major operational stages accepted.

Final local validation passed 5,394 tests with one expected non-Windows skip and one existing Starlette/httpx warning; the finalized Activity SSE/release/evidence/version/roadmap focus passed 453 tests. All sixteen bounded offline profiles and saved-evidence verification match 401 source files / `c4e393b5082d8fbaa2e8b1bd54e889f3d36d63c67583e626abfb4424dd0a926e`; `validation/release_trials_0_340_195.json` SHA256 is `6e619022b8f460c6a2c317a042893d0c40cf7e84fe8378610a89cdf31274f019`. Compilation, dependency integrity, generated-roadmap equality and all fourteen Node controllers passed. The 161-member wheel reports v0.340.195 and has SHA256 `66ad255015106353de5a4bea3f3a2893f658b45b4d45d8134e9fc28c717141be`. The final staged 914-path / 80,278,463-byte public-artifact audit found zero findings; local Gitleaks is unavailable, while exact-source v0.340.194 hosted security passed. These are local/synthetic checks, not actual broker, genuine-feed, native Windows, funding, live, cloud or release acceptance.

## v0.340.194 retained-input descriptor stability

The installed one-attempt Activity SSE operator now refuses a same-inode, same-size retained-input mutation restored between its outer fingerprint checks. File type, link count, identity, size, modification metadata and creation metadata must remain stable across the path and open descriptor samples, with separate same-handle and same-path creation-time comparisons for Windows portability. The regression first failed against the prior implementation and the full 292-test acceptance-probe module passed after the fix.

This is a fail-closed implementation improvement, not authorized paper-account, provider-source, received-coverage, sustained-market, native-storage, fee/hold/funds, live/cloud or release acceptance. It changes no Portal control, editable parameter or authority default. The estimate remains about 40% (30–45%, low confidence), with 0/9 major operational stages accepted.

Final local validation passed 5,393 tests with one expected non-Windows skip and one existing Starlette/httpx warning; the finalized Activity SSE/release/evidence/version/roadmap focus passed 435 tests. All sixteen bounded offline profiles and saved-evidence verification match 401 source files / `226ece33d61f63d6e5b0bc417575c6a3f2b9158420d05c0ad0df2ecee9b91fe0`; `validation/release_trials_0_340_194.json` SHA256 is `85b9a1a40d4dbd4466ce57938cbdc6c6d22094f01582ae23ea830aa690471f76`. Compilation, dependency integrity, generated-roadmap equality and all fourteen Node controllers passed. The 161-member wheel reports v0.340.194 and has SHA256 `2ccec439a51e9dc9fb62fd81caf298166996cca5517d6f6454d1e6e8439c0380`. The final staged 913-path / 80,078,300-byte public-artifact audit found zero findings; local Gitleaks is unavailable, while exact-source v0.340.193 hosted security passed. These are local/synthetic checks, not actual broker, genuine-feed, native Windows, funding, live, cloud or release acceptance.

## v0.340.193 security review hardening

The exact historical review is now atomic: a result containing either reviewed fixture coordinate must contain both. A one-sided result is explicitly incomplete and cannot turn green; a complete reviewed pair plus a new finding remains blocking. This closes a fail-open report-shape edge without changing the scanner, excluding history or broadening the two byte-verified historical matches. Exact-source v0.340.192 security passed; its research and Windows workflows were not both complete at recovery, so no all-green candidate claim is made.

Final local validation passed 5,392 tests with one expected non-Windows skip and one existing Starlette/httpx warning. All sixteen bounded offline profiles and saved-evidence verification match 401 source files / `0d3e227fc5115ca7e2f7b81a74fa1f6659926089ce22550b763330e6a3a5cdb2`; `validation/release_trials_0_340_193.json` SHA256 is `d4225a4e4c886d17d0dcd305152b34bb3423b56dfac34aea21c132413ca792e4`. Compilation, dependency integrity, deterministic roadmap, Portal/controller syntax and the 132-test release-trial/evidence verification passed. The 161-member wheel reports v0.340.193 and has SHA256 `24c52a27617a3cea1b3b90194d464c4c680a53f19ab31ed99fe3860d2192805c`. The current 907-path / 79,462,154-byte public-artifact scan found zero findings. These are local/synthetic checks, not actual broker, genuine-feed, native Windows, funding, live, cloud or release acceptance.

## Development completed in the prior cycle

v0.340.192 adds a separately trusted review after the unchanged full-history Gitleaks scan. Only two exact findings in one immutable, byte-verified historical synthetic fixture qualify. Commit, file, rule, fingerprint and exact line/column coordinates must match. New or altered findings remain blocking; missing proof, duplicate/malformed reports and scanner failures fail closed. The raw finding count remains visible: two raw findings, two reviewed synthetic findings, zero blocking findings only after source proof. No history is rewritten, no detector or fixture directory is excluded, and raw secret/match values are not logged or uploaded.

A real-scanner negative control placed the identical fixture in a new disposable Git commit; both findings remained blocking (two raw, zero reviewed, two blocking). Local v0.340.192 validation passed **5,386 tests** with one expected platform skip, **all 16 offline release profiles**, the 119-test focused review, compilation, dependency checks and all 14 controllers. The current snapshot passed artifact/secret scans with zero findings; historical results retain the explicit two reviewed synthetic findings. This addresses a recurring CI blocker without changing runtime trading permissions. Fresh exact-source hosted security, Windows and research checks must confirm the candidate. Local candidate validation is recorded in AGENTS.md, VERSIONING.md and the new versioned release-trial report; this document does not pre-approve those hosted results.

## Next work and owner contribution

1. Confirm fresh exact-source hosted security/Windows/research results and resolve any remaining failure.
2. Run the ordinary-user Windows checklist in OWNER_PAPER_ACCEPTANCE.md. Record the build, Windows version, fresh install/upgrade and sanitized behavior; keep raw data, paths, account IDs and keys private.
3. Use an authorized paper account and reviewed local credential setup for supervised read/recovery/stream/submission acceptance. Never paste credentials into chat or public GitHub.
4. Run sustained genuine public-feed paper observations and independent reconciliation/strategy review. Keep the computer/app on for local operation; computer-off cloud is unfinished.
5. Implement and accept funding, live and cloud workflows before the full agreed 1.0 gate.

Coding can continue without owner setup. Local virtual-paper testing needs no bank deposit or paid AI account. The detailed source-controlled remaining checklist is in ROADMAP.md and the existing Portal roadmap view; all 28 acceptance steps remain review-required until separately evidenced.
