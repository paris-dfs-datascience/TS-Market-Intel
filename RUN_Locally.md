# How to Run — TS Market Intel

Practical guide to running the pipeline: local setup, the run commands you'll actually
use, where output lands, the one-off tools, and how to watch logs. For *deploying* new
code into the Azure Container Apps Job, see **[DEPLOYMENT.md](DEPLOYMENT.md)**.

---

## What it does (30 seconds)

`main.py` is the single entrypoint. It runs Gemini-backed market-intelligence signals
across a set of accounts (companies), writes one `<COMPANY>/results_<DATE>.json` per
account to the configured sink, and — on a full or SQL-driven run — auto-exports a
Salesforce-import CSV at the end. Completed accounts are checkpointed, so re-running
skips work that's already done.

---

## Local setup

```bash
# 1. Create + activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.sample .env
# Edit .env — at minimum set GEMINI_API_KEY for local dev.
# Leave AZURE_STORAGE_ACCOUNT_URL unset to write output locally to ./output.
```

**Gemini key resolution order:** `--api-key` flag → `GEMINI_API_KEY` env → Azure Key
Vault. For local dev, the env var is simplest. Key Vault auth uses
`DefaultAzureCredential` (your `az login` locally, the Managed Identity in Azure).

**Output destination:** if `AZURE_STORAGE_ACCOUNT_URL` is unset, results write to the
local `OUTPUT_DIR` (default `./output`). Set the Azure vars to write to Blob instead.

---

## Running the pipeline

The default category is `all`, so a bare `python main.py` runs the full account set.

| Goal | Command |
|---|---|
| **Full run** (all categories, then auto-export CSV) | `python main.py` |
| One vertical | `python main.py --category biopharma` |
| Single company (exact, case-insensitive) | `python main.py --company "YALE UNIVERSITY"` |
| Several companies | `python main.py --companies "YALE UNIVERSITY, MIT"` |
| Super80 priority accounts only | `python main.py --super80` |
| One signal type only | `python main.py --category biopharma --signal pipeline` |
| **Small test** — first N per category | `python main.py --category biopharma --limit 5` |
| **Small test** — cap total across all categories | `python main.py --total-limit 10` |
| See all flags | `python main.py --help` |

**Category slugs:** `education`, `biopharma`, `cdmo_cro`, `clinical_dx`, `hospital`,
`industrial`, `government` (canonical names also accepted), plus `all`.

### Account source

By default accounts come from the baked-in list in `market_intel/accounts.py`. To pull
from elsewhere:

- **Salesforce CSV export:** `python main.py --from-csv accounts_db.csv`
  (or set `ACCOUNTS_CSV_PATH`). Filters to Customer80/Super80, maps segments to verticals.
- **Azure SQL** (`SalesForce.Account_base`): `python main.py --from-sql`
  (or set `ACCOUNTS_SOURCE=sql`). Token-auth via Managed Identity — no connection
  string. Needs `AZURE_SQL_SERVER` + `AZURE_SQL_DATABASE`. Hard-fails if the query fails.

Single-company / `--super80` modes always use the baked-in list; only the bulk
vertical-driven flows switch source.

---

## Output

Each account writes `<COMPANY>/results_<DATE>.json` (`<DATE>` = run date, UTC). A full
or SQL-driven run then writes the Salesforce CSV to
`_export/market_intel_export_<DATE>.csv`. Locally these live under `./output/`; in Azure
they're in the `market-intel-output` Blob container.

---

## One-off tools (skip the engine)

These read existing result JSONs / CSVs and exit without calling Gemini for new signals:

| Task | Command |
|---|---|
| Regenerate the SF export CSV from existing results | `python main.py --export-csv` |
| ...for a specific date | `python main.py --export-csv --export-date 2026-05-30` |
| Dedup analysis on an export CSV | `python main.py --analyze-dedup 2026-05-30` |
| Backfill `event_date` / `ai_summary` on result JSONs | `python main.py --backfill 2026-05-30` (or `--backfill all`) |
| Re-validate & repair `source_url`s (HEAD-check + re-ask Gemini on 404s) | `python main.py --fix-urls 2026-05-30` |

`--backfill` and `--fix-urls` are idempotent — already-processed files are skipped.

---

## Diagnostics

Probes that verify connectivity (run as modules from the repo root):

```bash
python -m diagnostics.check_gemini_api      # raw Gemini call — shows status code + quota
python -m diagnostics.check_sql_connection  # Azure SQL connectivity (needs creds / MI)
```

`check_gemini_api` is the fastest way to see exactly what Gemini returns for your key —
status code, the quota metric name, and `retryDelay` — which distinguishes a per-minute
rate limit (recoverable, just slow) from a per-day/billing wall.

---

## Watching logs

**Locally:** logs print to stdout. Rate-limit and error lines carry the full Gemini
response body — look for `⚠ Rate limit ... full Gemini response:` (per-minute, retried)
vs `✘ Gemini quota exhausted` (billing wall, hard-stops the run).

**In Azure** (Container Apps Job) — stream a running execution:

```bash
az containerapp job logs show -n thomas-intel-job -g marias_advisory_ai_rg \
  --container thomas-intel-job --execution <EXECUTION_NAME> --follow --tail 200
```

> Note: no `< >` brackets around the execution name — zsh reads them as a redirect.
> Put `--container <name>` and its value on the same line (a trailing `\ ` breaks it).

Search log history with KQL from the terminal:

```bash
ENV_ID=$(az containerapp job show -n thomas-intel-job -g marias_advisory_ai_rg --query "properties.environmentId" -o tsv)
WS=$(az containerapp env show --ids "$ENV_ID" --query "properties.appLogsConfiguration.logAnalyticsConfiguration.customerId" -o tsv)

az monitor log-analytics query --workspace "$WS" \
  --analytics-query "ContainerAppConsoleLogs_CL | where Log_s has 'full Gemini response' | project TimeGenerated, Log_s | order by TimeGenerated desc | take 50" \
  -o table
```

If the table isn't found, try `ContainerAppConsoleLogs` (no `_CL` suffix).

---

## Tuning concurrency / rate limits

These are env vars (set in `.env` locally or on the container — see `.env.sample`):

- `SEMAPHORE_SIZE` — max concurrent in-flight calls per account. **Lower this** if you're
  hitting per-minute 429s. The container env var **overrides** the code default, so
  changing it requires updating the env var, not just the code.
- `MAX_RATE_LIMIT_RETRIES` (default 8) — exponential-backoff attempts on a 429.
- `SIGNAL_HARD_TIMEOUT` (default 120s) — wall-clock kill per signal call.

Re-runs are safe: a 429 that kills a run just means top up / wait, then start again —
checkpointing skips the accounts already done.
