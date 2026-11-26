# Windows Emergency Identity Observation Design

## Failure

Both parent PR #128 Windows regression shards intermittently returned
`durable_order_halt_confirmed=False` in malformed-intent cases even though the
single halt command committed. The replay helper collapsed a transient
`lstat` denial and a definitive pathname/identity mismatch into the same false
result, so the existing bounded read-only retry was bypassed.

## Design

Classify each no-follow order-journal observation as available, structurally
invalid/mismatched, or temporarily unavailable. Retry only the unavailable
state within the existing three-attempt ceiling. Structural invalidity,
identity replacement and a definitive negative replay stop immediately. Never
retry the halt mutation or add reset/running authority.

## Acceptance

- A first transient identity-read denial can recover through read-only replay.
- A replacement, alias or invalid link count remains immediately unconfirmed.
- An unhalted replay is never retried into success.
- The third unavailable observation remains unconfirmed.
- The durable journal contains exactly one halt command.

This is local fault handling, not native Windows, installer, broker, sustained
trial, release or trading acceptance.
