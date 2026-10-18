# Public repository readiness — owner handoff

GitHub was observed **public** after the v0.111 application push; the owner changed visibility during this audit. The assistant did not change visibility or rewrite history. Repository-side hardening and scoped scans are implemented, but the owner-only checks below remain and must now be completed on the public repository. This is not an unconditional security clearance.

## Verified scope

The private repository was inspected at source `66ba32cd9e0d809799c89c805a09439b7b42a265`. Trusted security bootstrap `deb70a8ef176b43448a7d361120a38116bbf2abc` passed [security run39090533663](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39090533663), job117566120158:

| Check | Observed result |
| --- | --- |
| Reachable main/development history | Full fetch, required refs present; shallow scans refused |
| Historical artifact/content audit | 1,537 blobs, 310 unique paths, 27,576,983 bytes; zero prohibited artifacts |
| Gitleaks default/provider patterns plus decoding | Zero secret findings |
| False-positive handling | Exact public documentation token `API/UI/default` only; no folder/test exclusions |
| Current source policy | Runtime DBs, keys, backups, encrypted private copies and portable account reports excluded/blocked; renamed SQLite/encrypted/private-report files checked by content |
| CI token/dependency policy | Immutable official Action SHAs, checksum-pinned scanner, contents-read permission, checkout credential persistence disabled, trusted policy separate from scanned PR source |

Those numbers are a source-bound checkpoint, not a certificate for later commits or every possible personal-data pattern. The final current tracked-source snapshot also passed 296 paths/blobs and zero secret findings; the final development push runs the same full-history check again. Source inline allow comments and repo ignore fingerprints cannot bypass the trusted scan. Scan reports containing secret/match values are never uploaded; public logs contain only bounded counts and rule/commit/line/file hashes. Historical deleted refs, logs/artifacts, releases, issue/PR attachments, author emails and external copies are outside that scan.

## Encryption and private-data handling

| Material | Current protection | Required handling |
| --- | --- | --- |
| API key pairs/research key | Exact Windows Credential Manager backend; no plaintext file fallback | Keep provider secrets out of Git/Actions; revoke/rotate at provider if exposed |
| Local SQLite account/history/settings | Plaintext application data | Private OS directory/account and supported disk encryption; never commit |
| Offline recovery bundles | Plaintext verified copies | Private storage/disk protection; never publish |
| Ordinary inventory download | Plaintext private account/balance JSON | Keep private; encrypt an optional copy if needed |
| New optional `.nexusenc` copy | AES-256-GCM, fresh salt/nonce, fixed scrypt derivation and hidden prompts | Keep the passphrase separate; do not publish even encrypted private records |
| Public source/synthetic fixtures | Intentionally readable | No real credentials, banking details or account records |

The encrypted-copy tool does not delete the original plaintext, change database formats, configure Windows ACLs/disk encryption, save/recover passwords or start trades. Wrong passwords/tampering yield no plaintext output. Failed file IO can leave a partial new output; existing files are never overwritten. See SECURITY.md and STARTUP_GUIDE.md.

The application now also rejects non-loopback peers, untrusted/duplicate Host headers and mismatched/duplicate Origin headers before reading bodies or running routes. Cross-site browser requests are rejected, API replies use no-store, and the desktop launcher disables proxy-header trust. This is a local boundary, not cloud authentication or protection against a compromised local account. Never expose this development API through a public reverse proxy.

## Outstanding owner checks (repository now public)

1. Review the [security-only default-branch draft PR #2](https://github.com/S4-cyber/Nexus-Operative/pull/2) and apply its baseline to main. It changes eight security/policy files, without merging application code. Its trusted branch push passed [security run39090918862](https://github.com/S4-cyber/Nexus-Operative/actions/runs/39090918862); the PR check39090920582 correctly refuses the not-yet-installed main policy. SECURITY.md requires review before merging to main. The updated baseline push with inline/ignore bypasses disabled passed security run39091414120; its PR run39091416134 remains blocked on uninstalled main policy. PR checks deliberately refuse uninstalled/untrusted base policy during this one-time bootstrap; review the trusted branch-push scan instead, then retain strict trusted-base checks for future PRs.
2. In GitHub, verify branch protections/rulesets and required security/test/code-owner checks, restrict bypasses, use two-factor authentication, set read-only workflow permissions and require approval for outside-contributor workflows. Do not send secrets or write tokens to fork workflows. Enable secret scanning/push protection wherever available and check Dependabot alerts. Recheck rules after visibility changes; GitHub warns that push rulesets are disabled when making a repository public.
3. Review historical Actions logs/artifacts, releases, PR/issue attachments, commit metadata and deleted refs for private information. The connected API cannot provide blanket clearance for these surfaces or inspect/change the admin settings. If a real secret is found, revoke/rotate it before a reviewed cleanup plan. Do not erase accounting evidence or rewrite Git history automatically.
4. Keep runtime/private data outside Git after publication, including encrypted copies. Ordinary downloads/databases remain plaintext unless protected separately.

Official references: [visibility consequences](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility), [Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use), [secret scanning/push protection](https://docs.github.com/en/code-security/getting-started/quickstart-for-securing-your-repository).

## Release status

Development continues in 0.x. Public source, green scanners or encrypted copies do not close actual-provider/profile/fee/hold reconciliation, sustained market/strategy trials, independent security/risk review, governed funding/live execution, secure cloud operation or owner1.0 approval. Source-bound Windows packaging remains separate from Linux security tests.

v0.109 source `2d2e25b0f6c8472591891abee3b053e86da83931` passed packaging39088186619/job117560452568 and safety39088186630/job117559084799. The v0.110 packaging run39088678287/job117561701859 failed with no reported steps and unavailable logs (404); the cause is unverified, and that installer is not a verified release. New v0.111 native packaging must complete separately.

## v0.112 follow-up

Encrypted observation pairs can be reviewed offline directly in memory with explicit per-input flags and hidden prompts, avoiding decrypted intermediate files. The successful report remains private plaintext stdout; retained SQLite storage and original downloads remain unencrypted. Main-policy adoption/admin/log review above remain outstanding.

The v0.111 application build39091596247/job117569331946 compiled and passed standalone startup, then failed installed startup because the browser's automatic portfolio read created accounting storage. v0.112 makes that read non-initializing/read-only and strengthens installed smoke to exercise it explicitly. This is a source fix awaiting fresh native CI, not a verified installer claim.

## v0.113 follow-up

The preceding v0.112 commit938153c2469432ab6e5887491edfbb0f3337fec5 passed full reachable-history security39092253998/job117571286904:530 commits,1,591 blobs,319 paths,29,202,063 bytes, zero artifact/secret findings. This does not clear admin settings or historical non-Git surfaces.

Forward-session polling now preserves missing evidence storage and reads existing history without schema initialization/migration. A combined forward status read shares one history/capacity transaction; runner state and order accounting are not an atomic combined view. Native CI and all owner/security/financial release gates remain separate.

## Verified v0.113 checkpoint and v0.114 recovery follow-up

Source52e3f9239961dcec6c1be53e0466b8040bcef7f2 passed packaging39092515629/job117572710958, safety/browser39092515768/job117572058444 and full-history security39092515674/job117572057319. The security audit covered531 commits/1,606 blobs/321 paths/29,824,994 bytes with zero artifact or detected-secret findings. The earlier passive-startup source fix is now covered by successful native CI. v0.113 installer artifact12123328272 is unexpired until2026-10-25T03:19:42Z; the v0.114 changes still need their own exact-source runs.

Recovery/order/session inspection now avoids writable schema connections. Existing linked portfolio recovery uses a read-only attachment and the original full reconciliation governor; absent/corrupt/incomplete/nonregular/symlink evidence cannot be silently recreated by observation. Missing order history is explicitly unverified, not proof of a flat account. No provider/funds/cloud authority was added. Default-branch security PR#2 remains draft/open/unmerged; owner admin settings and historical non-Git review remain outstanding.

## v0.116 retained-trial report scope

Standalone RETAINED_FORWARD_OPERATIONAL_COVERAGE_REVIEW reports are private operational evidence and are now blocked by the development artifact scanner even when renamed. Synthetic nested offline-trial reports remain permitted. Coverage labels/hashes never attest source or permit release, execution, funding or deployment. Final v0.116 validation:1,897 Python tests passed (one existing Starlette/httpx deprecation warning), ten Node controllers, compileall, pip check and fourteen offline profiles plus saved-evidence verification passed. Source259 files/0e6f907fa17d01b9cacc31ca62f930ace9847bfd9bb6602dcd29432f4cad9bc3. Current tracked snapshot308 files passed artifact audit/Gitleaks with zero findings. New source needs its own exact-source native/security/safety CI; no local rendered-browser or actual market/account acceptance claimed. Verified checkpoint advances to v0.114 source7d132352f3ce559e94055e5d63c6f4a73e3e7f15: Windows39094330857/job117577506105, safety/browser39094330862/job117577506110 and security39094330879/job117577506088 passed. Installer12124392206 unexpired until2026-10-25T03:49:34Z; distribution12123669691. Keep all actual broker/funding/live/cloud/owner/independent gates open.
