# Risk and authority truthfulness design

## Intent

Close two independently reproduced fail-closed gaps in the local paper and
release-readiness surfaces. A direct reducing SELL must not commit from a quote
that expired while the ledger was being audited, and the read-only authority
inventory must not publish a safe summary that contradicts a malformed source
declaration.

## Goals

- Revalidate the exact already-captured public SELL quote after ledger audit
  and immediately before the first accounting mutation.
- Roll back the whole reducing exit when that quote is stale or invalid.
- Validate every authority declaration's exact shape, types, identifier,
  routes, gates, automatic behavior, and reviewed default before reporting it.
- Derive automatic-resume and OFF-default summary fields from validated rows.
- Keep every broker, funds, live, cloud, learned-policy and release authority
  false.

## Non-goals

- No second market read, retry, broker call, credential access or live order.
- No change to emergency-cut independence or reducing-exit availability when
  the supplied quote remains valid.
- No new Portal control, editable parameter, route or hover-help requirement.
- No change to the 38% weighted estimate, 0/9 accepted stages, release
  approval or trading permission.

## Approaches considered

1. Fetch a fresh quote inside the SELL transaction. Rejected because network
   I/O under an accounting lock would expand authority and failure modes.
2. Accept quote age at route admission only. Rejected because the observed
   ledger audit can consume the remaining freshness window.
3. Revalidate the exact captured quote immediately before mutation. Selected;
   it is deterministic, network-silent and symmetric with direct BUY.

For the authority inventory, permissively copying declarations and correcting
only top-level booleans was rejected because the rows could still contradict
the summary. Exact declaration validation followed by summary derivation from
the validated rows is selected.

## Verification

- A synthetic clock makes a SELL quote fresh at admission and stale after
  `verify_ledger`; the exit must fail with `Quote too old` and preserve the
  exact portfolio state.
- Mutated authority declarations with automatic start/resume, unsafe reviewed
  defaults, unknown identifiers, duplicate identifiers, malformed routes,
  malformed gates or wrong types must fail closed.
- Existing valid reducing exits, authority inventory, release profiles and all
  false-authority assertions remain green.
