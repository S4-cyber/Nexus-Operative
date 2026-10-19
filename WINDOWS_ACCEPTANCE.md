# Windows distribution and acceptance evidence

## Verified installer checkpoint — v0.166.3 (2026-10-19 UTC)

[Download the Windows installer artifact](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39189832242/artifacts/12160041702) (ZIP containing the setup EXE; expires 2026-10-26T02:42:39Z). The [distribution bundle](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39189832242/artifacts/12159802457) includes source-bound checksums, standalone EXE and offline evidence; expires 2026-11-18T02:42:35Z. GitHub may require sign-in to download.

Verified source: `79068059513052a09f0d338f3507f9482f7b03b7`. Windows39189832242, safety/browser39189832249 and security39189832236 completed successfully. Installer evidence artifact12159677711 expires 2026-11-18T02:42:38Z; standalone artifact12160231173 expires 2026-11-18T02:37:43Z.

This checkpoint includes typed activity-purpose acceptance, original-lease completion, exact nanosecond original-order snapshot ordering and every earlier safeguard. It excludes v0.167.0 through v0.168.2 receipt and consumer checks, which require their own exact-source native checks. This older binary may refuse new v3 pacing rows; preserve private backups and review version compatibility. Automated disposable installer/browser acceptance does not close ordinary-owner profile/ACL, native-window, actual broker, sustained-market or release gates. Keep trading halted during upgrade review and preserve private data/backups.


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


## v0.135 packaged setup and saved-choice acceptance

The installed-process smoke tool now offers explicit disposable `--setup-check first-run` and `--setup-check saved` profiles. First-run requires absent settings/accounting before launching, checks the native API’s $10,000/50-35-15/Terrestrial-only preset and every checkbox purpose, deliberately saves a $2,500 virtual budget and turns Terrestrial OFF, then confirms reload without any engine/order/provider start. Saved mode is read-only for configuration and verifies those exact fixture choices on the next process. Keep these fixed test modes restricted to separate disposable data directories, never retained recovery fixtures or personal data. Existing production UI and execution policy are unchanged.

Windows installer smoke now runs these profiles for both the explicit portable directory and default LOCALAPPDATA layout. It checks the settings database SHA-256 before/after same-version reinstall and before/after uninstall, in addition to existing order-journal/sentinel preservation. Record booleans only after the underlying assertions pass. Prior-version data recovery, native-window rendering, clean ordinary-user ACL/profile and full cross-version installer acceptance remain independent. A local source-process shim passing these checks is not evidence that the Windows EXE or PowerShell installer treatment ran here. Negative tests reject wrong budgets/tiers, empty or missing-purpose checklists, automatic start, promoted funds and reselection of an OFF switch; saved mode must perform no PUT.

Verified preceding checkpoint: v0.134 exact source84542b862aae5c840d46d4fd2d5c336b091ee61e passed Windows39141177457, safety/browser39141177351 and security39141177346. Installer12142365471 expires2026-10-25T15:44:30Z: https://github.com/S4-cyber/Nexus-Operative/actions/runs/39141177457/artifacts/12142365471 . Extract the downloaded ZIP and run NexusOperative-Setup-0.134.0.exe. Distribution12141991743 expires2026-11-17T15:44:27Z. This checkpoint predates the0.135 smoke extensions. All actual broker, sustained-market, funds, live, cloud, independent review and owner1.0 gates remain open.

## Denied-origin loopback close behavior

Exact v0.163.1 Windows build39176820299 passed, while safety/browser39176820200 reached 2,712 passing tests and failed one real-loopback test when Windows raised `WinError10054` after the server had already returned a403/no-store foreign-origin denial. Exact v0.164 safety39177466863 later passed the same test. v0.165.3 scopes reset tolerance to draining the unused response body after that precise pre-credential denial; success and every credential-bearing or other response still require a complete body. Exact-source Windows/safety CI must pass before this harness correction is accepted. This is CI portability, not native-profile, provider or release acceptance.
