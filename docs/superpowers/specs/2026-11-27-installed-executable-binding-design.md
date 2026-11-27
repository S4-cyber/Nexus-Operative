# Installed Executable Distribution Binding Design

## Goal

Bind the exact standalone Windows executable to both the first installed and
same-version reinstalled executable bytes, then carry those digests through
installer-result review, distribution-manifest review and owner-profile review.

## Safety boundary

This is CI artifact identity evidence only. It does not grant broker, account,
funds, live-order, cloud, release or trading authority. It does not claim a
clean-machine, ordinary-user, native-window or power-loss acceptance result.

## Contract

The Windows installer smoke receives an explicit standalone executable path.
Before installation it records that file's SHA-256. After the first install and
after same-version reinstall it hashes the installed executable and requires
both hashes to equal the standalone hash. The result records all three hashes
and exact boolean agreement fields.

The semantic result reviewer requires closed schemas, exact SHA-256 text and
three-way equality. The distribution reviewer requires the public
`NexusOperative.exe` manifest entry to match all three reviewed hashes. The
owner-profile reviewer consumes the expanded reviewed result and distribution
binding and requires the same manifest-bound executable identity before it can
accept the existing owner-operated probe.

## Failure behavior

Missing, malformed, type-confused, stale or unequal hashes fail closed. A
manifest that omits or changes `NexusOperative.exe` fails. No retry, network,
credential, provider or runtime engagement is introduced.
