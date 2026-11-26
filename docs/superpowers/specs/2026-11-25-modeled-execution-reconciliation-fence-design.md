# Modeled-execution reconciliation fence

## Problem

The retained forward-trial coverage review currently treats a schema-valid
`BUY` or `SELL` outcome as a complete, non-incident event when its local event
chain and quote digest agree. That proves only local event integrity. It does
not prove that the modeled execution is bound to the durable order/fill
reconciliation evidence required for an incident-free sustained trial.

As a result, an otherwise clean supplied fixture can report
`incident_free_coverage_target_met=true` even though its modeled-fill digest is
not independently reconciled to durable execution state.

## Decision

Add one fail-closed diagnostic fence to `forward_trial_review`:

- require every schema-valid `BUY` or `SELL` event to carry a quote-evidence
  digest, preventing execution events from being reclassified as lifecycle;
- require a matched outcome timestamp not to precede its bound quote;
- count in-window `OUTCOME` records whose decision is `BUY` or `SELL`;
- publish the exact count as `modeled_execution_events_in_window`;
- when the count is non-zero, retain raw declared-public coverage calculations
  but classify the result as an operational incident;
- add the fixed reason `MODELED_EXECUTION_RECONCILIATION_NOT_BOUND`;
- keep every authority flag false and do not attempt a positive reconciliation.

The release-profile validator will require the new field and exact fixture
count. Trial-readiness will continue to embed the coverage review unchanged,
so the blocker is preserved without creating, repairing, or mutating an order
journal.

## Safety and compatibility

This change is intentionally one-way and fail-closed. It cannot grant broker,
account, funds, strategy, sustained-trial, release, live-order, or money-
movement authority. `HOLD` outcomes remain a control case. No UI control,
button, editable parameter, provider access, network access, or execution path
is added, so existing seven-tab navigation and hover-help conventions are
unchanged.

A later positive acceptance increment must bind each modeled execution in the
reviewed window to explicit durable order/fill receipts under one coherent
snapshot. That larger composition is out of scope here.
