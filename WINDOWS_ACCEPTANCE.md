# Windows distribution and acceptance evidence

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
