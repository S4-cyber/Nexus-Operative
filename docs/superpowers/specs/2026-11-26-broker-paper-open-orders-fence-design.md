# v0.340.428 broker-paper open-orders fence design

## Problem

The explicit Alpaca paper POST boundary currently samples account cash,
non-marginable buying power and BTC availability before and after its durable
reservation. It does not sample the provider's open-order collection. Existing
open BUY or SELL orders can therefore consume funds or BTC availability that
the local admission arithmetic still treats as free, and an open-order change
between the earlier reads and the final POST can escape the existing stable
risk comparison.

## Design

Add one fixed read-only open-orders request to each of the three admission
phases: initial preflight, repeated pre-reservation review and final
post-reservation review. Require an exact built-in list, no more than the
existing inspection cap, containing exact built-in dictionaries whose optional
account identities match the selected account. Any reported row fails closed:
the boundary cannot prove provider holds or spendable funds, so it must not
interpret reported order terms as a safe capacity offset.

The initial sample must be valid and empty. A row in the repeated
pre-reservation sample fails before either durable attempt reservation. A row
in the post-reservation sample retains the lookup-only request marker, closes
the provider budget as NOT_SENT and never crosses the POST boundary. Invalid
collections fail with a fixed diagnostic without retaining provider content.

The final open-orders sample occurs after both durable reservations and before
the one-shot transport authorization, request-head exclusion and independent
emergency-stop sample. The reads are sequential and not an atomic broker
snapshot or provider hold attestation.

## Safety boundaries

The transport remains paper-only, one-shot and OFF by default. No retry,
strategy wiring, saved write credential, live endpoint, funding, ledger,
cloud, release or profitability authority is added. Cancellation, expiry and
the independent emergency stop are unchanged. No Portal button or editable
parameter is added.

## Acceptance

Disposable injected tests must prove that an existing valid open order and a
malformed collection both fail before either durable attempt marker, and that
an order appearing in either later comparison window prevents POST. The
success fixture must prove exactly three empty open-order reads and retain all
existing one-shot, durable uncertainty and emergency-stop guarantees. This is
not hold reconciliation or provider/account acceptance.
