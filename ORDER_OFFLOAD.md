# Offline order-history copy

Nexus v0.177.0 adds an explicit operator tool for making a verified physical copy of a logically archived order journal. It is a copy-only foundation for later long-run storage work. It does **not** compact or delete the live journal, reclaim command/ID capacity, resume trading, attest a broker account, or grant release authority.

## Before creating a copy

- Fully close Nexus. The command requires `--confirm-application-closed`; the flag is an operator assertion, not process detection.
- Use an existing `order_lifecycle.sqlite3` whose account is halted, flat and reconciled, with no active orders and at least one completed logical archive batch.
- Keep its linked `paper_portfolio.sqlite3` beside it.
- Resolve any `-journal`, `-wal` or `-shm` sidecar before proceeding. The tool refuses sidecars, symlinks, hard links, special files, missing companions and oversized files rather than repairing or converting them.
- Choose a new destination outside the live data directory and outside the application/source folder. Keep both source and copy on private, access-controlled storage; never commit either database or the manifest.

## Installed command

```text
NexusOperative.exe --paper-tool order-offload create PRIVATE_PATH\order_lifecycle.sqlite3 PRIVATE_ARCHIVE\epoch-001 --confirm-application-closed
```

Source installations use the same contract:

```text
python -m nexus.order_offload create PRIVATE_PATH/order_lifecycle.sqlite3 PRIVATE_ARCHIVE/epoch-001 --confirm-application-closed
```

Creation copies the order and portfolio databases into a new staged directory, hashes both files, runs SQLite integrity and complete archived-order replay checks, verifies that no active order or command epoch reappeared, and compares the source before and after publication. A manifest records only bounded counts, hashes, fixed scope text and false authority flags; it contains no source path or account value.

## Verify an existing copy

```text
NexusOperative.exe --paper-tool order-offload verify PRIVATE_ARCHIVE\epoch-001
```

or:

```text
python -m nexus.order_offload verify PRIVATE_ARCHIVE/epoch-001
```

Verification requires exactly the manifest plus both expected databases, rechecks their hashes and SQLite integrity, and performs a fresh archived-order replay. Any extra, missing, changed, linked or malformed item fails closed. A successful result proves only that this offline copy is internally consistent with its manifest at verification time. It is not an encrypted backup, independent archival acceptance, long-run rotation/compaction, market evidence, profitability evidence or permission to trade.

Do not delete the live journal after this command. Capacity remains unchanged until a separate reviewed compaction/rotation design and recovery acceptance exist.
