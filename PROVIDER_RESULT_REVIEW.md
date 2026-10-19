# Offline provider/result review

This operator diagnostic checks whether supplied normalized paper-account observations agree with their exact local account histories and the shared provider budget. It does not connect an account, collect evidence, validate market performance, prove spendable funds or permit trading. Everyday setup does not require this command.

From an installed Windows build:

```powershell
NexusOperative.exe --paper-tool provider-results --input "C:\NexusPrivate\review.json"
```

From a source checkout:

```sh
python portable/desktop.py --paper-tool provider-results --input /private/nexus/review.json
```

Keep the input and journals outside the source/installation directory, in a private owner-controlled directory. The input contains private financial observations. Do not publish it or paste credentials into it. The command does not encrypt existing files or establish their OS permissions. Its output contains account/history digests and aggregate counts; treat it as private diagnostic evidence too.

The UTF-8 JSON manifest has exactly these fields:

| Field | Meaning |
| --- | --- |
| `profile` | Exactly `nexus.paper-provider-result-input.v1` |
| `provider_budget` | Explicit ALPACA_PAPER local budget journal path |
| `expected_provider_head` | Exact current provider SHA256, supplied by the operator; never sampled implicitly |
| `accounts` | One to six account entries, each with a unique account UUID |

Each account entry contains exactly `account_id`, `pacing`, `expected_head` and `bindings`. `pacing` identifies that account's local read journal; `expected_head` pins its exact current SHA256. Absolute paths are allowed. Relative journal paths resolve beside the manifest, regardless of the process working directory. Paths cannot alias one another, the input file, SQLite recovery companions or ownership-lock families. Existing journal guards still apply.

Each binding contains exactly `after_head_sha256`, `normalized_result` and `request_scope`, as described in ALPACA_PAPER_CONTRACT.md for the five-reader review. Preserve the original normalized result and operation-specific scope; a fabricated hash, substituted target, omitted successful read or redacted observation cannot establish agreement. Activity evidence must preserve its collection scope and original-ID recovery must preserve its original target. This tool does not reconstruct missing observations from receipt hashes. No keys or API secrets belong in this format.

The input is bounded to 3 MiB serialized, with structural limits, at most six accounts and the existing 2 MiB aggregate binding limit. Duplicate JSON keys, duplicate account identities, invalid shapes, stale heads, nonregular files, linked manifest files and files changing during review fail closed. Missing paths stay missing. The command makes no default-data directories, receipt journal, network request, vault read, repair, retry or execution request. File checks and repeated journal replay are sampled checks, not atomic ownership, protection against valid-tail replacement or a distributed fence.

Exit code `0` means the review completed. Always inspect `local_provider_results_consistent` and `review_reasons`: an ordinary completed review can report disagreement. Even a consistent review remains `REVIEW_REQUIRED`, with every authority flag false. Exit code `2` means malformed/unavailable/changed input or another review failure; output uses a fixed error without echoing private paths or exception text. `--help` explains the single input argument.

The packaging smoke constructs disposable injected five-reader evidence and executes both matching and altered-result inputs through the exact source, standalone or installed command prefix. Those fixtures do not access a broker or satisfy broker-paper, native-profile, source/clock/fee/funds, sustained-market or release acceptance.
