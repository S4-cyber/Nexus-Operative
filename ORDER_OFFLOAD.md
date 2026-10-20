# Offline order-history copy

Nexus v0.177.0 adds an explicit operator tool for making a verified physical copy of a logically archived order journal. It is a copy-only foundation for later long-run storage work. It does **not** compact or delete the live journal, reclaim command/ID capacity, resume trading, attest a broker account, or grant release authority.

v0.177.1 closes the read-only SQLite integrity-check handles before the staged directory is published or cleaned up. This is required by Windows file locking and does not change the command, evidence, permission or authority contract below.

v0.178.0 adds a read-only `rotation-plan` preflight. It requires the same explicit application-closed assertion, verifies the copy, requires its order and portfolio hashes plus full replay digest/counts to exactly match the live source, then verifies both sides again. It does not create a replacement, delete or rewrite either side, reclaim capacity, resume the application or authorize later mutation.

v0.179.0 adds `prepare-replacement` and `verify-replacement`. Preparation requires the same closed, exact-source-bound source/archive pair, creates a separate fresh halted and flat order/portfolio pair from the final synthetic cash state, binds it to the archive with content and replay digests, verifies it, and atomically publishes only the new candidate directory. The live source and archive remain byte-for-byte preserved. The candidate is not installed and cannot resume Nexus or reclaim live capacity.

v0.180.0 adds explicit `install-replacement`, `verify-install-recovery`, `recover-install` and `rollback-install`. Installation first publishes a separate exact source recovery pair outside the live, archive and candidate directories. It then writes a content-free marker beside the live databases before replacing either file. Nexus durable order reads and writes fail closed while that marker exists. A successful install verifies the new halted pair and clears the marker; an interrupted install must be recovered explicitly. No command automatically resumes Nexus or deletes the archive, candidate or recovery copy.

v0.181.0 replacement manifests use schema v2. The fresh halted journal contains one replayed lineage command with a generation number, the exact predecessor archive-manifest digest and the prior epoch digest. A second rotation therefore proves a content-free generation 1 → 2 chain without restoring retired commands into live capacity. The verifier still accepts v0.180 v1 candidate and install-recovery manifests plus v1 interrupted-install marker records. Lineage distinguishes audit epochs; it is not a retained-ID index and does not itself prohibit a caller from deliberately reusing an old local-simulation ID. Full multi-epoch load and ID-policy acceptance remain separate gates.

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

## Check rotation readiness without changing data

```text
NexusOperative.exe --paper-tool order-offload rotation-plan PRIVATE_PATH\order_lifecycle.sqlite3 PRIVATE_ARCHIVE\epoch-001 --confirm-application-closed
```

The source command is equivalent:

```text
python -m nexus.order_offload rotation-plan PRIVATE_PATH/order_lifecycle.sqlite3 PRIVATE_ARCHIVE/epoch-001 --confirm-application-closed
```

This is a dry run. It accepts only an exact source/copy pair and checks both again after comparison. A successful result means the candidate inputs were internally consistent and unchanged during this bounded check; it does not mean a destructive rotation is implemented, safe to improvise or approved. If the live journal changed after the copy, create a new destination instead of reusing or editing the old copy.

## Prepare a separate halted replacement candidate

```text
NexusOperative.exe --paper-tool order-offload prepare-replacement PRIVATE_PATH\order_lifecycle.sqlite3 PRIVATE_ARCHIVE\epoch-001 PRIVATE_CANDIDATES\epoch-002 --confirm-application-closed
NexusOperative.exe --paper-tool order-offload verify-replacement PRIVATE_ARCHIVE\epoch-001 PRIVATE_CANDIDATES\epoch-002
```

Source installations use the same subcommands with `python -m nexus.order_offload`. The destination must be new and outside both the live data directory and archive directory. Preparation repeats the exact-source rotation preflight, creates a fresh halted account using the archived final flat synthetic cash state, links a fresh portfolio, and publishes a path/account-free manifest plus both databases only after replay and SQLite integrity verification. Verification requires the original archive and candidate; it rejects changed manifests, files, account continuity, histories or extra content.

The candidate contains three initialization/link/halt commands but no order IDs or archived orders. Do not copy it over the live files manually. Use only the guarded commands below after preserving private backups.

## Install with a separate recovery copy

```text
NexusOperative.exe --paper-tool order-offload install-replacement PRIVATE_PATH\order_lifecycle.sqlite3 PRIVATE_ARCHIVE\epoch-001 PRIVATE_CANDIDATES\epoch-002 PRIVATE_RECOVERY\install-002 --confirm-application-closed
NexusOperative.exe --paper-tool order-offload verify-install-recovery PRIVATE_ARCHIVE\epoch-001 PRIVATE_RECOVERY\install-002
```

Source installations use the same subcommands with `python -m nexus.order_offload`. The recovery destination must be new and outside the live, archive and candidate trees. Keep the application closed for the entire command. Success means the live order/portfolio pair is the verified fresh halted candidate and ordinary live journal capacity is reclaimed. It does not resume paper operation. Keep the archive, candidate and recovery directory; none is safe to delete based only on this local result.

If installation is interrupted or fails after the marker is written, keep Nexus closed and run:

```text
NexusOperative.exe --paper-tool order-offload recover-install PRIVATE_PATH\order_lifecycle.sqlite3 PRIVATE_ARCHIVE\epoch-001 PRIVATE_RECOVERY\install-002 --confirm-application-closed
```

The marker deliberately prevents ordinary order-gateway reads and writes until this command restores and verifies the original pair. Do not delete the marker or temporary files manually. For a completed installation, explicit rollback uses `rollback-install` with the same arguments. Rollback restores the preserved source pair and remains halted; it does not discard the candidate or recovery evidence.

The implementation flushes each file and uses atomic per-file replacement. Portable Python cannot fsync Windows directories, so native power-loss tests at every boundary, private-volume/profile ACL review, and independent recovery acceptance remain mandatory. This is interruption-recoverable local tooling, not yet independent proof of power-loss safety.
