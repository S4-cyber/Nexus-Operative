# Emergency control under feed load

v0.182.2 changes only a disposable real-loopback foreign-origin denial fixture. The v0.182.1 overload-test setup allowance, actual two-second Emergency Stop/status assertions and production runtime behavior are unchanged.

Emergency Stop remains an independent paper throttle cut. It leaves the research engine, observer and bounded learning worker in their prior state, preserves committed fills/holdings and attempts durable cancellation of pending simulated remainders. Reset clears only the cut; Engage remains explicit. No broker, deposit, transfer, live order or cloud connection is added.

## Scheduling and acknowledgment

POST /api/simulate/halt is an async admission handler. It publishes the process-local halt flag and advances the cut epoch before waiting. Cleanup uses a lazy ThreadPoolExecutor with one dedicated worker; neither Starlette/AnyIO blocking feed routes nor the asyncio general executor use that worker. Repeated requests coalesce onto one pending concurrent Future, with no additional cleanup queue. Every request still advances the epoch so a concurrent reset cannot override it. Default process startup and GET status do not allocate this worker.

The worker acquires the existing trading-control lock, persists the gateway emergency halt/cancellation, invokes the runner’s existing stop fence even if storage fails, and records the audit event. The handler acknowledges success only after that operation returns. Existing SQLite write/replay locks, execution freshness/stop hooks, cleanup handling and local ownership remain authoritative. Committed fills before the cut are retained; this does not liquidate holdings. A previously admitted quote fetch may finish after the fence, with the runner stopped and ownership held until cycle completion.

Shielding prevents cancellation/disconnection of an awaiting HTTP task from cancelling submitted cleanup. Pending state is released by the concurrent Future completion callback, independent of the caller’s event loop. Async result exceptions are consumed for disconnected callers; expected errors return sanitized 503 details and leave intake latched. A failed executor submit can enqueue before thread start fails; the failure path cancels queued items and retires that executor, allowing a later explicit retry. It does not acknowledge a durable cut when worker admission fails.

## Observable states and reset

GET /api/status is async, no-store and performs no IO. The synchronous status() helper remains available to local code. The snapshot reports:

| State | Meaning |
| --- | --- |
| CLEAR | No process-local emergency latch is active. This grants no trading permission. |
| PENDING | A cleanup Future is pending; local API intake is latched. Reset is disabled/rejected. |
| VERIFIED | The local durable cut, runner-stop fence and audit returned successfully, or startup recovered a validated durable halt. |
| UNCONFIRMED | Local intake is latched but completion was not verified; review/retry the cut and inspect recovery. |

Verified is scoped to those local operations. It is not broker cancellation evidence, liquidation, complete strategy/account health, independent audit or a hard deadline guarantee. A stopped worker’s separate diagnostic/recovery markers still need review.

Reset validates explicit confirmation, captures the current epoch and rejects pending cuts in async admission before it queues on general-worker capacity. The blocking reset then checks the epoch/pending state again under trading control and after its durable operation. A newer cut wins, including one received while reset waits on the general executor or storage. Reset never engages a runner or changes the research engine’s on/off state.

## Shutdown and bounds

Lifespan cleanup drains the dedicated executor before other component cleanup. Drain rejects new job admission, still latches local intake and returns unconfirmed 503 for such requests. Waiting does not cancel queued/running cut work. Shutdown releases the executor so a later local application lifespan can create a new one; old futures do not carry running permission. Normal process restart still starts research/paper stopped and recovers the durable gateway latch.

This reserves execution-worker capacity; it cannot bypass OS scheduling, a stalled event loop/GIL, a held trading/SQLite lock, failed storage, process death or all resource exhaustion. Storage and runner cleanup can delay acknowledgment. No overall realtime timeout is claimed and no success is returned merely because a request was admitted. Network socket, clean-machine/native-window, sustained feed/CPU/storage overload and remote broker acceptance remain separate.

## Verification

Thirteen cases in tests/test_emergency_lane.py use the actual ASGI app and injected blocking feed/storage callbacks. They verify Emergency Stop and no-store status while AnyIO feed workers and, separately, both general pools are saturated; twenty concurrent cuts sharing one cleanup; no reset window; success/failure cleanup after caller cancellation; worker-construction, submit and actual thread-start failures with explicit retry; lifecycle drain and reopening; cleanup/audit failure state; status without storage/worker allocation; and a reset queued before a newer cut. Existing start/reset/partial-fill/fetch/clock/ownership/accounting tests remain.

tools/engine_controls_smoke.cjs executes the actual UI controller for pending/verified/unconfirmed text and reset disabling, alongside the original engine/Engage/Disengage/emergency behavior. Other controller/browser checks preserve beginner black/purple help and all seven tabs. Tests use synthetic funds and injected IO, not a production outage, a sustained source run or live-money acceptance.


## v0.88: immediate runner veto before storage waits

Emergency admission now also calls the runner’s request_cut signal before creating/queuing a durable job or checking lane shutdown. This only sets a process-local Event: it acquires no runner execution/control lock, performs no IO, releases no ownership and grants no acknowledgment. The runner checks it before preparation, before session-identity replacement, at cycle/fetch/submission boundaries and inside the existing post-write-lock fill clock hook. It remains set if worker creation or durable storage fails. Status enabled=false then means intake is blocked; it does not mean every thread or durable cancellation has finished. Forward status separately exposes cut_requested.

An order whose fill hook observes the signal cannot fill merely because an earlier SQLite lock was released. Orders/transactions already beyond that observation boundary may still finish before the full durable stop fence; already committed fills remain. Existing submit/terms/cancel commands are retained, and durable halt plus runner stop still precede success acknowledgment. This adds no command type or stored callable and does not change the policy fingerprint, freshness limits, account migration rules or cost assumptions.

Stop, engine on/off retries and preparation never clear this signal. Only the coordinator clears it after durable reset and the final epoch check under cut admission. The stop Event remains set, so clearing the cut cannot restart a worker. Cut-only worker exit does not release its process ownership prematurely; normal fenced stop/cycle completion handles that. The distinct signal preserves existing STOP diagnostics instead of treating a pending request as a completed stop. Interrupted preparation preserves the previous session identity/history when it can veto before a new session begins.

Eleven additional tests in tests/test_emergency_storage.py use actual SQLite write reservations on both order and linked-portfolio files, actual BUY/SELL fill transactions waiting for the order write lock, a real SQLite busy timeout, worker-admission failure, cut retention during preparation/stop/engine retry and preserved prior session identity. They verify no new priced fill at the post-lock veto, retained earlier positions/partial fills, truthful pending/unconfirmed states, explicit retry, correct reset and independent research.

Two cases terminate a real child process before durable halt commit and after commit while audit/acknowledgment is withheld. Another fresh process validates the durable truth, linked accounting, preserved fills, canceled versus pending remainders, released OS ownership and stopped startup/admission. Before-commit death does not persist the in-memory cut: startup can report CLEAR, but neither research nor paper resumes, and outstanding orders block a new session for review. After-commit death recovers the durable halt. No automatic repair or inferred completion is allowed. A separately persistent pre-commit cut-intent mechanism remains future work; the signal is not durable/distributed fencing or evidence of a broker stop.

## v0.89 independent local intent recovery

The dedicated cut worker creates `emergency_cut.intent` beside the order database before attempting SQLite halt/cancellation. This bounded, secret-free local latch survives a process death at the pre-commit boundary. Creation uses exclusive admission and file flush/fsync; POSIX also syncs the containing directory. Windows standard Python cannot fsync directories, so this is not a power-loss durability guarantee. Existing files, empty/interrupted records, malformed records, directories and symlinks all conservatively mean cut requested; recovery never follows or repairs their contents. Unreadable state also blocks intake. No unbounded journal is added.

Startup combines intent presence with validated database recovery. An intent with a clear database reports UNCONFIRMED, sets the runner cut signal and leaves both engine and trading stopped. A validated database halt remains VERIFIED. Startup/status never cancel an order or silently reconcile a pending remainder. Retry Emergency Stop explicitly to complete cancellation; inspect retained fills/holdings before resetting. Reset never starts trading or liquidates holdings.

Cut persistence and explicit reset remain serialized by trading control. Reset first fences the runner and commits the durable reset, then removes the intent, then checks the cut epoch before clearing the in-memory signal. A newer cut wins and re-establishes halt/intent. Failure to remove the intent leaves the local cut UNCONFIRMED even if database reset committed; retry is explicit. Intent-write failure still attempts durable halt and fenced stop, but returns an unconfirmed failure rather than success. Status GET remains a zero-IO snapshot.

There is an unavoidable interval before intent creation: worker admission failure, waiting for trading control, OS/event-loop stalls or process death before the intent write can still lose the volatile request. Every process nevertheless starts stopped. This local file is not encryption, tamper attestation, distributed coordination, broker cancellation or a hard realtime guarantee. Existing owner controls do not establish exclusive ownership across multiple independent API processes; secure cloud coordination remains open.

### v0.90 Windows existing-entry correction

Windows can report PermissionError/EACCES when exclusive file creation encounters an existing directory. Persistence now checks for observable latch presence after either EEXIST or EACCES and conservatively retains that entry. A genuine permission failure with no observable latch still fails; inaccessible inspection also fails closed. The path is never followed, overwritten or automatically removed. Reset still rejects a directory through its normal unlink failure. The exact v0.89 Windows regression exposed this platform difference; v0.90 includes the implementation correction and injected Windows-error coverage for both present and absent entries, without weakening the original directory-preservation assertion.

## v0.100 fresh-install stop inspection

Cold application import still inspects the independent cut intent and existing durable halt state. When no order journal exists, it now records no durable halt instead of invoking the write-capable recovery report and creating order/portfolio databases. Missing history is not permission to run: Portal and Engage still start stopped and ordinary account/admission checks remain required. An intent without history cuts intake as UNCONFIRMED; inaccessible/corrupt state or a symlink order path fails closed. Existing journal replay behavior is unchanged. The release HTTP trial verifies a real fresh source-process server creates no accounting files during checked startup reads and performs no credential or external connection operations. Native Windows/profile and existing-account recovery acceptance remain separate.

## v0.137 failed-reset durability restoration

A reset can commit the database clear and unlink `emergency_cut.intent` before a later directory synchronization reports failure. Returning 503 while keeping only the process-local cut left a crash window in which neither durable barrier was guaranteed on restart. The reset failure path now conservatively attempts both exclusive intent persistence and the transactional gateway emergency halt before returning `HALT_RESET_STORAGE_UNAVAILABLE`. Each restoration is attempted even if the other fails; the in-process runner cut is never cleared and no engine or trading worker is started.

The current request remains UNCONFIRMED because the reset did not complete as acknowledged. If both restorative writes succeed, a cold inspection can independently verify the durable halt; this does not convert the failed reset response into success. A regression fixture removes the real intent before injecting the simulated post-unlink failure, then verifies the intent record and database halt were recreated, admission stayed cut and an explicit later reset remained required. Native Windows filesystem-fault and power-loss evidence, sustained CPU/feed pressure, distributed coordination and broker cancellation remain open.
