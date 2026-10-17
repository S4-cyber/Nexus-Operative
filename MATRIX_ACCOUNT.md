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
- The initial store is capped at 100,000 observations without automatic deletion (about seven active days at six seconds). Longer range selectors work over whatever history is retained, not a promise of a full year's coverage. Planned durable rollups/export/offload must preserve gap/coverage and per-position semantics before sustained month/year operation is accepted. Cap exhaustion pauses observation, not execution; the UI displays the reason. This is a remaining scale limitation.
- Browser screenshots use explicitly supplied account fixtures, not market performance evidence. Public quotes and liquidation values are estimates; account integrity remains independently unverified. Existing rain and synthetic telemetry are decorative/research components and clearly separate from account plots.

The current collector runs in the local application. Computer-off cloud continuity is a separate planned capability in CLOUD_OPERATION.md; changing this graph does not deploy a remote runner.

The display's observation/refresh cadence does not pace the execution engine or establish any broker throughput. Performance claims require independent end-to-end execution measurements.
