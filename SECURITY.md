# Nexus Operative security and public-repository policy

Nexus is a prerelease research/local-paper application. Public source code does not authorize bank deposits, withdrawals, connected broker execution or live orders. Do not enable production financial secrets in GitHub Actions.

## What stays outside Git

Never commit real API keys, passwords, tokens, private keys, bank/card information, customer/account records, local databases, account-observation files, backups or encrypted copies of private data. Encryption is not permission to publish private financial records. Keep encryption passwords/keys outside the repository and outside files shared with the encrypted copy. An ignore rule does not remove previously tracked/history data.

Provider keys use the exact built-in Windows Credential Manager backend with explicit save/readback/removal checks. There is no plaintext fallback key file. Environment-provided optional research credentials remain process/environment secrets; never put actual values in scripts or `.env.example`. Local credential removal does not revoke a provider key. If any actual key has been committed or logged, revoke/rotate it at the provider before history remediation; deleting the latest file is insufficient.

Source-tool commands below refer to the application development branch.

## What encryption does and does not exist

- Credentials: protected by the Windows OS credential store, outside Git. Ordinary-user Windows profile/ACL and key lifecycle acceptance is still required.
- Local SQLite history/settings, offline recovery bundles and the ordinary inventory JSON download: **not application-encrypted**. Use a private OS account/directory and supported OS full-disk protection; do not upload them to GitHub or public hosting. No automatic database rewrite/migration is performed.
- Optional encrypted observation copy: source users can run `python -m nexus.encrypted_observation seal Nexus-Paper-Observation.json --output observation.nexusenc`. It prompts privately twice and refuses a terminal fallback that would echo the password. Use a long unique random passphrase stored separately. AES-256-GCM authenticates the fixed version header, random salt and nonce; a fixed scrypt cost derives the key. There is no saved/recoverable password or key and no weaker fallback. Losing the password loses the copy.
- To explicitly recover a private plaintext copy: `python -m nexus.encrypted_observation unseal observation.nexusenc --output recovered.json`. Authentication and inner observation validation happen before creating output. The destination must be new; no original is deleted or overwritten. Failed file IO may leave a partial new output requiring manual review. POSIX output mode is private; Windows output inherits its directory ACL. Neither tool configures disk encryption or guarantees memory erasure.

The original plaintext download remains after encryption. Keep both files private. An encrypted copy verifies neither the broker source, freshness, completeness, account identity nor spendable funds. It grants no order or release authority. Source references: [cryptography AEAD](https://cryptography.io/en/stable/hazmat/primitives/aead/) and [OWASP scrypt guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

## Local HTTP boundary

All routes enforce loopback peers and canonical localhost/127.0.0.1 Host headers before reading bodies. Duplicate/mismatched Origin headers and cross-site browser requests are rejected; absent Origin remains available to local CLI tools. API replies are no-store, pages cannot be framed, and proxy-header trust is disabled in the desktop launcher. Credential routes retain their stronger explicit Origin checks. This is not remote authentication; never expose the development API through a reverse proxy.

## Automated publication checks

`Public repository security` fetches all reachable branch/tag history, requires main/development refs and refuses shallow history. A trusted policy checkout is separate from scanned PR code. The job never installs or executes scanned project code. It has read-only contents permission, immutable Actions references and no persisted checkout token.

The artifact audit checks historical filenames and blobs for runtime databases, key/archive material, encrypted observation envelopes and standalone private financial report formats, including renamed files. It fails on incomplete/capped scans, prints counts/rules/object/path hashes and no contents. It cannot recognize every kind of personal data.

Checksum-pinned Gitleaks extends default/provider rules with decoding enabled. There are no directory/test exclusions; source inline allow comments and repository ignore fingerprints cannot bypass the scan. One exact-value allowlist covers the public documentation phrase `API/UI/default`, verified as a false positive. Scan logs suppress secret/match values and reports are not uploaded. New exceptions require concrete review, never a blanket fixture/path bypass. Zero findings is evidence of checked patterns, not a guarantee that no unknown secret or personal data exists.

Other CI actions are SHA-pinned with read-only permissions and nonpersistent checkout credentials. Dependabot prepares update PRs; code ownership alone does not enforce branch rules. No workflow uses `pull_request_target`, production financial secrets, cloud credentials or self-hosted runners. Keep untrusted forks from receiving secrets or write tokens.

## Owner checks before making the repository public

The connector cannot verify/change repository security settings. These remain owner actions:

1. Review and apply the security baseline on the default branch as well as development. All PRs require review before merge to main. Require applicable security/test checks and code-owner review in branch protection/rulesets; restrict direct pushes/bypasses and enable account two-factor authentication.
2. Enable secret scanning and push protection wherever available. Check Dependabot/security alerts. Set Actions default workflow permissions to read-only, disallow write-token/secrets access for fork PRs and require approval for outside contributors' workflow runs. Recheck rules after changing visibility.
3. Review historical Actions logs/artifacts, releases, PR/issue attachments, commit author emails, deleted refs and external copies for private information. Reachable Git scans do not audit those surfaces. Old packaged evidence uses synthetic fixtures, but no blanket privacy clearance is claimed for every historical upload.
4. Keep runtime/private files outside Git even after publication; use private storage and OS disk protection. Do not expose the loopback development API or enroll a cloud host without separately reviewed authentication, encryption/key management and execution ownership.

GitHub documents that making a repository public exposes source and Actions history/logs and disables push rulesets: [visibility consequences](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility). Recheck applicable controls rather than assuming private-repository protections carry over.

## Reporting a suspected secret or vulnerability

Do not paste credentials or private account exports into public issues, PRs or logs. Revoke/rotate an exposed provider secret first. Use GitHub private vulnerability reporting if the owner enables it, or a private owner-approved channel. A historical rewrite, destructive evidence deletion or key/provider revocation requires a concrete owner-reviewed remediation plan; never silently remove accounting/history to conceal a finding.

Remaining production gates include independent security/risk review, actual provider/profile/fee/hold reconciliation, sustained real-feed paper trials, governed funding/live execution, encrypted cloud storage/remote stop and explicit owner release approval. Public visibility does not close these gates.

## Encrypted input for offline review

The explicit `paper_observation_review` source CLI accepts per-input `--opening-encrypted` / `--closing-encrypted` flags. Each encrypted input uses its own hidden prompt and authenticated bounded decode directly in memory, with no decrypted intermediate files. There is no suffix-based detection, plaintext fallback, passphrase argument, password cache, saved key or automatic retry. Read-only account/head/window checks remain unchanged. A successful review prints a **private plaintext diagnostic report**; keep terminal output/redirections private. This does not encrypt the retained SQLite database or original files, protect swap/terminal buffers, attest source data or grant order authority. See STARTUP_GUIDE.md for the explicit command.

## Explicit operator paper-account probe

The paper_acceptance_probe source CLI accepts expected account and private local pacing path, never keys/secrets. Read confirmation is required before saved-pair/provider/journal-write activity; local status-only mode is separate and vault/network silent. It preserves the existing fixed paper GET transport and saved-pair/revocation checks, uses durable local owned pacing and never installs/removes credentials or submits orders/transfers. Unknown pending/auth/cooldown/cap state must not be deleted/reset/bypassed. Failure may follow remote reads or retained success/uncertainty. Only the same local journal coordinates cooperating callers; no broker-wide quota or cloud lease is established.

Reports and journals are private plaintext, outside the source checkout/Git; the scanner blocks both standalone probe/status modes even renamed. Secret fields/raw paths/pending tokens are omitted from reports. App-local credential scope does not verify actual provider key permissions, Windows profile/ACL acceptance, source coverage, fees/holds/spendable funds or release authority. All these acceptance flags remain false; no real account/provider/profile was used in injected development tests.


## v0.118 managed paper-read pacing in Accounts

Accounts now provides **Read Paper with Pacing** and **Review Read Pacing**. Enter the expected canonical paper account UUID. The first action asks for separate consent before reading the saved pair and running the existing fixed-host four-GET inspector. It retains at least60 seconds of cooldown after success; pending/authentication/cap/interruption conditions require their existing waits or review. The second action replays local pacing only: no credential read, provider request, admission permission or missing-file initialization. New controls have practical beginner help and share duplicate-click, removal-during-read, stale-response and export invalidation safeguards. No startup, hover, tab or passive checklist action invokes them.

On Windows the managed journal is `%LOCALAPPDATA%/NexusOperative/paper-read-pacing/<account-sha256>.sqlite3` (home fallback when LOCALAPPDATA is absent). Source-only non-Windows fixtures use `~/.nexus-operative/paper-read-pacing/`. The account digest is an identifier, not encryption or anonymity. Browser requests cannot supply a path/transport/clock or provider URL; known source-checkout destinations are rejected. Only the same managed local journal coordinates cooperating callers. Existing **Read Paper Account Once**, explicit CLI journals, other profiles and other computers are outside its pacing scope. Never use these alternatives to bypass retained cooldown or uncertainty. This is not broker-wide quota/distributed ownership.

Removing saved keys does not delete or reset the journal. Failed reads may already have contacted the provider or retained success/pending evidence. Preserve history and review it; there is no automatic retry, pending expiry, reset, deletion or order activation. Diagnostic head hashes can equal a pending BEGIN digest; they are consistency references, not credentials or authority. Reports omit named pending-ticket fields and paths and keep all existing funds/order/source/profile/release flags unverified. Portable downloads remain inventory-only and exclude pacing/open-order terms. The journal is private plaintext outside the portable storage backup bundle; retain it separately under OS disk/ACL protection and never publish it. No new encryption/backup guarantee is made.

Development checks inject vault/HTTP responses and use temporary journals. Actual Windows credential/profile/ACL, authenticated account/fee/hold, sustained real-market, funding/live/cloud and independent release acceptance remain open. New exact-source Windows/browser/security CI is required; local controller/API tests are not native acceptance.


## v0.120 private journal file preflight

A disposable reproduction showed that opening a WAL-mode pacing database for status could create new -wal/-shm files. Pacing, supplied broker-activity retention and disconnected broker-request journals now share a bounded file preflight before SQLite opens. Reject nonregular files, leaf symlinks, multiple hard links, oversized files, sampled path/descriptor identity changes, unsupported SQLite read/write-version header bytes and any existing WAL/SHM sidecar, including an orphan beside a missing main file. Read at most20 unbuffered header bytes; recheck newly created files before SQLite initialization. Existing file caps, schema/account/hash replay, atomic DELETE-mode writes and rollback-journal recovery are retained. Existing invalid/empty databases still require ordinary schema/binding review; no guard initializes or repairs them.

Unsupported layouts fail for both reads and writes without converting journal mode, deleting sidecars or silently creating a replacement main database. Keep the entire private file set intact while stopped and seek a reviewed recovery plan; do not erase or force-convert files to bypass a block. These sampled local checks are not an atomic SQLite descriptor binding, protection against every concurrent path replacement, an ACL audit, encryption or source/financial attestation. Other application stores are outside this specific change; no universal filesystem guarantee is claimed.

The existing pacing release profile now requires actual WAL read/write denials and unchanged files. New cross-store tests reject FIFO/directory/symlink/hardlink paths before SQLite, retain orphan sidecars, cover descriptor replacement/growth and preserve normal DELETE-mode replay/duplicate behavior. Accounts pacing-status failures now correctly describe local-history review and explicitly say the check reads no keys and sends no broker request; preserve its files rather than changing credential setup automatically. No new runtime operation, retry, reset, broker routing or authority is added. Generate the current-version report before the full suite; all14 profiles and saved-evidence checks remain required. Actual provider/profile/account/fee/hold, sustained market, funding/live/cloud and independent/owner release gates stay open.

## v0.121 private acceptance-record minimization

The source-only paper-account probe now exports an allowlisted aggregate of the open IOC limit terms already observed by its fixed four-GET session. It emits no raw broker order ID, client ID, discarded provider field, key, secret, pacing path or pending token. Before output, it requires exact field allowlisting, count agreement with the nested portable observation, false authority flags and a recomputed summary digest. The record digest binds only normalized content and pacing heads; it is not a signature, provider attestation or permission. Reports and journals remain private plaintext outside Git. Current worktree and reachable fetched history were scanned with checksum-pinned Gitleaks v8.24.3 and the repository artifact policy with zero findings; deleted refs, external copies and historical Actions artifacts remain outside that local result.

## v0.122 private activity acceptance boundary

The activity acceptance CLI reads a bounded regular single-link private order snapshot and writes only caller-selected pacing/activity SQLite journals outside the checkout. Successful stdout excludes raw response rows, order/client/activity identities, cursors, paths and credentials; only aggregate counts, supplied fee totals, fixed reasons and hashes remain. The public-repository scanner rejects both new output modes even if renamed, while database/path rules reject retained stores. These are prevention layers, not encryption or proof of deletion. The private input, journals and report may contain financial evidence or linkable digests and require OS ACL/disk protection. Status is vault/network/order-file silent; failed collection may still leave a pending pacing event or atomically committed receipt that must not be erased to obtain another attempt.

## v0.123 private acceptance-history boundary

The activity probe adds a third caller-selected private SQLite journal, distinct from pacing, activity and order paths. It stores account and cross-journal head digests plus aggregate acceptance hashes; those values can still be linkable financial metadata and are not anonymization. The scanner blocks standalone acceptance-history output and every retained SQLite journal. Keep them outside Git under OS ACL/disk protection. Replay detects tampering and unbound cooperating-journal transitions but does not attest provider source, causal ordering, complete activity/fee coverage, account holdings or permissions. Missing/legacy/unbound state is fail-closed review evidence and must not be erased to obtain another read.

## v0.124 synthetic process-death evidence

The fifteenth release profile contains only temporary synthetic local journals and returns bounded booleans/counts—no raw journal bytes, account identifier, credential, activity identity or retained head. Child processes deny credential and external-network access during fresh replay. A green result demonstrates the tested SQLite commit boundary only; it is not source attestation, broker acceptance, private-evidence publication approval or protection against noncooperating writers.
