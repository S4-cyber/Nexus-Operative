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

## Automated publication checks

`Public repository security` fetches all reachable branch/tag history, requires main/development refs and refuses shallow history. A trusted policy checkout is separate from scanned PR code. The job never installs or executes scanned project code. It has read-only contents permission, immutable Actions references and no persisted checkout token.

The artifact audit checks historical filenames and blobs for runtime databases, key/archive material, encrypted observation envelopes and standalone private financial report formats, including renamed files. It fails on incomplete/capped scans, prints counts/rules/object/path hashes and no contents. It cannot recognize every kind of personal data.

Checksum-pinned Gitleaks extends default/provider rules with decoding enabled. There are no directory/test exclusions. One exact-value allowlist covers the public documentation phrase `API/UI/default`, verified as a false positive. Scan logs suppress secret/match values and reports are not uploaded. New exceptions require concrete review, never a blanket fixture/path bypass. Zero findings is evidence of checked patterns, not a guarantee that no unknown secret or personal data exists.

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
