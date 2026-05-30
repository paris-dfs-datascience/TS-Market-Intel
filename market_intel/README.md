# `market_intel/` — core runtime services

The importable package that does the actual work. `main.py` (at the repo root) is
the only entrypoint; everything it dispatches to lives here. Modules import each
other with relative imports (`from .prompts import …`), so the package is
self-contained.

## Modules

### `engine.py` — async execution engine
The heart of the pipeline. Drives one category at a time:

- **`run_category(category, sink, …)`** — top-level driver. Loads accounts (from
  `ACCOUNTS` or an `accounts_override`), skips checkpointed accounts, runs the
  rest concurrently, writes each result, refreshes the usage sidecar, prints the
  run summary. Returns the count of accounts actually run.
- **`run_account_async(...)`** — fires all of an account's signals concurrently
  under a per-account semaphore (`SEMAPHORE_SIZE`, default 4), each guarded by an
  `asyncio.wait_for` wall-clock kill (`SIGNAL_HARD_TIMEOUT`). Writes
  `<SAFE_COMPANY>/results_<run_date>.json`.
- **`UsageTracker`** — accumulates tokens, cost, and outcomes across a run. Key
  honesty rule: a transient **retry** (429 / timeout / empty that we re-fired)
  counts toward `api_calls` and `retries` but **never** toward a terminal
  outcome. Rates are reported over **`resolved`** (`successes + errors + timeouts
  + empty`), not over `api_calls`. (Pinned by `tests/test_metrics_honesty.py`.)
- **Retry policy** — three independent counters: `MAX_RATE_LIMIT_RETRIES` (429),
  `MAX_TIMEOUT_RETRIES`, `MAX_EMPTY_RETRIES`, with `RATE_LIMIT_SLEEP_CAP` on the
  backoff. Honors `Retry-After` via `_parse_retry_after`.
- **Auth** — `_resolve_api_key()` resolves the Gemini key (`--api-key` →
  `GEMINI_API_KEY` → Key Vault), cached for the process. `get_client()` builds the
  genai client.
- **Result post-processing** — `_resolve_source_url` / `_resolve_redirect` turn
  grounding-chunk redirect URLs into canonical ones; `_normalize_event_date`
  forces `YYYY-MM-DD`; `_generate_account_summary` produces the `ai_summary`.
- **Config** — all tunables are env vars read at import (`MODEL`, `TEMPERATURE`,
  `SEMAPHORE_SIZE`, the retry caps, `API_TIMEOUT_MS`, `SIGNAL_HARD_TIMEOUT`).

### `prompts.py` — signal definitions
- **`CATEGORY_TRIGGERS`** — which of the 21 signal types fire for each vertical.
- **`build_prompt(signal, entity, category, …)`** — assembles the grounded-search
  prompt, with per-vertical context blocks (`_GRANT_CONTEXT`, `_PIPELINE_CONTEXT`,
  …) so the same signal type searches differently per vertical. Recency window is
  `DAYS_BACK`; capital projects filter on `MIN_CAPEX_M`.
- **`FIELD_MAPS`** — the output JSON schema for each signal type.

### `storage.py` — the `Sink` abstraction
All persistence flows through `Sink`. `get_sink()` selects the implementation by
env var:
- **`LocalSink`** (default, `OUTPUT_DIR` or `./output`) — atomic `.tmp` +
  `os.replace` writes; supports log files.
- **`BlobSink`** (when `AZURE_STORAGE_ACCOUNT_URL` is set) — Azure Blob via
  `DefaultAzureCredential` (Managed Identity in Azure, `az login` locally);
  `overwrite=True` on every write. Stdout is the only log.

Both expose `read`, `write`, `write_text`, and `list(prefix)`. Engine code never
touches the filesystem or Blob SDK directly — swapping backends is an env change.

### `accounts.py` — the account universe
- **`ACCOUNTS`** — 482 accounts keyed by 7 Salesforce-aligned verticals.
- **`SUPER80`** — the priority subset.
- **`ACCOUNT_ALIASES`** — search-name synonyms fed into prompts.
- **`PARENT_ID_MAP`** — `Corporate_ID__c` → Salesforce `ParentId`, stamped onto
  every result and used by the CSV export.
- **`load_accounts_from_csv(path)`** — loads accounts from a Salesforce CSV export
  (`--from-csv`); curated `ACCOUNTS` classifications win over the `SEGMENT_RAW_MAP`
  heuristic. **`all_accounts_flat()`** flattens to `(name, category)` pairs for the
  `--company` / `--companies` lookups.

### `accounts_sql.py` — accounts from Azure SQL
- **`load_accounts_from_sql()`** — loads the same `{vertical: [{name, parent_id}]}`
  shape from `SalesForce.Account_base` (`--from-sql` / `ACCOUNTS_SOURCE=sql`).
  Token auth via `DefaultAzureCredential` → pyodbc (no connection-string secrets).
- **Hard-fail by design** — any connection/auth/query failure raises
  **`SqlAccountsError`** so the integration gap is loud, never a silent fallback to
  a stale list. Run `python -m diagnostics.check_sql_connection` to debug.

### `export_csv.py` — Salesforce import CSV
- **`run_export(sink, date_str=None)`** — reads every
  `<COMPANY>/results_<date>.json` for one date (default: today UTC), maps verticals
  + signal types to SF picklist **labels** (`VERTICAL_LABELS`,
  `SIGNAL_TYPE_LABELS`), and writes a UTF-8-BOM CSV to
  `_export/market_intel_export_<DATE>.csv`. Rows missing a `Parent_ID` are diverted
  to `_export/review_<DATE>.csv` instead of the import file. Auto-fires at the end
  of a full run; standalone via `--export-csv`. (Date-match contract pinned by
  `tests/test_export_date.py`.)

## Why the date matters

The engine and the export share **one UTC `run_date`** stamped by `main.py` for the
whole process. That's what keeps the export from reading a different date than the
engine wrote — the bug that previously produced an empty CSV.
