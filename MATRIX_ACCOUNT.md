# Singularity and Fractal account Matrix

Singularity plots whole local paper-account estimated liquidation value: cash plus each held quantity at fresh public bid, less the existing modeled 10 bps exit fee. Net change includes cash effects already recorded by the ledger. This is neither a connected broker balance nor a verified accounting audit.

Fractal plots separate colored per-product unrealized P/L in USD: estimated liquidation value minus held quantity times recorded average entry basis. It supports the currently available BTC, ETH and SOL holdings together. A closed holding ends its series; an incomplete mark creates a gap. New fills or a changed average basis can change unrealized P/L, so these lines are not lifetime realized trade returns. The latest quantities and values are also available as text.

Controls: Singularity / Fractal buttons, one minute, hour, day, month (rolling 30 days), year (rolling 365 days), year to date (January 1 UTC) and all-retained range selector, plus a dropdown of the most recent 50 journal adjustments. Selecting an adjustment shows timestamp, recorded quantity/basis and signed cash delta; it cannot submit or edit orders. All controls carry practical beginner help under UI_STANDARDS.md.

## Observations and limits

- `POST /api/matrix/account/observe` requires active, non-halted PORTAL. It reads existing paper accounting, obtains allowlisted public marks and writes only the separate `matrix_observations.sqlite3` observation database. It never initializes or changes paper positions, credentials or execution authority.
- Observations run approximately every six seconds while PORTAL is active. A local guard and SQLite lease prevent overlapping captures, with a minimum five-second interval. Capture does not hold the account's write lock while fetching quotes. If the account changes during fetch, discard the sample and retry on a later cycle.
- Both receipt and exchange timestamps must be fresh at valuation. An incomplete portfolio has no total equity point: never show cash alone as the whole account. Valid per-product marks may still be displayed. A time regression during fetching discards the sample.
- `GET /api/matrix/account?span=1d` reads retained data only and never fetches prices or creates an account or observation database. Invalid ranges fail explicitly. YTD is UTC; axis labels use the viewer's local timezone. No historical balances are reconstructed from present holdings.
- At most 1,200 actual points are returned per range. When downsampled, show spaced dots rather than invented connecting paths. Even full detail does not connect observations more than 15 seconds apart. No position movement is fabricated between samples.
- The active working table is capped at 100,000 observations. At capacity, completed UTC days move transactionally into a retained-history table in the same database, preserving every original ID, timestamp and JSON payload. The latest ID stays active so identities cannot be reused after a long offline gap. Copy and removal commit together under FULL synchronous durability; a failed move rolls back both. There is no automatic loss of retained observations.
- Total active plus archived retention is bounded at 6,000,000 samples (roughly 416 active days at six-second sampling, not a wall-time coverage guarantee). Cap exhaustion pauses observation, not execution; the UI displays the reason and retained counts. Month/year queries read both tables consistently and return at most 1,200 original points. Selection ranks IDs before loading JSON; downsampled views do not preserve every price extremum and deliberately display dots. Full original samples stay retained.
- This is logical history retention, not physical compression, disk-space reclamation, encrypted cloud storage or unlimited operation. The database grows with retained samples; no multi-million-row production-scale claim has been validated. A disposable 100,000-row synthetic check retained all rows and bounded its yearly response. Physical offload, byte-based admission, durable extrema/coverage rollups and large-scale acceptance remain future work. Old pre-archive databases are readable without GET schema writes; the next explicit observation adds the archive table.
- Browser screenshots use explicitly supplied account fixtures, not market performance evidence. Public quotes and liquidation values are estimates; account integrity remains independently unverified. Existing rain and synthetic telemetry are decorative/research components and clearly separate from account plots.

The current collector runs in the local application. Computer-off cloud continuity is a separate planned capability in CLOUD_OPERATION.md; changing this graph does not deploy a remote runner.

The display's observation/refresh cadence does not pace the execution engine or establish any broker throughput. Performance claims require independent end-to-end execution measurements.

Verified offline data-directory backup/restore includes active and archived Matrix history. A fresh-process fixture confirms the same retained points and account revision after restore. This does not encrypt a backup or authorize an automatic session restart.

Capture also rejects missing or conflicting active/retained ID heads before fetching prices. It does not automatically repair deleted or corrupted observation history.

## Storage admission

Observation collection now has a 1 GiB SQLite page budget and a 256 MiB free-disk reserve, in addition to the active and total sample caps. These are fixed local engineering limits, not configurable trading permissions. Byte/disk limits can stop collection well before six million observations or a full year's coverage. Already larger databases are not truncated or repaired automatically.

Before fetching quotes, collection checks free space, estimated insert capacity and temporary closed-day copy capacity. It leaves additional room for the existing database's rollback journal and a conservative write allowance. A second check after fetching catches space consumed during observation; denial rolls back a pending archive move and does not acknowledge a saved sample. SQLite's independent maximum-page setting rejects unexpected database growth; a real allocation-limit fixture verifies rollback and no acknowledgment. Individual new JSON observations are limited to 64 KiB.

The budget applies to this observation database, not execution journals, the entire data folder, backup copies or a cloud account. SQLite journals/sidecars can require extra temporary disk space. Free-space checks are snapshots, not reservations against other processes or a guarantee against a full disk. A missing storage volume or failed disk probe blocks collection without an automatic repair or a hung path search. Connections are explicitly closed on success and failure.

GET remains read-only and reports allocated/reusable database bytes, byte limit, disk free space, minimum/estimated required headroom and warnings. PORTAL displays used MiB and plain-language collection warnings while retaining historical graphs. No saved history is deleted to satisfy a budget, and no paper execution permission or session state is changed by these observation-only checks. Monitoring/collection is still local; this does not provide encrypted or computer-off cloud operation.
