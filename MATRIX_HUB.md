# Collective Matrix hub and learned momentum research

Status: local foundation implemented; no shared cloud host, global instance synchronization, external AI council service or model-driven execution is deployed. Read CLOUD_OPERATION.md for the free-tier and no-dedicated-account preferences and hosting limitations. A more complex model or more data does not establish an advantage over a market.

## Low-labor workflow

PORTAL now has a Matrix Hub card. Select BTC, ETH or SOL, five-minute/hourly/daily candles and a maximum research window. Build Hub & Learn makes four bounded public GET requests for up to 600 closed candles, deduplicates them directly into the hub, seals the dataset, fits or reuses a model artifact and trains or reuses the bundled three-learner council and two deterministic reviewers in that pack. Daily bars can supply longer context; completeness and availability are not guaranteed. No paid inference, credential read or order is part of this action. Overlapping builds are rejected within this backend process.

Learn from Stored Data performs no public downloads: it imports an existing valid local candle cache where present, then uses retained hub history. It requires at least 240 consecutive bars. A requested maximum up to 10,000 uses available records; absent history is not fabricated. Source digest corruption or local revision flags stop import. Successful earlier pages remain after a later download fails; retrying reuses the same identities.

Hub Status is read-only. Review Cached Prediction reads the small persisted model and current dataset-generation metadata; it does not rescan the price archive or train. Old/future timestamps and any changed dataset generation abstain. The model is not installed into the forward runner and does not alter its fingerprinted policy, tier permissions or risk checks.

API:
- `GET /api/matrix/hub`: counts, conflicts, storage and connection status; no initialization.
- `POST /api/matrix/hub/build?product=BTC-USD&granularity=3600&limit=1000`: explicit bounded public collection and research preparation.
- `POST /api/matrix/hub/prepare?...`: stored-data-only preparation.
- `GET /api/matrix/hub/signal?product=BTC-USD&granularity=3600`: cached research prediction or abstention.

## One identity, no collector copies

Canonical identity is venue + product + granularity + UTC candle start. Currently only Coinbase Exchange public BTC/USD, ETH/USD and SOL/USD spot candles are admitted, with separate interval namespaces. Numerically equivalent OHLCV strings normalize identically. Instance IDs, credentials, account balances, positions, custom URLs and extra fields are rejected from ingestion.

A repeated identity with identical canonical content counts as a duplicate without creating another row or changing first receipt time. Different content for that identity is retained once in bounded conflict quarantine; it never overwrites accepted history or wins by majority vote. A conflicted dataset cannot train. Different venues/intervals must not be falsely merged into one apparent price. Current storage is a local SQLite service foundation, not a network-shared SQLite file.

Ingest transactions serialize independent local collectors through SQLite. Failed capacity/commit does not acknowledge new rows. New records or new conflicts increment a per-market/interval generation in the same transaction; duplicates do not. Model publication rechecks that generation after fitting, and cached signals reject a changed generation. This avoids training on a moving dataset or continuing to use a model after a known conflict.

Public-source responses use the fixed allowlisted candle path, no redirects, exact response-URL checks, five-second timeouts and bounded reads. Only closed, aligned, structurally consistent bars are admitted. Missing periods stay missing. First hub receipt is recorded by the collector, but does not prove what market data was historically available at an earlier backtest date. Redistribution rights, independent provenance and poisoning resistance remain acceptance work before public multi-user pooling.

## Compact, read-only research artifacts

Hub data lives under `NEXUS_PORTABLE_DATA/matrix_hub`. Canonical bar payloads are zlib-compressed and SHA-256 checked; a price record is stored once per identity. Read-only dataset/model packs are addressed by the hash of canonical uncompressed content, so identical preparation reuses the artifact. Pack decompression, raw size, stored size and fields are bounded. Publication syncs a temporary file then atomically renames it; filesystem read-only attributes discourage accidental editing, and hash checks detect modification. They are not signatures, source attestation, encryption or a defense against the filesystem owner/administrator. Directory sync is performed where supported; Windows power-loss acceptance remains separate.

The materialized dataset pack is a bounded research view of canonical storage, not a second upload from every client. Models are tiny separate artifacts containing coefficients, training-only scalers, data identity, metrics and current feature context. Trade-path integration should use a validated, fresh compact signal with the normal governor; repeatedly scanning the entire history on every trade would add latency. No such execution integration is claimed here.

Budgets: 250,000 canonical bars, 5,000 unique conflicts, 128 MiB SQLite pages; up to 64 artifact/cache files and 32 MiB total cached artifacts, with per-pack limits of 2 MiB compressed/4 MiB raw. A 256 MiB free-disk check provides local headroom but is not a disk reservation or a cloud quota. Incomplete temporary files count toward cache admission; none are silently discarded by a capacity check. Budget exhaustion fails rather than evicting evidence. These are bounds, not proven production scale, unlimited history or the entire application's storage usage. Cold offload, authenticated artifact retention/garbage collection and multi-resolution summaries are future work.

Existing verified offline data-directory backup/restore includes hub rows and read-only artifacts. Backup copies are not encrypted by this feature. A restored model remains research-only and timestamps/generation are still checked.

## What actually learns

Three fixed-alpha ridge regressions learn future log-return targets at 1, 3 and 12 bars. Features use only closed past bars: momentum over 5/20/60 intervals, twenty-return volatility and volume surprise against twenty prior observations. The initial implementation is a lightweight trained linear baseline, not an LLM, neural trading system or demonstrated superior predictor.

The first 70% of bars form the training prefix. Labels overlapping the chronological held-out tail are purged; feature means/scales are learned only from training. Held-out errors are reported alongside zero-change and short-momentum baselines without choosing a winning model on the holdout. Zero returns are excluded from directional-hit scoring. The stored coefficients do not refit on the held-out tail. Training residual percentiles are diagnostics, not calibrated confidence intervals or success probabilities. Large extrapolations abstain.

Research limitations: retrospective source availability, repeated experiment selection, nonstationarity, sparse OHLCV rather than order books, overlapping horizon targets, unmodeled execution costs and single-source data. Held-out MAE or direction accuracy is not profit, executable capacity or release acceptance. Independent walk-forward/nested selection, uncertainty calibration, drift monitoring and cost/latency/forward evidence remain required. Stronger model families should be evaluated against this baseline rather than assumed better because they are larger.

The hub uses the built-in numerical council described below. The separate legacy external-adapter diagnostic retains five deliberately abstaining fixtures; no paid or external AI models are connected. A future adviser may read a sealed dataset through a restricted read interface and submit a bounded testable proposal. It must not mutate canonical records, invent tools or bypass the deterministic governor. Every current report and signal leaves paper/live order authority false.

## Shared-cloud target

Deploy one authenticated central public-data ingest service and durable quota-controlled store, then distribute signed/versioned read-only manifests/artifacts to authorized Nexus clients. Upload canonical public records only; never collect broker keys or private portfolios into the shared research pool. Verify source rights/attestation and rate/cost admission, use encrypted transport/storage/backups with separately managed keys, and test malicious/replayed clients, conflicts, outages and recovery. Clients should cache validated compact artifacts, not repeatedly upload or download full copies. Local OS locks and file read-only flags do not provide distributed/cloud authorization. No host, account, deployment or expenditure has been created by this implementation.

References reviewed 2026-10-17:
- Coinbase public candle specification and missing-history/size limitations: https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
- Chronological validation and gaps: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- Training-only preprocessing and leakage: https://scikit-learn.org/stable/common_pitfalls.html

The implementation uses the Python standard library; citing validation principles does not mean scikit-learn is installed or used. Run the full suite, Python compilation, PORTAL JavaScript syntax and `node tools/hub_controls_smoke.cjs`. Browser acceptance is fixture-intercepted and cannot prove live predictive performance.

## Built-in council (v0.80)

Hub preparation now trains and seals an onboard council in the same model pack. No API key, external provider, downloaded weights or new dependency is required. The existing external-adapter diagnostic remains an intentionally disconnected fixture and the separate execution governor remains unchanged.

Three ridge learners predict three-bar log returns using fixed feature views: trend (5/20/60-bar momentum), activity (volatility/volume surprise), and combined. They have separate fitted coefficients and training-only scalers but share a model family and dataset: their agreement is correlated, not independent confirmation. Training targets end strictly before the 70% holdout boundary. Three consecutive fixed holdout blocks must each have at least ten observations and strictly lower MAE than both zero-change and short-momentum baselines before a learner receives FURTHER_RESEARCH. No winner or parameters are selected on this tail. These blocks are retrospective diagnostics, not walk-forward validation, costs, confidence calibration or profit evidence.

A deterministic validation reviewer recomputes eligibility from measurements rather than trusting votes and flags directional disagreement. A separate provenance/execution reviewer abstains because independently verified source, executable fills and account evidence are absent. Every result has NO_ORDER_AUTHORITY and paper/live order flags false, including unanimous research support. No council result replaces the forward runner's fixed fingerprinted policy.

Use **Learn from Stored Data** after collecting new history. Unchanged dataset plus council engine version reuses the exact saved artifact; changed data or engine retrains. The complete candidate is sealed before the current research reference is published under the existing generation check. Failed training/publication leaves the prior reference and artifacts intact; old-generation signals still abstain. Previous versions remain readable for inspection, never automatic execution rollback. Existing artifact quotas stop further admission without deleting evidence. Legacy packs lacking the council migrate on next explicit preparation. v0.80 had no background scanner. v0.83 adds the bounded, explicitly armed local worker described below; no self-modifying source is installed.

### Keeping models current

Learning data updates coefficients; reviewed application releases update bundled algorithms and their engine version. The v0.83 local background worker coalesces stored changes under the limits described below; continuous source collection, longer unattended acceptance and execution promotion remain separate. Add stronger families and walk-forward/cost tests before claiming model quality. Shared cloud aggregation and anti-poisoning remain separate unfinished work.

Larger local language models are an optional future backend: separately installed licensed weights, a pinned runtime, local resource measurements, content hashes, explicit downloads, compatibility tests and retained prior versions are needed. Never execute downloaded/generated Python or trust a model's suggested permission changes. This release does not contain a language-model runtime or GPT weights. A new algorithm/weight release can improve quality or efficiency but cannot increase physical CPU/GPU speed, RAM or storage. Hardware must be measured on the actual installation, not inferred from this development container. GPT-6.1's API documentation does not establish downloadable weights; local gpt-oss deployment is a separate documented option, not a claim of equivalence.

References checked 2026-10-17: https://developers.openai.com/api/docs/models/gpt-6.1-sol and https://developers.openai.com/learn/gpt-oss . No provider request, weight download or hardware purchase occurred.

## Expanding-window and modeled-cost checks (v0.82)

Engine v2 adds three fixed expanding training prefixes, starting at half the sealed history. Each fold refits every feature specialist with training-only scalers and labels purged before its first decision. Earlier completed test data can enter the next fold's training prefix; future data cannot alter an earlier fold. Features are computed once per closed bar and shared across original horizon fits, council fits and fold fits. There is no parameter search or selection of a winning feature view.

A fixed diagnostic policy checks every fourth bar, enters long only when the model's estimated return exceeds 30 bps, waits a full bar after the decision close (open t+2), and exits at open t+5. Positions do not overlap. All views use identical schedules/thresholds, reported under 10/25/50 bps roundtrip cost assumptions, split equally between entry and exit. This delay intentionally tests sensitivity to execution timing; the model still predicts three-bar close returns. The policy is separate from the deployed paper runner and does not claim executable bar-open fills.

Reports persist each fold's fitted weights, boundaries, cost summaries, bounded trade examples and complete-trace digest. Trades can be reproduced from the sealed dataset and fixed implementation. Metrics use sequential unit-capital compounding, drawdown, nonempty trade counts, normalized single-trade-return profit factor and the number of positive net folds. No-trade periods are explicitly reported rather than treated as evidence of skill; unrepresentable compounded returns abstain without overflow. Profit factor is null when no loss denominator exists. These assumptions exclude account allocations, order-book depth, impact, partial fills and independent source/point-in-time verification.

The council's deterministic validation reviewer now also requires at least five delayed-fill trades and positive modeled net return at 25 bps in each of all three folds before a learner can retain FURTHER_RESEARCH. Duplicate/missing per-fold records cannot substitute for distinct tests. This is a fixed screening rule, not calibrated statistical significance or forward acceptance. All original MAE/block checks and the separate provenance/execution veto remain. Even unanimous research support cannot enable paper/live orders. Every feature view and cost scenario stays visible, including losses and empty results.

Manual Build and Learn-from-Stored-Data share one nonblocking backend job lock. Concurrent requests get a busy response rather than duplicating training; GET/status/cached signals remain read-only. This coordinates one application process, not multiple hosts or distributed ownership. Manual preparation remains available. v0.83 adds explicitly started, bounded local background refresh; sustained unattended/source-collection acceptance remains open.


## Bounded local refresh (v0.83)

Research → Start Local Learning Refresh requires an active Portal and explicit action. It captures the selected BTC/ETH/SOL market, five-minute/hourly/daily interval and at most 1,000 bars (minimum 240). Changing selectors does not change a running session. Stop and start again to apply changes. GET /api/matrix/hub/refresh reads state only; separate POST start/stop routes arm/stop it. A fresh process always starts stopped, and no permission/configuration is restored from disk.

The worker checks once per minute, imports at most the captured number of already saved public candles and compares the hub generation. It skips archive/model preparation entirely when generation is unchanged after a successful check. A first current pack can be reused without a new fit. It does not download prices or use credentials/providers: collect history separately. Without newly stored data, predictions expire normally rather than being fabricated. It is not a continuous exchange collector or cloud execution service.

Every session stops after 60 polls or eight training attempts, including attempts invalidated by concurrent data changes. The shared nonblocking build lock prevents overlap with manual jobs in this backend. A separate OS file lock excludes other refresh workers on this data directory, including other products; process death releases ownership. Manual jobs in other processes are not covered by that OS worker lock. Existing row/byte/disk/object quotas, immutable evidence retention, candidate validation and generation-checked publication remain mandatory. These are operation/data limits, not hard CPU-time limits or a preemptible training sandbox.

Pause, Emergency Halt/reset and application shutdown stop the worker. Stopping fences model-reference publication under the same worker lock used by publication. An in-flight import, calculation or candidate file seal may finish; it cannot publish a new current model after stop acknowledgment. Ownership remains held until the cycle finishes. Sealed candidates may remain as retained evidence and count against quotas. A new session is denied until the old cycle/thread/owner finishes. Shutdown also runs refresh cleanup if another component's cleanup fails.

Missing/short stored datasets wait within the poll budget. Data-generation races retry later and consume training-attempt budget; corrupt, conflicting, unusable, quota-exhausted or unavailable storage/models pause with sanitized reasons. Clock regression/nonfinite values, a changed data directory or lost Portal admission stop work. Failed candidates leave prior model references/artifacts intact; stale-generation signals still abstain. Read-only status reports phases, in-flight work, attempts/polls/skips, captured scope and the last worker model. Status refreshes locally every 15 seconds only while Research is visible. Runtime counters/reasons are not a durable audit or accounting journal.

The optional To Do row observes whether refresh is armed; it does not certify a fresh model or strategy. All research/model/execution vetoes remain. This worker cannot place paper/live orders, change the fixed forward policy, enable cloud operation or promise profitable self-improvement.
