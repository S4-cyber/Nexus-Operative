# Coherent Artifact Directory Snapshot Design

## Failure

Manifest verification previously validated every artifact path independently.
An attacker or concurrent publisher could replace the distribution directory
after the first file was hashed, allowing individually correct files from two
different directory generations to satisfy one manifest even though no single
public directory ever contained the certified set.

## Design

Reject aliases in every distribution-path component. Capture one directory
identity and retain every artifact handle before hashing. On platforms with
directory-relative opens, hold one no-follow directory descriptor and open
every leaf relative to it. On every platform, require public and retained leaf
identities to remain stable, then hash the entire retained set twice and require
both complete passes to match the manifest.

## Acceptance

- Replacing the distribution directory between two artifact hashes fails.
- A symlinked distribution directory fails before any certification.
- A symlinked distribution parent fails before any certification.
- A same-size cross-leaf rewrite cannot combine two content generations.
- Existing regular-file, single-link, byte-cap and stable-read checks remain.
- Verification still grants no signing, release or trading authority.

This is offline artifact-integrity evidence, not native installer acceptance,
provenance, code signing or owner release approval.
