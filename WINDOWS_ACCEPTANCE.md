# Windows distribution and acceptance evidence

## v0.181.0 candidate status

v0.181.0 adds a replay-bound replacement generation record and v2 candidate/install manifests while retaining v1 candidate, recovery and interrupted-install marker compatibility. The packaged-tools harness retains a 30-second ordinary child bound and permits a bounded 60 seconds only for the expensive encrypted-evidence self-tests (observation crypto and sealed quote evidence); application crypto and runtime behavior are unchanged. The preceding v0.180.0 head `a5c10881429f946621f6bb4d93b62781afb75593` passed public security (39426790639), but its safety run (39426790798) failed regression and release evidence while browser help passed, and Windows run 39426790658 failed install/test before packaging. Public annotations expose only exit code 1, so this local timeout correction is not proof that every remote failure is resolved. Local successive-rotation tests do not establish native Windows power-loss durability, private-volume/profile ACL behavior or sustained multi-epoch pressure. Exact-source Windows regression, standalone/installed command smoke, EXE startup and installer lifecycle remain required.

Local v0.181.0 validation passed 3,426 tests before one collection-time roadmap mismatch caused by regenerating the generated roadmap after import; the frozen-tree roadmap rerun passed 7/7, the final focused set passed 75 tests, and all sixteen saved-evidence profiles matched 347 source files / `d3a47f7e2a034672643ba121afd26c51169d140a756e3ec0d16fa122c030beb4`. This is Linux/local evidence with `native_windows_acceptance: false`, not an uninterrupted green complete-suite claim or a substitute for the exact-source Windows run.

## v0.180.0 candidate status

Exact v0.179.0 commit `1e3cc62a031470b874bc9c4301e9f4dcd745b240` passed Windows 39415934810, all safety/browser/evidence jobs in 39415934794 and security 39415934750. v0.180.0 extends the installed order-offload smoke through verified halted candidate installation, retained recovery-copy verification and explicit rollback. Fresh exact-source Windows regression, standalone/installed command smoke, EXE startup, installer, install/reinstall/uninstall and evidence retention are required. The local marker/recovery fixtures do not establish Windows power-loss boundaries, ordinary-user private-volume/ACL or actual broker/market acceptance.

Local v0.180.0 validation passed 3,416 Python tests, the 173-test focused set, all sixteen source-fingerprinted profiles, saved-evidence verification, fourteen Node controllers, Portal syntax, compilation, dependency consistency and roadmap equality. This is Linux/local evidence with `native_windows_acceptance: false`; it cannot replace the exact-source Windows run.

## Verified installer checkpoint — v0.172.3 (2026-10-19 UTC)

[Download the Windows test installer](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39269412789/artifacts/12196145555) (ZIP containing the setup EXE; expires 2026-10-26T14:49:40Z). The [distribution bundle](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39269412789/artifacts/12196145544) includes source-bound checksums, standalone EXE and offline evidence; expires 2026-11-18T14:49:36Z. GitHub may require sign-in.

Verified source: `a16b2178dc1cb22e5ab177a4e99416edb4bd90d3`. Windows39269412789, split safety/browser39269412866 and security39269412781 all report success for that exact source. Installer evidence12194874557 expires 2026-11-18T14:49:39Z; standalone12194334570 expires 2026-11-18T14:45:06Z. All artifact metadata was rechecked as unexpired during v0.174 development.

This test checkpoint includes the independent packaged emergency command, held-writer/CPU-pressure acceptance and separately budgeted safety gates. It excludes v0.172.4+ native read-only-storage fault and origin-OS changes, v0.173 normalized provider/result receipts and the v0.174 provider-results command; those require their own exact-source CI. New receipt profiles can be unreadable to older binaries. Preserve private backups and halt trading before reviewing an upgrade or downgrade. Automated installer/browser checks do not establish ordinary-owner profile/ACL, actual broker-paper, sustained-market, funds/live/cloud or production acceptance. This is an experimental paper-only installer, not a 1.0 release.

## Previous verified installer checkpoint — v0.166.3 (2026-10-19 UTC)

[Download the Windows installer artifact](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39189832242/artifacts/12160041702) (ZIP containing the setup EXE; expires 2026-10-26T02:42:39Z). The [distribution bundle](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39189832242/artifacts/12159802457) includes source-bound checksums, standalone EXE and offline evidence; expires 2026-11-18T02:42:35Z. GitHub may require sign-in to download.

Verified source: `79068059513052a09f0d338f3507f9482f7b03b7`. Windows39189832242, safety/browser39189832249 and security39189832236 completed successfully. Installer evidence artifact12159677711 expires 2026-11-18T02:42:38Z; standalone artifact12160231173 expires 2026-11-18T02:37:43Z.

This checkpoint includes typed activity-purpose acceptance, original-lease completion, exact nanosecond original-order snapshot ordering and every earlier safeguard. It excludes v0.167.0 through v0.169.0 receipt and consumer checks, which require their own exact-source native checks. This older binary may refuse new v3 pacing rows; preserve private backups and review version compatibility. Automated disposable installer/browser acceptance does not close ordinary-owner profile/ACL, native-window, actual broker, sustained-market or release gates. Keep trading halted during upgrade review and preserve private data/backups.


## Previous verified installer checkpoint — v0.166 (2026-10-19 UTC)

[Download the Windows installer artifact](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39182505029/artifacts/12156813547) (ZIP containing the setup EXE; expires 2026-10-26T01:11:27Z). The [distribution bundle](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39182505029/artifacts/12157391527) expires 2026-11-18T01:11:23Z.

Verified source: `732cd7db2aad56ec38576f675bcd660a7caf0736`. Windows39182505029, safety/browser39182504957 and security39182504988 completed successfully. This binary excludes the v0.166.1 through v0.166.3 changes.


## Previous verified installer checkpoint — v0.165.3 (2026-10-19 UTC)

[Download the Windows installer artifact](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39180194396/artifacts/12156108702) (ZIP containing the setup EXE; expires 2026-10-26T00:38:34Z). The [distribution bundle](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39180194396/artifacts/12156173370) includes source-bound checksums, standalone EXE and offline evidence; expires 2026-11-18T00:38:30Z. GitHub may require sign-in to download.

Verified source: `3036e83d15fffa2d09ce973d5f0be0e7450cfad9`. Windows39180194396, safety/browser39180194482 and security39180194394 completed successfully. Exact-source artifact metadata is unexpired and matches that commit. Installer evidence artifact12156272764 expires 2026-11-18T00:38:33Z; standalone artifact12156063336 expires 2026-11-18T00:33:59Z.

This checkpoint includes the shared path safeguards, retained admission samples, supplied projection precision refusal and scoped Windows denial-harness fix through v0.165.3. It excludes v0.166 exact snapshot ordering and the v0.166.1 PORTAL contract correction. Automated disposable installer/browser acceptance does not close ordinary-owner profile/ACL, native-window, actual broker, sustained-market or release gates. Keep trading halted during upgrade review and preserve private data/backups.


## Previous verified installer checkpoint — v0.164 (CI completed 2026-10-19 UTC)

[Download the Windows installer artifact](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39177466868/artifacts/12155865656) (ZIP containing the setup EXE; artifact expires 2026-10-26T00:05:42Z). The longer-retained [distribution bundle](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39177466868/artifacts/12155481695) includes the standalone EXE, source-bound checksum manifest and offline report; expires 2026-11-18T00:05:37Z. GitHub may require sign-in to download Actions artifacts.

Verified source: `458f2a212b2246c1991d172e2915ecf6564d86c5`. Windows run39177466868, safety/browser run39177466863 and security run39177466881 completed successfully. The Windows workflow passed standalone private-tool checks, EXE startup, installer creation, installed/reinstalled execution, preserved-data uninstall and distribution evidence/checksums. Installer acceptance evidence artifact12155516670 expires 2026-11-18T00:05:41Z; standalone artifact12155193315 expires 2026-11-18T00:01:00Z. Artifact metadata matches this exact source commit and is not expired at this check.

This checkpoint includes eight paper tools, encrypted full-chain quote evidence, the related-path quote-capture fix and declared read-purpose sequences. It does not include v0.165 admission samples, v0.165.1 precision fixes or v0.165.2 shared path preflight. It is available for desktop/install testing; newer diagnostic changes require their own exact-source native acceptance. Keep trading halted during upgrade review and preserve private data/backups. Automated disposable Windows/browser acceptance does not close ordinary-user profile/ACL, native-window, actual broker, sustained-market or production release gates.



## Previous verified installer checkpoint — v0.160 (2026-10-18)

[Download the Windows installer artifact](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39174322289/artifacts/12154332632) (ZIP containing the setup EXE; artifact expires2026-10-25T23:18:00Z). The longer-retained [distribution bundle](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39174322289/artifacts/12154745518) also includes the standalone EXE, source-bound checksum manifest and offline report; expires2026-11-17T23:17:56Z. GitHub may require your sign-in to download Actions artifacts.

Verified source: `26c8cdd36f91a4e467f1caa09e21c7b775655011`. Windows build39174322289, safety/browser39174322293 and security39174322314 all completed successfully. The Windows job passed standalone private-tool checks, EXE startup, installer creation, installed/reinstalled execution, preserved-data uninstall and distribution evidence/checksums. This is disposable automated acceptance, not an ordinary-owner-profile/ACL, native-window, actual broker or production signoff.

This checkpoint supports the six paper tools through v0.160. New quote-capture requires v0.162+; encrypted full-chain quote-evidence requires v0.163+. Source development and the newest unverified installer must not be presented as this tested checkpoint. Keep installed trading halted while reviewing upgrades and preserve private data/backups.

The `Windows simulated-research build` workflow builds a standalone EXE and Inno installer. The `NexusOperative-Windows-Distribution` artifact contains both plus `artifact-manifest.json`: source commit, development version, byte lengths and SHA256 checksums. Verify the extracted distribution in the source Python environment:

```powershell
python -m nexus.artifact_manifest verify artifact-manifest.json .
```

This checks file integrity against that manifest. It is not code signing, a malware assessment, independent source authentication or release authorization. Keep the manifest and source commit in the review record; downloading an altered manifest with altered binaries defeats this check.

## Automated coverage

CI uses a disposable install directory and explicit disposable data directory. It checks silent installation, installed backend version, bundled PORTAL, default paused/stopped state, no broker/live authority, a durable emergency halt, same-version reinstall, recovery of that halt, and silent uninstall. It compares the data journal before/after reinstall and uninstall and preserves a separate sentinel. It terminates the entire onefile process tree between launches. Installer logs, startup logs and result JSON are uploaded as `NexusOperative-Installer-Smoke-Evidence`, including available logs on failure.

This is a Windows CI runner test using browser mode and a data-path override. It does not verify the native WebView window, default user data location, cross-version migrations, ordinary-user installer interaction or every clean-machine configuration. Passing status must come from the workflow for the exact source commit; source-process Linux smoke results do not establish installed Windows behavior.

## Manual acceptance still required before 1.0

Record machine/Windows version, account privileges, exact artifact digest/source commit, test time, steps, expected/actual results and reviewer for each case:

| Case | Required evidence |
| --- | --- |
| Fresh ordinary-user install | Default per-user install succeeds without elevation; shortcuts launch native PORTAL; application version matches manifest. |
| Data location and permissions | Default data lives outside the install directory, remains writable by the intended user, and is not bundled with artifacts. |
| Cross-version upgrade | On a backup copy of a prior supported version's paper data, upgrade retains IDs, priced fills, accounting, policy evidence and halt; reconciliation passes and forward work remains stopped. |
| Halt/process interruption | Halt survives full process termination and restart; duplicate delivery does not duplicate fills; reconciliation errors block operation. |
| Uninstall/reinstall | Uninstall removes executables/shortcuts, preserves user data, and reinstall recovers that data without resuming forward work. |
| Recovery from backup | With application closed, restore a complete data-directory copy, check journal/ledger and quote evidence; retain original backup unchanged. |
| Native window dependencies | Test available/missing WebView2 behavior and browser fallback; verify visible PORTAL and controls. |

Do not run destructive acceptance steps on the only copy of user data. Do not infer successful migration from a same-version reinstall. M1 stays partial until the missing acceptance evidence is recorded and reviewed. See FORWARD_RUNBOOK.md for paper-session evidence and ROADMAP.md for the full release gates.

Offline backup and new-directory restore commands are documented in FORWARD_RUNBOOK.md (`python -m nexus.recovery_bundle`). They require Nexus to be closed and do not select a runtime data path automatically.

Both v0.62 Windows workflows passed for commit `35239e562c72078a0d7ea32e240518c35a57e247`, including installed startup, same-version reinstall, halt recovery, data-preserving uninstall and checksum generation. This closes that automated component; manual native-window/default-path and cross-version acceptance remains open.

## Restored data through the installed EXE

The installer workflow now creates a disposable linked paper fixture with injected BUY/SELL quotes, a halted/archived account and retained session evidence. It makes a verified offline backup, restores to a new directory and launches the installed executable against that copy. The smoke reads the installed API to compare exact account and archived order fingerprints, quote-chain head/count, policy fingerprint, modeled BUY/SELL counts and stopped/halted state. It verifies the retained backup again afterward. Fixture baseline, process logs and recovery results are included with installer evidence.

`python tools/packaged_recovery_smoke.py --exe <installed-exe> --version <expected-version> --evidence <new-evidence-folder>` runs this same disposable exercise. It uses source Python to create and verify the fixture, and the selected executable to serve restored data. Only CI results for the exact commit establish Windows packaged behavior. A source launcher can exercise the HTTP harness locally but cannot establish PyInstaller/installer correctness. This remains same-source recovery, not cross-version migration, native-window or real-feed acceptance.

## Pinned prior-version data recovery

Windows CI additionally checks out reviewed v0.64 commit `09bb7280cd2e654018d491db52e19660b2b8e180` into a separate source directory. A child Python process uses that source to create its own linked/archived paper fixture. The helper verifies the checkout's exact HEAD and rejects tracked changes. It records prior version and source commit in the baseline, backs up/restores a copy, then checks it through the new installed EXE's API using the same exact account/order/quote/policy comparisons. The original backup is reverified. Results live under the installer evidence artifact's `cross-version` directory.

This checks migration of one pinned prior source's generated data. It does not install the old Windows version or test replacing an old installation, default paths, native-window behavior or every historical schema. It uses current installed Python dependencies for fixture creation, not a reconstruction of the old runtime environment. Those broader acceptance cases remain open. Passing evidence must be from the new workflow's exact commit.

The source helper accepts `--fixture-source <reviewed-checkout>` and `--fixture-commit <exact-sha>` together. The fixture version must precede the target. Use reviewed local source only; this option executes its fixture code in a child process with a disposable data directory. No broker or public feed is used.

Push builds now check out and assert the triggering commit SHA rather than the moving branch tip. For older queued workflows, consult the distribution manifest source commit before attributing a packaged result to the workflow event commit.

Exact-source component evidence: [v0.70 Windows run](https://github.com/S4-cyber/Nexus-Operative/actions/runs/38995983189) for commit `8fa247aa2236fa97bf440dfc93a8e9d40cc43d21` passed triggering-SHA binding, installed application/reinstall/uninstall (including same-source and pinned v0.64 data recovery), distribution checksum creation and distribution upload. At this review the remaining evidence upload/job cleanup was still running. This closes those automated components for that source; it does not establish manual native-window, default-path or prior-installer replacement acceptance.

## Default Windows data-layout smoke

The installed smoke can now omit `NEXUS_PORTABLE_DATA` and use a disposable child-only `LOCALAPPDATA` root. CI launches the installed EXE twice in this mode, requires it to create `NexusOperative/storage/order_lifecycle.sqlite3`, checks halt recovery on restart, and confirms uninstall preserves that journal. It rejects accidental storage under the installation directory. Inherited audit/reserve/strategy-memory path overrides are removed from smoke child environments so the test cannot write to unrelated data paths.

Use `python tools/installed_smoke.py --exe <installed-exe> --default-data-root <disposable-appdata-root> --version <version> --output <report.json>` on Windows. This mode tests the frozen Windows path-selection rule using an isolated AppData root; it does not replace ordinary-user ACL, actual profile, native-window or clean-machine acceptance. Test output records which layout was used. The existing `--data` option continues to test explicit portable paths, and the two options are mutually exclusive.

Default-layout automated evidence: both workflows for commit `ad59507feafec4db2489fd05bc3f91cd9ee4c9a2` completed successfully, including child-only default AppData layout, restart halt recovery and uninstall preservation. Ordinary-user profile/ACL, native-window and clean-machine prior-installer replacement acceptance remain open.

## v0.174.1 provider-result manifest identity portability

Exact v0.172.5 security run 39283670255, Windows run 39283670294 and safety/browser/evidence run 39283670478 passed. Exact v0.174.0 security 39286221444 and safety/browser/evidence 39286221429 also passed. Its Windows run 39286221494 deterministically failed eleven provider-result manifest cases after 3,348 passes and 19 skips, before packaging: Windows path and descriptor stat calls can expose different `ctime` representations immediately after a file rewrite, so the reader rejected the manifest before the intended consistency/path-alias assertions.

v0.174.1 uses `os.path.samestat` for platform file identity, binds the initial and final path samples by identity and size to the opened descriptor, and requires descriptor size/mtime stability through the read. Regular-file, single-link, bounded-size, no-follow where available, exact read length and the second full manifest read remain. A regression injects path/descriptor `ctime` disagreement for the same file. Only a green exact-source Windows run can accept this repair; local Linux tests cannot establish installed EXE/installer behavior, ordinary-user profile/ACL, native rendering or clean-machine acceptance.

Local source validation passed 3,379 tests plus the 228-test focused release set and all sixteen saved-evidence profiles. That evidence has `native_windows_acceptance: false`; it is not an installed Windows result. Exact-source v0.174.1 packaging and installed smoke remain the next acceptance gate.

## v0.174.2 pacing-gate diagnostics and regression budget

Exact v0.174.1 Windows run 39292524827 reached 3,359 passes and 19 skips but failed one aggregate typed-read release invariant; all packaging and installed-smoke steps were skipped. Security 39292524872 passed. Browser/help and release-evidence jobs in 39292524821 passed, while the independent full regression exhausted its 25-minute limit. Exact-source failed-job reruns were requested and remain diagnostic only.

The release validator now maps each fixed typed-read boolean to a fixed content-free failure code, preserving the complete invariant set and suppressing all values/private evidence. The full-regression job gets a 35-minute independent budget; the other separately bounded jobs are unchanged. Exact-source v0.174.2 must pass regression, packaging and installed smoke before this component is accepted. Ordinary-user profile/ACL, clean-machine, provider and market acceptance remain independent.

Local v0.174.2 validation passed 3,379 Python tests, the 232-test focused set, all sixteen source-fingerprinted profiles, saved-evidence verification, fourteen Node controller smokes, Portal syntax, compileall, dependency and roadmap checks. The report matches 345 source files / `8c0fc047d966530bdf2270e36b340a806755534dbdd31a1ded6109a45d6b9e50` and has SHA256 `c6a1be0a4e5986af9ba5ddf1dd8b860a485ac0374a52515d0c81c6b31b7cea00`. The 478-path proposed snapshot passed public-artifact audit. This remains non-Windows evidence with `native_windows_acceptance: false`.


## v0.135 packaged setup and saved-choice acceptance

The installed-process smoke tool now offers explicit disposable `--setup-check first-run` and `--setup-check saved` profiles. First-run requires absent settings/accounting before launching, checks the native API’s $10,000/50-35-15/Terrestrial-only preset and every checkbox purpose, deliberately saves a $2,500 virtual budget and turns Terrestrial OFF, then confirms reload without any engine/order/provider start. Saved mode is read-only for configuration and verifies those exact fixture choices on the next process. Keep these fixed test modes restricted to separate disposable data directories, never retained recovery fixtures or personal data. Existing production UI and execution policy are unchanged.

Windows installer smoke now runs these profiles for both the explicit portable directory and default LOCALAPPDATA layout. It checks the settings database SHA-256 before/after same-version reinstall and before/after uninstall, in addition to existing order-journal/sentinel preservation. Record booleans only after the underlying assertions pass. Prior-version data recovery, native-window rendering, clean ordinary-user ACL/profile and full cross-version installer acceptance remain independent. A local source-process shim passing these checks is not evidence that the Windows EXE or PowerShell installer treatment ran here. Negative tests reject wrong budgets/tiers, empty or missing-purpose checklists, automatic start, promoted funds and reselection of an OFF switch; saved mode must perform no PUT.

Verified preceding checkpoint: v0.134 exact source84542b862aae5c840d46d4fd2d5c336b091ee61e passed Windows39141177457, safety/browser39141177351 and security39141177346. Installer12142365471 expires2026-10-25T15:44:30Z: https://github.com/S4-cyber/Nexus-Operative/actions/runs/39141177457/artifacts/12142365471 . Extract the downloaded ZIP and run NexusOperative-Setup-0.134.0.exe. Distribution12141991743 expires2026-11-17T15:44:27Z. This checkpoint predates the0.135 smoke extensions. All actual broker, sustained-market, funds, live, cloud, independent review and owner1.0 gates remain open.

## Denied-origin loopback close behavior

Exact v0.163.1 Windows build39176820299 passed, while safety/browser39176820200 reached 2,712 passing tests and failed one real-loopback test when Windows raised `WinError10054` after the server had already returned a403/no-store foreign-origin denial. Exact v0.164 safety39177466863 later passed the same test. v0.165.3 scopes reset tolerance to draining the unused response body after that precise pre-credential denial; success and every credential-bearing or other response still require a complete body. Exact-source Windows/safety CI must pass before this harness correction is accepted. This is CI portability, not native-profile, provider or release acceptance.
## v0.175.0 candidate status

Exact v0.174.2 commit `69631229444cd141889c25508affadcaea6746c6` passed Windows run 39301830393, safety/browser/evidence run 39301830361 and public-security run 39301830418. The Windows job passed regression, Portal syntax, standalone packaging, private paper-tool smoke, EXE startup, installer creation, install/reinstall/default-layout/cross-version recovery, data-preserving uninstall and artifact/checksum steps.

v0.175.0 adds retained local request-event binding to private execution-quality review. Local source validation passed 3,386 Python tests, the 185-test focused set, sixteen source-fingerprinted offline profiles, saved-evidence verification, fourteen Node controllers, Portal syntax, compile/dependency and roadmap checks. This local evidence has `native_windows_acceptance: false`; exact-source v0.175.0 Windows packaging, installed tool smoke, rendered-browser/safety and security workflows remain required. It does not establish ordinary-user profile/ACL, native-window, actual provider/account, sustained-market, funding/live/cloud or release acceptance.

## v0.175.1 candidate status

Exact v0.175.0 security run 39311553891 and all three safety/browser/evidence jobs in run 39311553832 passed. Windows run 39311553897 reached 3,365 passes and 19 skips but failed one emergency race assertion and one 35-second aggregate child timeout, so Portal syntax, standalone EXE, installer and installed-smoke steps were skipped. This is not a native packaging pass.

v0.175.1 allows only the two exact fail-closed decision/reason pairs possible at the independent cut-latch/stopped-runner boundary, while retaining every durable no-fill and reconciliation assertion. It expands only the full loopback-plus-tool child process budget to 120 seconds; individual operations and evidence checks remain bounded and unchanged. Exact-source v0.175.1 Windows CI must pass regression, packaging, private tool smoke, startup, install/reinstall/default-layout/cross-version recovery and data-preserving uninstall before this candidate gains automated native evidence.

Local v0.175.1 source validation passed 3,386 tests plus the 124-test focused set and all sixteen source-fingerprinted profiles. This has `native_windows_acceptance: false` and cannot replace the required exact-source Windows run.

## v0.176.0 candidate status

Exact v0.175.1 commit `fd42efa0ee296a1cc9f0abfa0f1b9e6eba5133b6` passed security run 39315209592, all three safety/browser/evidence jobs in run 39315209583 and Windows run 39315209635. The Windows run passed regression, Portal syntax, standalone packaging, private tool smoke, startup, installer creation, install/reinstall/default-layout and pinned recovery checks, data-preserving uninstall, checksums and evidence uploads.

v0.176.0 changes the read-only Paper Trial Readiness DTO and existing Portal rendering to include session and durable order-command/ID capacity. Its conservative full-window bound is not an expected trade count or permission. Exact-source v0.176.0 Windows packaging/installed smoke, rendered-browser/help, release-evidence and security workflows remain mandatory; ordinary-user profile/ACL, clean native window, sustained real feeds, physical long-run offload and actual broker-paper acceptance remain separate.

Local v0.176.0 validation passed 3,387 tests, the 89-test focused set, all sixteen source-fingerprinted profiles, saved-evidence verification, fourteen Node controllers, Portal syntax, compileall and dependency consistency. This is Linux/local evidence with `native_windows_acceptance: false`; it cannot replace the exact-source Windows run.

## v0.177.0 candidate status

Exact v0.176.0 commit `6f6eeaa44439d49e697597397c3678b409f1a974` passed public-security run 39327666704, all three safety/browser/evidence jobs in run 39327666689 and Windows/installer run 39327666522. That Windows job covered the prior standalone/installed tool catalogue and recovery contract.

v0.177.0 adds `--paper-tool order-offload create|verify`. The common packaging smoke now builds a disposable stopped, flat, reconciled and logically archived order/portfolio pair, invokes create and verify through the source/standalone/installed command prefix, requires full replay/hash/integrity agreement and verifies that source history remains unchanged with no capacity or authority claim. Exact-source v0.177.0 must pass native regression, Portal syntax, standalone packaging, ten-tool private smoke, startup, installer creation, install/reinstall/default-layout/pinned recovery, data-preserving uninstall, checksums and evidence upload before this installed path is accepted.

Local source success cannot establish private-volume ACLs, ordinary-user profile behavior, atomic publication under native faults, multi-epoch compaction/rotation or long-run storage acceptance. No Portal control, provider/account access, order route, funding/live/cloud operation or release authority is added.

Local v0.177.0 validation passed 3,398 tests, the 125-test focused set, all sixteen source-fingerprinted profiles, saved-evidence verification, fourteen Node controllers, Portal syntax, compileall, dependency consistency and roadmap equality. This evidence has `native_windows_acceptance: false`; the exact-source Windows workflow remains the acceptance gate for the packaged command.

## v0.177.2 candidate status

Exact v0.177.1 public-security run 39385781709 and safety/browser/evidence run 39385781778 passed. Windows run 39385781816 reached 3,379 passes and 19 skips, then failed `test_concurrent_instances_never_over_admit` before packaging: 30 simultaneous budget clients produced 8 durable admissions rather than consuming all 12 available slots. The safety bound held, but writer lock timeout failures caused conservative under-admission.

v0.177.2 retries only SQLite BUSY/LOCKED outcomes for a bounded 30-second monotonic window, closing each attempt. Continued contention remains fail closed under a fixed code, while exhaustion and all non-lock failures are never retried. The native contract now distinguishes the 12 expected successes from 18 exact quota denials. No UI, request cap, provider connection, account, execution or release authority changed. Exact-source Windows regression and the complete packaging/installer lifecycle remain mandatory.

Exact v0.177.2 subsequently passed public-security run 39393111840, all three safety/browser/evidence jobs in run 39393111890 and Windows packaging/installer run 39393111795.

## v0.178.1 candidate status

Exact v0.178.0 security run 39401619194 and every safety/browser/evidence job in run 39401619134 passed. Windows run 39401619265 reached 3,386 passes and 19 skips but stopped in `Install and test` with `RELEASE_TRIAL_TYPED_READ_OPERATIONS_ENVELOPE`; no EXE or installer acceptance can be claimed from that run. The same exact-source typed fixture passed in the separately budgeted regression and release-evidence jobs.

v0.178.1 checks the fixed typed-reader source envelope first, all 25 allowlisted invariants second, and the derived summary last. An intermittent false invariant now produces its precise fixed diagnostic without private values or paths. No retry, acceptance relaxation, runtime change, UI change, authority or broker access was added. Exact-source Windows regression, EXE/startup, installed command smoke, install/reinstall/uninstall and retained evidence are required.

Local validation passed 3,407 tests, the 189-test focused set, all sixteen source-bound profiles and saved-evidence verification. This is not a native Windows pass; exact-source workflow execution remains required.

## v0.178.0 candidate status

The installed `order-offload rotation-plan` command now verifies that a private offline copy still exactly matches the explicitly application-closed live order journal and linked portfolio, then rechecks both sides. The Windows source/standalone/installed operator smoke invokes it after create and verify, bringing the cross-platform command-prefix count to 21 plus the native read-only-journal emergency fault.

The new command writes nothing and cannot compact, delete, reclaim capacity, resume, contact a provider or grant authority. Exact-source Windows regression, EXE/startup, installed command, installer lifecycle and retained evidence are required before this candidate is accepted. Ordinary-user private-volume/profile/ACL and capacity-reclaiming multi-epoch recovery remain separate open gates.

## v0.177.1 candidate status

Exact v0.177.0 security run 39335584198 and the browser-help job in safety run 39335584233 passed, but that run's release-evidence job failed in the operations child. The offload integrity pass used SQLite connection context managers, which finalize transactions but do not close connections. Linux permitted the staged files to be moved and removed with those handles open; Windows correctly retained file locks. v0.177.1 explicitly closes both read-only integrity handles before replay, publication and cleanup, and adds a platform-neutral regression that blocks rename while any tracked integrity handle remains open.

No order, portfolio, permission, risk, UI, provider, account, funding, live, cloud or release behavior changes. Exact-source v0.177.1 regression, release evidence, standalone/installed command smoke, packaging, install/reinstall/uninstall and security remain mandatory.

Local v0.177.1 validation passed 3,399 tests, the 152-test focused set and all sixteen source-fingerprinted profiles. A direct descriptor audit found zero leaked offload handles, but this Linux result is not native Windows acceptance; the exact-source workflows remain mandatory.
