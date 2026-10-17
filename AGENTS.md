# Nexus Operative continuity instructions

Start with `ROADMAP.md`, the bundled read-only contract in `nexus/release_roadmap.py`, then `LAUNCH_DRIVE.md` and `VERSIONING.md`. The PORTAL exposes the same reference at `GET /api/roadmap`. Runtime users cannot edit it. Update source and regenerate ROADMAP.md together when milestones change.

Repository: S4-cyber/Nexus-Operative. Continue on `development/windows-portal-foundation`; verify the remote head and CI before writing. The local workspace can be an exported snapshot without .git. In that case use GitHub tree/commit/ref operations with the verified parent and an expected-head lease; do not overwrite concurrent work.

Prioritize v1.0 launch blockers in coherent batches; global opportunity expansion is secondary. Maintain broad refresh every 5 seconds, ranking every 15 seconds, top 30 AI candidates and adaptive acceleration as target settings. Individual Terrestrial/Solar/Universal permissions must affect buyer entry decisions; disengagement must not prevent risk-reducing exits. Pure decision throughput is not market fill throughput.

Current design is autonomous paper first. No external broker is connected. Alpaca paper is planned first, IBKR paper and Coinbase Advanced later. Respect existing explicit connection permissions. Preserve 0.x until all launch acceptance evidence exists and the owner approves 1.0; live-money activation is separately authorized.

Keep priced fills and linked paper-ledger writes atomic and idempotent. Existing traded accounts must not be merged or rewritten automatically. Never infer historical quantities from unpriced fills. A failed commit cannot acknowledge a fill. Validate restart, duplicate delivery, risk vetoes, partial cancellation and reconciliation.

Run appropriate tests plus `python -m compileall -q nexus tests`; validate the PORTAL script with `node --check` for UI edits. Windows packaging is verified by GitHub Actions, not a Linux unit-test result. Report actual test counts and CI status, exact commit and material remaining gates. Do not mark an entire milestone complete because a component passed.
