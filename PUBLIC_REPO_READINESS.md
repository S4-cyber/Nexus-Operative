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

## v0.117 operator acceptance probe privacy

The explicit source CLI uses an already saved pair and named private local pacing journal, with separately confirmed read or vault-silent local status. No secret argument, raw path/token output, order/funds/cloud action or approval is added. The scanner now blocks standalone PAPER_ACCOUNT_ACCEPTANCE_PROBE and PAPER_ACCEPTANCE_PACING_STATUS JSON even renamed. Private stdout and SQLite journals remain unencrypted; keep them outside Git in private OS-protected storage. Actual profile/provider scope/account/fee/hold/sustained/financial/cloud/independent release acceptance and owner default-branch/admin/historical review remain open.


## v0.118 managed paper-read pacing in Accounts

Accounts now provides **Read Paper with Pacing** and **Review Read Pacing**. Enter the expected canonical paper account UUID. The first action asks for separate consent before reading the saved pair and running the existing fixed-host four-GET inspector. It retains at least60 seconds of cooldown after success; pending/authentication/cap/interruption conditions require their existing waits or review. The second action replays local pacing only: no credential read, provider request, admission permission or missing-file initialization. New controls have practical beginner help and share duplicate-click, removal-during-read, stale-response and export invalidation safeguards. No startup, hover, tab or passive checklist action invokes them.

On Windows the managed journal is `%LOCALAPPDATA%/NexusOperative/paper-read-pacing/<account-sha256>.sqlite3` (home fallback when LOCALAPPDATA is absent). Source-only non-Windows fixtures use `~/.nexus-operative/paper-read-pacing/`. The account digest is an identifier, not encryption or anonymity. Browser requests cannot supply a path/transport/clock or provider URL; known source-checkout destinations are rejected. Only the same managed local journal coordinates cooperating callers. Existing **Read Paper Account Once**, explicit CLI journals, other profiles and other computers are outside its pacing scope. Never use these alternatives to bypass retained cooldown or uncertainty. This is not broker-wide quota/distributed ownership.

Removing saved keys does not delete or reset the journal. Failed reads may already have contacted the provider or retained success/pending evidence. Preserve history and review it; there is no automatic retry, pending expiry, reset, deletion or order activation. Diagnostic head hashes can equal a pending BEGIN digest; they are consistency references, not credentials or authority. Reports omit named pending-ticket fields and paths and keep all existing funds/order/source/profile/release flags unverified. Portable downloads remain inventory-only and exclude pacing/open-order terms. The journal is private plaintext outside the portable storage backup bundle; retain it separately under OS disk/ACL protection and never publish it. No new encryption/backup guarantee is made.

Development checks inject vault/HTTP responses and use temporary journals. Actual Windows credential/profile/ACL, authenticated account/fee/hold, sustained real-market, funding/live/cloud and independent release acceptance remain open. New exact-source Windows/browser/security CI is required; local controller/API tests are not native acceptance.


Verified checkpoint now v0.115 source54e3ff817a83ea3b8e1f0b41cc54650c27c32c5a: Windows packaging39094660616/job117579433581, safety/browser39094660669/job117578487153 and security39094660594/job117578486899 all passed. Installer artifact12124885341 expires2026-10-25T04:03:07Z; distribution12124661222 expires2026-11-17T04:03:04Z. Link: https://github.com/S4-cyber/Nexus-Operative/actions/runs/39094660616/artifacts/12124885341 . This is v0.115, not the new0.118 source. Clean-machine/native-window/ordinary-user ACL/prior-installer and actual provider/financial/cloud/independent acceptance remain open. Owner default-branch security PR#2/admin/historical non-Git review remains outstanding.


Final v0.118 validation:1,929 Python tests passed (one existing Starlette/httpx deprecation warning), ten Node controllers, full Portal script syntax, compileall, pip check, fourteen bounded offline profiles and saved-evidence verification passed. Current source263 files/9402610b832c85e2c1778ea12622a29621132e8fc1411957f6c01fc01b71cafe. Current tracked snapshot314 files passed artifact audit and Gitleaks with zero findings; this is not full historical/non-Git privacy clearance. Tests use injected vault/provider seams and temporary storage only. No local rendered-browser or native-profile acceptance is claimed. v0.117 safety/browser39095648318/job117581407953 is now successful; its Windows build is still in progress. New0.118 requires exact-source security/safety/Windows workflows. Latest complete native/security/safety checkpoint remains0.115; all actual broker/account/fee/hold, sustained market, funding/live/cloud, independent and owner release gates remain open.


Final v0.119 validation:1,951 Python tests passed (one existing Starlette/httpx deprecation warning), ten Node controllers, full Portal script syntax, compileall, pip check, fourteen bounded offline profiles and saved-evidence verification passed. Current source263 files/08b99a8dde7bb0b9ccddce08edc2c75568f9b2754c1ae961273aa12f0b354606. Current tracked snapshot315 files passed artifact audit/Gitleaks with zero findings; this is not full-history or historical non-Git privacy clearance. HTTP paper flow now checks explicit consent/origin/scope, fake saved-pair inspection, retained cooldown, concurrent removal while an injected GET waits, revoked-result suppression and fresh-process pending-state replay with no actual vault/provider access. No local rendered-browser/native account acceptance is claimed. Previous v0.118 sourceacd88f244ad7ae92e01e582fa016930485acc5d5 passed full-history security39096210235/job117583083273 and safety/browser39096210319/job117583084120; its Windows build remains pending. New0.119 requires exact-source Windows/security/safety CI. All actual broker/profile/account/source/fee/hold, sustained market, funding/live/cloud, independent and owner release gates remain open.


Verified installer checkpoint advanced to v0.117, source81f080658e3a309099de6f98552b606006828197. Windows39095648285/job117581727664, safety/browser39095648318/job117581407953 and security39095648336/job117581408158 all passed. Installer12125295332 expires2026-10-25T04:20:09Z; distribution12124931120 expires2026-11-17T04:20:07Z. Tester link: https://github.com/S4-cyber/Nexus-Operative/actions/runs/39095648285/artifacts/12125295332 . Download the ZIP, extract it and run NexusOperative-Setup-0.117.0.exe. This is0.117, not the newer0.120 source; it does not include the0.118 Accounts pacing buttons or0.120 storage fix. Clean-machine/native-window/ordinary-user ACL/prior-installer and actual broker/financial/cloud/independent acceptance remain open. Owner default-branch security PR#2/admin/historical non-Git review remains outstanding.


Final v0.120 validation:1,988 Python tests passed (one existing Starlette/httpx deprecation warning), ten Node controllers, Portal script syntax, compileall, pip check, fourteen offline release profiles and saved-evidence verification passed. Current source265 files/bd2114a32ade66f4f303c2cc3fa2a22236b53e1b4fcfb08a4b373558e9c2f559. Current tracked snapshot318 files passed artifact audit and Gitleaks with zero findings; exact-source full-history/security/native CI remains separate. API WAL regression proves both managed pacing actions return fixed no-store503 before additional vault/provider access and preserve the private file set. Cross-store preflight tests and the mandatory pacing profile cover unsupported layouts without automatic conversion/deletion; normal DELETE-mode recovery remains tested. No local rendered-browser or actual account/profile acceptance is claimed. Latest fully verified installer checkpoint is v0.117; new0.120 requires its own Windows/safety/security runs. Actual broker/profile/account/source/fee/hold, sustained real-feed, funding/live/cloud, independent security/risk and owner release gates stay open.

## v0.121 private acceptance-record boundary

The enlarged source-CLI acceptance record uses an exact allowlist and omits raw broker/client order identities, keys, secrets, paths, tokens and arbitrary provider fields. Its open-IOC aggregate is derived from the existing bounded read; no extra request or order/funding path exists. The record remains blocked by the existing private-artifact mode scanner. Keep stdout and pacing SQLite outside the repository. Local current-worktree and reachable fetched-history scans found zero secret/artifact-policy findings after generated caches were excluded; exact new-source GitHub security CI remains required after publication.

## v0.122 private activity evidence boundary

The new source-only activity probe requires its order input, pacing journal and activity store to resolve outside the source checkout. It emits aggregate counts/digests only and never copies raw activity/order identities, cursors, response rows or paths into stdout. Both activity output modes are blocked by the content scanner and all retained SQLite files remain blocked by path/content rules. Keep order snapshots, reports and journals private; hashes can still be linkable financial metadata and are not anonymization. Injected tests use temporary files and fake credentials/provider responses only. Exact-source reachable-history scanning remains mandatory before publication.
