# Durable fill remainder agreement design

## Intent

Retained provider activity must not establish local request/activity agreement
when one fill record contradicts its own completion type. A terminal `fill`
with a positive `leaves_qty`, or a `partial_fill` with zero remaining quantity,
is readable supplied evidence but cannot support a reconciled local history.

## Goals

- Detect both fill-type/remainder contradictions in the canonical retained
  activity normalization.
- Preserve the bounded record and surface an explicit review reason instead of
  deleting, repairing, or silently reclassifying supplied evidence.
- Propagate the reason through the durable broker activity review and prevent
  the request/activity composition from reporting local agreement.
- Bind the negative case into the offline activity release profile.
- Preserve existing quantity, cumulative-chain, request-term, fee, timing,
  exact-head, and journal stability checks.

## Non-goals and anti-goals

- This does not attest provider semantics, source coverage, fees, holds,
  spendable funds, account ownership, or broker acceptance.
- It does not add provider I/O, retries, repairs, routing, ledger mutation, or
  automatic continuation.
- It does not make legacy contradictory evidence disappear or reinterpret it
  as a different activity type.
- It does not accept D4, change the 38% weighted estimate, or authorize trading.

## Constraints

- Engagement and optional AI remain OFF by default.
- All broker, funding, live, cloud, spend, and release authority flags remain
  false.
- Reducing exits, limits, account checks, fail-closed behavior, and independent
  emergency-stop access remain unchanged.
- No button or editable parameter is added; the seven beginner tabs and
  black/bright-purple bottom-right hover help remain unchanged.

## Approaches considered

1. **Retain and block agreement with a dedicated reason — selected.** This
   matches the current readable-but-unaccepted legacy evidence policy and lets
   an operator investigate the original retained response.
2. **Reject the activity record during normalization — rejected.** It would
   make a malformed retained history unreadable and discard useful diagnostic
   context without improving provider authority.
3. **Silently infer the correct fill type from the remainder — rejected.** That
   would rewrite supplied broker evidence and could manufacture reconciliation.

## Verification boundary

The new reason proves only that the local supplied record is internally
contradictory. A clean result remains necessary but insufficient for provider
identity, source completeness, fill/fee links, holds, funds, routing, genuine
market trials, Windows acceptance, or release approval.
