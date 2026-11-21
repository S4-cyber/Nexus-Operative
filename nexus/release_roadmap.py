"""Read-only release contract and conversation recovery reference.

Change through reviewed source commits. There is no runtime update operation.
ROADMAP.md is generated from this module; LAUNCH_DRIVE.md contains implementation history.
"""
from dataclasses import dataclass, asdict
from nexus.milestone_execution_plan import execution_plan, next_work_batches, markdown_lines as milestone_task_markdown_lines

REVISION = '0.340.396'
BRANCH = 'development/windows-portal-foundation'


@dataclass(frozen=True)
class Milestone:
    id: str
    title: str
    status: str
    evidence: str
    remaining: str


MILESTONES = (
    Milestone('M1','Windows packaging and recovery','PARTIAL','Windows test, EXE startup and installer workflows; passive portfolio and forward-history/session/capacity reads preserve absent storage; existing state uses read-only transactions, combined forward polling shares one evidence snapshot; recovery/order/session reads use read-only journal replay and read-only linked-portfolio attachment, with missing history distinguished from flat-account proof; installed smoke explicitly exercises dashboard/recovery reads; operator-created paper snapshots reject source/destination link indirection, require regular single-link files, use bounded SQLite backup contention, enforce fixed source/count/aggregate/directory/free-space admission and remove failed candidate files without replacing or pruning retained snapshots; durable order recovery; installed-process/reinstall/uninstall smoke with explicit disposable fresh/saved setup profiles and settings-byte preservation assertions, plus distribution checksum tooling; verified offline backup/new-directory restore tooling and fresh-process linked-ledger/quote-evidence recovery drill and installed-API restored-data smoke tooling with pinned prior-version fixture support and default Windows data-layout checks; the Windows distribution includes a manifest-bound owner-operated non-elevated LocalAppData profile/effective-ACL probe and strict privacy-safe offline reviewer that retain no username, SID or path and keep all external authority false; documented beginner help for all PORTAL controls and isolated headless-browser acceptance tooling; seven named workspaces, friendly To Do introduction and automatic read-only startup checks with a plain-language purpose and setup scope for every box; new unused settings receive an atomic $10,000 virtual budget and Terrestrial-only entry preset, preserved saved/OFF choices and on-Engage compatible virtual accounting remove redundant form setup.','Exact-SHA automated installer/reinstall/uninstall and pinned prior-data recovery steps passed; execute and independently review the new probe on a clean ordinary-user owner profile, record native-window behavior, and complete prior-installer upgrade acceptance. Tool availability alone is not owner/profile/ACL acceptance.'),
    Milestone('M2','Market data and provenance','PARTIAL','Public observation, timestamp/gap checks, historical admission and source diagnostics; redirect-disabled public transport with exact response-URL checks; canonical compressed hub bars with cross-collector deduplication, conflict quarantine and hash-verified sealed datasets; bounded public candle collection selected by default on explicit Portal start, closed-boundary request coalescing, pre-network storage checks and receipt-time clock/stop fencing.','Independent provenance review; missing/revised/conflicting data and outage evidence.'),
    Milestone('M3','Decision speed and risk authority','PARTIAL','Bounded local research decision queue and retained-ID limits with invalid-clock rejection; disposable bounded linked-ledger burst timing, overflow/expiry and halt flushing; individual Terrestrial/Solar/Universal entry permissions with complete saved-setting validation, transactional enable admission and independent OFF under review; bounded per-product public quote request sharing with clock-fenced cache reuse and process-local transport/avoided-request diagnostics.','Measure full durable path under bursts; verify expiry, overload and permissions throughout autonomy.'),
    Milestone('M4','Order lifecycle and portfolio reconciliation','PARTIAL','Partial/full priced fills, fees, idempotency, cancellation, durable recovery; atomic opt-in paper-ledger linking, partial-position reconstruction, adverse quote-cost assumptions, post-lock runtime exchange/receipt freshness and expiry/stop checks, durable expiry, cleanup-failure pauses and forward process-death tests before/after fill commit.','Extend interruption/quote-path stress; independently review execution assumptions, existing-account migration and reconciliation.'),
    Milestone('M5','Durable latency and capacity','PARTIAL','Replay-validated checkpoints, two-snapshot retention, checkpoint interruption tests and isolated gateway and linked-ledger/tier-inclusive p50/p95/p99 benchmark; capacity admission diagnostics; opt-in atomic quote-order fixture treatment with retained commands and crash rollback tests; explicit logical archival with retained IDs and commands; read-only sustained-trial readiness reports quote/session headroom plus conservative every-cycle durable command and ID bounds; an explicit application-closed installed/source rotation tool verifies an offline archive, prepares a fresh halted candidate, retains a second exact source recovery copy, installs the candidate behind a persistent fail-closed marker and can explicitly recover an interrupted pair replacement or roll a completed install back. Archive/candidate/recovery publication, the marker and both live pair replacements use one durable move boundary: parent-directory fsync on POSIX and MoveFileExW WRITE_THROUGH on Windows, while file contents remain separately flushed. Each new v3 candidate retains replay-validated generation lineage plus a bounded private fingerprint set for all retired client-order IDs, rejecting cross-epoch reuse before journal mutation; v1/v2 candidate and legacy recovery/marker records remain readable. A legacy v2 source without fingerprints can proceed only through a private v1 baseline review bound to the exact closed source generation, epoch, replay digest and verified archive manifest. Known archived IDs must be included, raw IDs are never copied into the candidate, and the v4 lineage labels the result OPERATOR_REVIEWED_UNATTESTED. The mandatory operations fixture repeats four synthetic generations and 48 terminal orders, replays every retained archive/candidate/recovery set, verifies all 48 retired-ID fingerprints, exercises the legacy migration fence and requires each installed live journal to return to its four-command halted baseline.','Extended multi-epoch long-run scalability and pressure acceptance, sustained queue pressure, source-retirement policy and independent archive/private-volume/native Windows power-loss/profile/ACL acceptance remain open. The bounded synthetic fixture is not duration, market throughput or native interruption evidence. Write-through metadata publication reduces a known Windows durability gap but is not physical power-loss proof. The private fingerprint fence covers only a complete retained lineage; it does not establish caller identity, other-host coordination or broker-wide idempotency. A reviewed legacy baseline records an operator completeness assertion, not source attestation; independent review of the actual private scope remains required. Installation reclaims live journal capacity but preserves source history externally, never resumes operation and grants no broker, funds, live or release authority; the conservative trial bound is not expected trading volume or validated broker throughput.'),
    Milestone('M6','Scientific strategy evidence','PARTIAL','Chronological research, holdout, regime and cost-stress modules; persisted multi-horizon ridge momentum baselines with training-only scaling, purged chronological holdout, naive/momentum comparisons, generation invalidation and compact research signals; three built-in trained feature specialists with fixed three-block baseline checks, disagreement reporting and independent deterministic validation/provenance reviewers; versioned saved research packs and unchanged-data reuse; three expanding-window refits with purged training targets, delayed nonoverlapping modeled fills, fixed cost scenarios and fold-consistency checks; shared causal feature computation and serialized preparation; engine-started bounded learning with captured public/saved-only scope, bounded polls/fit attempts, generation skips, local OS ownership and stop-fenced publication.','Independent model calibration and walk-forward/forward evidence, licensed shared-source provenance, delisting/survivorship review and performance audit; trained baselines are not validated market edge.'),
    Milestone('M7','Unattended paper operation','PARTIAL','Explicitly started BTC public-quote paper runner; separate research-engine and explicit Engage Paper controls, independent emergency throttle cut preserving research, reset/start race fencing, dedicated coalesced emergency cleanup independent of feed/general workers, plus a separate installed emergency-stop process that publishes the durable latch before SQLite, starts no Portal/network/vault/reset path, is adopted by a running API and is consulted at paper start/quote/submit/fill boundaries; cancellation-shielded cut completion and pending/verified/unconfirmed status, queued-reset epoch checks, an immediate local runner cut signal and post-lock veto during real SQLite contention, independent bounded local cut-intent persistence and conservative restart recovery, native Windows read-only journal fault fencing with explicit post-recovery cleanup retry, process-death-before/after-cut-commit recovery fixtures, tier-gated entries, exits, halt/pause, feed-failure stop, durable session counters/heartbeats and fingerprinted fixed-policy admission; retained quote evidence, quote/fill price/quantity/fee/policy crosschecks, portable review summaries and exclusive local process ownership, clock-discontinuity pauses and exchange-regression warmup fencing and retained-storage preflight with operator runbook; bounded repeatable forward-runner fixture drill with fresh-process evidence comparison; operator recovery snapshot and pending-remainder inspect/cancel controls; Singularity account-equity and Fractal coordinated shared-time position constellation plus per-holding observed history with retained ranges and journal adjustment inspection and transactionally retained closed-day observation history with bounded active/total admission and range responses plus observation database byte/free-disk budgets and rollback-safe allocation failure handling.','Extend native Windows acceptance to ordinary-user ACL inheritance and whole-machine pressure; run sustained real-feed forward validation, independent policy evidence review and multi-market scheduling. Local subprocess/file-attribute outage tests are not broker or market performance evidence.'),
    Milestone('M8','Broker paper integration and operations','OPEN','Provider-neutral design plus disconnected Alpaca paper IOC-limit previews and bounded identity/status/cumulative-fill diagnostics and explicit execution/asset-fee balance projections plus chronological fee-reserved funding audits with equal-time ambiguity and nonterminal-reservation review using repeatable fixtures; a bounded account-scoped durable request/uncertain-attempt/observation journal with replay validation and crash/lost-ack fixtures, persisted lookup-attempt limits, shared cooldowns and conservative timeout/not-found/rate-limit/authentication outcome handling, read-only pending-ticket review and explicit current-head-fenced lookup-interruption records preserving attempts and order uncertainty; an opt-in fixed-host GET-only paper account inspection primitive with bounded reads, redirect rejection, clock/permission fencing, stable account identity and conservative observed cash/BTC checks, explicit Windows credential pair storage/removal with verified readback, local mutation fencing and bounded same-origin Portal controls for separately confirmed one-time inspection; an explicit original-client-ID recovery coordinator sharing the bounded reader, persisting attempts before IO and normalized outcomes under post-lock clock/permission checks, with saved-key mutation fencing and crash/uncertainty retention; a bounded supplied Trading REST BTC/USD activity adapter with explicit fill prices/quantities, semantic deduplication, nanosecond time preservation and cumulative-chain checks, keeping received-asset fee debits unlinked and date semantics/coverage unverified; an internal explicitly confirmed two-page paper activity collector with canonical creation-window paths, exact opaque cursors, account/key/time fencing and explicit partial/end-observed reports, retaining no verified coverage or automatic continuation; optional account-bound local activity retention with canonical record reuse, immutable order bindings, bounded full replay, atomic crash/lost-return recovery and exact post-commit main-file flush verification, read-only cross-batch fill-chain review with snapshot conflict/revision checks and declared-window gap diagnostics; an optional explicit local paper-read reservation journal shared across inspection, activity collection and recovery, retaining cooldown/rate/auth/interruption blocks across cooperating processes and holding local OS ownership through read cleanup, with explicit account/head/token-fenced interruption review preserving unknown outcomes and full-day cooldowns; a pure combined supplied balance review compares explicit linked fills/fees with total cash/BTC while separately checking availability/open IOC terms without inferring provider holds or spending permission; optional collection observations preserve bounded page identities, opaque cursor chains, read timing and normalized-content/query digests within atomic activity receipts, without attesting source or coverage; an exact-account/head-bound retained gross-fill and total-balance residual review preserves nanosecond groups, rejects stale/concurrently changed history and leaves all fee links/posting scope/funding unverified; the explicit paper inspector and Accounts display now separate reported cash, non-margin buying power, total/available BTC and unknown/unavailable differences with normalized fingerprints/sampled timing, fail-fast malformed/foreign collections and no non-USD cash projection; portable allowlisted JSON observations now support explicit local download and bounded offline content-consistency validation without source attestation; a read-only portable-observation pair review now binds account/manifests/history head, checks sequential read windows for retained boundary fills and conditionally compares total balances without rounding nanoseconds or promoting source/funds authority; the existing confirmed inspector and Accounts readout also summarize bounded simple IOC buy-limit principal and sell remainders, deduplicate matching terms, preserve nanosecond identities and leave unsupported/conflicting aggregates unknown without claiming holds; validated with injected responses only, with order adapters still disconnected; an explicit source-only Windows saved-pair acceptance probe composes the account-ID-bound four-GET inspector with caller-selected durable local pacing, vault-silent pacing status and a content-bound allowlisted open-IOC aggregate from the same read, without granting provider/account/fee/hold/funding/live acceptance; Accounts now offers separately confirmed managed paced reads and vault-silent retained pacing review, with account-hashed private OS-profile storage and no automatic retry/reset; pacing, activity and request journals now preflight regular single-link files, bounded headers and DELETE-mode history, rejecting WAL layouts and sidecars before SQLite without conversion or evidence deletion; private cross-journal status replays every accepted receipt and exact successful pacing/activity head transition, separating latest-tail from historical-content disagreement without automatic repair; a read-only reconciliation composition requires no unverified legacy baseline and exactly one accepted record per retained pacing session, activity batch and collection observation, binds two portable observation manifests to that exact retained activity head and requires an exact nanosecond union of accepted declared creation windows to cover the full observation envelope before arithmetic, then rechecks all journals without promoting source, provider boundaries, received coverage, funds or routing authority.','Alpaca paper first after explicit paper-connection authorization; actual-account original-ID recovery acceptance, broker-wide pacing, account/asset eligibility and asset-denominated fee/position reconciliation. IBKR paper then Coinbase Advanced expansion.'),
    Milestone('M9','Release acceptance and authorization','OPEN','0.x numbering and release gates enforced; read-only retained forward coverage review revalidates recorded eligible quotes under the admitted policy before measuring a requested72-hour/95-percent target using clipped, unioned same-session public-labeled eligible intervals, excludes injection/gaps and leaves sustained-market/account acceptance unverified; repeatable source-fingerprinted offline trial suite covers 20 forward sessions, 24 operation rounds, backup/restore, durable capacity and real loopback HTTP startup/shutdown; fresh-install stop inspection avoids accounting initialization; bounded origin-first optional-AI credential controls use fixed errors, exact backend binding and verified save/remove readback. Public-repository hardening adds checksum-pinned full reachable-history secret/artifact scans, immutable Action references, nonpersistent checkout credentials, dependency updates/code ownership, a global loopback/Host/Origin boundary, no-store API responses and password-encrypted observation-copy tooling and explicit encrypted-input offline pair reviews without decrypted files; admin settings, historical logs/artifacts, default-branch policy adoption and native disk/ACL acceptance remain separate. Saved trial evidence is revalidated against every required profile and the exact current source; distribution checksums bind the bundled offline report as well as binaries, without attesting CI or approving release; real loopback HTTP trials now exercise paced paper reads with injected vault/transport seams, concurrent key removal, revoked-result suppression, retained cooldown/pending state and vault/network-silent fresh-process replay. Mandatory acceptance recovery observes a real pre-commit rollback sidecar, fingerprints all three main journals plus rollback/WAL/shared-memory sidecars and re-derives every accepted collection scope/account fields/observation/canonical activity input plus exact pacing/activity head transition from its retained receipt across fresh-process status. Release-run failures expose only allowlisted profile-specific child/timeout codes while suppressing child stderr, exception text, paths and environment data. See RELEASE_ACCEPTANCE.md for scope and unverified gates.','Independent security/risk review, operator runbook, complete acceptance evidence and explicit owner approval for 1.0. Live-money activation requires separate authorization.'),
)

# Advance the recovery evidence without rewriting the long-lived M1 history.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence.replace(
        'enforce fixed source/count/aggregate/directory/free-space admission and remove failed candidate files',
        'enforce fixed source/count/aggregate/directory/free-space admission, flush completed content, sync the directory entry where supported and require a stable read-only SQLite quick-check plus complete local hash-chain/accounting reconstruction before issuing a receipt, then remove failed candidate files')
    if item.id=='M1' else item.evidence,item.remaining) for item in MILESTONES)

# Keep the long-lived M8 history readable while advancing its current boundary.
# Generated/public roadmap text must not continue to call every order adapter
# disconnected now that the internal single-attempt paper-only boundary exists.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence.replace(
        'validated with injected responses only, with order adapters still disconnected;',
        'an internal explicitly confirmed single-attempt Alpaca paper POST boundary now checks fixed account/position/asset endpoints, requires an exact-head shared-local provider POST budget, commits both reservations before IO, rechecks permission/stop/credentials after the marker and never retries; a separate explicit reviewed recovery boundary clears only an ambiguous shared-budget latch after two exact-head replays prove the original client ID has a retained terminal observation, while preserving the full cooldown/history and permanently attempted request; a bounded read-only exact-head history composition now joins every completed POST attempt to the checked submission marker in up to seven account request journals, requires RESPONSE_OBSERVED to retain a request snapshot and NOT_SENT to retain none while preserving OUTCOME_UNKNOWN uncertainty, and refuses pending/interrupted, omitted, stale, changing, legacy, unbound, duplicate or related-path evidence; a second bounded read-only composition joins those exact POST attempts through the same request heads to complete accepted activity histories, requiring observed and recovered attempts to match retained order/fill evidence, NOT_SENT to retain no observation/fills and unresolved unknown outcomes to remain blocked; a single-account private exact-head composition now joins that POST/activity result to complete request/fill replay and four ordered stability-guarded balance observations, rechecking every journal and retaining gross-only residuals as non-authoritative diagnostics; a stricter fee-bearing composition additionally partitions every accepted receipt and requires every retained fee record to carry an explicit order/execution reference pair resolving to an accepted original fill while retaining reference semantics, date-only timing, fee coverage, posting allocation, holds and funds as unverified; a further known-fee reserve composition requires every accepted order to participate, reserves only retained explicitly referenced fee obligations at their accepted fill groups, rejects unfunded group debits and requires the local ending totals to match supplied closing inventory without promoting completeness, posting, holds or spendable-funds authority; validated with injected responses only, with no Portal, saved-write-credential or strategy route;'),
    ('Accept the internal paper POST boundary, shared-local provider budget and terminal original-ID recovery against an authorized account; add separately scoped saved write credentials, broker-wide/distributed POST coordination and explicit operator controls only after review; complete actual execution/fee/hold/funds reconciliation and sustained acceptance. IBKR paper then Coinbase Advanced expansion.'
     if item.id=='M8' else item.remaining)) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a no-network supplied Activity SSE schema review now fail-closes to BTC/USD TRD fills and one-to-one USD fees, requires strict ULID cursor order and unique stable ref IDs, binds each admitted fee parent_id to a supplied fill, and rejects corrections, busts, bulk fees, FX and unsupported financial activities without claiming stream entitlement, delivery, replay coverage or provider source' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Retained request-bound quote evidence now crosses the same explicit commit
# boundary as the broker activity journals that later reconcile against it.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every acknowledged request-bound paper quote receipt '
        'now binds committed SQLite connection bytes to one stable bounded '
        'main-file fingerprint, hashes the exact writable descriptor before '
        'and after explicit fsync and flushes a newly created exact POSIX '
        'parent entry; flush uncertainty retains one exact receipt for '
        'no-provider duplicate replay without source, coverage, routing or '
        'release authority' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Require durable Activity SSE checkpoint and reconnect acknowledgements to
# cross the same explicit main-file flush boundary as the paper journals they
# coordinate. Lost acknowledgement remains explicit replay, never retry.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every acknowledged Activity SSE cursor checkpoint, '
        'reconnect reservation and reconnect result now binds committed '
        'SQLite connection bytes to one stable bounded main file, hashes the '
        'exact writable descriptor before and after explicit fsync and flushes '
        'a newly created exact POSIX parent entry; flush uncertainty retains '
        'the checkpoint or attempt for exact no-network replay while keeping '
        'provider-request, automatic-retry, routing, funds and release '
        'authority false' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Sustained-trial session and quote evidence must cross an explicit commit
# boundary even though the injected profile remains non-authoritative.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every acknowledged forward-session or retained-quote '
        'evidence commit now binds the exact SQLite main connection and '
        'committed bytes to a bounded single-link main file, hashes the exact '
        'writable descriptor before and after explicit fsync and flushes a '
        'new exact POSIX parent entry; flush uncertainty stops the runner, '
        'releases ownership and leaves the retained session requiring review '
        'without granting sustained-market or release acceptance'
        if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a supplied two-segment reconnect review now requires the exact last processed ULID as since_id, idempotently deduplicates immutable ref_id redelivery, advances only on later event IDs and rechecks fee-parent links, while durable cursor storage, automatic backoff, real reconnect and complete replay remain explicitly unverified' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a bounded supplied-wire Activity SSE session review now binds the exact v2beta1 since_id target, text/event-stream metadata, arbitrary byte chunks, strict UTF-8/SSE/JSON framing, heartbeats and the existing linked trade/fee envelope review, while slow-consumer drops and internal-error comments require reconnect review and remain ineligible for cursor checkpoint authority' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; an explicitly confirmed single-attempt paper Activity SSE transport now opens only the exact since_id target, disables proxies and redirects, validates streaming metadata before reading, fences every bounded read with permission, emergency-stop, credential and clock checks, and passes a complete two-event segment through the strict wire review without retry, reconnect, cursor mutation or authority' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; an explicitly confirmed one-attempt Activity SSE checkpoint coordinator now binds a caller-supplied private prior segment and the exact cursor-journal head to one fixed-target resumption GET, re-proves immutable duplicate redelivery plus forward advancement, rejects interruption comments and commits at most one crash-recoverable cursor transition while retaining no automatic retry/backoff, ledger, routing or funds authority' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a private account-bound reconnect budget now reserves every explicitly confirmed SSE attempt before IO, enforces a fixed 1/2/4/8-second process-recoverable backoff with a four-attempt cap, blocks pending/lost-result episodes and composes one due attempt with the exact cursor checkpoint while never sleeping, looping or retrying automatically' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a reservation-bound cursor-checkpoint receipt and separately confirmed no-network result reconciliation now close only the exact pending reconnect attempt after a checkpoint/result crash window, reject legacy/foreign/stale evidence and make lost recovery acknowledgment replay idempotent without another provider request' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; an exact-head, doubly confirmed no-network uncertainty close now permanently retires a reconnect reservation whose provider outcome could not be durably timestamped, retains the consumed attempt and review requirement, schedules no retry and makes exact lost-close acknowledgement replay byte-stable' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; after a failed reconnect attempt with no cursor transition, outcome-clock failure now durably closes from the last trusted admission time as terminal review state, recovering a lost result acknowledgement by inspection and never creating retry authority; successful or cursor-advanced attempts remain pending for exact checkpoint reconciliation' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; successful reconnect completion and explicit no-network checkpoint reconciliation now recover a lost result acknowledgement in the same call only when the current terminal result exactly binds the reservation token, requested and final success values, zero retry fields and checkpoint receipt; a new write also binds the supplied result time, while an uncommitted or mismatched result remains uncertain without another provider request' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; failed reconnect results now apply the same exact same-call acknowledgement recovery to retry, review, checkpoint-uncertain, provider-delay and emergency-stop-converted outcomes, requiring the current result head, reservation token, result time, requested/final values, zero checkpoint fields and exact delay state; uncommitted or mismatched results remain pending and the original provider failure is suppressed only by a fixed uncertainty diagnostic' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; automatic closure after a failed reconnect whose completion clock is unavailable, invalid or regressing now applies the same exact current-result proof, including result-head identity, reservation token, trusted admission time, requested/final review outcome, absent checkpoint fields and zero delay state; failed or malformed recovery inspection returns a fixed uncertainty diagnostic without exposing private faults or repeating provider IO' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; post-attempt cursor inspection is now a fixed fail-closed boundary: unreadable, malformed or invalid cursor state returns a sanitized cursor-uncertainty diagnostic, retains the exact pending reconnect reservation with no result or retry due, and performs no second provider request; only one valid digest head may decide unchanged versus advanced checkpoint state' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; explicit checkpoint reconciliation now treats every post-result reconnect-budget inspection as an untrusted crash-recovery boundary: lost acknowledgements, unreadable storage and malformed retained views produce only fixed recovery uncertainty, preserve any committed terminal result and never repeat provider IO; exact completion still requires stable result-head, reservation, timestamp and checkpoint receipt agreement' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; explicit checkpoint reconciliation now also sanitizes its initial reconnect-budget and cursor-binding preflight views before any recovery write: unreadable or malformed budget state returns fixed recovery-state uncertainty, unreadable or malformed cursor binding returns fixed binding-review failure, and neither path mutates the pending reservation or repeats provider IO' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; reconnect admission now treats its initial cursor inspection as the same fail-closed boundary used after an attempt: unreadable, malformed or invalid state returns fixed cursor-state uncertainty before reservation or provider IO, leaving both stores untouched and granting no retry authority' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; reconnect admission now also validates the initial reconnect-budget view before reservation: unreadable storage, missing fields, invalid digests, clocks, counters, flags or reasons return fixed admission-state uncertainty, expose no private fault, leave both stores untouched and perform no provider IO' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; reconnect reservation now treats any non-policy exception as a fail-closed acknowledgement boundary: failures before commit and lost acknowledgements after commit return fixed reservation-state uncertainty, never start provider IO, and preserve any durably pending reservation with no result or retry due for explicit review' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; reconnect reservation acknowledgements are now admitted only when their token/head, cursor binding, bounded attempt and exact boolean replay/authorization fields form one internally consistent result; missing, malformed or conflicting fields return fixed reservation-state uncertainty before provider IO and leave any committed reservation pending for explicit review' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a structurally valid reconnect reservation acknowledgement now receives an independent exact durable-journal proof before provider IO: the pending token/current head, attempt, admitted cursor, prior budget head, evaluation time, reason and eligible prior retry state must agree; forged uncommitted returns, corrupted fields and unreadable post-commit state fail with fixed reservation uncertainty and leave any real reservation pending without request authority' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the exact reconnect reservation is now re-proved at every downstream permission fence, including the transport checkpoint immediately before opening the provider request; a concurrent result, changed token or unreadable journal revokes stale request authority with fixed reservation uncertainty, preserves the current durable owner state and performs no provider IO' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the explicit local emergency-stop abort, retry-wait close and outcome-uncertainty close now pin the exact reconnect-budget path/account binding across every callback, replay, transaction and result acknowledgement, returning fixed store-binding uncertainty before a retargeted store can be inspected or written' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; reconnect-budget admission and terminal-result persistence now pin their own exact path/account binding across initial replay, both emergency-stop samples, exact-head transactions and final acknowledgement inspection, rolling back a retargeted write and preserving a replayable schema-only fresh journal' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the reconnect-budget public inspection boundary now pins its exact path/account identity across file preflight, database connection and full journal replay, returning fixed store-binding uncertainty rather than a mixed-scope view if any internal seam retargets the mutable store' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; terminal reconnect-budget acknowledgements are now treated symmetrically whether lost by exception or returned malformed: success, failed-attempt scheduling, trusted-time uncertainty closure and explicit no-network checkpoint reconciliation cold-replay the exact durable result before continuing, while uncommitted or mismatched state remains fixed uncertainty and no additional provider request is granted' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; no-network outcome-uncertainty and emergency-stop closures now also discard structurally returned acknowledgements and accept only an exact cold-replayed terminal journal record, while an uncommitted return or changed file remains fixed uncertainty' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; those no-network closures now recognize their production-prefixed fixed acknowledgement-uncertainty diagnostics before exact recovery, while unrelated typed denials remain authoritative' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the private three-journal observation reconciliation now samples bounded no-follow SHA-256 fingerprints for the exact pacing, activity and acceptance SQLite files before and after semantic replay, rejecting same-head byte rewrites, identity/metadata changes and rollback/WAL/shared-memory sidecars without exposing paths or private digests' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the higher account-evidence composition extends that stability guard to its request journal and spans pinned, explicitly sampled current-head and four-observation reviews, so all four exact local files must remain byte, identity and metadata stable around the composed result' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the internal single-attempt paper POST boundary now requires two identical normalized account and BTC-position admission samples around the asset read, so any late permission, pending-funds/fee, cash/buying-capacity or BTC quantity/availability change fails before its durable reservations and only allowed POST' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every exceptional single-attempt POST-budget closure now ignores its write-side return and requires two stable read-only replays of the exact terminal token, account/client, request/payload heads, outcome and actual-POST count before preserving the original failure; lost acknowledgements recover locally, while missing, malformed or unstable closure state remains pending or review-blocked under a fixed uncertainty diagnostic without another provider request' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the single-attempt POST boundary also discards the provider-budget BEGIN acknowledgement and cold-replays the exact pending token, account/client hashes, request/payload heads and owner identity twice before creating the request marker; only the original in-process ownership lease may continue, while missing, malformed, unstable or replacement-owner state stops before provider IO under fixed reservation uncertainty' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider-budget and request-marker cold replay now also binds each durable admission timestamp to the caller-observed permission, emergency-stop and monotonic-clock window, retains the provider reservation admission in its replay receipt/history, and requires the marker admission to follow that reservation before any POST; out-of-window durable state remains pending or closes as NOT_SENT with no provider IO' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; terminal provider-budget cold replay now also binds RESPONSE_OBSERVED and the zero/one-POST NOT_SENT/OUTCOME_UNKNOWN safety closures to caller-witnessed completion windows; a safety closure uses the exact last trusted admission sample so revoked permission or an active emergency stop never needs to be relaxed merely to retire local state, while an out-of-window terminal row remains fixed uncertainty without another provider request' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; zero/one-POST safety closure now brackets its provider-budget terminal write with stable full-chain request-journal receipts, requiring the identical exact request head/account/payload afterward so a concurrent original-ID lookup advance yields only fixed uncertainty and never another provider request' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; successful response completion now also brackets the provider-budget terminal write with exact cold replay of the immutable OBSERVE prefix, so an invalidated response event or post-write semantic-reader failure yields fixed completion uncertainty without another provider request, while a later valid original-ID lookup remains permitted' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; resolved-unknown POST-budget recovery now ignores its write acknowledgement and requires two identical read-only receipts for the exact recovery head, blocked attempt, terminal request-resolution head and unchanged cooldown, so a lost acknowledgement is recovered without re-arming the original request while absent or fabricated durable state remains blocked' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Product milestones complement the nine technical evidence tracks above.
# These are implementation/acceptance states, never permissions to move money.
DELIVERY_STAGES = (
    dict(id='D1',title='Usable research application',status='FOUNDATION_IMPLEMENTED',
         progress='Windows packaging, seven workspaces, beginner help, Matrix views and engine/throttle controls exist. New unused installations receive recommended local paper defaults; To Do explains Start Portal, separate Engage, each check’s purpose and optional/future scope.',
         remaining='Clean-machine/native-window/profile and upgrade acceptance; keep startup and recovery dependable.',depends_on=[]),
    dict(id='D2',title='Learning models with defensible strategy evidence',status='PARTIAL',
         progress='Deduplicated local history, bounded public collection, trained numerical council, held-out and modeled cost checks exist.',
         remaining='Independent provenance, calibration and sustained forward evaluation; verify net results after costs and drawdowns before governed policy promotion.',depends_on=['D1']),
    dict(id='D3',title='Autonomous paper trading',status='EXPERIMENTAL_IMPLEMENTED',
         progress='Explicit Engage Paper runs the fixed BTC experiment with virtual funds, tier gates, durable accounting, recovery and an independent emergency cut.',
         remaining='Sustained real-feed reliability, incident evidence and reviewed strategy quality. Current council models do not direct orders.',depends_on=['D1','D2']),
    dict(id='D4',title='Connected broker paper trading',status='OPEN',
         progress='Disconnected Alpaca paper request/status/fee fixtures, durable account-scoped request recovery and retained lookup pacing/outcome diagnostics and retrospective fee-reserved funding checks and a separately opt-in paper account inspector with Windows credential onboarding and explicit read-only Portal controls and an internal original-ID recovery coordinator bound to retained request terms and a supplied REST fill/fee-debit normalizer with an internal bounded two-page collector and optional account-bound replay-validated local activity retention with cross-batch snapshot/fill/window review and unverified fee links plus optional durable shared local read reservations and owned interruption review plus combined supplied balance/availability/open-term diagnostics and retained collection observations plus head-bound nanosecond gross-fill/residual diagnostics and beginner-facing separated inventory observations, private portable report download/offline validation, head-bound observation/activity comparison, reported open-IOC terms and a source-only paced private-activity acceptance record with replay-validated cross-journal bindings, explicit unbound/latest-content/historical-content disagreement recovery classification and mandatory before/after-commit process-death release evidence that fingerprints the complete cooperating SQLite file set and re-derives every accepted content record and exact pacing/activity transition from its retained receipt, plus an exact-head portable-observation reconciliation composition with before/after journal replay and a caller-driven durable four-attempt Activity SSE reconnect budget with fixed backoff, exist. An internal explicitly confirmed paper-only single-attempt POST boundary now requires two stable account/BTC-position admission samples and durable local reservations, but has no Portal, saved-write-credential or strategy route; injected tests do not attest an account.',
         remaining='Actual Windows credential/profile acceptance, authorized paper account setup, authenticated inspection acceptance, scoped-secret review, actual-account recovery/outage evidence, actual-account collection acceptance, durable activity coverage and verified execution/fee associations, broker-wide rate limits and cash/position/hold reconciliation.',depends_on=['D3']),
    dict(id='D5',title='Secure deposits and available-funds verification',status='NOT_IMPLEMENTED',
         progress='Funding is preview-only. No bank link, deposit, withdrawal or real balance access exists.',
         remaining='Use a selected provider-held account and reviewed hosted funding workflow; isolate credentials, never collect raw banking credentials, reconcile pending/settled transfers and spendable cash, verify failure/reversal and duplicate handling. No app custody or automatic transfers.',depends_on=['D4']),
    dict(id='D6',title='Governed autonomous live trading',status='BLOCKED',
         progress='Real-money execution remains disabled; research and simulated balances cannot authorize spending.',
         remaining='Validated and explicitly approved policy, live provider integration, reconciled spendable funds, hard exposure/loss/order caps, pacing, monitoring, emergency cut and independently reviewed limited rollout. Separate owner authorization is mandatory.',depends_on=['D2','D3','D4','D5']),
    dict(id='D7',title='Secure computer-off operation and shared Matrix hub',status='PLANNED',
         progress='Local capped data/model stores and process ownership exist; cloud deployment and instance synchronization do not.',
         remaining='Select an eligible free-tier host without automatic billing; authenticated controls, encryption/key recovery, durable shared state, distributed fencing, quotas, remote stop and multi-day computer-off paper tests.',depends_on=['D3','D4']),
    dict(id='D8',title='Speed, resilience and independent acceptance',status='PARTIAL',
         progress='Durable latency/queue fixtures, crash/recovery tests, Windows workflows, shared public-quote intake diagnostics, actual ASGI emergency-control tests under saturated general workers and repeatable bounded process/HTTP/restore/capacity trials with source fingerprints, reachable-history publication checks and opt-in authenticated encrypted observation copies and explicit in-memory encrypted pair reviews exist.',
         remaining='Measure full feed-to-decision-to-broker path, sustained load/outages, long-run storage, security/risk review and documented operations; fixture speed is not exchange fill speed.',depends_on=['D2','D3','D4']),
    dict(id='D9',title='1.0 production release',status='BLOCKED',
         progress='The project remains a 0.x prerelease with source-controlled acceptance gates.',
         remaining='All agreed production workflows, including secure funding and governed autonomous execution, must be implemented and accepted; complete testing, reconciliation, security/risk review, recovery/runbooks, cloud acceptance for the agreed scope and explicit owner release approval. A release never activates live trading by itself.',depends_on=['D1','D2','D3','D4','D5','D6','D7','D8']),
)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'].replace(
        'injected tests do not attest an account and no external order adapter is connected.',
        'an internal single-attempt paper POST boundary with an exact-head shared-local provider budget and bounded cross-journal marker/outcome/recovery history review now exists with injected tests only; terminal recovery events must bind back to the exact request prefix retaining the matching terminal original-ID observation, but it has no saved write credential, Portal or strategy route and does not attest an account or broker-wide quota.'),
    remaining='Authorized-account acceptance of the single-attempt boundary and shared-local budget, separately scoped saved write credentials, explicit operator integration, broker-wide/distributed POST coordination, authenticated recovery/collection, durable activity coverage, verified execution/fee associations and cash/position/hold/funds reconciliation.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; linked durable paper accounts now protect one slot inside the existing fixed intent-ID limit from ordinary intake, while an internal replayable reducing-exit command may consume that slot only after binding an exact current product, quantity and expected position; cold replay, duplicate conflict, ordinary-route denial and account-change fences preserve the total cap' if item.id=='M5' else
        '; the paper-forward runner now admits entries only when both the ordinary-ID slice and the protected reducing-exit slice are available, and binds every reserved SELL to the exact reconciled holding before its modeled fill; public simulation requests cannot consume or invoke that exit slice' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Linked local-paper accounts now reserve one existing durable intent slot from ordinary intake and expose separate total, ordinary and reducing-exit headroom. The internal reducing command binds the exact SELL product, requested reduction and expected current holding in replayed history, rechecks position continuity at fill, and never raises the total ID limit. Public simulation, BUY, wrong-product, flat, unlinked, oversized, stale-position, halted and cut paths cannot consume the slot. This is bounded local exit-capacity hardening, not broker routing, market realism, provider/account acceptance, funding/live/cloud readiness or permission.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; persisted counterfactual broker-intent reconciliation now brackets its exact request-head check with two fresh durable session-order replays, requires both to equal the reviewed non-halted order snapshot, and leaves the order journal unlocked so an emergency halt can proceed independently and invalidate the result' if item.id in ('M4','M8') else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + (' Persisted counterfactual broker-intent reconciliation now requires the reviewed non-halted order snapshot to survive fresh durable replay before and after the exact request-head check. No order-journal lock spans reconciliation, so an emergency halt remains independent and makes the retained review stale. This is bounded sequential local reconciliation evidence; the plan remains unsendable and no provider, account, funds, routing or release authority is granted.' if stage['id'] in ('D3','D4') else ''))
    if stage['id'] in ('D3','D4') else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the provider-POST reconciliation boundary now validates the retained observation\'s shared gross-balance and activity subreviews, binds both account/activity heads to the outer observation, replays the gross-balance scope hash and rejects nested authority promotion or removed replay/arithmetic assurances' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the bounded Activity SSE response scope now preserves the original fixed safety failure even when a hostile response context attempts to suppress it or raises an ordinary cleanup exception, while process-control signals remain independently visible; the durable reconnect budget records the one GET as non-retryable review with no cursor advance, retry or sleep' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; failed fresh activity-acceptance connection, pragma, transaction, schema and baseline writes now remove only their exact newly created empty sidecar-free pathname so one explicit bind retry can initialize cleanly, while replacement or close uncertainty remains retained and blocked' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Fresh private activity-acceptance initialization now captures the exclusive main-file identity before SQLite opens and restores absence after failed connection, pragma, transaction, schema or baseline writes only when the exact file is empty, regular, single-link and sidecar-free. Replacement, content, sidecar or close uncertainty retains the path and fails closed; retry is explicit, never automatic. This is sampled local initialization recovery, not provider identity, broker acceptance, sustained-market evidence, physical durability or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; activity-acceptance baseline and append writers now reject a substituted SQLite main connection before mutation, explicitly fsync the exact committed writable descriptor, recheck its bounded bytes and flush a new POSIX directory entry before cold acknowledgement replay' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Private activity-acceptance baseline and append writes now prove the writable SQLite main path before mutation, explicitly flush the exact committed main-file descriptor, require unchanged bounded descriptor/path content across that barrier and flush a newly created POSIX directory entry. Flush uncertainty retains the committed baseline or record and recovers only through explicit cold replay; no retry is automatic. This is sampled local durability evidence, not physical power-loss, portable Windows directory persistence, provider identity, authorized broker acceptance, sustained-market evidence or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; activity-acceptance append now pins its store binding and main-file content around every untrusted admission/input callback, derives the expected chain head independently and requires two exact cold reads before acknowledging a commit' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Private activity-acceptance append now brackets every admission/input callback with exact store-path and bounded main-file proofs, derives rather than trusts the write return, and requires two stable cold reads of the committed record before acknowledgement. A post-commit retarget loses the acknowledgement, retains the exact record and recovers only through explicit duplicate replay. This is sampled local reconciliation integrity, not provider identity, authorized broker acceptance, native durability, sustained-market evidence or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; private activity-acceptance inspection and ordered replay now pin one resolved store path, regular-file identity, bounded main-file fingerprint and exact serialized SQLite connection content while retaining pre-commit rollback-journal evidence for read-only recovery review' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Private cross-journal activity-acceptance inspection and replay now require one immutable resolved store binding plus exact bounded main-file and serialized-connection content before and after replay. Valid foreign, path-spoofed and retargeted stores fail closed, while an existing pre-commit rollback journal remains untouched and reviewable. Mandatory process-death evidence covers all three seams and replays the exact original two-transition history. This is sampled local reconciliation integrity, not provider identity, authorized broker acceptance, native durability, sustained-market evidence or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; retained provider/read receipt inspection now hashes the bounded serialized main-database image before and after replay and rejects a path-spoofed foreign SQLite connection whose bytes differ from the captured store fingerprint' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Retained provider/read receipt inspection now compares the bounded serialized SQLite main image with the exact pre-connect store size and SHA-256 on both sides of complete replay. A foreign valid database cannot pass by spoofing the expected connection-local pathname; it receives a fixed content-binding refusal, both files remain unchanged, and the original receipt later replays exactly. This is sampled local read-only integrity evidence, not provider identity, authorized broker acceptance, native durability, sustained-market evidence or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; retained provider/read receipt inspection now also proves that the opened SQLite connection has exactly one main database bound to the captured resolved store path, rejecting a valid foreign connection before replay' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Retained provider/read receipt inspection now binds the SQLite connection itself to exactly one main database at the captured resolved store path before and after complete replay. A substituted valid foreign connection is rejected with a fixed diagnostic while both stores remain unchanged. Disposable release evidence covers the connection seam. This is local read-only integrity evidence, not provider identity, authorized broker acceptance, native durability, sustained-market evidence or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider/read receipt inspection now pins one exact resolved store path, regular-file identity and bounded fingerprint across connection, complete replay, close and exceptional unwind, rejecting mutable-store retargeting and same-path mutation before returning any public receipt history' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Public provider/read receipt inspection now fixes one resolved store binding plus bounded identity/content sample before opening SQLite and rechecks it around connection, full replay, close and exceptional unwind. Retargeting or same-path mutation returns a fixed refusal without accepting a mixed-scope view. Disposable release evidence covers both faults. This is local read-only integrity evidence, not provider identity, authorized broker acceptance, native durability, sustained-market evidence or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; both reviewed provider POST and provider-read recovery commands now repeat their exact budget SQLite identity and committed fingerprint proof during return-expression and exception finalization while outer ownership remains held' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Both reviewed provider POST and provider-read recovery paths now recheck the exact budget SQLite identity and committed fingerprint as return finalization exits, before outer ownership releases. A late replacement or rollback overrides a lower-priority result and cannot become successful recovery, retry or reclaimed capacity. This remains sampled local storage evidence, not atomic descriptor binding, ACL/administrator protection, physical durability or provider/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; reviewed account-read pacing recovery now accepts a lost local write acknowledgement only after two exact cold receipts, while the account journal identity and committed fingerprint remain stable through return finalization' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Account-read pacing recovery now cold-replays the exact committed recovery twice and retains its journal identity/fingerprint proof through return finalization. Lost acknowledgement cannot duplicate recovery; replacement or rollback remains fixed uncertainty without reclaiming session capacity. This is local injected durability evidence, not broker/account acceptance, provider quota coordination or sustained market evidence.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the exact-head provider/account read-history composition now treats retained pending or review-blocked account pacing state as a first-class reconciliation blocker, so clearing only the shared provider budget can never promote a split recovery to consistent history' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Cross-journal provider/account read review now symmetrically refuses retained account pacing pending/review state as well as provider-budget blocks. A provider-only recovery remains inconsistent until the exact account-side block is also explicitly recovered; the complete histories and cooldown/session uncertainty stay retained. This is local injected reconciliation evidence, not broker/account acceptance, automatic recovery or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the exact-head provider/account split-recovery fixture now requires both explicit recovery orders, proving that whichever journal recovers first remains blocked by the untouched side until the second recovery completes' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Provider/account read reconciliation now carries mandatory order-invariance evidence: provider-first and account-first recovery each remain inconsistent after one side clears, and only the second explicit exact-head recovery restores local consistency. This is injected local recovery-order evidence, not automatic recovery, broker-wide coordination, provider acceptance or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; mandatory exact-head release evidence now proves provider-only, account-only and aligned dual-pending read histories all remain read-only review blockers and can never be promoted to matched completed sessions' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Provider/account read reconciliation now carries mandatory three-way pending-history evidence: provider-only, account-only and aligned dual-pending tails each remain inconsistent, report zero matched sessions and leave every journal byte unchanged. This is disposable local fail-closed evidence, not automatic recovery, provider IO, broker acceptance or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the exact-head release fixture now composes two account journals and proves that one pending account cannot hide behind a fully matched peer or yield partial matched-session credit' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Multi-account provider/read reconciliation now carries mandatory non-masking evidence: one pending account alongside a fully matched peer keeps the whole exact-head review inconsistent, retains both complete six-session histories for diagnosis, reports zero promoted matched sessions and changes no journal bytes. This is disposable local fail-closed evidence, not partial account acceptance, provider IO or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; mandatory multi-account reader-failure evidence now proves that one account semantic reader cannot mutate a peer journal and hide that change behind its own exception' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Exact-head multi-account provider/read review now carries a mandatory cross-account exceptional-exit proof: if one account reader changes a selected peer journal and then raises, the outer composition reports the fixed file-set refusal and never returns partial consistency. Journal bytes remain unchanged. This is disposable local integrity evidence, not provider identity, broker acceptance or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; multi-account read reconciliation now requires every selected account independently to retain all five read purposes, so a complete peer cannot mask another account with only a completed subset or earn partial matched-session credit' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Multi-account provider/read reconciliation now evaluates five-purpose completeness per selected account instead of across the aggregate. A fully covered peer cannot mask another account with only a completed subset; the whole review remains inconsistent, reports zero matched sessions and changes no journal bytes. This is disposable local fail-closed evidence, not partial account acceptance, provider IO or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; durable provider/read review retention now pins the provider budget and every selected account journal to one identity and bounded fingerprint set across all semantic replays, callbacks, commit acknowledgement, exception unwind and final return' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Durable provider/read receipt retention now carries one end-to-end source-file proof across repeated semantic review, caller revocation callbacks, receipt commit acknowledgement, exception unwind and final return. A same-head source mutation receives the fixed outer file-set refusal and cannot create a successful receipt result. This remains sampled local integrity evidence, not atomic writer exclusion, provider identity, broker acceptance or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; durable provider/read review receipt retention now ignores its write-side acknowledgement, accepts an exact lost acknowledgement only after two stable cold receipts and pins the committed receipt-store identity and bounded fingerprint through postcommit callbacks and final return' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Durable provider/read receipt retention now derives its result from two stable exact store replays instead of trusting the write return. A lost acknowledgement recovers one committed receipt; a fabricated return remains fixed durable-state uncertainty. The committed store identity and bounded fingerprint are rechecked through callbacks, exception unwind and return finalization. This is sampled local durability evidence, not physical durability, provider identity, broker acceptance or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' An explicit one-attempt Activity SSE coordinator now composes the fixed-target transport, private prior segment, strict reconnect review and exact-head cursor journal into one fail-closed local checkpoint; injected tests only.',
    remaining='Accept this exact-head reconnect/checkpoint path on an authorized paper account and native private volume; verify account entitlement, provider source/delivery and replay coverage, slow-consumer recovery and broker-wide/distributed coordination before any automatic reconnect loop. ' + stage['remaining'])
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The paper POST preflight requires stable pre/post-reservation risk samples and now retains exact-path/account/provider-bound request and POST-budget store instances across all admission callbacks; a caller-object retarget fails closed, sends nothing and cannot write a decoy store. This remains local injected evidence, not broker-wide coordination or accepted provider state.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' After both paper POST reservations, every remaining admission and transport checkpoint now replays the exact request and provider-budget tails twice and requires the checked marker head, pending budget token and immutable request state to remain unchanged until the sole POST begins. A same-file head advance fails closed before provider write. This is sampled local replay, not multi-file atomicity or provider acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' A returned paper POST response is appended only by an atomic exact-marker-head and payload check, and provider-budget completion binds the exact committed observation head. Concurrent original-ID lookup cannot be silently composed with the response or substituted as its budget reference. This remains local injected evidence, not broker idempotency, provider acceptance or market evidence.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Provider-budget response completion is now accepted only after stable exact cold replay of the committed token, account, client order, request/payload/observation heads, outcome and one-POST count. A lost local completion acknowledgement never repeats provider IO; an uncommitted or malformed completion remains pending and review-blocked. This is local injected durability evidence, not provider acceptance or broker-wide coordination.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Request response observation now has a read-only exact-event receipt that validates the complete current chain and replays the prefix through the OBSERVE event immediately following the submission marker. Stable cold replay recovers a lost local observation acknowledgement without another provider POST; missing, different or uncommitted events retain OUTCOME_UNKNOWN review. This is local injected durability evidence, not provider acceptance or broker-wide coordination.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Failure-path provider-budget closure now receives the same exact cold-replay treatment as successful response completion: the intended NOT_SENT or OUTCOME_UNKNOWN terminal row must bind the precise reservation and actual POST count before the original failure is preserved. Lost acknowledgements do not repeat provider IO; unproved closure returns fixed uncertainty. This remains local injected durability evidence, not broker or account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The paper request-marker write acknowledgement is now ignored. Two stable full-chain reads must prove the marker is the current exact child of the prepared request head, with the expected payload and canonical admission record, before provider IO can continue. A lost committed acknowledgement proceeds once; fabricated, uncommitted, unstable or later-advanced state sends nothing and closes the provider budget conservatively. This remains local injected durability evidence, not broker or account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Successful response completion and both zero/one-POST safety closures now retain and cold-replay completion times inside the exact caller-witnessed window. Safety closure uses the last trusted sample independently of current permission or emergency-stop state; a forged or out-of-window terminal row grants no recovery, resend or routing authority. This remains injected local evidence, not provider/account acceptance or market reconciliation.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Every zero/one-POST safety closure now requires stable request-journal receipts before its provider-budget terminal write and the identical receipt afterward. A concurrent original-ID lookup advance makes the cross-journal closure uncertain without retrying provider IO. This remains injected local evidence, not provider/account acceptance or market reconciliation.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Successful response completion now replays the exact immutable OBSERVE prefix both before and after the provider-budget terminal write. An invalidated response event or failed post-write semantic replay yields fixed completion uncertainty without retry; later valid original-ID lookup history remains allowed. This remains injected local evidence, not provider/account acceptance or market reconciliation.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Resolved-unknown provider POST recovery now cold-replays the exact committed recovery receipt after the write while request-writer and budget ownership remain held. Lost acknowledgements recover without a second recovery append; missing, fabricated or unreadable durable state remains fixed uncertainty. This remains injected local evidence, not provider/account acceptance or completed fee/hold/funds reconciliation.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; resolved-unknown POST recovery now samples and rechecks the exact provider-budget SQLite file identity across source replay, the guarded recovery write and both post-commit cold receipts, rejecting pathname replacement as fixed uncertainty' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The reviewed recovery command now pins the exact provider-budget file identity before reading source history and rechecks it before and after the recovery transaction and each cold receipt. A same-path replacement is rejected even if its retained bytes are otherwise valid. This is sampled local identity evidence, not an ACL, administrator-exclusion, physical durability or provider/account acceptance proof.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the same recovery now fingerprints the bounded provider-budget bytes before mutation and pins the newly committed fingerprint across both cold receipts, rejecting same-file content or metadata change as fixed uncertainty' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The reviewed recovery command now requires a stable bounded provider-budget file fingerprint throughout source replay and immediately before its guarded write, then captures the committed fingerprint and requires it to remain unchanged across both cold receipts. Same-inode content or metadata mutation is fixed uncertainty. This remains sampled local evidence, not an atomic descriptor transaction, ACL/administrator protection, physical durability or provider/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; after both recovery receipts the command replays the exact locked request proof again, so post-commit request content, metadata or head change remains fixed uncertainty rather than a successful cross-journal acknowledgement' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Resolved-unknown recovery now extends request-writer exclusion through both provider-budget cold receipts and then requires the original exact request proof once more. A post-commit request mutation returns fixed uncertainty after the conservative recovery commit; it never re-arms or resubmits the request. This remains sampled local evidence, not multi-file atomicity, ACL/administrator protection, physical durability or provider/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the reusable locked request-snapshot guard now rechecks exact identity and bounded content during context exit, including return-expression and exception unwind, so mutation after a caller\'s final replay cannot become a successful cross-journal result' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The reusable locked request-snapshot guard now verifies the exact request file again before releasing writer exclusion, covering caller return and exception-unwind seams. A late same-inode or pathname mutation receives fixed locked-snapshot uncertainty even when lower code failed, while rollback remains local and no request is re-armed. This remains sampled local evidence, not multi-file atomicity, ACL/administrator protection, physical durability or provider/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider POST ownership now rechecks that its lock pathname still names the opened regular single-link file before releasing the descriptor, so a late replacement cannot become a successful recovery return' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Provider POST ownership now validates the exact opened lock-file identity during context exit, including normal return and exception unwind. A late pathname replacement produces fixed ownership uncertainty after any conservative recovery commit and cannot grant retry or request authority. This remains a cooperating-process local fence, not broker-wide/distributed ownership, ACL/administrator protection, physical durability or provider/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider-ownership exit negative controls now use actual lock-path replacement where the operating system permits it and deterministic late identity failure on Windows, whose mandatory-share lock correctly rejects renaming an open file' if item.id=='M9' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Provider-ownership exit negative controls now preserve the same error-priority assertions across POSIX and Windows: POSIX exercises real pathname replacement, while Windows injects the equivalent late identity failure because the kernel denies renaming the open locked file. This is test portability for the local guard, not native owner-profile, broker or independent acceptance.')
    if stage['id']=='D9' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; account-read and provider-read ownership now recheck their exact opened regular single-link lock file during context exit, while all three read/POST guards normalize an unavailable lock path to fixed ownership uncertainty' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Account-read and shared provider-read ownership now retain their local lock proof through context finalization, matching the POST guard. Late replacement, disappearance or inaccessible-path sampling yields fixed ownership uncertainty on normal return and exception unwind; all descriptors are still closed. This is cooperating-process local hardening, not broker-wide/distributed ownership, native ACL acceptance or provider/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; explicit reviewed provider-read budget recovery now ignores its append acknowledgement and requires two identical read-only receipts binding the exact recovery head, account digest, blocked session/outcome, recovery count, unchanged cooldown and unreclaimed capacity while ownership remains held' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Reviewed provider-read budget recovery now accepts a lost local append acknowledgement only after two identical cold receipts prove the exact committed recovery and conservative state while provider ownership remains held. Uncommitted, fabricated, changed or unreadable state stays blocked; history, cooldown, uncertainty and consumed session capacity are preserved. This is injected local durability evidence, not provider/account acceptance, broker-wide coordination, fee/hold/funds reconciliation or market evidence.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider-read budget recovery now pins the existing SQLite pathname identity and bounded fingerprint through source replay and mutation, then requires the committed fingerprint around both cold recovery receipts' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Provider-read budget recovery now rejects same-path replacement and same-inode byte or metadata mutation across source replay, its guarded recovery write and both cold receipts. The committed recovery may remain conservative, but mutated or rolled-back state cannot become success or reclaim capacity. This is sampled local storage evidence, not atomic descriptor binding, ACL/administrator protection, physical durability or provider/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a failed first provider/read receipt transaction now removes only its exact newly created empty SQLite pathname after rollback, rejects replacement or sidecar ambiguity and restores absent state for one explicit exact retry' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' A failed first durable provider/read receipt transaction no longer leaves a schema-less SQLite pathname that poisons an exact operator retry. Cleanup is limited to the captured new identity after rollback and only when the main file is regular, single-link and empty with no SQLite sidecars; any replacement or uncertain state remains retained and blocked. This is sampled local cleanup and retry evidence, not physical durability, broker/account acceptance or automatic retry authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; active provider/read receipt-store fingerprint sampling now uses the same Windows-portable pathname/open-handle boundary as the quiescent guard, excluding nonportable Windows ctime while retaining identity, size, mtime, link and bounded-byte checks' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Active receipt-store transaction sampling now compares the portable Windows intersection of pathname and open-handle metadata rather than treating their different ctime precision as mutation. Identity, size, mtime, link count and bounded bytes remain checked. This repairs a five-test native Windows regression; fresh exact-source packaging is still required and not inferred from the fix.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; fresh provider/read receipt initialization now captures the exclusively created pathname identity before SQLite opens and restores absence after connection, pragma, transaction-begin or schema-creation failure only when the exact empty file has no sidecars' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Fresh receipt-store initialization now applies the same narrow rollback cleanup to failures before append receives a database connection. The exclusive file identity is captured from its open descriptor; connection, pragma, transaction-begin and schema failures close SQLite first and remove only that exact empty sidecar-free pathname. Ambiguity remains retained and blocked. This is sampled local initialization recovery, not physical durability, automatic retry or broker/account acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; committed provider/read receipt stores now cross an explicit writable main-file fsync boundary on every accepted commit, plus parent-directory fsync for a newly created POSIX pathname; flush uncertainty retains the exact committed receipt for inspection and explicit duplicate replay' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Every committed provider/read receipt now receives an explicit writable main-file flush before its cold acknowledgement proof. A newly created POSIX pathname also receives parent-directory fsync; Python exposes no portable Windows directory fsync, so Windows retains the main-file barrier and native acceptance gate. Flush uncertainty preserves the committed receipt, fails closed and permits only an exact duplicate replay after inspection. This narrows local durability uncertainty but is not physical power-loss, broker/account or native-owner acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the committed provider/read receipt fingerprint is now fixed before its explicit main-file flush and rechecked before and after that flush plus any new POSIX parent-directory flush, so same-identity content or metadata change cannot become an accepted durability baseline' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The exact bounded committed receipt-store fingerprint is now captured before explicit file sync and required before and after the main-file barrier and after any new POSIX directory barrier. Same-inode byte or metadata change returns fixed durability uncertainty, retains the committed receipt and permits only explicit exact-head duplicate replay after inspection. This is sampled local integrity evidence, not physical power-loss, provider/account, native-owner or broker acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the exact writable provider/read receipt descriptor is now bounded-byte hashed before and after its explicit file sync and must match the already committed pathname fingerprint, so descriptor/path divergence cannot pass as a durable acknowledgement' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The exact O_RDWR receipt-store descriptor is now independently bounded-byte hashed before and after fsync and required to match the committed pathname fingerprint. Descriptor tampering is rejected before sync, the committed receipt remains retained and only explicit exact-head duplicate replay can recover after inspection. This is sampled local descriptor/path integrity evidence, not physical durability, broker acceptance or native-owner acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; new POSIX provider/read receipt stores now pin the resolved parent-directory identity before opening it, require the fsync descriptor and post-flush pathname to retain that exact device/inode, and reject wrong-directory substitution before directory sync' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' New POSIX receipt-store directory persistence now opens the resolved parent with no-follow semantics and requires the descriptor and post-flush pathname to match the captured directory identity. A substituted directory descriptor is rejected before directory fsync, the committed receipt remains retained and explicit exact-head duplicate replay remains the only recovery. Windows has no portable Python directory-fsync API and keeps its independent native gate. This is sampled local identity evidence, not physical durability or broker acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the mixed account-read review receipt now independently matches the provider-read receipt durability boundary: every committed exact writable descriptor is bounded-byte matched and fsynced, each new POSIX pathname flushes its exact pinned parent directory, and uncertainty retains exactly one receipt for explicit duplicate recovery' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The mixed account-read review receipt now has explicit file-sync parity with the provider-read receipt. Its exact O_RDWR descriptor must match the committed pathname fingerprint before and after fsync; a new POSIX entry also flushes only its pinned parent-directory descriptor. Flush, descriptor or directory uncertainty fails closed while preserving one committed receipt for inspection and explicit exact-head duplicate recovery. Mandatory pacing evidence now includes this account receipt boundary. This is sampled local durability handling, not physical power-loss, native-owner or broker acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; fresh mixed account-read receipt initialization now pins the exact exclusively created descriptor identity, removes only that unchanged empty file after an uncommitted setup failure, and retains any substituted path under fixed cleanup uncertainty' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Fresh mixed account-read receipt initialization now captures the device/inode identity from the exclusive create descriptor. An uncommitted connection or setup failure removes only that exact unchanged empty regular single-link file; a substituted empty pathname, sidecar, close uncertainty or any bytes are retained under fixed `UNCOMMITTED_CLEANUP_UNCERTAIN`. Explicit clean retry is proven after safe cleanup, and mandatory pacing evidence covers both recovery and replacement refusal. This is local initialization integrity, not broker, native-owner or physical-durability acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; an installed OFF-by-default Activity SSE operator now combines the saved read-only Windows credential pair, private retained initial segment, exact cursor/reconnect heads, bounded one-attempt budget and independent emergency-cut latch; status is vault/network/storage silent, every attempt needs three explicit confirmations, and aggregate output omits credentials and event/order identities' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The installed Activity SSE operator now exposes a local-only status and one explicitly confirmed saved-pair acceptance attempt. It binds a private retained segment and exact cursor/reconnect heads, samples the independent emergency-cut latch at every downstream safety checkpoint, makes at most one GET and one cursor checkpoint, and emits aggregate hashes/counts only. It remains OFF by default and grants no trading, funding, routing, source, delivery, reconciliation or release authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The installed Activity SSE attempt now validates its first wall-clock sample as an exact finite bounded number before credential, provider or journal access. Ordinary clock failures collapse to one fixed refusal without private detail; process-control signals remain independently observable. This is local fail-closed operator hardening, not provider/account acceptance, source or clock attestation, sustained-market evidence, reconciliation or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The saved-pair Activity SSE seam now samples exact permission and independent emergency-stop admission before opening the Windows credential vault. Revoked, indeterminate or ordinarily failing callbacks collapse to one fixed refusal with no credential read; process-control signals remain observable. This is local fail-closed secret/admission hardening, not provider/account acceptance, sustained-market evidence, reconciliation or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The installed Activity SSE attempt now requires a stable, repeated cursor/reconnect snapshot whose bounded file identities, fingerprints and exact heads match operator input before opening saved credentials. Invalid, stale or incoherent heads fail locally; the coordinator still rechecks after vault admission. This is sampled local preflight integrity, not an atomic multi-journal snapshot, provider/account acceptance, sustained-market evidence, reconciliation or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The installed Activity SSE operator now retains the validated resolved cursor, reconnect-budget and order-journal targets instead of caller-controlled path aliases. A mutable parent symlink can no longer redirect status or attempt storage after scope validation, hide an existing independent emergency latch or move later writes to another directory. This is sampled local path-scope hardening, not an ACL/administrator boundary, provider/account acceptance, sustained-market evidence, reconciliation or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The installed Activity SSE attempt now pins the validated regular retained-input target before identity/fingerprint capture and rejects a parent-alias change observed during pinning. A later parent-symlink redirect cannot silently substitute a different stable private event file, while leaf symlinks remain refused. This is sampled local input-scope hardening, not provider/source attestation, broker acceptance, sustained-market evidence, reconciliation or release approval.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; mandatory installed recovery evidence now carries one failed GET through durable backoff into one explicitly invoked due-time retry, a single completed checkpoint and vault/network-silent cold replay that denies a third attempt' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Mandatory installed Activity SSE recovery evidence now executes the full local failure-to-success lifecycle: one failed injected GET is retained, pre-deadline restart is denied, due-time status remains read-only, one separately explicit retry creates the only checkpoint, and a fresh process replays attempt two as completed without vault/network access or third-attempt eligibility. This is disposable local injected recovery evidence, not provider identity, delivery coverage, broker acceptance, native durability, reconciliation or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; mandatory installed secret-lifecycle evidence removes the saved pair after a failed GET, proves a due retry fails before network access without consuming its durable attempt, and requires explicit non-authoritative credential restoration before one confirmed completion' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Mandatory installed Activity SSE secret-lifecycle evidence now removes the saved credential pair after a failed GET and retained backoff. The due retry fails before any network request and leaves the exact cursor/reconnect snapshot eligible and unchanged; only explicit credential restoration plus a fresh confirmation can complete attempt two. Removal, restoration and completion grant no routing, funds, live or release authority. This is disposable local injected evidence, not provider identity, broker acceptance, native-vault durability, sustained-market coverage or reconciliation acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; mandatory installed in-flight credential-rotation evidence now proves one bounded GET is withheld from checkpoint authority, commits durable manual review and denies any second GET even after a valid replacement pair exists' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Mandatory installed Activity SSE in-flight rotation evidence now replaces the saved pair during response consumption. The old generation is revoked, exactly one bounded GET fails without a cursor checkpoint, the reconnect budget enters durable manual review, and a subsequent attempt is denied before network access despite the valid replacement pair. Cold review is vault/network/storage-write silent. This is disposable local injected evidence, not cross-process/native-vault proof, provider identity, broker acceptance, sustained-market coverage, reconciliation or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the independent emergency-stop process now durably latches after an in-flight credential rotation has already forced manual review, preserving the exact cursor/reconnect heads and denying another GET with replacement credentials' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Mandatory installed concurrency evidence now advances the durable Activity SSE cursor from an untrusted final control callback while leaving the reconnect reservation untouched. The dedicated pre-open fence detects the stale cursor, performs zero GETs and retains the pending reservation for explicit review. This is disposable local injected evidence, not native power-loss proof, provider identity, broker acceptance, sustained-market coverage, reconciliation or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; forward evidence snapshots now enforce their linked-ledger row cap before semantic reconciliation readers run, bind the read-only SQLite attachment to the exact preflighted regular-file identity throughout fingerprint and audit reads, bind each direct start-preflight SQLite read to one regular-file identity, content-metadata sample and SQLite-reported path, require all order/portfolio/forward/settings roles and SQLite companions to remain relationship-distinct, and hold storage-directory plus complete expected-file-set presence/metadata fences across the multi-file diagnostic before allowing its local result' if item.id=='M4' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; read-only start preflight now samples the actual cross-process forward-owner lock before replay, while lock acquisition and sampling reject symlink, hardlink, malformed and pathname-replaced lock stores without treating the sample as a reservation' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the forward worker now revalidates its exact opened regular single-link owner path through preparation, quote intake, submit/fill boundaries, diagnostic commits and release, while cancellation and the independent emergency cut remain available after ownership uncertainty' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; forward and research-refresh owners now also bind the physical directory that scopes their lock, reject a symlinked owner directory and fail closed if that directory is replaced even when the same single-link lock inode is moved back under the public path' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every active owner validation now brackets the public lock-path identity sample with the acquired directory identity, so replacement between those samples cannot acknowledge even when the same locked inode is moved into the replacement directory' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; research refresh now revalidates its acquired owner lease before collection requests, retained-data intake, training and model-reference publication, discarding work and pausing for review when the public owner path changes' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Forward evidence review brackets its separate quote/session snapshot with two exact order-and-linked-ledger reconciliation snapshots. Linked-ledger fingerprint/audit reads and direct settings/unused-portfolio start-preflight reads remain bound to their preflighted regular-file identities; direct reads also bind content metadata and SQLite-reported paths. All order, portfolio, forward and settings roles plus their SQLite companions must be relationship-distinct before recovery reads. Storage-directory identity and the complete expected SQLite file-set presence/identity/metadata span the entire multi-file diagnostic and recheck on success, early return and ordinary failure. Read-only preflight additionally samples whether a separate process owns the forward-worker lock, without creating or reserving it; unsafe lock-store identity fails closed. This is local supplied-history and cooperating-process integrity, not an atomic multi-file snapshot, broker/account source attestation, genuine-feed acceptance, sustained-trial evidence, native durability, funds authority or routing permission.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The active forward worker now binds its OS lock descriptor to the same regular single-link public pathname throughout preparation, quote intake, order submit/fill checkpoints, forward diagnostic commits and final release. A late replacement stops the worker and prevents successful acknowledgement while safety cancellation and the process-independent emergency cut remain available. This is sampled cooperating-process local fencing, not distributed ownership, an ACL/administrator boundary, physical durability, broker acceptance or sustained-market evidence.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Forward and research-refresh ownership now binds both the opened lock file and its physical containing directory. Symlinked owner directories are refused, and replacement of the lock scope fails closed even if the original single-link lock inode is moved into the replacement directory. This is cooperating-process local fencing, not distributed ownership, ACL/administrator isolation, physical durability, provider identity, broker acceptance or sustained-market evidence.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Active owner validation now samples the acquired directory both before and after validating the public lock path. A directory swap in that interval fails closed even if the same opened single-link lock inode remains at the public path. This narrows a local cooperating-process race; it is not an atomic filesystem transaction, distributed ownership, ACL/administrator isolation, broker acceptance or sustained-market evidence.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The opt-in research-refresh worker now actively revalidates its OS owner lease across collection, import, training and model-reference publication boundaries. A replaced public owner path pauses the worker for explicit review and cannot acknowledge a later publication. This is sampled local cooperating-process fencing, not distributed ownership, source attestation, model acceptance or sustained-market evidence.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; paper-forward ownership now requires the fixed one-byte marker before and after every OS lock acquisition and during active lease validation, rejecting malformed existing state without repair and invalidating an in-place marker change before further acknowledged work' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Paper-forward ownership now validates its fixed one-byte marker before and after OS lock acquisition and during every active lease check. Corrupt existing state is not repaired, and a same-inode marker change invalidates the lease before further acknowledged work. This is local cooperating-process integrity, not distributed ownership, ACL isolation, broker acceptance or sustained-market evidence.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; on POSIX, paper-forward ownership also requires the lock file and containing directory to remain owned by the current effective user, and the lock file to remain owner-readable/writable without group/other write access, rejecting unsafe inherited state without repair and detecting mode drift during an active lease' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' POSIX paper-forward ownership now rejects a lock file or containing directory not owned by the current effective user, and requires the lock file to stay owner-readable/writable without group/other write access throughout an active lease. Unsafe inherited state is never repaired. This is mode-bit/current-user local hardening, not directory ACL, extended-ACL, Windows ACL, administrator, broker or sustained-market acceptance.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the POSIX owner directory is now created private and must remain free of group/other write permission before acquisition and throughout an active lease, rejecting unsafe inherited state without repair' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The exact POSIX owner directory is now created private and must remain non-group/world-writable across availability, acquisition and every active lease check. Unsafe inherited mode and later drift fail closed without repair. This remains an exact-directory mode-bit fence, not ancestor-path, extended-ACL, Windows ACL, administrator, broker or sustained-market acceptance.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; on POSIX, shared provider POST, provider-read, account-read pacing and Activity SSE cursor ownership now require the lock file and exact containing directory to remain owned by the current effective user, require owner read/write on the lock file and reject group/other write access before mutation and throughout the active lease' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' POSIX broker-paper coordination locks for provider POST, provider-read, account-read pacing and Activity SSE cursor checkpoints now require current-user lock/directory ownership, owner read/write lock access and no group/other write permission before mutation and throughout the active lease. Unsafe inherited locks and exact directories are not repaired; active mode drift fails closed. This is local mode-bit coordination hardening, not ancestor-path or extended-ACL review, Windows ACL acceptance, distributed ownership, provider identity, broker acceptance or sustained-market evidence.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider POST, provider-read, account-read pacing and Activity SSE cursor ownership now also require the complete fixed marker before OS lock acquisition and throughout the active lease, rejecting malformed inherited markers without repair and detecting same-inode marker corruption before further acknowledged work' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Shared broker-paper owner locks now validate the complete fixed marker before OS locking and during every active lease check. Malformed inherited markers are not repaired, and same-inode mutation invalidates the lease before further acknowledged work. This is cooperating-process local marker integrity, not distributed ownership, provider identity, broker acceptance, sustained-market evidence or routing authority.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider POST, provider-read, account-read pacing and Activity SSE cursor ownership now pin the exact containing directory device/inode before opening and bracket every public lock-path/marker validation with that identity, so replacing the directory cannot preserve lease authority even when the same locked inode is moved back into the public pathname' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Shared broker-paper owner locks now bind the exact containing directory identity before opening and bracket active lock-path/marker checks with that identity. A replacement directory cannot inherit the lease by receiving the same still-locked inode. This is local exact-directory identity hardening, not distributed ownership, extended-ACL review, Windows owner-profile acceptance, provider identity, broker acceptance or sustained-market evidence.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider POST, provider-read, account-read pacing and Activity SSE cursor ownership now initialize the fixed marker only after exclusive lock-file creation, while every inherited lock must already be an exact regular single-link marker file; empty, multiply linked or malformed inherited state is rejected byte-for-byte without repair' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Shared broker-paper owner locks now distinguish exclusive creation from inheritance before marker initialization. Only a freshly and exclusively created regular single-link file may receive the fixed marker; inherited empty, multiply linked or malformed files are rejected unchanged. This is local cooperating-process state-integrity hardening, not distributed ownership, extended-ACL review, Windows owner-profile acceptance, provider identity, broker acceptance or sustained-market evidence.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every valid provider POST, provider-read, account-read pacing and Activity SSE cursor owner marker now crosses an exact file-fsync barrier before lock authority, then flushes its identity-bound parent directory on POSIX; inherited retries repeat the same barrier, while sync uncertainty or a substituted directory descriptor fails closed' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Every valid shared broker-paper owner marker now requires exact file fsync before lock authority and identity-bound parent-directory fsync on POSIX. Inherited retries repeat the same barrier, so a prior graceful sync failure cannot be promoted by reopening the retained marker. File-sync uncertainty, directory-sync uncertainty or a substituted directory descriptor fails closed. Python exposes no portable Windows directory-fsync API, so Windows retains the file barrier and independent native acceptance gate. This is graceful local durability handling, not physical power-loss, distributed ownership, native-owner, provider/account or broker acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every broker-paper owner lease now pins the no-follow immediate-parent directory identity before opening its marker and brackets all active marker/directory checks with that identity, so moving the same owner directory under a replacement parent fails closed before acknowledged work and during exit' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Every valid shared broker-paper owner lease now captures the no-follow identity of the directory that publishes its exact owner-directory entry before opening the marker. That identity is required during marker preparation, every active validation and context exit, so a replacement immediate parent cannot inherit authority by moving the same owner directory back under the public pathname. This is one additional local ancestry level, not full ancestor-chain, extended-ACL, distributed-owner, native-profile, provider/account or broker acceptance.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; final caller-selected reconciliation journal leaves must now remain regular single-link files bound to the canonical preflight identity before semantic acceptance, so symbolic links, hard links and observed leaf replacement fail closed' if item.id=='M4' else
        '; the read-only admission inventory now separately binds the dormant Alpaca paper-order and optional AI credentialed POST transports to exact fixed targets while proving that transport existence is not route exposure or operational authority' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Reconciliation acceptance now rejects symbolic or multiply linked caller-selected pacing, activity and acceptance journal leaves before semantic reads and rebinds each canonical preflight result to the exact selected identity. The source-declared authority inventory separately reports application routes and the two exact credentialed POST transports: the paper-order transport remains OFF and unrouted, while every broker, funding, live, cloud, spend and release authority flag remains false. This is local evidence-integrity and truthful capability inventory, not provider/account connectivity, broker acceptance, genuine-market coverage, sustained operation, funding/live/cloud readiness or permission.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; exact-TTL expiry of a residual reducing-exit slice now returns an explicit no-fill result while preserving the immutable full-position mandate, so the runner durably records a veto instead of a modeled SELL and may resume only under later fresh terms and quote' if item.id=='M4' else
        '; paper-forward execution now distinguishes per-slice expiry from terminal reducing-mandate expiry, retaining the bounded position/account binding and independent halt path across a cold restart without fabricating a fill' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Residual position-reducing mandates now treat an exact execution-term TTL boundary as a per-slice no-fill veto. The current position, durable fill count and quote-event count remain unchanged; a later cycle must establish newer terms and a fresh quote before the same bounded mandate can continue. This is local reconciliation correctness, not broker-paper, genuine-market, liquidity, sustained-operation or release acceptance.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

# Human engineering estimates; reassess on substantial integration/evidence or
# scope changes. Weights are relative scope, not measured hours or permission.
PROGRESS_BASELINE = (
    ('D1',10,85),('D2',15,55),('D3',15,70),('D4',20,35),
    ('D5',10,0),('D6',10,10),('D7',10,10),('D8',5,35),('D9',5,0),
)


def progress_estimate():
    stages={row['id']:row for row in DELIVERY_STAGES}
    assert {row[0] for row in PROGRESS_BASELINE}==set(stages)
    assert len(PROGRESS_BASELINE)==len(stages) and sum(row[1] for row in PROGRESS_BASELINE)==100
    assert all(type(weight) is int and weight>0 and type(done) is int and 0<=done<=100 for _,weight,done in PROGRESS_BASELINE)
    weighted=sum(weight*done for _,weight,done in PROGRESS_BASELINE)
    rounded=((weighted+250)//500)*5
    accepted=sum(row['status']=='ACCEPTED' for row in DELIVERY_STAGES)
    return dict(method='WEIGHTED_ENGINEERING_ESTIMATE_V1',as_of='2026-10-18',
        basis_commit='b2a9bc6da241bb8c7f0e1dfe1b2ed6b251bf467c',
        scope='Full agreed1.0: desktop, intelligence, local/broker paper, secure funding, governed live execution, cloud and independent/owner acceptance.',
        implementation_estimate_percent=rounded,uncertainty_low_percent=30,uncertainty_high_percent=45,
        confidence='LOW',weighted_index_percent=weighted/100,weights_total=100,
        fully_accepted_stages=accepted,total_stages=len(DELIVERY_STAGES),
        release_ready=False,release_approved=False,live_orders_enabled=False,money_movement_enabled=False,
        estimated_release_date=None,
        methodology='Manual relative-scope weights multiplied by rough implementation estimates; overall rounded to the nearest5 percentage points. Estimates include foundations and partial work, not independently accepted operations. They are not measured task/hour completion or time remaining. Reusable foundations can contribute to unfinished integrations. The range is judgment, not a statistical confidence interval.',
        update_policy='Reassess after meaningful integration, acceptance or scope changes; no automatic increase for versions, patches or passing test counts. Estimates may decrease. Report occasionally in development updates.',
        stages=[dict(id=identity,title=stages[identity]['title'],weight_percent=weight,
            implementation_estimate_percent=done,acceptance_status='ACCEPTED' if stages[identity]['status']=='ACCEPTED' else 'OPEN',
            basis=stages[identity]['progress'],remaining=stages[identity]['remaining']) for identity,weight,done in PROGRESS_BASELINE])


# Remaining acceptance work, not a runnable workflow or permission checklist.
# Keep tuples immutable; every public view constructs independent containers.
ACCEPTANCE_STEPS = (
    ('D1', (
        ('Native setup', 'Test a fresh ordinary-user Windows profile, install/upgrade/uninstall and preserved saved/OFF choices.'),
        ('Beginner controls', 'Verify startup defaults, seven tabs, hover help, observed Matrix history and separate Portal/Engage controls on the installed build.'),
        ('Owner recovery', 'Record restart, outage, emergency-cut and backup/restore results using OWNER_PAPER_ACCEPTANCE.md; retained private data stays outside Git.'),
    )),
    ('D2', (
        ('Data quality', 'Independently review history provenance, deduplication, conflicts and leakage-free training/test splits.'),
        ('Strategy evaluation', 'Measure held-out and forward results after fees, spread, slippage, liquidity limits and drawdowns against simple baselines.'),
        ('Policy governance', 'Review calibration, drift and model updates; explicitly approve each candidate before any execution integration. Council output remains advisory.'),
    )),
    ('D3', (
        ('Sustained observations', 'Retain genuine public-feed paper observations for at least 72 hours and review the 95% declared-coverage diagnostic; coverage alone does not accept the strategy.'),
        ('Failure trials', 'Exercise disconnects, stale data, restarts, risk vetoes and independent emergency stop with durable accounting and no automatic re-engagement.'),
        ('Independent review', 'Review actual incidents, costs, reconciliation and strategy quality before accepting autonomous paper operation.'),
    )),
    ('D4', (
        ('Authorized environment', 'Use an owner-authorized paper account and native private Windows volume; accept scoped credential onboarding and authenticated read-only inventory first.'),
        ('Recovery and coverage', 'Verify original-ID recovery, activity collection/replay, reconnect budgets, lost acknowledgements and stop behavior against that account; retain exact evidence privately.'),
        ('Paper submission integration', 'Accept the internal single-attempt boundary, then review separately scoped saved write credentials, explicit operator controls and broker-wide pacing before connecting any strategy route.'),
        ('Broker reconciliation', 'Match actual fills, fee associations, pending orders, holds, cash and positions; resolve discrepancies and obtain independent connected-paper acceptance.'),
    )),
    ('D5', (
        ('Provider funding design', 'Select a provider-held account and reviewed provider-hosted authorization/funding flow; Nexus must not collect raw banking credentials or take custody.'),
        ('Funds reconciliation', 'Implement pending versus settled transfers and spendable cash with scoped secrets, duplicate handling, failures and reversals.'),
        ('Funding acceptance', 'Test provider sandbox flows, then obtain separately authorized end-to-end funding/security acceptance before treating balances as spendable.'),
    )),
    ('D6', (
        ('Live risk boundary', 'Implement a separate live adapter with accepted policy, reconciled spendable funds and hard exposure, loss, order and pacing caps.'),
        ('Controlled rollout', 'Independently review monitoring, reconciliation, emergency cut and recovery; obtain separate explicit owner authorization before any limited real-money trial.'),
        ('Live acceptance', 'Review supervised limited-rollout evidence before expanding any autonomous live scope; release approval never activates trading.'),
    )),
    ('D7', (
        ('Hosting choice', 'Verify an eligible free-tier host and quotas using a supported account; do not assume anonymous always-on compute or enable billing automatically.'),
        ('Secure headless engine', 'Implement authenticated controls, encrypted transport/storage/backups, credential isolation, key recovery, shared Matrix deduplication and one execution owner across desktop/cloud.'),
        ('Computer-off trial', 'Prove remote halt, quota/outage recovery, distributed fencing and durable reconciliation during multi-day paper operation with the desktop off.'),
    )),
    ('D8', (
        ('End-to-end measurements', 'Measure feed-to-decision-to-provider timings, costs and bounded queues on real accepted paths; fixture throughput is not broker fill speed.'),
        ('Resilience trials', 'Run sustained load, long-run storage, outage, power-loss, restore and rollback trials on native profiles.'),
        ('Independent assurance', 'Complete security/risk review and operational runbooks against the exact release candidate and retained evidence.'),
    )),
    ('D9', (
        ('Scope acceptance', 'Accept all D1–D8 production workflows in the agreed scope and resolve remaining defects; percentages cannot substitute for evidence.'),
        ('Release candidate', 'Bind source, full regression, browser checks, security/history scans, Windows installer/upgrade/recovery and signed or checksum-verified distribution evidence to one exact candidate.'),
        ('Owner release decision', 'Obtain independent final review and explicit owner approval to label 1.0. Keep live activation as a separate authorization.'),
    )),
)


def acceptance_plan():
    stages = {row['id']: row for row in DELIVERY_STAGES}
    return dict(read_only=True, purpose='REMAINING_ACCEPTANCE_WORK_NOT_EXECUTION_AUTHORITY',
        release_approved=False, live_orders_enabled=False, money_movement_enabled=False,
        next_major_gate='D4',
        stages=[dict(stage_id=identity,
            unaccepted_dependencies=[dependency for dependency in stages[identity]['depends_on']
                if stages[dependency]['status'] != 'ACCEPTED'],
            steps=[dict(id=f'{identity}.{index}', title=title, required_evidence=evidence,
                state='REVIEW_REQUIRED') for index, (title, evidence) in enumerate(steps, 1)])
            for identity, steps in ACCEPTANCE_STEPS])


SETTINGS = (
    ('broad_refresh_seconds',5),('ranking_seconds',15),('ai_candidate_count',30),
    ('adaptive_acceleration',True),('autonomous_paper_first',True),
)
FIRST_RUN = dict(goal='Open the EXE, Start Portal, then separately confirm Engage Paper; no settings editing on a new unused installation.',
    virtual_budget_usd=10000,default_entry_tier='terrestrial',research_product='BTC-USD',research_interval_seconds=3600,
    research_bars=1000,collect_public_history=True,built_in_models_included=True,
    external_account_required=False,paid_ai_required=False,starts_trading_automatically=False,
    existing_choices_preserved=True,acceptance='Validate the fresh native EXE, ordinary profile, saved-choice upgrade, outage and recovery paths. Recommended defaults are not proven optimal returns.')
PRODUCTION_USER_FLOW = (
    'Install and open Nexus with included intelligence and practical defaults.',
    'Securely authorize and fund an account through the financial provider; bank linking where required stays in the provider-hosted flow. Not implemented.',
    'Choose a risk allowance that limits the engine; returns are not guaranteed.',
    'Press Engage to run automatic readiness checks and start separately authorized autonomous execution. Live execution is not implemented.',
)
NEXT = (
    'Accept the explicit one-attempt Activity SSE reconnect/checkpoint coordinator on an authorized paper account and native private Windows volume. Keep the prior segment, cursor journal and resulting report private; exercise exact-head races, revoked permission, the independent emergency stop, credential changes, disconnects, slow-consumer/internal-error frames, lost acknowledgments and process restart. Do not add an automatic reconnect loop until provider source/delivery/replay coverage, bounded backoff, broker-wide coordination and native durability are independently accepted.',
    'Use the private exact-response bundle review on an authorized adjacent ACCOUNT_INVENTORY and current-tail ACTIVITY_COLLECTION pair: preserve every exact response body plus the portable account observation outside Git, pin the shared current pacing/activity heads and both session heads, and independently review the composed normalization result. Local byte/hash agreement is not provider identity, source or received-activity coverage, account ownership, fee/hold/funds acceptance or routing permission.',
    'Use the private account-response review on an authorized retained ACCOUNT_INVENTORY session: preserve the exact four response bodies and portable observation outside Git, pin the current pacing head and session head, and independently review the normalization result. Exact local byte/hash agreement is not provider identity, source coverage, account ownership, fee/hold/funds acceptance or routing permission.',
    'Use the private activity-response review on an authorized retained ACTIVITY_COLLECTION session: preserve the exact account and one/two page response bodies outside Git, pin the current pacing/activity heads and session head, and independently replay the retained normalized receipt. Exact local byte/hash agreement does not prove source or creation/execution/fee coverage, posting times, fee links, holds, funds or routing permission.',
    'Accept retained checked-admission samples on authorized native paper histories, including full prefix replay and later cross-request clock regression. Keep older markers untimed. Independently review actual send/arrival event capture, clock domains and contemporaneous quotes before creating execution benchmarks; admission is not proof of transport or broker arrival.',
    'Accept the separately confirmed EXE quote-capture, offline status and encrypted full-chain evidence review on authorized paper-account/native-profile history. Preserve one quote per successful retained pacing transition, original-request prefixes and interruption blocks; accept the new declared-operation history, review central budget coordination and a complete mixed-operation receipt model, and add exact original-event-time bindings before deriving decision/arrival benchmarks; do not treat a latest quote, local clock or reported size as verified order arrival, executable liquidity or latency.',
    'Accept the exact-head paper execution-quality review on authorized retained account history and native Windows builds. Accept the exact local original-request event binding, then add independently reviewed contemporaneous benchmark/arrival quote capture, transport-event evidence, clock-domain evidence and verified fee links before net-cost or broker-latency claims; preserve unknown fees and include every retained fill.',
    'Verify all fourteen EXE paper tools on standalone and installed Windows builds, including exact-byte account/activity response normalization, the application-closed order-offload copy, private exact-head quality review, unchanged inputs, unknown fees and unverified benchmark authority; preserve current-head/public account review, crypto, no default-data/UI startup and actual account/profile acceptance. Response reviews require private bodies outside Git and grant no source, coverage or funds authority; quality review requires v0.159+; quote-capture requires v0.162+ and explicit confirmation for a read; encrypted full-chain quote evidence requires v0.163+ and a hidden password, with actual native/private-volume/ACL recovery acceptance still required.',
    'Accept durable request-origin replay on authorized paper data: verify stored v3 request heads against historical prefixes and exact retained order inputs across restarts and later appends; retain mixed v2 history without inventing origin bindings or authorizing routing.',
    'Accept request-journal-derived activity collection on authorized Windows paper data, including changed heads during reads/retention/acceptance and lost returns; original observations and local hashes remain unverified, and no fee, funds or routing authority follows.',
    'Use the private combined account-evidence review on authorized retained paper data: bind original requests, accepted fills and opening/closing inventory to common heads, investigate term violations and gross-only differences, and obtain separate source/fee/hold/funds acceptance before any routing.',
    'Accept bounded next-request recovery on an authorized Windows paper account with real retained request history, exact-head races, shared pacing, interruptions and original-ID outcomes. Use the explicit exact-head account-recovery tool only after reviewing a blocked pacing tail; it preserves cooldown, history, capacity and uncertainty and grants no read authority. A local candidate is not permission; never auto-loop, clear uncertainty, resubmit or promote account/funds acceptance from read success.',
    'Accept the new confirmed paced paper BTC asset-field read on an authorized Windows paper account; independently review current minimum/increment semantics, stale metadata and account restrictions before any plan/routing integration. Reported asset fields do not prove entitlement or spending permission.',
    'Accept the minimal first-run path on a fresh Windows profile: no settings edits, clear checkbox purposes, optional connections excluded, preserved saved/OFF choices, separate Engage consent and independent emergency cut.',
    'Collect and explicitly review retained public-feed paper observations using To Do Paper Trial Readiness; satisfy the72-hour/95% declared coverage diagnostic and obtain separate source, strategy, account, incident/recovery and independent acceptance. Passing local regression is not a launch-date estimate.',
    'Measure shared quote intake and the full feed-to-decision path under concurrent readers, failures and sustained real feeds.',
    'Extend emergency-control acceptance to sustained feeds and CPU/event-loop pressure; exercise the repaired failed-reset durability boundary under native Windows storage faults and continue interrupted-acknowledgment recovery while preserving durable fencing.',
    'Validate linked-ledger recovery, risk vetoes and reconciliation under repeated interruption.',
    'Extend linked quote-model stress and independently review cost, liquidity and expiry assumptions.',
    'Accept the explicit halted replacement install/recovery/rollback flow on private native Windows volumes and ordinary-user profiles, including power-loss interruption at each replacement boundary; then run multi-epoch long-run capacity and cost tests. Live capacity can now be reclaimed only after an exact source archive, candidate and separate recovery copy are verified, while source history remains preserved externally.',
    'Validate the opt-in BTC paper runner across sustained public feeds, outages and restart; integrate broader research-backed admission.',
    'Validate sustained local candidate refresh and independent walk-forward/cost evidence and reviewed optional local-weight runtime upgrades; never promote learned output automatically.',
    'Accept the internal single-attempt paper submission boundary, shared-local provider POST budget and exact terminal original-ID recovery against an authorized account; add separately scoped saved write credentials, broker-wide/distributed POST coordination and explicit operator controls only after review, then reconcile real fills, fees, holds and funds before any strategy route or policy promotion.',
    'Validate the explicit Windows paper credential/read controls in a real authorized paper account and review scoped-secret lifecycle; accept the journal-bound original-ID recovery coordinator against authorized paper data, then accept the bounded activity collector against authorized paper data, validate bounded local retention and implement authenticated source coverage and verified fee associations/timing, broker-wide pacing and cash/position/hold reconciliation before provider-paper routing; finish forward, packaging and independent release acceptance.',
    'Validate Found Markets on sustained real public feeds, missing data, outages and shared-IP budgets; add licensed/authorized asset connectors, leakage-free walk-forward cost evidence, calibrated forecasts, portfolio diversification and governed policy integration before any discovery-to-order route.',
    'Expand markets incrementally through verified data, legal eligibility, cost and paper-evidence gates.',
    'Evaluate free-tier computer-off hosting and secure headless operation without assuming anonymous hosting, free unlimited compute or automatic paid upgrades.',
)

# Record the current bounded coordination evidence without promoting either
# milestone: native Windows and sustained operational acceptance remain open.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; Trial Readiness now consumes an exact durable-capacity report that separates ordinary commands from the fixed halt/cancel/expiry reserve, fails closed on malformed values and refuses next-cycle or conservative full-window readiness when that emergency/control reserve is depleted' if item.id == 'M5' else
        '; Activity SSE cursor checkpoint ownership now combines the OS-backed lock with a PID-and-normalized-path process claim so a second cooperating Windows handle cannot checkpoint during provider open; fresh native cross-process and provider-paper acceptance remain required' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Keep the standalone cut's public-root claim sampled and conservative.  This
# does not replace native ordinary-profile or sustained pressure acceptance.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the standalone emergency-stop process now samples an existing resolved data-root identity before priority elevation, exclusively initializes a root observed absent only afterward, and requires every sampled root or absent-root ancestor path to remain canonical while rechecking identity throughout intent/SQLite work, refusing both replacement roots and same-inode parent aliases instead of reporting VERIFIED for an ambiguously addressed cut' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# Best-effort process priority is diagnostic only.  Unexpected helper failures
# must never preempt the independent intent latch or durable halt attempt.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the standalone emergency-stop process contains unexpected best-effort priority-helper failures and malformed reports as STANDARD/unconfirmed diagnostics, then continues the authoritative intent-latch and durable-halt path' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# The authoritative emergency phase contains unforeseen ordinary helper/storage
# failures as an unconfirmed result only after its nested fallback attempts.
# Malformed halt acknowledgements never grant durable-halt confirmation.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the standalone emergency-stop process contains unforeseen ordinary latch, durable-halt and status-helper faults after completing its nested fallback attempts, and accepts only an ordinary explicit boolean halt acknowledgement' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# Public emergency evidence accepts only the exact boolean result from the
# intent reader.  Truthy malformed helper returns cannot alter its schema or
# promote an otherwise incomplete cut.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the standalone emergency-stop process accepts only the exact boolean True from final intent inspection, so malformed truthy helper returns remain unconfirmed and every public cut-state field retains its boolean schema' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# A separate reset can remove or replace the latch after the first observation.
# Preserve the independent halt attempt but never promote that broken handoff.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the standalone emergency-stop process samples the exact latch entry after its first confirmed observation and rechecks the same entry immediately before durable-halt handoff, so a concurrent clear or replacement loses ordering confirmation without suppressing the independent halt or final-latch attempts' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# Canonical root/ancestor sampling must finish after its trailing identity
# observation.  Otherwise a same-inode parent alias inserted after the prior
# resolution could authorize one storage step before the next sample failed.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; each emergency data-root and absent-root ancestor identity sample is followed by a final canonical-path resolution, preventing a same-inode parent alias inserted after the earlier resolution from authorizing root creation or the first latch write' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# The exact development Windows lane completed regression and installer
# acceptance before its old outer timeout cancelled only the distribution
# evidence tail. Preserve every gate with explicit finite measured margins.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the source-bound Windows regression and packaging lane now has a finite 150-minute outer budget and a separate 20-minute distribution-evidence tail after exact development run 43595758062 exhausted the old 120-minute budget only after full regression, EXE, install/reinstall, prior-version recovery, uninstall and phase review had passed' if item.id == 'M1' else ''),
    item.remaining) for item in MILESTONES)

# Manual installer validation may bind an immutable stacked review commit, but
# only after proving the current development head is its ancestor. The default
# dispatch remains the refreshed exact development head.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; owner-dispatched Windows validation can bind one exact 40-character review commit only after proving it descends from the refreshed development head, while an omitted commit must equal that refreshed head' if item.id == 'M1' else ''),
    item.remaining) for item in MILESTONES)

# A Windows directory cannot be opened for fsync through the standard Python
# API. Keep its emergency latch entry operation bound to a sampled resolved
# parent identity, while POSIX retains its stronger descriptor/fsync boundary.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; emergency-latch persist and clear now bind the resolved parent identity before every entry operation and recheck it before acknowledgement even when Windows cannot supply a directory descriptor, including the already-absent clear path' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# Native Windows can pin a directory with CreateFileW even though the standard
# Python API cannot open it for fsync. Excluding delete sharing prevents a
# cooperating rename from retargeting the latch pathname during acknowledgement.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; Windows emergency-latch persist and clear now hold a backup-semantics directory handle without delete/rename sharing across entry access and final identity acknowledgement, with native rename-denial acceptance retained for the Windows lane' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# Open the exact public Windows parent with OPEN_REPARSE_POINT rather than its
# resolved target, then prove that handle is a non-reparse directory. This keeps
# a late junction/symlink substitution inside the fail-closed lease boundary.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the Windows emergency-latch lease now opens the exact public parent path and rejects non-directory or reparse-point handles before entry access, while the native regression contract records ordinary Windows file-handle rename denial as conservative success rather than sampled-identity failure' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# A failed first provider-POST budget transaction must not strand an empty,
# schema-less main file that blocks an exact clean retry. Cleanup is limited to
# the captured new identity with zero bytes and no SQLite sidecars; ambiguity is
# retained fail-closed for inspection.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; a failed first local provider-POST budget transaction now removes only its captured unchanged empty regular single-link main file when no SQLite sidecar exists, allowing one exact clean retry while replacement, bytes, links, sidecars or cleanup uncertainty remain retained and blocked' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Freeze the selected provider-POST parent before any operation and bind the
# mutable store path/scope/digest through owner acquisition, SQLite setup and
# rollback cleanup. Retargeting cannot redirect a write or delete a decoy.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the local provider-POST budget now freezes its selected parent while retaining final-leaf checks, binds path plus provider scope/digest through active ownership, requires the captured regular single-link main-file identity and exact SQLite main path throughout setup, and performs failed-first-write cleanup only against the captured path so mutable aliases, inode replacement, foreign connections or object retargeting cannot redirect a write or delete a decoy' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Windows permits a directory rename despite the directory handle used by the
# prior lease on the hosted runner. Hold a verified regular child without
# delete sharing as the effective parent-rename barrier.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the native Windows emergency-latch lease now also holds a fixed validated regular single-link child guard without delete sharing through final acknowledgement because hosted run 43634033695 proved the directory handle alone did not deny parent rename; unsafe guards fail closed and a fresh exact-source native pass remains required' if item.id == 'M7' else ''),
    item.remaining) for item in MILESTONES)

# Bind the shared provider-GET budget to one selected physical path and provider
# scope, including narrow recovery of a failed first SQLite transaction.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the shared local provider-GET budget now freezes its selected parent while retaining final-leaf checks, binds path plus provider scope/digest through active ownership, requires the captured regular single-link main-file identity and exact SQLite main path throughout setup, and removes only its captured empty sidecar-free file after an aborted first transaction so aliases, inode replacement, foreign connections or object retargeting cannot redirect a write or poison an exact operator retry' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Carry the captured provider POST/GET main-file identity through transaction
# finalization, connection close and the final local acknowledgement sample.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider POST and GET write contexts now retain the captured SQLite main-file identity through transaction finalization, require the exact main connection and file before close, recheck the pathname identity after close, reject uncommitted returns for both existing and new stores, and fail closed on an injected or actual during-transaction replacement without deleting the replacement or reclaiming uncertain capacity' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Make a committed provider-budget acknowledgement observe an explicit flush
# of the exact captured main-file descriptor. New POSIX entries also flush the
# validated parent directory; Windows retains the portable main-file boundary.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; provider POST and GET committed writes now hash the exact captured writable main-file descriptor around explicit fsync, recheck its bounded pathname fingerprint, and flush a newly created exact parent entry on POSIX; any file or parent flush uncertainty retains the committed pending reservation and blocks automatic retry or capacity reuse' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Match the shared account-read admission/result journal to the provider
# budgets' explicit commit boundary. A committed but unflushed reservation is
# retained for review and cannot silently become reusable request capacity.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every committed shared account-read pacing write now '
        'matches its exact writable SQLite main descriptor and bounded '
        'pathname bytes around explicit file fsync, plus an identity-bound '
        'parent-directory fsync for a newly created POSIX entry; flush '
        'uncertainty retains the committed reservation and blocks reuse' if
        item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)

# Require the broker request/recovery journal itself to cross the same explicit
# descriptor-bound commit boundary as its provider budgets and read pacing.
MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every acknowledged broker request/recovery write now '
        'matches the committed SQLite connection bytes to a stable bounded '
        'pathname fingerprint, hashes the exact writable main descriptor '
        'before and after explicit fsync, and flushes a newly created exact '
        'parent entry on POSIX; flush uncertainty retains PREPARE, submission, '
        'observation, lookup or interruption state without retry or routing '
        'authority, while the account-read and request-journal fingerprint '
        'schemas explicitly retain their portable omitted-ctime representation '
        'on Windows' if item.id == 'M8' else ''),
    item.remaining) for item in MILESTONES)


MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; pull-request security and Windows research jobs now explicitly check out and verify the immutable pull-request head before testing, while an offline exact-CI inventory reviewer binds one requested source to exact workflow paths/events, checked-out SHA values, unique run/job identifiers and attempts, and exact required job sets from a bounded stable single-link input; this records source and inventory consistency only and grants no CI, installer, Windows, broker, live or release authority' if item.id=='M9' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the Activity SSE checkpoint coordinator now requires the complete private transport acknowledgement schema, exact policy/limitations/source mode, account and cursor digests, bounded count arithmetic, interruption status and every denied authority before any durable cursor mutation; this is an internal fail-closed composition boundary using injected fixtures, not provider source, stream coverage, paper-account reconciliation or broker acceptance' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the Activity SSE checkpoint coordinator now freezes an existing bounded single-link cursor main-file identity and bytes, or proves a fresh path remains absent, across admission, the sole provider read, private parsing and reconnect review before checkpoint handoff; a byte-identical pathname replacement cannot preserve cursor authority, while the cursor store retains its separate descriptor-bound commit and recovery checks' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; Activity SSE reconnect reasons now require one exact built-in allowlisted string at every offline review, saved-credential, reconnect-budget, checkpoint and owner-acceptance ingress before callbacks, journal inspection or creation, reservation and the sole provider GET; malformed or equality-forging reasons are network/storage silent and retain no request authority' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the allowlisted public account-evidence summary now requires exact built-in top-level keys, mode, policy and status text plus exact built-in gross-currency keys before comparison or public copying, so equality-forging text subclasses cannot pass private policy/status text or ambiguous keys through the privacy boundary' if item.id=='M4' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; simulated tier-entry permission now preflights one exact built-in tier and a finite strictly positive exact built-in integer, float or Decimal notional before settings/permission reads, while durable and forward callers convert their already validated stored values to Decimal; Boolean, stringified, non-finite, zero, negative and equality-forging inputs fail storage-silent' if item.id=='M3' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; durable simulated-order admission now requires exact built-in order/product/side/provider/tier/event identifiers and event kinds before lookup, comparison or mutation, so equality-forging text cannot persist a command that later poisons journal replay' if item.id=='M4' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the opt-in paper forward runner now admits only an exact built-in public-quote mapping with exact provenance/permission text and booleans plus finite positive built-in prices and a finite built-in receipt clock before feed evaluation or modeled order creation; rejected polymorphic inputs retain only a fixed invalid-evidence marker' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; supplied Activity SSE reconnect review now requires exact built-in persisted and since cursors before transcript normalization, preventing equality-forging cursor subclasses from reporting exact reuse or forward advancement with mismatched cursor digests' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; public ticker parsing and paper-forward admission now share a finite 1e-18 through 1e12 price envelope before float publication, cache sharing, retained quote evidence or modeled execution, so extreme finite provider values cannot underflow, overflow or reach entry arithmetic' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; supplied Activity SSE link review now preflights the exact account before inspecting events and validates exact built-in list/mapping/key/scalar types into a detached immutable-use snapshot, preventing polymorphic or concurrently changed envelopes from presenting different budget and normalization views; the installed checkpoint rejects such initial evidence before provider IO or cursor storage' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; direct PaperEngine capital, fee, cost, notional, market and mark values now share a finite 1e-18 through 1e12 envelope before risk-state mutation, preventing extreme finite values from producing zero/infinite public floats or corrupting cash, position and history state' if item.id=='M3' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; public quote-bundle summaries now admit one exact built-in review mapping/schema and capture exact text/count scalars once, preventing stateful mapping rereads or equality-forging text from moving private content into the public diagnostic' if item.id=='M4' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; public ticker product selection now requires an exact built-in allowlisted product before cache, flight, transport or URL construction, preventing equality-forging strings from changing the hard-coded public path' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; Activity SSE checkpoint initial and replay events now pass exact envelope validation into a detached snapshot before any caller-controlled copy protocol, so deepcopy laundering cannot create accepted evidence or reach provider IO and cursor storage' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the explicit Activity SSE checkpoint now freezes the exact non-symlink cursor parent-directory identity, or proves that parent remains absent, across external provider IO and every subsequent semantic boundary through retained acknowledgement; replacing the directory or creating a fresh parent cannot inherit checkpoint authority even if the main file is preserved, complementing the independent cursor-file and descriptor-bound store protections' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the Windows complete-regression pull-request lane now uses two deterministic exact-node pytest execution shards while retaining full collection in each job; exact-head and step-inventory reviewers require both named shards green, so cancellation, omission, skip or failure of either shard remains release-blocking rather than being mistaken for complete regression' if item.id=='M9' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Pull-request release jobs now bind checkout and verification to the immutable requested head rather than an implicit merge ref. The offline inventory reviewer requires exact workflow/event/source/run-attempt/job membership and stable bounded input while keeping every authority flag false. This is source and inventory consistency, not hosted-service attestation, job-step integrity, artifact-byte provenance, native Windows acceptance or independent release approval.')
    if stage['id']=='D9' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The complete Windows regression pull-request lane is deterministically split across two exact-node execution shards after full collection in each job; both exact names and their complete checkout/compile/test step sequences are mandatory in offline inventory review, and either shard missing or non-green remains a denial. This reduces per-job timeout exposure without reducing collected test scope, but hosted success, native Windows application behavior and independent release approval remain separate.')
    if stage['id']=='D9' else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; bounded linked-ledger burst percentiles now carry an exact process-local measurement-window attestation whose monotonic sample count, elapsed window, dequeued/completed/expired/flushed outcome partition and emergency-halt inclusion must reconcile with the public burst report; a clock regression fails closed rather than becoming expiry, while market/broker latency and throughput authority remain explicitly false' if item.id in ('M3','M5') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the mandatory disposable forward release profile now executes isolated missing-source, stale-quote, regressed-clock and invalid-clock treatments through the actual runner, requires every session to pause and release ownership with zero orders, and binds exact bounded fetch/cycle/reason counts while automatic retry and every external authority remain false; standalone feed admission rejects polymorphic mappings and invalid runtime clocks before baseline mutation, and the research decision lane distinguishes clock regression from ordinary expiry' if item.id in ('M3','M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; those four fault treatments now run through the actual worker loop, retain the PAUSED session while its worker and process ownership are still active, observe zero post-pause fetches, terminate before ordinary cleanup and prove a cold runner remains disabled and fetch-free until an explicit distinct healthy session reacquires released ownership; the failed session remains orderless and its scoped evidence hash is unchanged' if item.id in ('M3','M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the internal single-attempt paper POST boundary now re-samples the exact BTC/USD eligibility and size/quantity/price increments after both durable reservations, consuming changed or ineligible terms as NOT_SENT before transport; the local decision benchmark rejects invalid, regressing and non-advancing measurement clocks instead of manufacturing a passing rate' if item.id in ('M3','M8','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the actual paper-forward worker now schedules cycle starts against fixed monotonic deadlines instead of adding cycle duration to every interval, durably records bounded overruns and resets from completion without catch-up bursts, and pauses before further fetches if the independent scheduler clock is invalid, regresses or stalls; the mandatory forward release profile binds nominal, overrun and fail-closed actual-worker evidence while provider, retry, sustained-market and release authority remain false' if item.id in ('M3','M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; pull-request repository security now passes the immutable exact base and candidate commits into the trusted base policy, preserving complete reachable-history inspection while enforcing finite per-object and newly reachable delta limits; push and manual runs bind the exact source to itself for a zero delta without weakening the complete scan' if item.id=='M9' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the protected local-paper reducing intent is now one immutable full-position mandate across multiple fresh-quote slices and explicit runner sessions: every slice retains the $200 ceiling, exact session/cycle, monotonic fill sequence and current-position/cumulative-reduction equality; cold replay reuses exactly one valid mandate without a new ID, session review separates modeled slice costs, and the independent emergency halt terminally cancels pending work without liquidating' if item.id in ('M4','M5','M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; the complete per-session review is now sealed by a privacy-safe receipt that binds both replay-validated order snapshots, quote/event heads, the event reconciliation, final reason set and exact independent-cut evidence state; generic halts cannot impersonate an independent cut, malformed or changing cut evidence fails closed, and clean forward/recovery fixtures consume the receipt without granting source, broker, account, sustained-trial or release authority' if item.id in ('M4','M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every modeled quote expiry now retains one replayable no-fill attempt bound to exact session, cycle, order, side, event and quote digest, and per-session review requires an exact matching expired-without-fill outcome before local consistency can pass' if item.id in ('M4','M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; each durable no-fill attempt is now cross-bound to its independent forward VETO by a canonical digest over every retained attempt field; the mandatory forward profile terminates a real child after the durable commit and before the VETO, then requires cold replay to preserve the position, fill sequence and resumable mandate while retaining an explicit review gap without automatic repair' if item.id in ('M4','M7','M9') else ''),
    item.remaining) for item in MILESTONES)

def snapshot():
    return dict(read_only=True, revision=REVISION, progress_summary=PROGRESS_SUMMARY, progress_diagram=PROGRESS_DIAGRAM, project='Nexus Operative', repository='S4-cyber/Nexus-Operative',
                branch=BRANCH, release_target='1.0.0', current_mode='OFFLINE_PAPER_PRERELEASE',
                current_hardening=CURRENT_HARDENING,progress_estimate=progress_estimate(),acceptance_plan=acceptance_plan(),launch_assessment=dict(stage='EXPERIMENTAL_LOCAL_PAPER_PREPRODUCTION',full_release_date_established=False,percentage_complete_established=False,rough_engineering_estimate_available=True,next_major_gate='CONNECTED_BROKER_PAPER_AND_SUSTAINED_TRIAL_ACCEPTANCE',minimum_coverage_window_hours=72,coverage_is_release_acceptance=False),first_run_setup=dict(FIRST_RUN),
                opportunity_discovery=dict(implemented=True,scope='USD spot crypto research only',default_with_portal=True,burst_seconds=900,markets_per_burst=4,requests_per_hour_shared_local_data_root=36,durable_request_admissions=True,provider_or_ip_wide_budget_verified=False,selected_market_limit=6,summary_limit=256,stored_in_process_memory=True,execution_integration=False,forecasts_validated=False),
                production_user_flow=list(PRODUCTION_USER_FLOW),live_workflow_available=False,
                institutional_design=dict(document='INSTITUTIONAL_DESIGN.md',status='PUBLIC_DESIGN_REVIEW_OFFLINE_FOUNDATION',
                    execution_quality='EXACT_HEAD_LOCAL_ACCEPTED_PAPER_FILLS_SUPPLIED_UNVERIFIED_BENCHMARK',council_role='ADVISORY_OUTSIDE_EXECUTION_LOOP',
                    target_executor='DETERMINISTIC_SHARED_ACCOUNT_RISK_AND_SINGLE_EXECUTION_OWNER',
                    native_cpp_core_implemented=False,nanosecond_execution_verified=False,proprietary_code_imported=False,
                    automatic_policy_promotion=False,order_routing_enabled=False),
                delivery_stages=[{**stage,'depends_on':list(stage['depends_on'])} for stage in DELIVERY_STAGES],
                production_scope='Secure provider-held funding, governed autonomous execution and computer-off operation are product goals. All agreed production workflows require acceptance before 1.0; live activation always requires separate authorization.',
                settings_scope='TARGET_DESIGN_NOT_EFFECTIVE_RUNTIME', settings=dict(SETTINGS), milestones=[asdict(m) for m in MILESTONES], milestone_execution_plan=execution_plan(), milestone_work_batches=next_work_batches(), next_steps=list(NEXT),
                supporting_track='Global market expansion and secure computer-off cloud continuity are explicit staged goals; venue/data/risk and hosting acceptance remain required.',
                strategic_goals=[dict(id='G1',title='Global opportunity expansion',status='PLANNED',goal='Broaden assets, markets and providers through the existing 12-avenue expansion initiative; validate source rights, eligibility, liquidity, costs and reproducible paper evidence before enabling each route.'),
                                 dict(id='G2',title='Computer-off cloud operation',status='PLANNED_NOT_DEPLOYED',goal='Run the headless paper engine independently of the desktop, with authenticated remote observation, durable reconciliation, one execution owner, encrypted transport/storage/backups, credential isolation and tested shutdown/recovery.',cost_preference='Free-tier only; avoid a separate dedicated account where a supported existing account can be reused. No verified anonymous always-on option selected; never silently enable billing.',acceptance='Prove durable storage, quota/outage behavior, remote halt, desktop/cloud fencing, key recovery and multi-day computer-off paper operation. See CLOUD_OPERATION.md.'),
                                 dict(id='G3',title='Collective Matrix data and prediction hub',status='LOCAL_FOUNDATION_CLOUD_NOT_DEPLOYED',goal='One authenticated central public-data unit with canonical venue/product/interval/time identities, deduplicated ingestion, conflict quarantine, compact read-only snapshots and versioned model evidence. Local bounded hub and trained momentum baselines are implemented; global instance synchronization, source rights/attestation, cloud encryption, stronger model acceptance and governed paper execution remain open. See MATRIX_HUB.md.')],
                speed_target='Aim for roughly 30+ market moves/second in the long term; internal throughput is not broker acknowledgments or fills.',
                resume='Read AGENTS.md, ROADMAP.md, LAUNCH_DRIVE.md and VERSIONING.md; verify remote branch head, CI and current source before editing.')


MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; one disposable linked-ledger drill now survives two ordered process deaths on the same history: a durable modeled BUY before its forward outcome, explicit review/cancellation, then a reducing-exit no-fill attempt before its VETO; cold replay requires both gaps, stable first-gap evidence, monotonic fills, exact remaining-position equality and disabled/unowned forward state without repair, retry or restart' if item.id=='M4' else
        '; repeated-interruption evidence now preserves the first fill gap across a second no-fill crash and requires both sessions to remain independently review-required, while all provider, broker, live and release authority stays false' if item.id=='M7' else
        '; the mandatory forward profile now binds a same-history two-crash recovery treatment with exact exit codes, immutable first-gap evidence, no-fill digest replay and denied automatic/external authority' if item.id=='M9' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; one exact-head same-host reconciliation now holds broker-request writer exclusion while replaying the complete retained counterfactual plan and request summary, revalidates the fresh stopped forward receipt, event reconciliation, order snapshots and full modeled-fill source, then rechecks pathname identity and bounded file bytes on exit; stale heads, changed source evidence and legacy unbound plans fail closed without journal mutation, credential access, provider IO or routing authority' if item.id in ('M4','M8','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; one strict offline POST/GET/Activity-SSE uncertainty matrix now requires the exact disposable component schemas together: GET authentication denial and lost recovery acknowledgement, POST single-attempt safety and unknown-outcome windows plus terminal original-ID recovery, and SSE bounded backoff, independent emergency-stop revocation, cursor races, lost acknowledgements and byte-stable cold replay; every provider, credential, storage-retention, broker, funds, live and release authority remains false' if item.id in ('M4','M8','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; retained forward-trial coverage now defers interval eligibility until after the chronological cross-session consistency pass, removes both intervals adjacent to a conflicting boundary point, and uses retained context outside the requested window without leaking that context into in-window disagreement counts; rapidly rotating sessions can no longer report nominal full coverage by resetting price-jump state' if item.id in ('M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; equal-time cross-session quote disagreements now reject every supplied conflicting observation instead of granting lexical-first authority, and rejected observations cannot become the chronological reference for later coverage; requested-window disagreement counts remain scoped while every dependent interval fails closed' if item.id in ('M7','M9') else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; executable-price continuity now checks bid, ask and last across ordinary feed observations and retained cross-session trial boundaries, so an unchanged last trade cannot conceal a strategy or modeled-fill price jump' if item.id in ('M3','M7','M9') else
        '; every cut-guarded durable order transaction now rechecks the independent emergency latch after its final apply and before SQLite commit, rolling back the order journal and linked portfolio when the cut wins that seam' if item.id=='M4' else
        '; retained known-fee reserve composition now detaches all four supplied account observations before component review or balance arithmetic, preventing caller mutation from binding different manifests across the composed result' if item.id=='M8' else ''),
    item.remaining) for item in MILESTONES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; simulated tier-entry admission now reads its durable permission switch and budget/allocation cap inputs from one authoritative SQLite snapshot, preventing a stale enabled result from being combined with later settings' if item.id=='M3' else
        '; paper-forward runtime now retains repeated exchange timestamps as ineligible incidents without advancing feed or coverage baselines, and the disposable forward drill requires the duplicate to be rejected' if item.id=='M7' else
        '; direct post-reconciliation and fee-reference compositions now validate and detach all four supplied account observations before component review or replay, preventing caller mutation from selecting transient manifests' if item.id=='M8' else
        '; retained forward-trial review now requires strictly advancing exchange timestamps, so repeated ticks cannot contribute eligible observations, coverage intervals or an incident-free 72-hour result' if item.id=='M9' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Retained trial coverage now excludes intervals adjacent to cross-session-disqualified observations and validates requested-window boundaries with retained neighboring context. This is local operational-evidence correctness, not genuine-feed provenance, sustained-trial acceptance, performance or release permission.')
    if stage['id']=='D8' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Equal-time cross-session price disagreements now reject all conflicting supplied observations without an arbitrary session winner, and none of those rejected points can establish a later chronological reference. This remains conservative local evidence review, not source attestation or sustained-trial acceptance.')
    if stage['id']=='D8' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + (' Feed admission and retained cross-session trial review now apply the same bounded jump check to bid, ask and last; a stable last trade cannot conceal a large executable-price shift. Cut-guarded durable order transactions also recheck the independent latch after their final mutation and before commit, rolling back both order and linked-portfolio state when the cut wins. These are offline fail-closed controls, not genuine-market or sustained-trial acceptance.' if stage['id']=='D3' else
        ' Known-fee reserve composition now validates and detaches every supplied account observation before any component review or arithmetic, preventing caller-owned mutation from mixing manifests. This is local supplied-evidence integrity, not provider/account/funds acceptance.' if stage['id']=='D4' else
        ' Executable-price trial continuity and the final pre-commit emergency-cut fence now have dedicated offline regressions. This does not establish native durability, market realism, independent acceptance or release permission.'),
    ) if stage['id'] in ('D3','D4','D8') else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + (' Tier entry now derives its permission and exact cap from one durable settings snapshot, while duplicate exchange ticks remain retained but ineligible and cannot update runtime feed state. This is local fail-closed admission evidence, not genuine-market or sustained-operation acceptance.' if stage['id']=='D3' else
        ' Direct post-reconciliation and fee-reference reviews now detach all four validated observations before component review and replay. This is caller-mutation resistance over supplied local evidence, not provider/account/fee/funds acceptance.' if stage['id']=='D4' else
        ' Retained trial review now treats non-advancing exchange timestamps as incidents rather than coverage. A 72-hour sequence made from repeated timestamp pairs cannot satisfy the incident-free target. This is conservative local evidence review, not source attestation or sustained-trial acceptance.'),
    ) if stage['id'] in ('D3','D4','D8') else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' The mandatory reconciliation profile now carries one exact-schema offline matrix across the single-attempt POST, bounded GET and Activity SSE uncertainty treatments. Missing cells, polymorphic values and promoted authority fail closed. This is compact injected local coverage, not provider identity, authorized paper-account acceptance, broker-wide pacing, genuine stream delivery, sustained operation or release permission.')
    if stage['id']=='D4' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Current modeled fills are independently bound across the durable order journal and forward event chain at full event/economic detail. A cut published after a runtime-clock callback or between atomic quote-order steps revokes stale mutation authority and rolls back the whole local transaction. This is offline local consistency and emergency-stop hardening, not actual provider execution, broker acceptance, market realism or release permission.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + (' Paper-forward session preparation now replays retained quote chains and hydrates the highest accepted exchange timestamp plus its price baseline before a new session can start. A cold restart therefore cannot reuse a boundary tick or immediately reset executable-price continuity. This is local supplied-history hardening, not genuine-feed or broker acceptance.' if stage['id']=='D3' else
        ' Retained trial review now enforces exchange-time progression across session boundaries, tracks even price-rejected supplied ticks against later reuse, and removes intervals adjacent to a repeated or regressed point. Rapid session rotation cannot manufacture incident-free 72-hour coverage by resetting the marker. This is conservative local evidence review, not sustained-trial acceptance.'),
    ) if stage['id'] in ('D3','D8') else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; paper-forward fresh exchange timestamps now enter a dedicated append-only chain that FULL-syncs before price-guard evaluation, and cold restart derives its monotonic floor from those admissions even when process death leaves no quote/outcome row; new-format quotes bind the exact session/cycle, sanitized quote digest, source, policy and evaluation/exchange times, while legacy sessions remain explicitly distinguishable and readable' if item.id in ('M3','M4') else
        '; paper-forward diagnostics now reserve and report the independent exchange-admission capacity without pruning, and retained trial review binds the admission chain into its stable snapshot while treating a crash-tail marker as a review incident with zero coverage authority' if item.id=='M5' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + (' A FULL-synced exchange-admission chain now closes the process-death seam between freshness admission and price review: restart consumes a committed tail marker even when no quote/outcome row survived, while eligible and price-vetoed quotes must bind the exact marker. This is local reconciliation integrity, not genuine-source or broker acceptance.' if stage['id']=='D3' else
        ' Retained trial review includes exchange-admission heads/counts in its stable binding; unmatched crash tails are operational incidents, add no coverage and cannot be replayed into a later session. This is conservative local evidence accounting, not a passed sustained trial.'),
    ) if stage['id'] in ('D3','D8') else stage) for stage in DELIVERY_STAGES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + (' Every structurally valid fresh exchange tick now consumes the monotonic runtime and cold-restart marker even when its prices are rejected, while only eligible quotes seed the price guard. Repricing the same timestamp can no longer convert a veto into strategy input. This is local fail-closed feed handling, not genuine-source or broker acceptance.' if stage['id']=='D3' else
        ' Retained trial review now carries rejected fresh ticks through the global cross-session exchange floor without granting them price-reference or coverage authority. A later eligible observation cannot reuse that timestamp to manufacture coverage. This is conservative local trial accounting, not sustained-trial acceptance.'),
    ) if stage['id'] in ('D3','D8') else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; direct simulated BUY admission now obtains SQLite writer reservation before its integrity audit and performs the audit, exposure/cash checks and mutation in that one transaction, preventing another process from corrupting the chain between a passing audit and new exposure while preserving reducing SELL exits' if item.id in ('M3','M4') else
        '; standalone paper-ledger integrity review now holds one explicit read snapshot, and the exact-head Windows saved-evidence job has a finite 45-minute outer budget after v0.340.386 CI exhausted the former 15-minute budget during bounded profile execution; per-profile child limits remain fail closed' if item.id=='M9' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + (' Direct simulated BUY admission now reserves the ledger writer before running its integrity governor, so a passing audit and new-exposure mutation share one authoritative transaction. Reducing SELL exits remain available. This is offline local risk/accounting integrity, not actual broker execution or funds acceptance.' if stage['id']=='D3' else
        ' The exact-head saved-evidence Windows job now has measured finite orchestration headroom after the prior 15-minute cap cancelled active bounded profiles. Profile child deadlines remain fail closed; fresh exact-source CI is still required and this change grants no Windows, installer or release authority.'),
    ) if stage['id'] in ('D3','D8') else stage) for stage in DELIVERY_STAGES)

MILESTONES = tuple(Milestone(item.id,item.title,item.status,
    item.evidence + ('; every standalone and API emergency cut now treats the in-process gateway return only as a warm acknowledgement and independently reopens/replays the exact order journal before reporting durable cleanup confirmed; absent, replaced, malformed or still-unhalted history remains UNCONFIRMED while the independent latch and runner stop stay active' if item.id=='M7' else ''),
    item.remaining) for item in MILESTONES)

DELIVERY_STAGES = tuple((dict(stage,
    progress=stage['progress'] + ' Emergency-stop completion now requires both the exact warm gateway acknowledgement and a fresh durable journal replay proving retained history and halted state. A false success return, lost commit or valid unhalted replacement cannot produce VERIFIED; intake remains latched and cleanup is explicitly retryable. This is bounded local fail-closed evidence, not native interruption, broker-account or sustained-operation acceptance.')
    if stage['id']=='D3' else stage) for stage in DELIVERY_STAGES)

PROGRESS_SUMMARY = 'Current position: local research and experimental local paper trading are implemented foundations; broker-paper integration and acceptance are the next major gate. Secure funding, governed live workflows, computer-off cloud and final independent/owner acceptance remain unfinished. Version numbers and passing test counts are not percent-complete estimates.'
CURRENT_HARDENING = "v0.340.396 makes persisted counterfactual broker-intent reconciliation fail closed while the independent emergency-cut latch is present, including the interval before its SQLite halt commits. It samples the latch before, inside and after the exact request guard without locking or delaying the stop writer. The composition also binds the exact production order replay, request guard and request fingerprint implementations and rejects class-level substitution before a matching fake reader can execute. Existing order/request physical identities, bounded fingerprints and semantic replays remain pinned. This is bounded sequential local reconciliation evidence, not broker/account/funds acceptance, genuine-feed or sustained-trial acceptance, native Windows/installer/power-loss acceptance, performance, profitability or release approval. The counterfactual plan remains permanently unsendable and every broker, funding, live, cloud, spend and release authority flag remains false. Runtime engagement and optional AI remain OFF by default; limits, account checks, reducing exits and fail-closed behavior remain unchanged. No button or editable parameter was added; seven beginner-friendly tabs and black/bright-purple bottom-right hover help remain unchanged. Rough implementation remains about 40% with an exact weighted index of 38% (30-45%, low confidence), with 0/9 major stages fully accepted."
PROGRESS_DIAGRAM = 'flowchart TD\n    A["Local research and paper foundation"] --> B["CURRENT: broker-paper integration"]\n    B --> C["Sustained trials and independent review"]\n    C --> D["Secure funding and governed live workflows"]\n    C --> E["Secure computer-off cloud"]\n    D --> F["Complete acceptance and owner approval"]\n    E --> F\n    F --> G["1.0 release"]'

def markdown():
    d=snapshot()
    lines=['# Nexus Operative — read-only 1.0 roadmap','',f"Revision: {REVISION}. Branch: `{BRANCH}`.",'',
           'Generated from `nexus/release_roadmap.py`. View in PORTAL or `GET /api/roadmap`. Changes require source review; the application has no roadmap write endpoint.','',
           PROGRESS_SUMMARY,'','Current hardening: '+CURRENT_HARDENING,'','```mermaid',PROGRESS_DIAGRAM,'```','',
           '| Milestone | Status | Evidence | Remaining acceptance |','| --- | --- | --- | --- |']
    lines += [f'| {m.id}: {m.title} | {m.status} | {m.evidence} | {m.remaining} |' for m in MILESTONES]
    lines += ['','## Product milestones: funding, autonomy and final release','','| Stage | Status | Built so far | Still required |','| --- | --- | --- | --- |']
    lines += [f"| {s['id']}: {s['title']} | {s['status']} | {s['progress']} | {s['remaining']} |" for s in d['delivery_stages']]
    lines += ['',d['production_scope']]
    estimate=d['progress_estimate']
    lines += ['','## Rough implementation progress — not release acceptance','',
        f"As of {estimate['as_of']}: about {estimate['implementation_estimate_percent']}% of the full agreed implementation scope (rough range {estimate['uncertainty_low_percent']}–{estimate['uncertainty_high_percent']}%; low confidence). {estimate['fully_accepted_stages']}/{estimate['total_stages']} major stages fully accepted. No release date established.",'',estimate['methodology'],'',
        '| Stage | Relative scope weight | Rough implementation | Acceptance |','| --- | --- | --- | --- |']
    lines += [f"| {row['id']}: {row['title']} | {row['weight_percent']}% | {row['implementation_estimate_percent']}% | {row['acceptance_status']} |" for row in estimate['stages']]
    lines += ['',estimate['update_policy'],'',f"Assessment basis: commit `{estimate['basis_commit']}`; exact weighted index {estimate['weighted_index_percent']} before rounding. This is an assessment, not an independently measured completion percentage or permission."]
    lines += ['', *milestone_task_markdown_lines()]
    lines += ['', '## Remaining milestone acceptance checklist', '',
        'This is a read-only work plan, not an executable workflow or trading permission. Steps remain REVIEW_REQUIRED until separately evidenced and reviewed. Dependencies describe acceptance sequencing; development can proceed in parallel. The next major operational gate is D4, alongside sustained D3 trials.']
    stages_by_id = {stage['id']: stage for stage in d['delivery_stages']}
    for stage in d['acceptance_plan']['stages']:
        dependencies = ', '.join(stage['unaccepted_dependencies']) or 'None'
        lines += ['', f"### {stage['stage_id']}: {stages_by_id[stage['stage_id']]['title']}", '',
            f'Unaccepted prerequisite stages: {dependencies}.', '']
        lines += [f"- [ ] {step['id']} — {step['title']}: {step['required_evidence']}" for step in stage['steps']]
    lines += ['','## Standing target settings (verify actual runtime separately)','']+[f'- {key}: {value}' for key,value in SETTINGS]
    lines += ['','## Intended production experience','']+[f'{i}. {step}' for i,step in enumerate(PRODUCTION_USER_FLOW,1)]
    lines += ['','Bank authorization, real funding and live execution are future work. Built-in numerical intelligence is included; external AI is optional. The current fixed BTC paper policy executes trades; learned council output remains advisory.','']
    lines += ['','## Minimal first-run setup','',FIRST_RUN['goal'],'',
        'Defaults: $10,000 virtual budget; Terrestrial entry allowance; BTC hourly research with1,000 bars; bounded public-history collection and included numerical models. Solar/Universal, paid AI and provider accounts are optional. Existing settings, explicit OFF selections and retained accounting/stop history are preserved. Compatible unused virtual accounting is created only on confirmed Engage; sessions never restart automatically.',
        '', 'Every To Do checkbox explains what it checks, why it matters and whether it is included, required for saving/paper configuration, created on Engage, optional or future work. Optional and future boxes may stay unticked.', '',FIRST_RUN['acceptance']]
    lines += ['','## Next sequence','']+[f'{i}. {step}' for i,step in enumerate(NEXT,1)]
    lines += ['','## Institutional design strategy','',
        'See [INSTITUTIONAL_DESIGN.md](INSTITUTIONAL_DESIGN.md) for the dated public-source comparison of AIM/PORT/EMSX, FlexTRADER, Charles River, Iress, Tbricks, quantitative execution research, IBKR and licensed C++ candidates.',
        '', 'Implemented: original bounded offline BTC/USD execution-quality arithmetic plus private exact-head locally accepted request/fill derivation and repeat journal replay. Quotes/clocks remain supplied and unverified; fees stay unlinked and all-cost totals unknown. Planned: independently accepted quote/fee/event-time bindings, event-driven adapters, cost-aware proposals, shared reservations, measured routing and selective native acceleration.',
        '', 'Council research remains advisory. Independent deterministic risk/stop checks and one execution owner per account are the target execution design. No proprietary code import, native C++ execution core, nanosecond broker-fill claim, broker routing, guaranteed growth or automatic policy promotion is implemented.']
    lines += ['','## Explicit expansion and cloud goals','']
    for goal in d['strategic_goals']:
        lines += [f"- {goal['id']}: {goal['title']} — {goal['status']}. {goal['goal']}"]
        if 'cost_preference' in goal:lines += [f"  Cost preference: {goal['cost_preference']} Acceptance: {goal['acceptance']}"]
    lines += ['',d['speed_target'],'','## Resume after a conversation cutoff','',d['resume'],
              '', 'Run `python -m pytest -q`, Python compilation and PORTAL JavaScript validation. Record exact commits and CI outcomes. Keep 0.x until release gates pass and owner approval is explicit. Never describe an unverified gate as complete.','']
    return '\n'.join(lines)


if __name__=='__main__':
    print(markdown(),end='')
