# Thomas Scientific — Market Intelligence

Automated pipeline that identifies sales opportunities for Thomas Scientific, a B2B scientific supply distributor. Monitors **237 named accounts** across **7 industry verticals** for **signals** — real-world events that indicate upcoming demand for lab supplies, reagents, consumables, and equipment.

Examples: a university receiving a large NIH grant (new lab spend), a pharma company opening a manufacturing site (capacity build-out), a hospital issuing a procurement tender (direct purchasing opportunity).

Uses **Gemini 2.5 Flash with Google Search grounding** to surface the signals from press releases, SEC filings, news articles, and official publications.

---

## Quick start

```bash
# 1. Clone and create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy the env sample and fill in the Gemini key
cp .env.sample .env
# edit .env and set GEMINI_API_KEY

# 3. Run (writes to ./output/)
python main.py --company "YALE UNIVERSITY" --signal grant    # smoke test
python main.py --category biopharma --limit 5                # 5 BioPharma accounts
python main.py --category all                                # full run (all 237)
```

---

## Architecture

Single entrypoint (`main.py`) dispatches by `--category`, `--company`, or `--super80`. All persistence flows through a `Sink` that writes to the local filesystem by default, or to Azure Blob Storage when env vars are set.

```
main.py          ← argparse CLI, dispatch
  ↓
engine.py        ← async execution engine (semaphore, retries, checkpointing, usage tracker)
  ↓
prompts.py       ← 21 signal prompts, CATEGORY_TRIGGERS, FIELD_MAPS
accounts.py      ← 237 accounts by category, aliases, Super80 list
storage.py       ← LocalSink / BlobSink (selected by env at runtime)
```

### Output layout

Each account's result lands in its own folder, making ingestion into Salesforce (or any other downstream system) a clean 1:1 mapping:

```
<sink root>/                         (./output/ locally, Blob container in Azure)
  YALE_UNIVERSITY/results.json
  HARVARD_UNIVERSITY/results.json
  PFIZER/results.json
  ...
  _usage/biopharma.json              ← per-category cost + timing sidecar
  _usage/education_research.json
  _logs/biopharma.log                ← LocalSink only; stdout on BlobSink
```

Folder names come from `engine._safe_name()` — upper-cased, with non-`[A-Z0-9_]` characters collapsed to `_`.

### Per-account result shape

```json
{
  "account": "YALE UNIVERSITY",
  "category": "Education & Research",
  "timestamp": "2026-04-23T14:30:00",
  "signals": {
    "grant": [
      {
        "summary": "Yale receives $12M NIH R01 for cancer immunotherapy",
        "recipient": "Yale School of Medicine",
        "department_or_lab": "Dept. of Immunobiology",
        "agency": "NIH",
        "amount": "$12M",
        "event_date": "April 10, 2026",
        "why_it_matters": "Large new grant means new lab buildout...",
        "source_url": "https://..."
      }
    ],
    "faculty": [],
    "capital": []
  }
}
```

Every signal record carries four common fields — `summary`, `event_date`, `why_it_matters`, `source_url` — plus signal-type-specific fields from `FIELD_MAPS` in `prompts.py`. The full schema is documented in the [Signal types](#signal-types) table below.

### Checkpointing / resume

Each account's result is persisted as soon as it finishes. On restart, the pipeline checks for `<COMPANY>/results.json` and skips accounts that already have one. This means:

- Interrupting a run mid-flight is safe — just re-run the same command.
- Container crashes don't lose progress.
- To force a re-run for one account, delete its folder (or blob prefix) and restart.

---

## CLI reference

```bash
python main.py [--category <slug>] [--signal <name>] [--company <name>]
               [--limit N] [--super80] [--api-key KEY]
```

| Flag | Description |
|---|---|
| `--category` | Run one vertical. Accepts slug (`biopharma`, `education`, `cdmo_cro`, `clinical_dx`, `hospital`, `industrial`, `government`) or canonical name. Default `all`. |
| `--signal` | Run only one signal type (e.g. `grant`, `pipeline`, `tender`). See [signal types](#signal-types). |
| `--company` | Run one company by **exact** (case-insensitive) name. E.g. `"YALE UNIVERSITY"`. Substring matches are rejected to prevent silent over-match. |
| `--limit N` | Cap to first N pending (not-yet-completed) accounts. Useful for smoke tests. |
| `--super80` | Run the Super80 priority subset across all verticals. |
| `--api-key` | Override `GEMINI_API_KEY` from the env. |

Valid categories (slug or full): `biopharma`, `education`, `cdmo_cro`, `clinical_dx`, `hospital`, `industrial`, `government`, `all`.

---

## Configuration

All config is environment-driven. See `.env.sample` for the annotated list.

### Gemini API key (required — choose one source)

Resolution order: `--api-key` flag → `GEMINI_API_KEY` env → Azure Key Vault.

| Var | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio key with access to Gemini 2.5 Flash + Search grounding. Use for local dev. |
| `AZURE_KEY_VAULT_URL` | Key Vault URL, e.g. `https://<vault>.vault.azure.net/`. When set, the pipeline fetches the secret via `DefaultAzureCredential`. Preferred in Azure. |
| `GEMINI_API_KEY_SECRET_NAME` | Secret name inside the vault. Default `gemini-api-key`. |

The Managed Identity used to read from Key Vault needs the **Key Vault Secrets User** role on the vault.

### Output destination (choose one path)

**Azure Blob Storage** (for containerized deploys):
| Var | Purpose |
|---|---|
| `AZURE_STORAGE_ACCOUNT_URL` | e.g. `https://tsmarketintel.blob.core.windows.net` |
| `AZURE_STORAGE_CONTAINER` | Blob container name (must exist or be createable by the identity) |

Auth is via `DefaultAzureCredential` — Managed Identity in Azure Container Apps, or `az login` / service-principal env vars locally. The identity needs **Storage Blob Data Contributor** on the storage account (plus **Key Vault Secrets User** on the vault if `AZURE_KEY_VAULT_URL` is used).

**Local filesystem** (default when Azure vars are unset):
| Var | Purpose |
|---|---|
| `OUTPUT_DIR` | Directory to write results under. Default `./output`. |

### Tuning (all optional)
| Var | Default | Purpose |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Override to test other Gemini models |
| `GEMINI_TEMPERATURE` | `0.2` | Lower = more deterministic |
| `SEMAPHORE_SIZE` | `13` | Max concurrent in-flight API calls per account (covers BioPharma's 13 signals) |
| `MAX_RETRIES` | `3` | Attempts per signal before giving up |
| `SIGNAL_HARD_TIMEOUT` | `120` | Wall-clock kill per signal call (seconds) |
| `API_TIMEOUT_MS` | `60000` | HTTP connection timeout (connection establishment only) |
| `DAYS_BACK` | `30` | Lookback window for signals — set to match your run cadence |
| `MIN_CAPEX_M` | `50` | Minimum capital project value to report ($M) |

### Rate-limit handling

There is **no artificial call delay**. Rate control comes from:
1. A semaphore that caps concurrent in-flight Gemini calls per account to `SEMAPHORE_SIZE`.
2. Accounts processed sequentially (signals within an account fire in parallel).
3. **429 retries with exponential backoff** — 10s → 20s → 40s, capped at 120s, up to `MAX_RETRIES` attempts per signal. On persistent 429 the signal is skipped (empty list) so one rate-limited call doesn't abort the whole run.

---

## Docker

```bash
docker build -t ts-market-intel .
docker run --env-file .env -v $PWD/output:/app/output ts-market-intel --category biopharma --limit 5
```

The container entrypoint is `python main.py`; all CLI flags pass through. Pass Azure env vars via `--env-file` to write to Blob instead of the mounted volume.

---

## Deploying to Azure (Container Apps Jobs)

The pipeline is designed to run as an **Azure Container Apps Job** — a purpose-built one-shot compute unit for batch workloads. High-level steps:

1. **Build and push** to Azure Container Registry:
   ```bash
   az acr build --registry <acr> --image ts-market-intel:latest .
   ```

2. **Store the Gemini key in Key Vault**:
   ```bash
   az keyvault secret set --vault-name <vault> --name gemini-api-key --value <your-gemini-key>
   ```

3. **Create a user-assigned Managed Identity** and grant it access to both Blob Storage and Key Vault:
   ```bash
   az identity create -g <rg> -n ts-intel-mi
   MI_PRINCIPAL=$(az identity show -g <rg> -n ts-intel-mi --query principalId -o tsv)

   az role assignment create --assignee $MI_PRINCIPAL \
     --role "Storage Blob Data Contributor" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<sa>

   az role assignment create --assignee $MI_PRINCIPAL \
     --role "Key Vault Secrets User" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault>
   ```

4. **Create the Job** — no `GEMINI_API_KEY` env var; the container fetches it from Key Vault at startup:
   ```bash
   az containerapp job create \
     --name ts-market-intel \
     --resource-group <rg> \
     --environment <containerapps-env> \
     --trigger-type Manual \
     --replica-timeout 21600 \
     --replica-retry-limit 1 \
     --image <acr>.azurecr.io/ts-market-intel:latest \
     --cpu 0.5 --memory 1Gi \
     --mi-user-assigned <mi-resource-id> \
     --env-vars \
         AZURE_STORAGE_ACCOUNT_URL=https://<sa>.blob.core.windows.net \
         AZURE_STORAGE_CONTAINER=market-intel-results \
         AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net/ \
     --args "--category" "all"
   ```

5. **Trigger a run**:
   ```bash
   az containerapp job start -n ts-market-intel -g <rg>
   ```

6. **Or schedule it** (e.g. every Monday at 06:00 UTC):
   ```bash
   az containerapp job update -n ts-market-intel -g <rg> \
     --trigger-type Schedule --cron-expression "0 6 * * MON"
   ```

**Runtime notes:**
- A full `--category all` run processes ~2,100 Gemini calls. Expected runtime: ~45–90 min on a paid Gemini tier (semaphore-limited). Set `--replica-timeout` accordingly.
- For maximum parallelism, create 7 separate jobs (one per vertical) and fire them concurrently.
- Logs stream to the Log Analytics workspace attached to the Container Apps Environment.
- Checkpoint-resume works in Azure the same as locally — accounts with an existing blob are skipped.

---

## Signal types

21 signals total, defined in `prompts.CATEGORY_TRIGGERS`. The same signal name (e.g. `grant`) uses vertical-specific search language.

| Signal | What it detects | Verticals |
|---|---|---|
| `grant` | NIH / NSF / BARDA / government grant awards | Edu, BioPharma, Clinical, Hospital, Gov |
| `faculty` | New faculty or research leadership hires | Edu, Hospital |
| `capital` | New lab/facility/manufacturing construction (≥ `MIN_CAPEX_M`) | All |
| `contract` | Open RFPs, procurement bids, expiring supply contracts | All except Industrial |
| `pipeline` | New drugs, IND filings, clinical trials, diagnostic assay launches | BioPharma, CDMO, Clinical, Hospital |
| `expansion` | New sites, geographic entry, capacity expansion | All |
| `partnership` | Licensing, co-development, joint ventures | BioPharma, CDMO, Clinical, Hospital, Industrial |
| `funding` | VC rounds, government program awards, strategic investments | Edu, BioPharma, CDMO, Clinical, Hospital, Industrial |
| `project` | New multi-year programs with disclosed budgets | All |
| `regulatory` | FDA approvals, warning letters, GMP inspections, accreditation changes | BioPharma, CDMO, Clinical, Hospital |
| `hiring` | Bulk hiring announcements (≥50 net new) | BioPharma, CDMO, Industrial |
| `tender` | Public procurement tenders, GPO bids | Hospital, Gov |
| `breakthrough` | Nobel prizes, landmark publications, major science awards | Edu |
| `ma` | Mergers & acquisitions | BioPharma, CDMO |
| `spinoff` | New company formations and carve-outs | BioPharma |
| `production` | Production line changes, plant retooling | Industrial |
| `volume` | Lab test volume increases, new test menu additions | Clinical |
| `competitive` | Competitor wins on lab supply contracts | Clinical |
| `mandate` | Government mandates triggering lab spending | Gov |
| `legislation` | Budget appropriations for lab infrastructure | Gov |
| `closure` | Facility closures, lab shutdowns, programme terminations | All |

Full per-signal field schemas live in `FIELD_MAPS` in `prompts.py`.

---

## Adding or modifying accounts

Accounts are defined in `accounts.py`:

- `ACCOUNTS` — dict keyed by canonical category name
- `SUPER80` — list of highest-priority cross-vertical accounts
- `ACCOUNT_ALIASES` — maps internal names to public / alternate names used in Gemini prompts (e.g. `"MASSACHUSETTS INSTITUTE OF TEC"` → `["MIT", "Massachusetts Institute of Technology"]`)

When adding: put the account in the right `ACCOUNTS[category]` bucket; if the internal name differs from its public name, add an `ACCOUNT_ALIASES` entry so Gemini's search finds the right company.
