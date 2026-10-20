# Nexus workspaces and startup checklist

For a new unused installation, open the EXE, choose Start Portal, then separately confirm Engage Paper when ready. Defaults are already provided; no virtual-budget edit, separate manual account initialization, AI key or payment method is required. Existing choices and recovery blockers are preserved. The To Do introduction explains every step and checkbox.

The interface has seven tabs. Portal observes account movement and controls the fixed experimental paper session. Risk contains virtual allocations, individual entry-tier switches and risk screens. Research contains public data collection, saved model training and historical studies. AI explains the included numerical council and optional paid commentary. Accounts contains virtual balances, recovery diagnostics and future connection/funding plans. To Do contains the automatic setup list, capability inventory and bundled release roadmap. Help explains the workflow and stop controls.

Start/Stop Portal, Engage Paper, Disengage, Emergency Stop and Reset Trading Cut remain outside the panels so they are reachable on every tab. Selecting a tab preserves form values and session state; it never activates, stops or grants permission to a runner. Left/Right and Home/End select workspace tabs; Enter/Space activate focused buttons. All new controls use the standing bottom-right black/purple beginner help standard.

Open To Do, then Refresh Checklist if needed. Its disabled checkboxes are derived by GET /api/onboarding/tasks; there is no completion-write endpoint. Opening To Do performs only that read. Rows distinguish completed local observations, setup/refresh needed, unknown state, optional connections, unavailable features and future plans. Actions open/focus the relevant existing section; they do not perform setup. A failed refresh clears previous ticks rather than displaying them as current. Some checks are separate reads, not a reserved or atomic permission snapshot.

The checklist verifies built-in availability, directory write-permission indications, valid effective default-or-saved budget/allocations, an engaged saved entry tier, saved virtual initial/cash balances, and the recent unchanged-generation/current-engine BTC hourly model pack. It inspects configuration only: storage access is not a test write or integrity audit; account setup is not reconciliation or runner-link compatibility; a model pack is not source attestation, calibrated performance or strategy approval. Model checks use the default BTC/hourly market explicitly; other trained markets do not tick this row. Quotes remain on-demand so the checklist does not probe the Internet. Corrupt/unreadable state shows unknown without repairs or raw error/private-data echo.

Built-in research and learning need no account, payment or external provider key. Optional AI configuration means only configured locally, never provider connectivity. Bank/payment linking and brokerage adapters remain unavailable; cloud operation remains planned. Do not create fake completion flags, solicit payment details or route those details into the API-key field. External calls and paper-session starts retain their existing explicit controls and permissions. This is not plug-and-play live-money trading or release certification.

Validation: tests/test_startup_tasks.py exercises no-write/no-network startup, actual saved-state changes, corrupt/invalid state, optional AI semantics, model freshness/generation/engine checks and the read-only API. tools/workspace_tabs_smoke.cjs executes the actual controller for keyboard selection, retained inputs, no setup action on navigation, automatic checkbox behavior and failed refresh cleanup. Windows browser smoke visits all tabs, hovers visible controls in every panel, checks To Do fixture rows, retains setup screenshots and verifies form-value preservation. Local controller tests do not replace Windows/native visual acceptance.

Engine v2 also displays expanding-window/delayed-fill cost diagnostics in Research. Review all models and cost cases, including empty/losing periods. These modeled unit-capital results are separate from your virtual account and never approve or start the fixed paper policy. Manual model preparation remains explicit; Portal starts bounded local refresh with the selected research settings; a current saved pack ticks only the existing scoped setup row.


Current workflow (v0.85): **Start Portal** turns on the public observer and bounded learning checks. Research defaults are BTC-USD, one-hour candles, a 1,000-bar maximum window and public price history selected. Uncheck public history before starting for saved-data-only operation. Changing a selection alone never starts work or changes a captured running scope. Retrying Start Portal while already active is idempotent and never replenishes worker budgets. Startup failure turns the engine off and reports a sanitized error. A running worker is not proof of a successful fit or profitable forecast.

**Engage Paper** separately starts the existing fixed BTC momentum experiment after confirmation, with the selected entry tier and compatible virtual accounting. Save allocations and explicitly allow that tier in Risk first. This may initialize/link unused virtual accounting; existing traded accounts are never silently merged. Council research remains advisory, not an automatically promoted trading policy. Deposited real funds, broker execution and money movement are unsupported. Engage never claims to operate real funds.

**Disengage** stops this paper session while observation and learning continue. **Emergency Stop** latches paper execution off, attempts durable cancellation of pending simulated remainders and stops the runner. Already filled holdings remain recorded; the cut does not liquidate them. Research, learning and account observation keep running. The button remains available to retry an unconfirmed cut. **Reset Trading Cut** requires confirmation, clears only the latch and never re-engages; research retains its current on/off state. A concurrent newer cut takes precedence over reset. **Stop Portal** stops the runner, observer and learning worker. Restarting the application starts both engine and paper runner stopped; the durable cut persists until reset.

The learning worker checks once per minute, skips unchanged generations and ends after 60 polls or eight training attempts. Public collection may request four bounded pages initially and one after a newly closed candle, at most 12 request admissions per session. No provider account, paid AI or cloud service is used. A stopped in-flight request may finish, but its response cannot be ingested after stop acknowledgment. Storage, source, clock and model faults pause for review without deleting evidence. Research is local and requires the computer/application running. Existing standalone refresh API/core calls remain saved-only when their public flag is omitted; Portal defaults and UI defaults explicitly pass true.

Engine/throttle verification: tests/test_engine_throttle.py uses actual durable accounting and injected observation to check cut/reset races, partial cancellation with retained fills, startup rollback, independent research, in-flight quote stop fencing and no automatic engagement. tools/engine_controls_smoke.cjs executes the actual controller with intercepted APIs, including an emergency click during a pending Engage request.

For the application-closed, copy-only archived order-journal command introduced in v0.177.0 and corrected for Windows handle closure in v0.177.1, use [ORDER_OFFLOAD.md](ORDER_OFFLOAD.md). It is an advanced private operator tool, not a Portal control or first-run step. It never deletes the source or reclaims live capacity.

v0.178.0 changes no startup or Portal control. Its optional installed/source `order-offload rotation-plan` operator command is a read-only, application-closed exact-copy preflight; ordinary startup, explicit Start Portal behavior, engagement defaults and all permissions remain unchanged.

v0.179.0 adds only an explicit application-closed offline replacement-candidate command after verified archival/preflight. It does not alter startup, install the candidate, delete live history, resume paper operation, or change any UI control, permission or risk limit. See `ORDER_OFFLOAD.md`.

v0.180.0 adds advanced application-closed install/recovery/rollback commands. A retained rotation marker makes ordinary order-gateway startup fail closed after an interrupted pair replacement; keep Nexus closed and use the documented `recover-install` command rather than deleting the marker. A completed install remains halted. The Portal, seven tabs, OFF defaults, permissions, account checks and risk limits are unchanged. See `ORDER_OFFLOAD.md`.

v0.182.0 adds mandatory bounded multi-epoch replacement-pressure evidence without changing startup or authority. It does not resume Nexus, restore old orders into live capacity or authorize ID reuse, providers, funds or live execution.

v0.182.1 changes only emergency overload-fixture setup timing. The application, seven tabs, help, startup, OFF defaults, emergency behavior and two-second control-response assertions are unchanged.

v0.183.0 changes only private order-replacement lineage and retired-ID admission. Application startup, the seven tabs, black/bright-purple help, OFF defaults, credential controls, emergency behavior and runtime HTTP policy are unchanged.


v0.86: To Do → View Release Roadmap displays both product stages and technical evidence tracks, including secure deposits/available cash, connected broker paper, governed live execution, computer-off operation and final 1.0 acceptance. These entries are plans, not permissions or connected features. Current funding remains preview-only and current Engage remains virtual/fixed-policy only. The final release requires accepted production workflows, testing, reconciliation, security/risk review, recovery and explicit owner approval; it does not automatically activate live money.

Research → Check Feed Efficiency reads local counters only: public ticker reader calls, transport attempts, cache hits, shared returns, failed transports, wait timeouts and successful transport mean. Concurrent default readers of one market share a pending request and may reuse a still-fresh cached quote for less than three seconds. Each result retains its actual receipt, and follower returns recheck age and clock continuity. The feature starts no network request, strategy, account or learning session by itself. Counters reset with the process and omit injected/fixture readers; they are diagnostic evidence, not profit, strategy approval or broker latency. See PUBLIC_DATA_EFFICIENCY.md.


v0.87 emergency feedback: Emergency Stop uses reserved worker capacity so ordinary blocked feed calls do not consume its cleanup worker. The status snapshot also avoids those workers. The interface distinguishes **TRADING CUT PENDING**, **TRADING CUT VERIFIED** and **CUT UNCONFIRMED**. Pending means cancellation/runner-stop work is still running and Reset is disabled. Verified means this local durable cut, stop fence and audit completed; it does not prove broker safety, a healthy strategy or liquidation. Unconfirmed means local intake is latched but cleanup could not be confirmed: retry Emergency Stop and inspect recovery. Filled holdings remain. Repeated requests share one pending cleanup job; closing a browser request does not cancel it.

Reset requests received during pending cleanup are rejected. Reset captures its intent before general-worker queuing; a later emergency cut invalidates the queued reset. The cut still permits ongoing observation and learning, and no reset starts trading. Reserved scheduling cannot promise instantaneous action during event-loop/OS/CPU stalls, durable lock contention or process failure. See EMERGENCY_CONTROL.md for scope and test evidence.


v0.88: Emergency Stop signals the local paper runner before waiting for storage or starting its durable cleanup worker. Pending verification can therefore block further runner decisions even if the order database is busy. The forward readout shows LOCAL TRADING CUT REQUESTED when present; this is not confirmation that durable cancellation finished. Only successful reset clears that signal, and it never starts trading. Previously committed fills remain. A process killed before the durable cut commits loses the in-memory signal; startup stays stopped and pending orders require review. Inspect recovery after any missing acknowledgment rather than assuming completion. See EMERGENCY_CONTROL.md.

From v0.89, an interrupted Emergency Stop can leave a small local intent latch. Restart stays stopped. If status says CUT UNCONFIRMED, inspect paper recovery and explicitly retry Emergency Stop to finish pending cancellation; retained fills and holdings remain. Reset Trading Cut only clears the cut after its stop/storage checks and leaves trading disengaged. An unreadable or malformed latch also blocks admission; do not delete evidence to bypass recovery. See EMERGENCY_CONTROL.md for recovery boundaries.

Accounts → **Check Broker Request Recovery** (v0.90) runs a temporary offline restart/partial-cancellation example. It explains why an uncertain broker response must keep its original order ID for lookup. No broker setup, account changes or order retries occur. Broker connection, secure funding and live trading remain future setup stages; this diagnostic does not complete them.

From v0.91, the offline broker request example also demonstrates a retained timeout, bounded lookup attempts and a shared rate-limit wait. These are simulated checks. “Not found” or a timeout never means it is safe to send another order. An interrupted lookup remains pending for review after restart; the app does not connect or retry automatically.

From v0.92, Check Broker Fee Fixture also shows whether the supplied example covers each cash/BTC debit after reserving its declared fees. “Covered in supplied history” is an offline arithmetic result, not verified spendable funds. Unknown order sequencing, nonterminal reservations or incomplete fee evidence require review. Real deposits, broker balances and trading permission are not enabled by this check.

From v0.93, the offline request recovery example also shows an interrupted lookup being explicitly reviewed and its late reply rejected. That review keeps the original order uncertain and preserves attempt limits/cooldowns; it never means an order was canceled or can be sent again. Startup never performs this action automatically. Actual broker transport and account reconciliation remain unavailable.

From v0.95, Accounts → **Alpaca Paper: Read Only** offers optional Windows credential storage and a separately confirmed one-time paper account inspection. Offline research and local virtual trading need none of this. Enter your own paper key pair only in those local password fields, then choose Save Paper Keys Locally; the fields clear and no broker request starts. Check Saved Paper Setup inspects the local credential record only. Read Paper Account Once asks separately before reading account/positions/open orders; an optional known account UUID rejects a mismatch. Results are observations, not verified spendable funds, an atomic account snapshot or permission to trade. No orders, transfers or deposits are available here.

Remove Saved Paper Keys invalidates this process's pending reads and verifies deletion from the separate Windows credential entry. It does not revoke keys at Alpaca, cancel broker orders or modify your virtual portfolio. If an action cannot be confirmed, do not assume keys were saved/removed or a read succeeded; recheck local setup or retry removal. Another process and a late synchronous network call are not physically controlled by this local action. Existing saved keys never start requests on launch or when opening tabs. To Do's paper-read row remains an unticked on-demand setup link and never opens the vault. Actual Windows credential/profile and authenticated paper-account acceptance remain pending; external order routing is still unavailable.

v0.96 adds an internal original-order-ID recovery coordinator for future broker integration. It is not a new startup action or button: existing account inspection does not create a broker journal, lookup orders or retry them. The recovery library requires an already retained uncertain request and matching account, records each attempt before network reads and preserves pending review after an interruption. “Not found” never means an order was unfilled or safe to submit again. Actual authenticated recovery and complete fee/cash/position/hold reconciliation remain acceptance work before provider-paper order routing.

v0.97: Check Broker Fee Fixture also displays a separate raw-activity example: explicit fills and unlinked fee debits, with coverage NOT verified. The other part of the example uses deliberately supplied fee links for arithmetic. Those agreeing amounts do not prove raw fees belong to particular trades or establish their posting times. Nothing is collected from a broker by this check, and no account changes. Real activity coverage, corrections, fee links and cash/position/hold reconciliation remain unfinished before broker-paper order routing.

v0.98 adds an internal bounded activity collector for future integration, not a startup or Accounts action. It reads at most two pages within a specified past creation window and never automatically follows a continuation hint. “End observed” means a short response was seen; it does not prove trade/fee history is complete or funds are spendable. No activity rows/cursor are saved by this component. Routine runtime collection, verified coverage/fee links and cash/position/hold reconciliation remain unfinished before broker-paper order routing.


## Keep a paper-account observation for review

1. In Accounts, use Read Paper Account Once and confirm the read. This reports observations and grants no trading permission.
2. After a successful read, select Download Observation and confirm that the file contains private account identity and balances. Save it somewhere private. The JSON contains no keys; it is not encrypted. A new read/setup action or changing the expected account clears the pending download.
3. For source installations, check the file offline with `python -m nexus.portable_paper_observation Nexus-Paper-Observation.json`. A content-consistency result means its structure, arithmetic and fingerprints agree. It does not prove the provider source, current funds or permission to trade. The validator makes no provider request and changes no account.

Unknown values stay unknown. Total BTC and available BTC are different observations, and their difference is not a verified hold. Keep the file as evidence for review; the application cannot import it to authorize funds or trading.


## Compare two saved observations with retained activity (source tools)

The offline review tool can compare two observation exports with an existing activity database. This is a developer/operator diagnostic; it cannot import balances into trading or start orders. Supply the exact account and current history head deliberately:

```text
python -m nexus.paper_observation_review --activities history.db --account ACCOUNT_UUID --head HISTORY_SHA256 --opening first.json --closing last.json
```

It prints a private JSON report containing balance observations and retained activity details. Keep that output private. The tool changes no files or accounts and sends no provider requests. If an observed fill occurred during either sequential read, or the reads overlap, it refuses the gross balance calculation. Unknown totals stay unknown. A calculated difference is not profit or verified spendable funds; matching hashes and balances still need independent source, fee and hold reconciliation. The tool requires an already retained history and never creates a replacement to bypass a mismatch.


After Read Paper Account Once, Accounts also reports supported open IOC limit terms: buy-limit principal and remaining sell quantity. These amounts are observations, not verified provider holds or the price a trade will receive. Pending cancellation still counts as open. Unsupported or conflicting orders make both totals unknown. Download Observation saves the inventory-only report; it does not include these order terms. Nothing in this read places orders or verifies spendable funds.


## Keep private exports encrypted and outside the public repository

The ordinary inventory download is plaintext. Source users can create a password-encrypted copy with:

```text
python -m nexus.encrypted_observation seal Nexus-Paper-Observation.json --output observation.nexusenc
```

Enter a long unique random passphrase at the hidden prompts. Do not put it in command arguments, scripts, environment files, Git or alongside the encrypted copy. No password/key is saved or recoverable. The tool refuses an echoing prompt fallback, never overwrites an existing file, and leaves the original download in place. Keep that original private too. To deliberately recover a new private plaintext file:

```text
python -m nexus.encrypted_observation unseal observation.nexusenc --output recovered.json
```

These source tools do not encrypt local databases or backups. Use a private directory and supported OS disk protection; never commit private data even when encrypted. No encryption result authenticates broker balances or starts trading. See SECURITY.md and PUBLIC_REPO_READINESS.md before changing repository visibility.

### Review encrypted observations without making decrypted files

Source users can supply encrypted observations directly to the existing offline review:

```text
python -m nexus.paper_observation_review --activities history.db --account ACCOUNT_UUID --head HISTORY_SHA256 --opening first.nexusenc --opening-encrypted --closing last.nexusenc --closing-encrypted
```

Use each `--opening-encrypted` / `--closing-encrypted` flag only for that encrypted input. Mixed plaintext/encrypted pairs are supported; suffixes are never auto-detected. Enter the respective passphrase at each hidden prompt, even if both copies use the same one. No password arguments, saved keys, automatic retries or decrypted intermediate files are used. Authentication and portable-profile validation precede review; account, retained-history head, read-window and reconciliation checks remain required. A failure prints only a fixed error, with no partial report.

**The successful report printed to the terminal is private plaintext**, including account observations and activity details. Terminal history/scrollback, redirection, screen recording, process memory and OS swap can expose it; this feature does not configure their encryption or erase memory. Original files and the plaintext history database remain unchanged. Keep all private inputs and output outside Git. These are source tools, not newly verified installer features, and they grant no trading authority.

### Passive forward-session status

Opening Portal and polling the stopped paper runner no longer creates a forward-history database. With no recorded sessions, history is empty and capacity shows unused space; that is not trading permission. Existing session history is read without schema migration or repair. If a history file is unreadable, corrupt or incomplete, diagnostics report unavailable instead of silently replacing it. Stop and preserve the evidence for review. Forward history and capacity displayed by one status request share a database snapshot; runner state and order accounting are separately sampled and can change during observation.

### Inspect recovery without creating accounting

Recovery and retained-order inspection now open existing local order history read-only. A missing journal stays missing; the recovery panel warns that empty results do not prove a flat account or absence of a separate portfolio. Unknown order IDs remain unavailable. Linked portfolio accounting must already exist and pass the same reconciliation checks; missing, corrupt or incompatible evidence is reported unavailable, without an automatic replacement or schema repair. Explicit paper initialization, trading and emergency halt remain separate actions. Inspection does not start or resume trading.

### Verified Windows test checkpoint

The verified v0.114.0 test installer is [available here](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39094330857/artifacts/12124392206), from exact source7d132352f3ce559e94055e5d63c6f4a73e3e7f15. Its packaging, installed/reinstall/recovery/uninstall, regression/browser and security workflows passed. This artifact expires2026-10-25T03:49:34Z and may require GitHub sign-in. It is a prerelease test build, not the pending v0.116 source or1.0 acceptance. Future builds must be checked against their own source and workflow results.

### Check a saved offline trial report

From the matching source checkout, run `python -m nexus.release_evidence validation/release_trials_0_120_0.json`. Success means every required offline profile still satisfies its checks and the report matches these exact source bytes. It does not rerun tests, prove the report is authentic, validate broker balances or approve1.0. Failure prints a fixed error and changes no account data. Regenerate trials from the reviewed source if the report is stale; do not hand-edit hashes to make it pass.

New Windows distribution bundles include their own `release-trial-evidence.json`, covered by the artifact checksum manifest along with the EXE and installer. Source checkouts with different line endings also have different fingerprints. Treat the manifest as a consistency check, not a signature; verify workflow results and remaining acceptance gates separately.

### Measure progress toward a sustained paper trial

Use the existing Start Portal and Engage Paper controls only when you intend to run the local virtual-funds experiment. Keep the computer running; this is not cloud or broker execution. Stop the session before its final review. From a source checkout, inspect the retained history with an explicit UTC window:

```text
python -m nexus.forward_trial_review --history PRIVATE_PATH/paper_forward.sqlite3 --after 2026-10-18T00:00:00Z --until 2026-10-21T00:00:00Z
```

Replace the path and dates with your actual private history/window. The tool does not start anything or request prices. Missing history stays missing; damaged history is not repaired. It reports covered time, rejected/injected observations, uncovered time, longest gap and sessions needing review. Only adjacent eligible observations labeled as the built-in public reader, within one session and at most10 seconds apart, count toward declared coverage. Overlaps count once. A proposed72-hour/95% operational target is shown, but meeting it does not prove genuine data, uninterrupted runtime, strategy quality, account reconciliation or broker-paper acceptance. The output remains REVIEW_REQUIRED with every execution/release acceptance flag false. Keep the report private and outside Git. Review actual fee/hold/account and outage evidence independently.

### Prepare an actual paper-account acceptance read on Windows

This source-only tool uses the paper credential pair you already saved through Accounts. It never accepts keys on the command line or uses a plaintext key-file fallback. Use the exact expected paper-account UUID and one persistent pacing path outside the public source checkout. Replace YOU and ACCOUNT_UUID in these examples. First review local pacing without reading the vault or contacting the provider:

```text
python -m nexus.paper_acceptance_probe --expected-account ACCOUNT_UUID --pacing "C:\Users\YOU\NexusPrivate\paper-read-pacing.sqlite3" --pacing-status-only
```

A missing journal stays missing. Pending/review outcomes need operator review, not deletion or automatic reset. If you separately intend to make one read-only paper-account request session, run:

```text
python -m nexus.paper_acceptance_probe --expected-account ACCOUNT_UUID --pacing "C:\Users\YOU\NexusPrivate\paper-read-pacing.sqlite3" --confirm-read-only-paper
```

This explicitly allows the existing four fixed-host paper GETs and local pacing-journal writes. It creates no orders or transfers. Reuse that same journal: successful sessions retain at least60 seconds of cooldown; rate/authentication/interruption/cap conditions can require longer waits or review. Do not create another journal to bypass them. Portal reads that use no/different journal and other computers are not coordinated by this file; it does not establish broker-wide quotas or cloud ownership.

Successful stdout is a **private inventory, aggregate open-IOC-term and pacing record**, not broker-paper acceptance or verified spendable funds. The record binds the inventory manifest, allowlisted open-term summary and pacing heads with a consistency hash. It never includes raw order/client identities. Buy principal and sell remainder are observations from the same sequential read, not verified holds, complete order coverage or execution prices. The nested portable observation remains inventory-only. Source coverage, provider key permissions, Windows profile acceptance, actual account/fee/hold reconciliation, routing/live/funding/cloud and release approval remain unverified. Failure may occur after remote reads, retained success or an uncertain pending reservation; check status and preserve the evidence instead of automatically retrying. The journal and report are plaintext; keep them outside Git in private OS-protected storage. This tool is not a newly verified installer feature. No actual provider or vault was used during development tests.


## v0.118 managed paper-read pacing in Accounts

Accounts now provides **Read Paper with Pacing** and **Review Read Pacing**. Enter the expected canonical paper account UUID. The first action asks for separate consent before reading the saved pair and running the existing fixed-host four-GET inspector. It retains at least60 seconds of cooldown after success; pending/authentication/cap/interruption conditions require their existing waits or review. The second action replays local pacing only: no credential read, provider request, admission permission or missing-file initialization. New controls have practical beginner help and share duplicate-click, removal-during-read, stale-response and export invalidation safeguards. No startup, hover, tab or passive checklist action invokes them.

On Windows the managed journal is `%LOCALAPPDATA%/NexusOperative/paper-read-pacing/<account-sha256>.sqlite3` (home fallback when LOCALAPPDATA is absent). Source-only non-Windows fixtures use `~/.nexus-operative/paper-read-pacing/`. The account digest is an identifier, not encryption or anonymity. Browser requests cannot supply a path/transport/clock or provider URL; known source-checkout destinations are rejected. Only the same managed local journal coordinates cooperating callers. Existing **Read Paper Account Once**, explicit CLI journals, other profiles and other computers are outside its pacing scope. Never use these alternatives to bypass retained cooldown or uncertainty. This is not broker-wide quota/distributed ownership.

Removing saved keys does not delete or reset the journal. Failed reads may already have contacted the provider or retained success/pending evidence. Preserve history and review it; there is no automatic retry, pending expiry, reset, deletion or order activation. Diagnostic head hashes can equal a pending BEGIN digest; they are consistency references, not credentials or authority. Reports omit named pending-ticket fields and paths and keep all existing funds/order/source/profile/release flags unverified. Portable downloads remain inventory-only and exclude pacing/open-order terms. The journal is private plaintext outside the portable storage backup bundle; retain it separately under OS disk/ACL protection and never publish it. No new encryption/backup guarantee is made.

Development checks inject vault/HTTP responses and use temporary journals. Actual Windows credential/profile/ACL, authenticated account/fee/hold, sustained real-market, funding/live/cloud and independent release acceptance remain open. New exact-source Windows/browser/security CI is required; local controller/API tests are not native acceptance.


Verified installer checkpoint advanced to v0.117, source81f080658e3a309099de6f98552b606006828197. Windows39095648285/job117581727664, safety/browser39095648318/job117581407953 and security39095648336/job117581408158 all passed. Installer12125295332 expires2026-10-25T04:20:09Z; distribution12124931120 expires2026-11-17T04:20:07Z. Tester link: https://github.com/S4-cyber/Nexus-Operative/actions/runs/39095648285/artifacts/12125295332 . Download the ZIP, extract it and run NexusOperative-Setup-0.117.0.exe. This is0.117, not the newer0.120 source; it does not include the0.118 Accounts pacing buttons or0.120 storage fix. Clean-machine/native-window/ordinary-user ACL/prior-installer and actual broker/financial/cloud/independent acceptance remain open. Owner default-branch security PR#2/admin/historical non-Git review remains outstanding.


## v0.131 declared collection-window barrier

The private accepted-reconciliation composition now validates that the union of **accepted caller-declared creation windows** covers the entire envelope from the opening observation's first read to the closing observation's last read, before any balance arithmetic. Earlier synthetic reconciliation fixtures used observation dates outside their retained requested windows; they now use dates within those windows and explicitly prove the old out-of-window case is rejected. Only receipts referenced by replayed acceptance records contribute. Missing/changed receipts, a changed retained head or scope count, reversed/overlapping observation windows, outside endpoints and interior gaps fail closed. Merge adjacent/overlapping/duplicate intervals without bridging a positive gap; use canonical UTC nanoseconds, never float rounding. Bind the window-union digest in the v2 reconciliation input scope and retain the final three-journal recheck.

This is geometric coverage of requested filters, **not received activity coverage**. Exact endpoint equality does not verify a provider's inclusive/exclusive filter semantics; `requested_window_boundaries_verified`, source/activity/fee/hold/account/funds/routing/live/release flags remain false. Short final pages still only establish bounded cursor completion. Late/corrected executions, fees and sequential balance uncertainty remain open independent reviews. No provider/vault call, API/UI/control, automatic retry/continuation, evidence write or ledger/order/funding/cloud authority was added. Keep all actual observations, accepted journals and private diagnostic output outside Git; the existing standalone-report artifact guard applies to the v2 policy too.

The sixteenth release profile now requires out-of-window rejection, covered accepted geometry and false provider-boundary authority in addition to all prior acceptance/replay fences. Generate the current-version16-profile report before full tests, then verify saved evidence against the exact source. Regression checks include pre-arithmetic rejection with unchanged files, contiguous/overlapping/duplicate union, a one-second interior gap, exact endpoints and a one-nanosecond overrun. The general supplied observation-review tool retains its explicitly conditional diagnostic scope; this stricter barrier belongs to accepted reconciliation.


Verified v0.130 checkpoint: exact source ac9b9e8ebb40c033762fe2d6e4a466462deca94b passed Windows39134886278/job117697275954, safety/browser39134886222 and full-history security39134886220. Installer12139379184 expires2026-10-25T14:20:57Z; distribution12139721464 expires2026-11-17T14:20:53Z. Installer link: https://github.com/S4-cyber/Nexus-Operative/actions/runs/39134886278/artifacts/12139379184 . Download ZIP, extract and run NexusOperative-Setup-0.130.0.exe. This verified0.130 installer excludes the new0.131 declared-window barrier. Native-window/ordinary-user ACL/prior-installer and actual broker/account/fee/hold/sustained/funding/live/cloud/independent and owner release acceptance remain open. Owner default-branch security PR#2/admin/historical non-Git review remains outstanding.


## v0.132 retained quote eligibility replay for sustained-trial review

The private forward-trial review previously counted adjacent eligible flags without independently checking the retained quote fields. It now replays a fresh session-local single-source FeedGuard plus exact product, explicit read-only/order-denied permissions, positive finite prices, spread/jump heuristics, recorded error absence, receive/exchange freshness and exchange regression checks at each recorded evaluation time. Require the exact currently admitted policy as well as its fingerprint; a self-consistent rewrite cannot install looser limits. Never use the reviewer's current time or upgrade a recorded rejected quote. This is a conservative check on supplied retained fields, not a replay of every runtime/ownership/accounting state or independent market-source verification.

Only a recorded eligible quote that passes revalidation and carries the existing public-reader label contributes to clipped same-session coverage intervals. Bad quotes break intervals. Keep original declared eligible/rejected counts with an explicit recorded-flag scope, add revalidated counts and count claimed-eligible contradictions separately. Any contradiction in the selected window blocks the proposed72-hour/95% target, even if geometric coverage still exceeds95%. All source, realism, broker, strategy, funds, live, automatic-start and release acceptance flags remain false. Preserve unknown/injected source exclusion, bounded read-only SQLite replay, no gap/session bridging and private-output handling.

Regression fixtures now supply complete synthetic quote fields; the old provider-only shape is explicitly rejected for coverage. Tests cover incomplete fields, stale/future receipt and exchange time, missing timezone, exchange regression, product/provider/permission mismatches, crossed/excessive spreads, nonfinite/overflow numeric data, price jumps, recorded errors, false flags, rehashed unadmitted policies and a nearly complete72-hour history containing one contradictory eligibility claim. The existing mandatory forward profile requires applied revalidation, consistent counts and zero contradictions in its injected runner fixture; it still cannot claim public coverage or market acceptance. No UI/API/control, actual provider/vault call, trading policy or execution authority changed. Run all16 exact-source profiles and saved-evidence verification before full regression and publication.


v0.132 Matrix observation standard (owner clarification): Singularity is one whole-account movement graph. Fractal is a coordinated multi-point view of all observed holdings, with individually traceable position histories. The implemented shared-time constellation encodes signed unrealized USD P/L around a zero ring on a shared range scale, keeps measured history below and removes missing/closed latest nodes. Angles and connecting edges are layout, never correlation, forecast or execution activity. Preserve gaps, downsampled dots, accessible quantities/value summaries and practical black/purple control help. Mode switching and adjustment inspection remain IO-free. Current data is local paper observations, not connected broker coverage. Future position sources must preserve the same comprehensive distinction and honest source/timing labels.


## v0.133 minimal first-run setup

Standing owner requirement: minimize setup to essentials and provide ready-made local paper defaults, editable afterward. Loading settings in a genuinely new unused installation now commits the existing $10,000 virtual budget/50-35-15 allocations and Terrestrial ON/Solar OFF/Universal OFF in one SQLite transaction, with one preset audit event. No engine, order, account, feed, vault, provider or paid AI operation starts from this action. Existing settings files (including empty ones), retained accounting/forward journals or sidecars and emergency cut intent suppress automatic risk selection; existing explicit OFF choices and custom settings survive reload. Initialization failures roll back and never grant a running session. Explicit first tier choices made before settings loading also win. The low-level missing-switch fallback for existing installations remains OFF; this supersedes the historical blanket all-tiers-OFF new-install wording.

The ordinary fresh path is open EXE → Start Portal → separately confirm Engage Paper. Public-history collection, BTC hourly/1,000-bar research and built-in numerical learners already have defaults. Solar/Universal do not add engine processing power; no external AI/payment/provider account is needed for the local paper experiment. Compatible unused virtual accounting is already created and linked on confirmed Engage; do not demand a separate Initialize or Save Allocations step. Existing traded/incompatible accounting still requires review, never automatic reset/migration. Restart remains stopped and the emergency throttle cut remains independent. The preset is a starting configuration, not statistically optimal settings or profitability evidence.

To Do now introduces the machine and engine/throttle/stop workflow. Every row has a plain-language why and setup scope; optional and future boxes may stay unticked. The budget check validates effective built-in defaults plus saved overrides without writes; malformed saved state remains unknown or needs review. Missing virtual accounting is marked When needed, not a mandatory setup form. Checks remain read-only and are observations rather than authority. New navigation uses practical data-help and preserves the fixed bottom-right black/purple description panel.

First-run acceptance includes atomic fresh presets, concurrent first reads, rollback on injected storage failure, saved budget/OFF preservation, old empty-file preservation and retained-accounting/sidecar/cut suppression; the actual fixed runner prepares with defaults without manual forms or price IO. The mandatory real loopback HTTP release profile still proves passive startup creates zero files, then exercises the normal settings GET/permissions/checklist path: only the settings database is created, Terrestrial is selected, each purpose is present, optional connections are not required, an explicit OFF survives reload and trading stays stopped. Six new mandatory HTTP evidence booleans fail closed. Windows/native profile, actual broker/market/funding/cloud and independent/owner1.0 acceptance remain separate open gates.


## v0.135 packaged setup and saved-choice acceptance

The installed-process smoke tool now offers explicit disposable `--setup-check first-run` and `--setup-check saved` profiles. First-run requires absent settings/accounting before launching, checks the native API’s $10,000/50-35-15/Terrestrial-only preset and every checkbox purpose, deliberately saves a $2,500 virtual budget and turns Terrestrial OFF, then confirms reload without any engine/order/provider start. Saved mode is read-only for configuration and verifies those exact fixture choices on the next process. Keep these fixed test modes restricted to separate disposable data directories, never retained recovery fixtures or personal data. Existing production UI and execution policy are unchanged.

Windows installer smoke now runs these profiles for both the explicit portable directory and default LOCALAPPDATA layout. It checks the settings database SHA-256 before/after same-version reinstall and before/after uninstall, in addition to existing order-journal/sentinel preservation. Record booleans only after the underlying assertions pass. Prior-version data recovery, native-window rendering, clean ordinary-user ACL/profile and full cross-version installer acceptance remain independent. A local source-process shim passing these checks is not evidence that the Windows EXE or PowerShell installer treatment ran here. Negative tests reject wrong budgets/tiers, empty or missing-purpose checklists, automatic start, promoted funds and reselection of an OFF switch; saved mode must perform no PUT.

Verified preceding checkpoint: v0.134 exact source84542b862aae5c840d46d4fd2d5c336b091ee61e passed Windows39141177457, safety/browser39141177351 and security39141177346. Installer12142365471 expires2026-10-25T15:44:30Z: https://github.com/S4-cyber/Nexus-Operative/actions/runs/39141177457/artifacts/12142365471 . Extract the downloaded ZIP and run NexusOperative-Setup-0.134.0.exe. Distribution12141991743 expires2026-11-17T15:44:27Z. This checkpoint predates the0.135 smoke extensions. All actual broker, sustained-market, funds, live, cloud, independent review and owner1.0 gates remain open.


## v0.136 saved paper settings require review

Default convenience applies to new unused profiles and untouched legacy empty configuration, never a malformed saved value. The shared bounded reader now requires all four known saved keys when any is present, rejects duplicate keys, non-text/oversized/malformed JSON, invalid numeric types, booleans, nonfinite/out-of-range budgets, invalid allocations and totals other than100. If all settings disappear after a retained settings-save/new-install audit marker, require review rather than substituting the larger default budget. Unknown unrelated keys stay ignored. This is local content validation, not source authentication or protection against a coherent rewrite of settings and audit history.

Load and To Do use the same validated read snapshot. Completely materialize the at-most-five selected scalar rows before raising so a retained exception frame cannot leave a live SQLite cursor blocking an explicit repair. Validation failures must not repair or change file bytes. Settings and tier GET failures use fixed503 review text without private data. Enabling a tier validates configuration under that tier write transaction before changing permission; disabling a tier remains possible with malformed values and reports only the confirmed OFF/review state, without guessing other caps. Low-level database failure still cannot acknowledge a successful disable. Existing emergency controls are independent; fixed-runner risk-reducing SELL remains available when only paper settings are malformed.

Risk clears unverified budget/allocation form values, shows practical recovery guidance and preserves entered edits after a rejected save. Saving valid intended settings is explicit and refreshes the tier display; it neither starts a session nor re-enables a saved OFF switch. Every existing control retains black/purple beginner help. The new actual paper-settings Node controller verifies failed-read clearing, fixed error text, explicit save and absence of trading requests; it is also run by pytest.

Regression coverage includes malformed values, missing fields/all-field loss, duplicate fields, no-mutation failed reads/enables, confirmed OFF, explicit repair without re-engagement, pre-price-IO entry veto and a real fixed-runner fixture SELL after budget corruption. The existing mandatory HTTP release profile adds corrupt-read rejection, failed-enable preservation, successful disengagement and explicit repair retaining OFF. All16 profiles still use disposable fixtures and cannot establish real-market, broker, funds, live/cloud or release acceptance.


## v0.138 autonomous-engine onboarding and Engage checks

The introduction and every read-only To Do checkbox explain the component's role in an autonomous trading engine. The intended production journey is install, provider-hosted account authorization/funding, risk allowance and Engage. Provider bank authorization, real funding, broker routing, learned-model execution and cloud operation remain unimplemented acceptance gates; never collect bank passwords/card details in AI settings. Current included intelligence is research/advisory; the actual autonomous executor is the fixed BTC local paper experiment. External AI is optional. Current paper use retains Start Portal and explicit Engage confirmation.

Engage first GETs a read-only local preflight, shows fixed practical blocker guidance and asks for confirmation only after a clear diagnostic. It creates no accounting, performs no repair, requests no prices and grants no running permission. The check samples current local state with a1GiB per-file diagnostic budget; this is not a journal retention cap, cross-file transaction or descriptor-bound guarantee. It cannot establish another process's ownership, clock continuity or price freshness. Actual start repeats admission; tier changes, emergency cuts and duplicate clicks during the check cannot silently initiate a session. Held linked BTC may retain exit monitoring with an OFF tier or malformed entry budget. Existing independent emergency and durable reset behavior stays in place.

The mandatory real-loopback HTTP profile checks the stopped preflight with no startup storage creation and false authority flags. Unit and actual-controller fixtures cover default-ready setup, corrupt/local sidecars and account mismatches, retained exits, rejected/promoted responses, cancellation, duplicate requests and emergency/tier changes. Native installation, rendered browser, actual market/provider/funds and independent/owner release acceptance remain separate.


## v0.139 Found Markets and checklist scope

Read FOUND_MARKETS.md before extending discovery. To Do separates current baseline prerequisites, optional improvements and future connected features. Found Markets is optional and selected by default in the page for Start Portal; legacy Portal API callers remain opt-in. It runs four candidates per 15-minute burst, up to36 requests/hour across cooperating Nexus processes sharing one local data root,256 in-memory summaries and six fresh research selections, with explicit deterministic 24-hour/30-day/180-day council screens. Each public allowance is durably committed before transport; start/stop and process restart cannot reset it. Stop and engine admission fence publication; no background worker starts on application restart. Current-page switch preference is not persisted. GET status and checklist remain passive and create no budget journal.

Never promote discovery scores to expected profit, calibrated predictions or execution permissions. This first connector scans public USD spot crypto, not all asset classes. It rotates a bounded sample, revisits earlier leaders, uses single-source closed bars and coarse last-day turnover, and drops stale/gapped/negative/risky evidence. The shortlist uses best eligible horizon score, not balanced diversification. Trained-model forecasts, multi-asset connectors, costs/spread/depth, holdouts, eligibility, provider/shared-IP budgets beyond the cooperating local journal and sustained real-source acceptance remain open. The fixed BTC execution policy and emergency throttle remain independent.

Every new switch/button has practical black/purple help. The actual discovery controller tests optional startup/stop, coalesced reads/control requests, promoted reply rejection, stale-result clearing and private-error isolation without trading requests. The mandatory loopback HTTP profile adds passive discovery and explicit checklist necessity assertions; all examples remain offline fixtures.


## v0.140 launch assessment and installed trial review

The full agreed1.0 remains experimental local-paper preproduction: broker-paper execution and real-account reconciliation, sustained real-feed/incident trials, independent strategy/source/security review, provider-held funding, governed live workflows, secure cloud continuity and owner approval remain open. Do not turn version numbers or regression counts into a percent-complete or a launch date. A paper preview is a narrower scope than the agreed full1.0; no scope reduction or live activation is implied.

To Do now exposes Paper Trial Readiness through an explicit GET of the configured forward-history path, with recent1-hour/24-hour/72-hour/7-day windows ending at server UTC time. Missing history is a successful no-history diagnostic and creates no directory/database. Existing history uses the established one-transaction retained-field/chain coverage reviewer. It estimates additional quote records at the fixed policy cadence;72 hours needs51,841 additional records, seven days exceeds the empty100,000-record cap at that cadence. This is only quote capacity, never an order/ID/session/disk reservation or a guarantee that exits remain available. No automatic start, data fetching, repair, deletion, account/credential access or window extension occurs.

Both installed and CLI reviewers now preflight rollback-journal/WAL/shared-memory sidecars, regular single-link files, DELETE-mode headers and a1GiB per-file diagnostic budget. Reject without opening SQLite or repairing evidence; sampling does not provide descriptor-bound protection against path races. Existing payload/metadata/session limits and20-second SQLite-progress deadline remain. A stopped session is needed for stable final review; the report is operational content consistency, not source attestation, account reconciliation, strategy profitability, broker acceptance or a passed sustained trial. Minimum72-hour/95% declared coverage is one diagnostic target only.

The UI coalesces clicks, freezes the window during review, rejects promoted authority flags/malformed capacity/private unknown reasons, uses plain-language allowlisted reasons and clears failed results. Every new control has the standard black/purple help. Browser fixtures exercise no-history/capacity text without backend IO. The mandatory real-loopback profile proves passive missing-history diagnostics create no files or authority. Run trial readiness/controller, retained reviewer and help tests, all regression and16 offline profiles before publishing.


## v0.154 explicit packaged paper tools

New v0.154+ builds accept `NexusOperative.exe --paper-tool list` (or --help) for the three-command catalogue. `--paper-tool activity` invokes the existing explicit activity/status CLI, `--paper-tool account-review` the private combined evidence review, and `--paper-tool recovery` the single-step recovery/review CLI. Add --help after a command for its existing required arguments. The same source entry works as `python portable/desktop.py --paper-tool ...`. These operations dispatch before configure, app/uvicorn import, data-directory creation, sockets, server threads or UI. Unknown commands/extra top-level arguments fail with a fixed code, never desktop fallback. Child CLI SystemExit/return codes are preserved and sys.argv is restored. The frozen path never adds the installation folder to Python imports. No arbitrary module, Python -c, drills or subprocess-based release suite is exposed through this allowlist. Plain EXE launch remains the existing desktop workflow.

Commands retain their existing explicit paper-read confirmation, saved Windows-pair checks, exact account/head/current-epoch guards, durable pacing and bounded private histories. Read-only review/status requests no key/provider access. Frozen private paths additionally reject the EXE's own folder and resolved aliases; use a separate private user-data folder. Encrypted review inputs still decrypt in memory after private prompts; command output remains private plaintext, suitable only for an explicitly chosen private output destination. This is an operator acceptance path, not a required first-run settings chore or permission to send financial orders. Older EXEs lack this dispatch and may launch the desktop when passed these arguments: require v0.154+ and verify the catalogue/version before using commands.

`tools/packaged_paper_tools_smoke.py` executes five bounded child commands: catalogue, missing recovery review, missing activity status, supplied two-fill account review and an invalid command. It checks no default data/UI initialization, unchanged private journals/manifests, exact false authority flags and no echoed argument values. Error artifacts retain only allowlisted codes, never private child stdout/stderr or paths; timeout cleanup targets only the smoke's own process tree. Windows CI runs it on both standalone and installed EXEs, preserving separate public aggregate JSON in installer evidence. Source release HTTP trials run the same command-prefix treatment using Python; these source results explicitly do not claim frozen/native/profile/ACL/account acceptance. No provider/key reads are requested by this fixture, and it is not an OS-level network/vault audit. Existing browser/native and installer/recovery checks remain. Sixteen source profiles now require this additional evidence and reject stale reports missing it. No new UI controls or tooltips are needed; standard beginner help remains unchanged.

All v0.152 request-origin binding/prefix and earlier stop/key/recovery/cost checks remain. New exact-source Windows/browser/security workflows are still required before calling the package verified. Actual authorized broker-paper/source/fee/hold/fund acceptance, sustained market trials, funding/live/cloud and independent/owner1.0 approval remain open. Rough implementation stays about40% (30–45%, low confidence),0/9 accepted major stages.


## v0.155 packaged account acceptance probe

Use `NexusOperative.exe --paper-tool account-probe --help` to see the explicit account-probe arguments. For a no-key local review, supply the expected account UUID, a private pacing path outside the application folder and `--pacing-status-only`. Only a separately confirmed read may access the saved Windows pair and fixed paper GET endpoints. Output may contain private account observations; redirect it only to an intentionally private location outside Git.

The command dispatches before desktop/data initialization and never starts the PORTAL. It grants no order, retry, funding or live permission. v0.154 and older builds do not expose this command; verify the catalogue reports v0.155+ before using it.


## v0.156 encrypt a private portable observation

First verify the installed crypto path without opening a private file:

`NexusOperative.exe --paper-tool observation-crypto self-test`

Create a separate authenticated encrypted copy in a private user-data folder:

`NexusOperative.exe --paper-tool observation-crypto seal PRIVATE_OBSERVATION.json --output PRIVATE_OBSERVATION.nexusenc`

To create a separate plaintext copy later:

`NexusOperative.exe --paper-tool observation-crypto unseal PRIVATE_OBSERVATION.nexusenc --output PRIVATE_OBSERVATION_REVIEW.json`

Passwords are entered through hidden terminal prompts, are never accepted as arguments, and are not stored. Seal asks twice and requires at least 14 characters. Output paths must not already exist. The original is never deleted; protect or securely dispose of plaintext separately. Keep every input/output outside Git and the installation/source folder. This tool encrypts the supported portable observation profile only and grants no broker, account, order, funding, live or release authority.


### Easier offline account review (v0.157+)

You can ask the reviewer to use the current local journal fingerprints instead of copying them by hand. A fingerprint identifies the exact retained history being checked. This option still stops if the history changes; it never retries or contacts the broker.

1. Use the existing `--paper-tool account-review --help` instructions to supply your account ID, four separate private journal paths and opening/closing observations. Keep them outside Git and the installation/source folder.
2. Add `--use-current-heads` instead of both `--request-head` and `--activity-head`. Use the two manual fingerprints when you need the review to match fingerprints you recorded yourself; if either has changed, the review fails. You cannot combine these methods.
3. Add `--public-summary` if you want a short diagnostic you can choose to share. It omits account/order IDs, balances, money amounts, dates, paths, fingerprints and private reasons. It still reveals activity counts and whether checks agree. Without this flag, the detailed output remains private financial information.
4. Add `--opening-encrypted` / `--closing-encrypted` for encrypted observation copies. Passwords use hidden terminal prompts; reviews decrypt in memory without writing plaintext files. The public summary never prints the password or decrypted observation.

The summary reports `NO_GROSS_DIFFERENCE`, `DIFFERENCES_PRESENT` or `NOT_EVALUATED`. These compare gross fills only: no difference does not mean zero fees, profit or money available to spend. An individual fill can still violate its original terms even when the gross balance matches. All results remain `REVIEW_REQUIRED` and grant no trading, funding or release permission. These optional operator diagnostics are not required first-run setup steps.


### Institutional mechanics, simple personal operation

See [INSTITUTIONAL_DESIGN.md](INSTITUTIONAL_DESIGN.md) for our public research and staged execution strategy. The intended experience stays simple: supported account, risk choice, Portal checks and separate Engage. The complexity belongs behind those controls.

The new offline execution-quality diagnostic checks whether supplied executions were better or worse than the price benchmark when a decision was made. It separates waiting, spread, later price differences, explicit fees and unfilled quantities. Unknown fees stay unknown. A negative cost comparison is not profit, and nanosecond event timestamps do not imply nanosecond broker fills. `python -m nexus.execution_quality` runs a synthetic self-test only; this is not a new EXE paper-tool command, mandatory setup task or trading permission. Actual broker-evidence/UI integration and measured routing remain future work.


### Review retained paper execution prices (v0.159+)

The optional `quality-review` tool compares all retained priced fills of one original paper order with a supplied decision/arrival quote benchmark. It obtains the quantities and prices from the journals, so you do not copy individual fills by hand. A shortened time window cannot hide an unfavorable retained fill: the check refuses instead. History changes also stop the check without retrying.

First verify the EXE catalogue version, then run `NexusOperative.exe --paper-tool quality-review --help`. Supply the account ID, four private journal paths, exact current request/activity fingerprints, the original client order ID and a separate private benchmark JSON. The benchmark must contain exactly `decision_mid`, `decision_at_ns`, `request_event_at_ns`, `arrival_bid`, `arrival_ask`, `arrival_at_ns`, `end_at_ns` and `unfilled_mark`. Prices are decimal strings; timestamps are integer nanoseconds. `request_event_at_ns` must exactly equal the selected original request's retained local submission-admission wall-clock sample and fall between the decision and arrival samples. A missing older marker, sub-nanosecond value or mismatch refuses the review without changing history. Use null for an unavailable unfilled mark. No fee field is accepted or guessed. The source equivalent is `python -m nexus.paper_execution_quality_review --help`.

The current tool binds that local marker but does not prove a transport occurred, establish broker arrival, synchronize clocks, independently capture/attest the benchmark quotes or link fees. Fee-inclusive totals remain unknown, and the comparison does not establish profit or actual broker latency. Detailed output is private financial information; keep it, the benchmark and every journal outside Git and the installation/source folder. This command has no public-summary switch. It never reads a provider/key, submits orders, changes balances or initializes the desktop. These diagnostics are optional acceptance tools, not required beginner setup.


### Quote evidence development checkpoint (v0.160)

An internal confirmed paper quote reader now observes BTC/USD bid/ask data between two paper-account identity checks. It rejects stale quotes, changed accounts and revoked saved keys, and shares the existing private read cooldown. Quote sizes are reported values, not a promise that your order can fill; timestamps do not prove broker arrival or latency.

This is developer/acceptance groundwork. It adds no setup checkbox, button or EXE command, and does not fetch data at startup or retain quote files automatically. Automatic retained benchmarks, order-time binding and a separately accepted streaming feed are still future work. Existing Portal, Engage, emergency-cut and beginner-help behavior stays the same.


### Optional private quote acceptance diagnostic (v0.162+)

This advanced diagnostic helps review an already retained paper request. It is not needed for local virtual paper startup and does not start trading. Use a private folder outside the installation/repository and the same selected quote-capture pacing history each time. Do not use an accepted-activity pacing journal or rotate paths to bypass cooldown.

First review without contacting Alpaca or reading saved keys:

```powershell
NexusOperative.exe --paper-tool quote-capture --expected-account ACCOUNT_UUID --request-journal PRIVATE_REQUESTS --quote-store PRIVATE_QUOTES --pacing PRIVATE_QUOTE_PACING --request-head REQUEST_SHA256 --status-only
```

Status reports retained heads and any unbound history. A missing quote/pacing history has a64-zero head; absence does not prove that no earlier read occurred elsewhere. Review all problems before continuing; no reset/repair is provided. If this exact account/read has been authorized, add the three exact status heads and original client ID, omit `--status-only`, and separately confirm one read:

```powershell
NexusOperative.exe --paper-tool quote-capture --expected-account ACCOUNT_UUID --request-journal PRIVATE_REQUESTS --quote-store PRIVATE_QUOTES --pacing PRIVATE_QUOTE_PACING --request-head REQUEST_SHA256 --quote-head QUOTE_SHA256 --pacing-head PACING_SHA256 --client-order-id ORIGINAL_CLIENT_ID --confirm-read-only-paper
```

This uses the already saved Windows paper-key pair; never put keys/passwords on the command line. It reads the account, one fixed BTC/USD quote and the account again, then retains a bounded private observation. The provider read is GET-only; local pacing and quote files are written. Failures may already have retained a read or quote: run status, preserve history and do not automatically retry. Retention/read success does not establish provider acceptance or authorize orders. Source, decision/arrival timing, fees/funds and 1.0 acceptance remain unverified. Private output is not for Git or public logs, and this quote store does not encrypt its files.


### Optional encrypted quote-evidence copy (v0.163+)

Use this advanced offline tool to move an already captured paper history to another computer for review. It does not connect an account or start trading. Get the three exact heads from quote-capture status, choose a NEW encrypted output outside the application/repository, and keep the original private journals:

```powershell
NexusOperative.exe --paper-tool quote-evidence seal --expected-account ACCOUNT_UUID --request-journal PRIVATE_REQUESTS --quote-store PRIVATE_QUOTES --pacing PRIVATE_QUOTE_PACING --request-head REQUEST_SHA256 --quote-head QUOTE_SHA256 --pacing-head PACING_SHA256 --output PRIVATE_COPY.nexusenc
```

Enter a strong unique password at the hidden prompts; it is not stored and cannot be recovered by Nexus. The original databases remain unchanged and require their own private-volume protection. Review the encrypted file on another machine without broker keys or decrypted files:

```powershell
NexusOperative.exe --paper-tool quote-evidence review --source PRIVATE_COPY.nexusenc --public-summary
```

The public summary contains counts and local consistency only. Omit that flag for private IDs/heads; keep private stdout out of public logs. This checks the copied history, not the broker, current source state, profitability or trading readiness. It does not import or resume the copied databases. Never put encrypted account copies into the public repository.

Keep quote-capture request, quote and pacing database paths separate from one another and from their SQLite sidecars (`-journal`, `-wal`, `-shm`) and pacing `.owner.lock`. v0.163.1 refuses those relationships before any saved-key or provider access.


### Read-purpose counts in optional paper diagnostics (v0.164+)

Pacing status now identifies which standard reader used the local budget: inventory, BTC asset rules, activity pages, original-ID recovery or BTC quote. A pending purpose means that read has an unknown outcome requiring review. Older sessions say LEGACY_UNSPECIFIED because their purpose was never recorded; Nexus does not guess or rewrite them. These counts need no keys/network and do not authorize trading. Each purpose shares the same selected journal's cooldown and limits. Changing readers or creating new journal paths must not be used to bypass provider limits. Mixed operations still need a separately accepted receipt/budget model. Existing status commands stay read-only; no extra startup checklist or setting is required.

### Optional offline provider/result review (v0.174+)

Operators can run `NexusOperative.exe --paper-tool provider-results --input PRIVATE_MANIFEST.json` to compare supplied observations with pinned local provider/account histories. This is an optional diagnostic, not a startup requirement or an account connection. See [PROVIDER_RESULT_REVIEW.md](PROVIDER_RESULT_REVIEW.md) for the manifest format. Keep it private and outside the installation/source directory. The command never opens keys, contacts the broker, starts the engine or writes receipts. A successful command can report disagreement; even matching evidence remains REVIEW_REQUIRED and grants no trading permission.
