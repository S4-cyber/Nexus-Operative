# Computer-off operation and expansion goals

Status: planned, not deployed. Reviewed provider documentation on 2026-10-17; recheck terms, limits, availability, geographic eligibility and billing before selecting a service.

The user explicitly wants broader market opportunities, operation while the desktop is off, free-to-use hosting, and no separately dedicated account if possible. Prefer reusing a supported existing provider identity/project. Signing in through GitHub still creates or uses a hosting-provider account; it does not make hosting anonymous. No reviewed service meets anonymous/no-account, durable, secure, continuously running trading requirements. Do not silently replace these constraints with a paid tier or create accounts.

## Provider feasibility, not a deployment recommendation

| Candidate | Relevant documented restrictions | Nexus suitability to evaluate |
| --- | --- | --- |
| Cloudflare Workers Free | Provider account required; currently 100,000 requests/day and 10 ms CPU per free invocation, including scheduled invocations. | Lightweight remote status/alerts or bounded jobs are candidates. This does not prove compatibility with the existing persistent Python/SQLite runner, continuous loops or 30+ market actions/second. Storage and scheduling need separate validation. |
| Render Free web services | Account required; services sleep after 15 minutes without incoming traffic; local files are lost on restart, redeploy or sleep. | Disposable UI/API previews. Do not put the current SQLite journals on its ephemeral disk or depend on it for unattended trading. Do not use artificial keep-alive traffic to claim uptime guarantees. |
| Oracle Cloud Always Free compute | Account/eligibility verification; the general signup FAQ describes card verification; idle compute can be reclaimed and availability is not guaranteed. | Closest reviewed VM-style candidate for evaluating the existing Python engine and persistent volumes, if the user accepts signup requirements. Backup, retention, capacity and recovery must be proved. No signup or billing upgrade is authorized by this design note. |

Official sources:
- https://developers.cloudflare.com/workers/get-started/guide/
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/platform/pricing/
- https://render.com/docs/free
- https://www.oracle.com/cloud/free/faq/
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

Free services have quotas and interruptions. A free tier is not an uptime commitment, a permanent allocation or a free trading/market-data entitlement. No provider is selected or connected. If strict no-provider-account remains required, remote execution remains blocked until an acceptable host exists; a browser tab, static website, encrypted file bucket or sleeping laptop cannot supply continuous execution.

## Required staged design and acceptance

1. Separate the headless paper engine from the desktop viewer. Closing the viewer must not stop an explicitly authorized remote session. The current local runner still requires an active local PORTAL and must not be described as computer-off capable.
2. Use one durable execution owner with broker/strategy fencing across machines. Existing local OS locks do not coordinate desktop and cloud; do not share or live-sync writable SQLite files across hosts. A lost lease must prevent new entries. Prove idempotent broker reconciliation before retry after ambiguous responses or failover.
3. Authenticate and authorize the remote control plane, including explicit session start and remote halt. The existing local development API must not be exposed to the Internet unchanged. Encrypt transport, restrict ingress and administrative access, and retain an audit trail without credentials.
4. Encrypt persistent data and backups with authenticated encryption; manage key access, rotation, recovery and least-privilege broker credentials separately. A working runner must be able to decrypt necessary data in memory: encryption at rest is not protection against a fully compromised authorized runtime. Do not claim anonymous cloud storage or encryption alone makes unattended trading safe. Do not upload desktop credential-vault files as a cloud secret strategy.
5. Enforce zero-cost resource admission: reject automatic paid upgrades, require quotas and conservative storage/network/CPU budgets, pause safely at resource limits and retain audit evidence. Verify provider permissions for intended workload. AI and broker/data costs remain independent.
6. Prove restart, provider sleep/reclamation, lost network/storage, clock drift, stolen/revoked keys, backup restore and desktop/cloud split-brain behavior. Retain several days of paper-only computer-off evidence with an independently reviewable remote stop/recovery drill. Never infer broker order failure from a host timeout.
7. Add retained multi-resolution account-history rollups/export so the Matrix's minute-to-year views remain meaningful without unbounded six-second samples. Preserve gaps, extrema and realized/unrealized distinctions and show coverage.
8. Expand from supported spot markets into the existing 12-avenue research catalogue incrementally: licensed/time-aligned data, exchange/broker eligibility, costs, liquidity, corporate actions and independent paper evidence before enabling each venue or asset class. More discovery is not automatic trading permission.

These are explicit roadmap goals. They do not mark M7 unattended operation, M8 broker integration or M9 release acceptance complete, and do not authorize a deployment, new hosting account, spending or live-money trading.
