# Direct Paper Quote Freshness Design

## Intent

Prevent a direct local-paper BUY from committing with a public quote that was
fresh at route admission but aged beyond the existing ten-second limit while
waiting for the portfolio and tier-settings writer reservations.

## Constraints

- Keep every engagement and tier permission OFF unless already explicitly enabled.
- Preserve the independent emergency cut, reducing SELL exits, account checks,
  existing exposure/cash limits and fail-closed rollback.
- Perform no broker request, credential access, retry, funds movement or live action.
- Add no UI control or editable parameter; preserve the seven tabs and the
  black/bright-purple bottom-right hover help.
- Keep the version below 1.0 and do not change the 38% weighted roadmap baseline
  or 0/9 accepted-stage denominator.

## Approaches

1. Re-fetch a quote after acquiring the writers. Rejected: this would perform
   external I/O while holding two durable writer reservations and would create a
   second observation with new failure and pacing semantics.
2. Acquire the writers before the existing quote fetch. Rejected: it would hold
   accounting locks across the public transport call and increase stop/exit
   contention.
3. Revalidate the captured quote after both writers are held. Selected: the
   existing exact quote is checked against the existing provenance, age, numeric
   and spread policy again immediately before mutation, without new I/O.

## Behavior

`paper_portfolio.buy` keeps its early validation for cheap fail-fast rejection.
After the portfolio transaction and tier permission writer guard are both held,
and after the emergency latch is sampled, it calls `check_quote` again and uses
only the newly returned ask for quantity/accounting. A quote older than ten
seconds at that point raises `Quote too old`; the surrounding transactions roll
back and the API returns the existing 422 response. Emergency cut remains the
higher-priority refusal when both conditions are present.

## Evidence boundary

The regression uses a synthetic clock transition during the protected ledger
audit and proves zero new position or ledger event. It is local deterministic
evidence only, not provider/source acceptance, genuine-market realism, sustained
trial acceptance, profitability, or release approval.
