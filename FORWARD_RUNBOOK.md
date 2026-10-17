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

## Retained storage preflight

PORTAL now displays retained quote/session counts and warnings at the final ten percent of either limit. `GET /api/paper/forward` includes the same read-only `storage_capacity` diagnostics. The current caps are 100,000 observations and 1,000 session summaries. New-session admission checks both before initializing or linking accounting. Existing sessions may continue when the session-count cap is reached; exhausted quote storage pauses before fetching another observation. Append-time checks remain authoritative if another writer changes capacity after preflight.

No history is deleted or archived automatically. Storage availability is one admission check, not execution permission or a guarantee of future exit capacity. The system remains bounded; physical offload and indefinite operation are unvalidated. Stop and back up before limits are reached, review any held paper position and retain all evidence. Do not edit or delete records to bypass a cap.

The Windows installer smoke also exercises the recovered fixture through the installed application API. See WINDOWS_ACCEPTANCE.md for its exact scope and artifacts; it never runs the forward policy in the installed process or connects to a market feed.

## Modeled execution cost consistency

Session evidence now checks each quote-driven fill's price, quantity, fee, assumed available quantity, cost rates, timing and unique positive event sequence. It recomputes price/fee arithmetic, compares fee/slippage rates with the recorded policy and checks summed event notionals/fees against the order's totals. Disagreements appear as `FILL_MODEL_OR_POLICY_DISAGREEMENT`; no evidence is repaired. Existing quote-to-observation checks remain separate.

Reports and portable summaries include exact decimal modeled buy/sell notionals and fees for events that pass these model checks. These are execution-cost diagnostics, not realized strategy returns, independently available liquidity, a market-performance score or release acceptance. The durable ledger remains authoritative. Checks can reveal local inconsistency but cannot attest coordinated rewrites or validate the policy's economic assumptions.

## Damaged diagnostic session records

Before a cycle fetches data, and before a diagnostic write, the runner validates the retained session's timestamps, state, policy, nonnegative integer counters and last event. The stored policy must exactly match the active admitted policy before any write. Invalid data pauses intake; diagnostic writes never overwrite a changed policy to make it look valid.

Stop still fences execution and releases ownership if recording STOP fails. `STOP_DIAGNOSTIC_STORAGE_FAILURE` means the final session record needs review, not that the worker has running permission. Malformed history/session diagnostics return unavailable responses; invalid request limits remain validation errors. Preserve the original data and investigate a copy. Do not edit counters or policy records to suppress the failure. Ledger reconciliation remains independent and authoritative.

## Exact quantities on risk-reducing exits

When the full held position fits the per-order notional cap, the runner now sells its exact reconciled quantity rather than rounding it down to the precision used for new entries. This avoids leaving dust from earlier priced fills with more than 12 decimal places. If the position exceeds the cap, the runner still rounds the capped partial exit down; later qualifying exits can close the remaining exact quantity. Entry tier disengagement continues to permit these exits. Accounting and modeled execution costs remain checked and reconciled.

## Forward process interruption boundary tests

The regression suite now terminates an actual child process immediately before quote fill and immediately after fill commit but before its BUY counter persists. Recovery must reconcile the linked ledger and retained quote chain, release the OS owner and remain stopped. An outstanding order blocks a new session until explicit review/cancellation. Duplicate order submission must not add a command or repeat the fill.

After a committed fill with a missing diagnostic counter, evidence keeps reporting `SESSION_COUNTER_FILL_DISAGREEMENT`; recovery does not rewrite the old record. A new explicitly prepared session may monitor and close that holding with the entry tier off. A flat account with its tier off still cannot enter. These deterministic process-death tests complement checkpoint/archival interruption tests; they do not establish sustained live-feed operation or independent acceptance.

Runtime fill evaluation now refreshes wall time and checks stop/clock continuity inside the write transaction after SQLite lock acquisition and state replay. Lock waits cannot reuse the earlier pre-transaction evaluation time. The ordinary journal stores the sampled timestamp for deterministic recovery; the runtime callback is never serialized. A stale quote veto clears entry warmup and cancels the unfilled order; expiry without fill also clears warmup. Offline callers without a runtime clock retain explicit synthetic timestamps.

## Repeatable disposable forward-runner drill

Run `python -m nexus.forward_drill --rounds 3` from the source checkout. The default is three sessions; `--rounds` accepts 1–20. Each session drives the actual runner through warmup, BUY, duplicate tick, HOLD, entry-tier disengagement, SELL, blocked flat reentry and three injected transport failures ending PAUSED. It retains all sessions, quotes and orders across reconstructed gateways, halts when flat, then compares account/order/evidence fingerprints in a separate fresh Python process. Any inconsistent outcome fails the command.

The tool uses temporary child-process data and changes no caller settings or accounts. Its JSON includes full-cycle local p50/p95/p99 timings, separate from the gateway-only burst benchmark. Synthetic time advances without sleeping, and transports are explicitly injected. This is a bounded repeatable fixture exercise, not a sustained public-feed run, scheduler test, process-kill test or market throughput result. The temporary databases are discarded after verification; the existing backup/recovery drill separately exercises durable backup copies.

## Public endpoint changes and redirects

The standard Coinbase reader now rejects redirects (301/302/303/307/308), including redirects within Coinbase. It checks the final response URL against the exact allowlisted requested product before reading a body. A provider endpoint migration therefore fails closed until the source is reviewed and updated. Unexpected URLs, non-200 responses, oversized bodies and malformed JSON do not populate its quote cache. Successful default requests remain GET-only with a four-second transport timeout and a 64 KiB body limit.

Rejected transports count toward the runner's existing three-failure pause and clear entry warmup. An existing position remains recorded; the reader does not fabricate a price or liquidate on transport failure. Review retained accounting and restore verified observations before explicitly starting a new session. These controls narrow transport destinations; they do not establish independent price verification or make the single-source reader an executable-price guarantee.

Exchange timestamp age is now checked again after the fill transaction acquires its write lock and restores state. The runtime hook uses the same wall sample that passed its monotonic continuity check. A receipt can still be recent while an older exchange observation has passed the policy's ten-second limit. Such a fill returns `STALE_OR_FUTURE_EXCHANGE_QUOTE_AT_FILL`, clears entry warmup and cancels the pending unfilled order. This applies equally to BUY and SELL; existing holdings remain recorded when a stale exit is vetoed.

Stopped-session evidence review independently compares every modeled fill's retained evaluation time with its source exchange timestamp. Missing/naive timestamps, future exchange times or ages beyond the stored policy limit add `FILL_EXCHANGE_TIME_DISAGREEMENT`, which is also preserved in exported review blockers. Review failures never repair or rewrite old fills. The fixed experimental policy itself is unchanged.

## Failed or uncertain order cleanup

`ORDER_CLEANUP_FAILED_REVIEW_REQUIRED` pauses intake when final cancellation raises a storage or integrity error. The diagnostic identifies the order and exception class, with `cleanup_acknowledged=false`. A fill may already be committed; cancellation failure never rolls it back or changes the recorded holding. Conversely, a lost acknowledgment can occur after cancellation committed, so inspect the durable order before assuming it remains open.

Review the order, linked accounting and session evidence, resolve the storage fault and explicitly cancel any pending remainder. Idempotent cancellation of an already terminal order adds no new command or fill. New session admission refuses outstanding orders. A separately started session can monitor an existing holding and exit with its entry tier disabled. The original session retains `ORDER_CLEANUP_REQUIRES_REVIEW` in its evidence and portable review blockers even after cleanup; no diagnostic history is rewritten. If the PAUSED record cannot persist, the runtime still stops and releases ownership; the retained RUNNING session appears interrupted after reconstruction and requires review.

## PORTAL recovery panel

Choose **Review Recovery Actions** in the paper forward section. The snapshot lists pending remainders, retained position count and flagged sessions among the most recent 20 examined (API `GET /api/paper/forward/recovery?limit=20`, valid range 1–100). Older retained sessions remain accessible separately. Cleanup-failure PAUSED sessions now show **REVIEW REQUIRED** in session history as well as evidence exports.

Use **Inspect Order** to view retained execution terms and fills. After stopping the runner and waiting for its active cycle, **Cancel Pending Remainder** explicitly uses the existing local-paper cancellation operation. Refresh and inspection never cancel or start anything. A cancellation changes only the pending remainder; committed fills and holdings remain. If acknowledgment is uncertain, inspect the durable order before retrying. Cancellation does not erase the historical session review marker.

The snapshot reads separate databases and reports when this runner still owns execution. Other application processes are not probed; operate only one application instance for recovery. The UI rechecks local cycle state before cancellation, but the snapshot is not a reservation or execution permission. A stopped holding requires separately authorized explicit monitoring/start after reviewing accounting; no recovery view automatically restarts, liquidates, initializes or repairs data.
