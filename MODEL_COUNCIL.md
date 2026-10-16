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
