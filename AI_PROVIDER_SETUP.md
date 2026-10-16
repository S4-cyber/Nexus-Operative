# Opt-in external AI research adviser (pre-release 0.41)

The existing five-role model council remains a deterministic fixture. Nexus now
includes a **separate, disabled-by-default** OpenAI Chat Completions research
adapter for advisory-only responses. It never routes an order, produces an
approval, accesses accounts or receives market-data-provider credentials.

## Recommended Windows setup — one-time, inside PORTAL\n1. Open Nexus PORTAL and expand **Connect AI Research (Optional)**.\n2. Create your own OpenAI API key and set an appropriate provider-side spending limit.\n3. Enter the key in **API Key (local vault only)** and choose **Save Key Locally & Enable**. The UI asks for explicit approval before storage; this saves it to Windows Credential Manager, not a plaintext config file.\n4. The system will not send billable inference requests until you separately click **Send One Research Request** and approve its confirmation. **Disconnect & Remove Saved Key** deletes the locally stored credential.\n\nThis built-in vault setup is currently Windows-specific. It runs on local loopback, so do not expose Nexus PORTAL to external networks. All AI features remain optional, and the system never requests funds or enables trades.\n\n## Alternate local environment setup (optional)
1. Obtain an OpenAI API key directly from your own provider account and
   understand that requests can incur provider charges.
2. On your own machine, set `NEXUS_ENABLE_LLM_RESEARCH=1` and set
   `OPENAI_API_KEY` as a local environment secret. **Never commit either
   credentials or .env files to GitHub** or paste the key into chat.
3. Start the Nexus desktop backend locally. Inspect the non-sensitive
   `GET /api/paper/ai-provider/status` output. "configured" means local
   opt-in is present; it does **not** verify a live provider connection.
4. Explicitly POST a bounded **research snapshot only** to
   `/api/paper/ai-provider/market_regime/research`.
   The other fixed roles are `strategy_research`, `execution_quality`,
   `portfolio_risk`, and `adversarial_review`.

## Safeguards and limitations
- No secret values appear in the status output.
- Fixed HTTPS endpoint and `gpt-4o-mini` model, strict JSON-schema output,
  no tools/function-calling, four-second HTTP timeout.
- Input is limited to 4 KB; replies to 16 KB; maximum five attempted requests
  **per process**, counted before network access. Restarting resets that cap.
- Output roles/verdicts are schema-checked and verified again by local code.
  Provider errors, refusals and invalid outputs become an abstention.
- **No hard dollar budget is guaranteed.** Token prices change and the
  model-supplied cost estimate is not billing telemetry. Use provider-side
  billing controls, and keep the feature disabled until desired.
- The remote model receives any data you include in a research snapshot.
  Never send secrets, private financial information, or personally identifying
  data. Treat summaries, instructions embedded in datasets, and model responses
  as untrusted.
- The new adapter is **not automatically wired to the trading council**:
  the deterministic veto governor remains independent. No brokerage actions
  or money movement can occur through this API.

References: OpenAI API docs, Chat Completions structured response formats.
