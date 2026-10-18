# Nexus Council — specialist model integration contract (v2.2)

Five independent areas will eventually be served by separately evaluated models:

1. Market regime — microstructure, trend and liquidity classification
2. Strategy research — out-of-sample statistics and regime sensitivity
3. Execution quality — spreads, slippage, fees and delays
4. Portfolio risk — capital, exposure, tail events and drawdowns
5. Adversarial review — challenge stale data, leaks, overfitting and contradictions

**Current state**: Five *deterministic* rule evaluators in `nexus/council.py`. No LLM model API, inference, training, paid model account, broker order routing, or auto-trading is connected.

## Secure next-stage adapter interface

Future models must be unprivileged advisory processes receiving only bounded,
non-sensitive snapshots. They must return versioned JSON with: model identifier,
prompt version, reference data hashes, classification, evidence cited,
counterarguments, uncertainty and abstention reason. Their messages are
untrusted inputs and cannot change position caps, bypass the governor,
provide API keys or call broker adapters.

A separate deterministic risk engine independently checks every candidate.
Disagreement, missing evidence, slow model responses, cost uncertainty,
unreconciled positions or model outages mean NO TRADE.

Evaluation includes blind market periods, forward-only replay, adversarial
market shock simulation, calibration and false-positive rates. Model agreement
is not independent evidence if the models share the same data or training.

This architecture does not promise profit or zero losses; it is intentionally
designed to fail closed.


## v0.139 Found Markets and checklist scope

Read FOUND_MARKETS.md before extending discovery. To Do separates current baseline prerequisites, optional improvements and future connected features. Found Markets is optional and selected by default in the page for Start Portal; legacy Portal API callers remain opt-in. It runs four candidates per 15-minute burst, up to36 requests/hour across cooperating Nexus processes sharing one local data root,256 in-memory summaries and six fresh research selections, with explicit deterministic 24-hour/30-day/180-day council screens. Each public allowance is durably committed before transport; start/stop and process restart cannot reset it. Stop and engine admission fence publication; no background worker starts on application restart. Current-page switch preference is not persisted. GET status and checklist remain passive and create no budget journal.

Never promote discovery scores to expected profit, calibrated predictions or execution permissions. This first connector scans public USD spot crypto, not all asset classes. It rotates a bounded sample, revisits earlier leaders, uses single-source closed bars and coarse last-day turnover, and drops stale/gapped/negative/risky evidence. The shortlist uses best eligible horizon score, not balanced diversification. Trained-model forecasts, multi-asset connectors, costs/spread/depth, holdouts, eligibility, provider/shared-IP budgets beyond the cooperating local journal and sustained real-source acceptance remain open. The fixed BTC execution policy and emergency throttle remain independent.

Every new switch/button has practical black/purple help. The actual discovery controller tests optional startup/stop, coalesced reads/control requests, promoted reply rejection, stale-result clearing and private-error isolation without trading requests. The mandatory loopback HTTP profile adds passive discovery and explicit checklist necessity assertions; all examples remain offline fixtures.
