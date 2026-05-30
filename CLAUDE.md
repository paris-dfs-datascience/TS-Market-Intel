# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What This Is

A market intelligence platform for Thomas Scientific. Uses Gemini 2.5 Flash (with Google Search grounding) to detect sales signals across 482 B2B scientific supply accounts spread across 7 industry verticals. Runs are parallelized async API calls, checkpointed to JSON, and containerized for deployment to an Azure Container App.

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

Valid `--category` values: `BioPharma`, `Education & Research`, `CDMO / CRO`, `Clinical / Molecular Diagnostics`, `Hospital & Health Systems`, `Industrial`, `Government`, `all`. Slugs: `biopharma`, `education`, `cdmo_cro`, `clinical_dx`, `hospital`, `industrial`, `government`.

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
| `main.py` | Argparse CLI; dispatches runs by category / company / super80. Stays at repo root (Docker entrypoint). |
| `market_intel/engine.py` | Async execution engine — semaphore, retries, checkpointing, usage tracking |
| `market_intel/storage.py` | `Sink` abstraction (`LocalSink` / `BlobSink`); selected via env by `get_sink()` |
| `market_intel/prompts.py` | 21 signal prompts (`build_prompt()`), vertical→signal mapping (`CATEGORY_TRIGGERS`), output schemas (`FIELD_MAPS`) |
| `market_intel/accounts.py` | 482 accounts keyed by 7 SF-aligned verticals (`ACCOUNTS`), `SUPER80`, `ACCOUNT_ALIASES`, `PARENT_ID_MAP` |
| `market_intel/accounts_sql.py` | Loads the account list from `SalesForce.Account_base` in Azure SQL (Managed Identity / token auth) |
| `market_intel/export_csv.py` | Exports all `results_*.json` to a single CSV for Salesforce import |
| `tools/backfill_results.py` | One-off re-processor for existing results JSONs (`--backfill`, `--fix-urls`); run via `python -m tools.backfill_results` |
| `tools/analyze_dedup.py` | One-off dedup analysis on an export CSV (`--analyze-dedup`); run via `python -m tools.analyze_dedup` |
| `diagnostics/check_gemini_api.py` | Probe Gemini grounded-search with the live key/auth path; run via `python -m diagnostics.check_gemini_api` |
| `diagnostics/check_sql_connection.py` | Probe Azure SQL connectivity + MI permissions; run via `python -m diagnostics.check_sql_connection` |
| `tests/` | Pytest suite (`pytest -q` from repo root). Not copied into the Docker image. |

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

## Run History & Learnings

### Dated Output Files
Output files use `results_YYYY-MM-DD.json` naming (not `results.json`). Same-day re-runs overwrite; different dates create new files. Checkpoint reads today's dated filename.

### CLI Flags Added
- `--companies "CO1,CO2,..."` — run a subset of companies by exact name (case-insensitive); groups by category and calls `run_category` once per category with `accounts_override`
- `--total-limit N` — cap total accounts across all categories when using `--category all`
- `--export-csv` — skip the engine; just regenerate the SF-import CSV from every `<COMPANY>/results_<TODAY>.json` already present in the sink. Writes `_export/market_intel_export_<DATE>.csv` (UTC date) back to the sink. Pairs cleanly with `--category all`, which now auto-runs the export at the end of every full run.

### SF Export Pipeline
`export_csv.py` exposes `run_export(sink, date_str=None)`. It reads result JSONs via the `Sink` abstraction (works with both `LocalSink` and `BlobSink`), filters to a single UTC date (defaults to today), translates verticals + signal types to the SF picklist labels (`VERTICAL_LABELS`, `SIGNAL_TYPE_LABELS`), and writes a UTF-8-BOM CSV at `_export/market_intel_export_<DATE>.csv`. Auto-fires at the tail of `main.py`'s `--category all` branch; standalone-invokable via `--export-csv`. To support these, `Sink` gained `list(prefix)` and `write_text(name, text)` methods on both subclasses.

### New Accounts Added
- **Industrial**: `ACT LABORATORIES`, `AMWATER`, `ADVANCED VISION SCIENCE`
- **Government**: `CASTATE NASPO`, `CCSANFRAN`

### Azure Container App Job — Arg Passing (+ env-wipe gotcha)
Only reliable method to change job CLI args: PATCH the job template via `az rest`, then start with no body override. `az containerapp job start --args` and `az rest POST /start` body overrides are unreliable — the stored template takes precedence.

**Gotcha**: a PATCH against `properties.template.containers[]` *replaces the entire container object* with whatever JSON you sent. If you PATCH only `{name, image, args}`, the `env` block is wiped silently and the next run fails (e.g. `No Gemini API key found` because `AZURE_KEY_VAULT_URL` is gone). Always send the full desired spec — image + args + env — in a single PATCH body.

```bash
# Patch stored template args
az rest --method PATCH \
  --url "https://management.azure.com/subscriptions/<SUB>/resourceGroups/<RG>/providers/Microsoft.App/jobs/<JOB>?api-version=2024-03-01" \
  --body '{"properties":{"template":{"containers":[{"name":"<CONTAINER>","args":["--category","all"]}]}}}'

# Start with no body — uses the patched template
az containerapp job start --name <JOB> --resource-group <RG>
```

### Azure RBAC (Critical)
Managed identity needs **`Storage Blob Data Contributor`** on the storage account — NOT just `Contributor`. `Contributor` is management-plane only and does not grant blob data-plane read/write under OAuth/token auth. Assigning `Contributor` alone causes `AuthorizationPermissionMismatch` on every blob write.

### API Credit Depletion
When Gemini prepaid credits run out, the engine receives `429 RESOURCE_EXHAUSTED` and exits. All previously completed accounts remain checkpointed and safe. To resume: top up credits at `aistudio.google.com`, then re-run `python main.py --category all` — the checkpoint will skip completed accounts automatically.

### Run Status as of 2026-05-14 (run `thomas-intel-uq13n1x`)

| Vertical | Status | Accounts | Signal Hits |
|---|---|---|---|
| Education & Research | ✅ Complete | 46/46 | 244 |
| BioPharma | ✅ Complete | 52/52 | 246 |
| CDMO / CRO | ✅ Complete | 17/17 (checkpointed from prior run) | — |
| Clinical / Mol Dx | ⚠️ Partial | ~33/48 | partial |
| Hospital & Health Systems | ❌ Not started | — | — |
| Industrial | ❌ Not started | — | — |
| Government | ❌ Not started | — | — |

Stop cause: `429 RESOURCE_EXHAUSTED` — Gemini API credits exhausted mid-run during Clinical/Mol Dx (stopped at TEMPUS).

**To complete**: top up Gemini credits, then `python main.py --category all`. Checkpoint resumes from TEMPUS onward.

### Signal Timeouts (non-fatal)
Per-signal `SIGNAL_HARD_TIMEOUT` (default 60s for BioPharma, 30s for some others). Timed-out signals are skipped; the account is still saved with all signals that completed. Observed in runs: STANFORD UNIVERSITY [expansion] 30.3s timeout, SANOFI [ma] 60.0s timeout — both accounts saved correctly.

### Transient 503 Errors (auto-recovered)
Multiple signals hit `503 UNAVAILABLE` mid-run (e.g. UNIVERSITY OF CINCINNATI, UNIVERSITY OF PENNSYLVANIA, ATCC). Engine auto-retries up to 3 times with backoff; all recovered with no data loss.

---

### Salesforce Data Model Alignment (2026-05-15)

Salesforce CSV import matches picklist fields on **labels**, not API values. This caused upload failures from `output/market_intel_export.csv`:

- `account_vertical`: "CDMO / CRO" and "Clinical / Mol Dx" were wrong labels → fixed
- `signal_type`: `capital`, `faculty`, `ma` were API keys, not labels → fixed to "Capital Project", "Faculty Hire", "M&A"

**How the pipeline stores vs. exports verticals:**
- `engine.py` stores the SF API value in JSON (e.g. `"account_vertical": "Clinical_Mol_Dx"`)
- `export_csv.py` maps JSON API values → SF picklist labels for import
- `_vertical_api_name()` in `engine.py` has a hardcoded override for "Clinical / Molecular Diagnostics" → `"Clinical_Mol_Dx"` (regex alone would produce "Clinical_Molecular_Diagnostics")

**SF picklist values (canonical reference):**

| SF Label | SF API Value |
|---|---|
| BioPharma | `BioPharma` |
| CDMO / CRO | `CDMO_CRO` |
| Clinical / Molecular Diagnostics | `Clinical_Mol_Dx` |
| Education & Research | `Education_Research` |
| Government | `Government` |
| Hospital & Health Systems | `Hospital_Health_Systems` |
| Industrial | `Industrial` |

The internal vertical name used throughout the codebase matches the SF label exactly.

### Account Reclassification & Expansion (2026-05-15)

`ACCOUNTS` was rebuilt from 237 to 482 accounts with 7 SF-aligned verticals (removed non-SF keys: `Resellers`, `Advanced Technology`, `International`; renamed `Clinical / Mol Dx` → `Clinical / Molecular Diagnostics`; added `CDMO / CRO` and `Hospital & Health Systems`).

**Vertical account counts:**

| Vertical | Accounts |
|---|---|
| Industrial | 192 |
| BioPharma | 84 |
| Education & Research | 61 |
| Government | 41 |
| Clinical / Molecular Diagnostics | 41 |
| Hospital & Health Systems | 35 |
| CDMO / CRO | 28 |
| **Total** | **482** |

Industrial is largest because it absorbed the former Resellers (~98 accounts), Advanced Technology (~31), and original Industrial legacy accounts (~51).

**`load_accounts_from_csv()` precedence**: when loading from a CSV (`--from-csv`), manually curated `ACCOUNTS` classifications take precedence over the `segment_raw` heuristic. `SEGMENT_RAW_MAP` is only the fallback for accounts not found in `ACCOUNTS`.

**`PARENT_ID_MAP`**: 469-entry dict in `accounts.py` mapping `Corporate_ID__c` → Salesforce `ParentId`. Populated from the SF Account_base export. Used by `engine.py` to stamp `parent_id` on every results JSON and by `export_csv.py` to populate the `Parent_ID` column.
