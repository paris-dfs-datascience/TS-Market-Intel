# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What This Is

A market intelligence platform for Thomas Scientific. Uses Gemini 2.5 Flash (with Google Search grounding) to detect sales signals across 237 B2B scientific supply accounts spread across 7 industry verticals. Runs are parallelized async API calls, checkpointed to JSON, and containerized for deployment to an Azure Container App.

## Local Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.sample .env
# fill in GEMINI_API_KEY (and optionally Azure Storage vars)
```

Activate the venv (`source .venv/bin/activate`) at the start of every session.

## Running the System

Single entrypoint: `main.py`. Output destination is env-driven (see `storage.py`) — local filesystem by default, Azure Blob Storage when `AZURE_STORAGE_ACCOUNT_URL` is set.

```bash
# All categories (default)
python main.py --category all

# Single category
python main.py --category biopharma

# Single signal type
python main.py --category biopharma --signal grant

# Single company (substring match across all categories)
python main.py --company "PFIZER"

# Super80 priority subset
python main.py --super80

# Test mode — limit to N pending accounts
python main.py --category biopharma --limit 5

# Override API key (otherwise reads GEMINI_API_KEY from env)
python main.py --api-key YOUR_KEY
```

Valid `--category` values: `BioPharma`, `Education & Research`, `CDMO/CRO`, `Clinical/Mol Dx`, `Hospital & Health Systems`, `Industrial`, `Government`, `all`.

### Docker

```bash
docker build -t ts-market-intel .
docker run --env-file .env ts-market-intel --category biopharma --limit 5
```

The container's entrypoint is `python main.py`; all CLI flags pass through.

## Environment Variables

See `.env.sample` for the full list. Everything not covered below is tuning config.

### Gemini API key (required)

Resolution order in `engine._resolve_api_key()`: `--api-key` flag → `GEMINI_API_KEY` env → Azure Key Vault.

| Env var                        | Meaning                                                                 |
|--------------------------------|-------------------------------------------------------------------------|
| `GEMINI_API_KEY`               | Direct API key. Use for local dev. |
| `AZURE_KEY_VAULT_URL`          | Key Vault URL (e.g. `https://<vault>.vault.azure.net/`). When set and `GEMINI_API_KEY` is unset, the secret is fetched via `DefaultAzureCredential`. |
| `GEMINI_API_KEY_SECRET_NAME`   | Secret name inside the vault. Default `gemini-api-key`. |

Resolved once per process and cached via `functools.lru_cache` — `--category all` does not refetch per category.

### Output destination

| Env var                      | Meaning                                                                 |
|------------------------------|-------------------------------------------------------------------------|
| `AZURE_STORAGE_ACCOUNT_URL`  | Setting this enables Azure Blob output (e.g. `https://x.blob.core.windows.net`) |
| `AZURE_STORAGE_CONTAINER`    | Blob container name (required when account URL is set)                  |
| `OUTPUT_DIR`                 | Local output directory (default `./output`; used when Azure vars unset) |

### Managed Identity roles (Azure deployment)

The container's Managed Identity needs:
- **Storage Blob Data Contributor** on the target storage account (for Blob output)
- **Key Vault Secrets User** on the Key Vault (only if using `AZURE_KEY_VAULT_URL`)

Locally, `DefaultAzureCredential` falls back to `az login` creds or `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` env vars.

## Architecture

### Core Files

| File | Purpose |
|------|---------|
| `main.py` | Argparse CLI; dispatches runs by category / company / super80 |
| `engine.py` | Async execution engine — semaphore, retries, checkpointing, usage tracking |
| `storage.py` | `Sink` abstraction (`LocalSink` / `BlobSink`); selected via env by `get_sink()` |
| `prompts.py` | 21 signal prompts (`build_prompt()`), vertical→signal mapping (`CATEGORY_TRIGGERS`), output schemas (`FIELD_MAPS`) |
| `accounts.py` | 237 accounts keyed by industry (`ACCOUNTS`), `SUPER80`, `ACCOUNT_ALIASES` |

### Execution Flow

1. `main.py` parses args and calls `get_sink()` — returns `BlobSink` if Azure env vars are set, else `LocalSink`.
2. `run_category(category, sink, …)` loads accounts from `ACCOUNTS[category]` (or an override like Super80 / a single company).
3. Checkpoint resume — for each account, `sink.read("<SAFE_COMPANY>/results.json")` is checked; accounts with a prior result are skipped.
4. For each pending account, all signals fire concurrently (semaphore-limited); `asyncio.wait_for` enforces `SIGNAL_HARD_TIMEOUT`.
5. Each account's result is persisted to `<SAFE_COMPANY>/results.json` via `sink.write()` as soon as it finishes. The usage sidecar at `_usage/<category>.json` is refreshed after every account.
6. Log files are only written when `sink.supports_log_files` (LocalSink) — path is `_logs/<category>.log`. Under BlobSink, stdout is the only log — captured by Azure Container App Logs.

### Output Layout

```
<sink root>/
  YALE_UNIVERSITY/results.json        ← one file per account
  HARVARD_UNIVERSITY/results.json
  PFIZER/results.json
  ...
  _usage/education_research.json      ← one usage sidecar per category run
  _usage/biopharma.json
  _logs/education_research.log        ← LocalSink only (BlobSink → stdout)
```

Each `<SAFE_COMPANY>/results.json` is a single account object (not an array):

```json
{
  "account": "PFIZER",
  "category": "BioPharma",
  "timestamp": "2026-04-23T10:30:00",
  "signals": {
    "grant": [
      {
        "summary": "...",
        "recipient": "...",
        "agency": "NIH/BARDA",
        "amount": "$12M",
        "event_date": "April 10, 2026",
        "why_it_matters": "...",
        "source_url": "https://..."
      }
    ],
    "pipeline": [],
    "capital": []
  }
}
```

`SAFE_COMPANY` is the account name upper-cased with non-`[A-Z0-9_]` characters collapsed to `_` (see `engine._safe_name`). Each signal type has its own field schema defined in `FIELD_MAPS` in `prompts.py`.

### Signal Types

21 signals total, defined in `prompts.py` → `CATEGORY_TRIGGERS`. See that file for the per-vertical mapping.

## Key Design Decisions

- **Sink abstraction**: all persistence goes through `storage.py`. Swapping local ↔ Azure Blob is an env-var change; engine code is storage-agnostic.
- **Atomic local writes**: `LocalSink` uses `.tmp` + `os.replace()` to avoid corruption on interrupt.
- **Blob overwrite semantics**: `BlobSink.write()` uses `overwrite=True`. Every per-account save rewrites the full results blob — safe because checkpoint resume reads the latest blob before starting.
- **Checkpointing**: completed accounts live in the output JSON itself; re-runs skip them.
- **Stdout logging in Azure**: file log handlers are skipped under `BlobSink` so Azure Container App Logs stay the source of truth. ANSI color codes are stripped from the stdout handler when stdout is not a TTY (containers, CI, redirects), so Log Analytics output stays clean.
- **Category-aware prompts**: same signal type (e.g. "grant") uses different search language per vertical.
- **Managed Identity for Azure auth**: `DefaultAzureCredential` covers Blob Storage and Key Vault with a single auth path — no connection strings or API keys baked into env.
- **Key Vault for secrets**: production fetches `GEMINI_API_KEY` from Key Vault at startup (see `_resolve_api_key`). Local dev can still set `GEMINI_API_KEY` directly in `.env`.

## Dependencies

All dependencies pinned in `requirements.txt`.

| Package | Purpose |
|---------|---------|
| `google-genai` | Gemini API client |
| `python-dotenv` | `.env` file loading |
| `azure-storage-blob` | Blob Storage client |
| `azure-keyvault-secrets` | Key Vault client for fetching `GEMINI_API_KEY` in Azure |
| `azure-identity` | `DefaultAzureCredential` for Managed Identity + local auth |
