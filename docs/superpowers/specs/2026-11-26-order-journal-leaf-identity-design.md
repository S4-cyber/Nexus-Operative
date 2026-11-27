# v0.340.427 order-journal leaf identity design

## Problem

Order-journal writes pin the containing directory, but SQLite still opens the
configured leaf by pathname. An existing symlink or hardlink can therefore
redirect a safety write into another journal. A public leaf replacement after
SQLite opens can also let the retired inode commit while the public pathname
names different evidence. Read-only recovery already rejects these shapes, so
the write boundary must provide the same fail-closed guarantees before any
schema or command mutation.

## Design

Introduce a write-side leaf lease owned by `DurableOrderGateway`:

- safely create an absent journal with exclusive, no-follow semantics;
- require an existing journal to be a regular file with exactly one link;
- retain the exact leaf identity and resolved pathname;
- verify SQLite's attached `main` database is exactly that retained leaf;
- recheck the retained public leaf immediately before transactional commit.

The existing storage-root guard remains mandatory around every leaf sample.
Cancellation, expiry and emergency halt remain journal-only and must not gain a
portfolio dependency. The boundary is sampled local filesystem integrity, not
an administrator-proof or continuously atomic lease.

## Acceptance

Real disposable SQLite regressions must prove that symlink and hardlink aliases
cannot mutate their targets, and that a public-leaf replacement before commit
rolls back the retired journal. Normal first creation, replay, safety commands,
portfolio composition and Windows-compatible code paths must remain green.
No Portal control or editable parameter changes.
