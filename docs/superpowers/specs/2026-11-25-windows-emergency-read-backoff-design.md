# Windows Emergency Read Backoff Design

## Failure

Exact PR #130 kept a strict three-attempt read-only replay ceiling but sampled
all three attempts immediately. Both hosted Windows shards still intermittently
returned an unconfirmed durable halt in different malformed-intent parameters,
while the halt mutation itself remained committed.

## Design

Keep exactly three observations. After only a transient unavailable identity or
busy replay sample, pause for fixed 20 ms and 50 ms intervals before the next
read-only attempt. Never pause or retry after a definitive identity mismatch, a
negative durable replay or the third unavailable sample. Never reissue the halt
mutation or expose reset/running authority.

## Acceptance

- A transient observation that clears after one yield can confirm the committed halt.
- The attempt ceiling remains exactly three.
- Mismatch, negative replay and exhaustion remain unconfirmed.
- The durable journal still contains exactly one halt command.
- Exact-child native Windows CI must pass before platform verification.
