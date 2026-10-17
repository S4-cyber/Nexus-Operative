# Collective Matrix hub and learned momentum research

Status: local foundation implemented; no shared cloud host, global instance synchronization, external AI council service or model-driven execution is deployed. Read CLOUD_OPERATION.md for the free-tier and no-dedicated-account preferences and hosting limitations. A more complex model or more data does not establish an advantage over a market.

## Low-labor workflow

PORTAL now has a Matrix Hub card. Select BTC, ETH or SOL, five-minute/hourly/daily candles and a maximum research window. Build Hub & Learn makes four bounded public GET requests for up to 600 closed candles, deduplicates them directly into the hub, seals the dataset, fits or reuses a model artifact and gives five existing council fixture adapters the same compact evidence. Daily bars can supply longer context; completeness and availability are not guaranteed. No paid inference, credential read or order is part of this action. Overlapping builds are rejected within this backend process.

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

All five current council adapter roles receive the same dataset/model hashes and forecast summary. They are deliberately abstaining local fixtures; no paid or external AI models are connected. A future adviser may read a sealed dataset through a restricted read interface and submit a bounded testable proposal. It must not mutate canonical records, invent tools or bypass the deterministic governor. Every current report and signal leaves paper/live order authority false.

## Shared-cloud target

Deploy one authenticated central public-data ingest service and durable quota-controlled store, then distribute signed/versioned read-only manifests/artifacts to authorized Nexus clients. Upload canonical public records only; never collect broker keys or private portfolios into the shared research pool. Verify source rights/attestation and rate/cost admission, use encrypted transport/storage/backups with separately managed keys, and test malicious/replayed clients, conflicts, outages and recovery. Clients should cache validated compact artifacts, not repeatedly upload or download full copies. Local OS locks and file read-only flags do not provide distributed/cloud authorization. No host, account, deployment or expenditure has been created by this implementation.

References reviewed 2026-10-17:
- Coinbase public candle specification and missing-history/size limitations: https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
- Chronological validation and gaps: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- Training-only preprocessing and leakage: https://scikit-learn.org/stable/common_pitfalls.html

The implementation uses the Python standard library; citing validation principles does not mean scikit-learn is installed or used. Run the full suite, Python compilation, PORTAL JavaScript syntax and `node tools/hub_controls_smoke.cjs`. Browser acceptance is fixture-intercepted and cannot prove live predictive performance.
