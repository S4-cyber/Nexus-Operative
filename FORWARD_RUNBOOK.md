# Experimental paper forward operation and evidence review

This runbook applies to the BTC-only fixed momentum experiment. It is unvalidated paper research. No external broker is connected. No session resumes automatically. The full roadmap remains in ROADMAP.md and GET /api/roadmap.

## Before starting

Record the source commit, application version, Windows build artifact and machine/time configuration. Review GET /api/execution/recovery and the PORTAL execution capacity diagnostics. Resolve any reconciliation, storage or recovery error first. Preserve the portable data directory. Never combine an existing traded account with execution accounting automatically.

Choose an engaged Terrestrial, Solar or Universal tier and review its limits. Activate PORTAL, then explicitly start the experimental paper runner using its confirmation control. Starting may explicitly link compatible unused paper accounting. The independent observer does not start the runner. Record the session ID and policy fingerprint. The fixed policy, costs and liquidity assumptions must remain unchanged during the session.

## During operation

Check forward status, session heartbeats, quote freshness, capacity and order reconciliation. An outage or long gap clears entry warmup; repeated feed failures pause operation. Pause/halt stops the runner. Tier disengagement prevents new entries while preserving risk-reducing exits. Stop does not liquidate held paper positions. Do not reset, archive or restart merely to hide a failed observation or veto.

For a planned validation run, record its duration and acceptance criteria before starting. Keep an external incident log of outages, restarts and observed behavior. Injected test quotes and scripted operation drills do not satisfy sustained market-feed validation. Single-source public quotes are not independently attested exchange depth or executable liquidity.

## Stop and preserve

Use Stop before review and confirm the worker is stopped. Review Session Evidence and Quote/Fill Evidence in PORTAL. A RUNNING session left behind after a crash requires investigation; no automatic resume is permitted. Session counters are diagnostic; the order journal is authoritative. Investigate every quote-chain, policy, fill or counter disagreement without rewriting history.

Download the portable summary at:

`GET /api/paper/forward/sessions/<session-id>/review.json`

For example, in PowerShell (replace the session ID and PORTAL port if different):

```powershell
Invoke-WebRequest 'http://127.0.0.1:8000/api/paper/forward/sessions/0123456789abcdef/review.json' -OutFile review.json
python -m nexus.forward_review review.json
```

The verifier requires the source Python environment. It uses no network and places no orders. A successful result verifies the summary digest only. It does not replay the source databases, validate strategy performance or approve release. Retain the exact source commit beside the report. The export flags active/interrupted sessions, injected provenance and existing consistency errors; an empty blocker list is not acceptance.

After stopping, exit the application before copying the entire portable data directory, including all SQLite files and any journal/WAL sidecars. A live file-by-file copy is not a consistent backup. Preserve the original data and a separately stored checksum manifest; perform recovery drills on a copy. The summary contains no raw quote stream or complete order journal and cannot replace that backup. Local checksums cannot detect coordinated rewriting of both data and checksum.

## Remaining 1.0 acceptance

Record sustained real-feed runs, independently reviewed policy/cost assumptions, network interruption and restart evidence, independent security/risk review, and clean-machine Windows install/upgrade/uninstall/recovery results. M1–M7 remain partial; disconnected broker-paper integration M8 and release authorization M9 remain open. Alpaca paper connection and 1.0 promotion require their respective explicit authorization. Never infer live-money permission from paper acceptance.

## Verified offline recovery tool (source Python environment)

Close Nexus before these commands. Use a separate backup destination and a new restore directory:

```powershell
python -m nexus.recovery_bundle backup "C:\path\to\storage" "D:\NexusBackup" --confirm-application-closed
python -m nexus.recovery_bundle verify "D:\NexusBackup"
python -m nexus.recovery_bundle restore "D:\NexusBackup" "C:\path\to\recovery-test" --confirm-application-closed
```

The tool retains all ordinary data files recursively, checks copied file hashes and SQLite quick-check results, and detects source changes during copying. It refuses existing destinations, destinations within the source, symlinks, and pending SQLite journal/WAL/SHM sidecars. It stages work and publishes only a verified result. Failed work is removed from the temporary staging directory; original data is not rewritten. Restore never switches the application's configured data path or starts it.

If sidecars remain after a crash, preserve the entire original directory and investigate recovery on a separate complete copy before using this tool. Do not delete sidecars to bypass the check. The offline tool deliberately does not recover or checkpoint pending journals. Confirmation is an operator assertion that Nexus is closed; hash checks cannot prevent another process from starting or prove a globally consistent snapshot while writes occur.

Backups are not encrypted and may contain private settings/evidence. Keep them in an appropriately protected location. Hashes are local integrity checks, not external attestation. After restore, use an isolated configuration targeting the new directory to check halted/stopped state, authoritative order replay, linked-ledger reconciliation and session evidence. Never point a recovery drill at the only original copy. Record the source commit beside the backup.

## Disposable complete recovery drill

Run `python -m nexus.recovery_drill` in the source environment. It creates disposable data in a child process, enables a tier, uses explicitly injected quotes to buy and sell, disengages the tier before the exit, stops, halts and logically archives. It backs up all four fixture databases and restores into a new directory. Another fresh process checks linked accounting, archived session orders, exact order/account fingerprints, policy fingerprint and retained quote chain. The runner must remain stopped and the account halted. The retained backup is reverified afterward; temporary data is removed.

The JSON report records modeled buys/sells and eight injected observations. It is a reproducible recovery exercise, not a sustained public-feed run, a process-kill test, a cross-version upgrade or independent strategy validation. The command does not change the caller's data directory or environment and has no broker connection. Its execution is included in the regression suite.

## One local forward owner per data directory

The runner now holds an OS file lock before session admission, account initialization or quote intake. A competing runner targeting the same order-data directory fails with `PAPER_FORWARD_OWNED_BY_ANOTHER_RUNNER`. Stop and failure pauses release ownership after the active cycle finishes; a blocked fetch retains it until that cycle exits. Process termination releases the OS lock. Status reports `ownership_held` independently from worker/enabled state.

The `paper_forward.owner.lock` file may remain after stop or process death. File existence does not imply ownership and does not grant running permission. Never delete or replace this file while an application is running: that can defeat coordination. Use ordinary local storage; this lock is not distributed coordination for copies, different data directories or network filesystems. It does not prevent independent manual simulation commands or grant broker authority. A restarted application still requires explicit session start and all normal checks.

## Clock discontinuities and exchange timestamp order

Runtime compares wall-clock elapsed time with an independent monotonic clock before observation, after transport, before submission and before fill. Backward clocks, nonfinite samples or a difference exceeding two seconds pause the session, clear entry warmup and release ownership after the cycle finishes. This is a runtime safety guard, separate from the fixed experiment's strategy parameters. A submitted but unfilled order is cancelled if the guard trips before fill. Diagnostic records identify invalid clock samples; their timestamp uses the last valid wall sample when needed.

Repeated regressing exchange timestamps now count toward the existing three-failure limit and clear warmup immediately; their retained quote observations are marked rejected. A normal long elapsed gap with consistent clocks clears warmup without being labeled a clock failure. Duplicate exchange ticks never add entry warmup observations. Clock correction does not automatically restart a paused session; inspect accounting and session evidence before explicitly starting again.

The standard runner uses `time.time` and `time.monotonic`. Deterministic callers injecting a custom wall clock default to that clock for elapsed-time simulation; tests can pass an independent `monotonic_clock` explicitly. This does not synchronize exchange time or independently attest feed timestamps.
