# Coherent Artifact Directory Snapshot Plan

**Base:** exact v0.340.408 PR #130 tree
`a5c95acab6fe0b5f68f76f6d38b82326a33a3eca`.

1. Reproduce mixed-generation verification by swapping the distribution
   directory immediately after the first reviewed digest completes.
2. Add alias regressions for the distribution directory and its parent.
3. Pin one directory identity across every manifest entry, using a relative
   no-follow directory descriptor where supported and a strict path fallback.
4. Run manifest, Windows workflow and distribution-binding regressions.
5. Synchronize prerelease metadata, generated roadmap and saved evidence.
6. Run complete regression, packaging, security and exact-source checks.
7. Obtain independent review and publish only with an exact parent-head lease.
