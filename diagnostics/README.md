# `diagnostics/` — operational probes

Connectivity/auth checks for the two external dependencies. They are **not** tests
(they hit live services and need real credentials) — they were renamed from
`test_*` so a future `pytest` run won't try to collect them. Run as modules from
the repo root: `python -m diagnostics.<name>`.

## `check_gemini_api.py` — Gemini grounded-search probe

`main()` resolves the API key through the same path the pipeline uses
(`market_intel.engine._resolve_api_key` → `--api-key` / `GEMINI_API_KEY` / Key
Vault), then sends a direct grounded-search POST and prints the **raw** response
(status code, error reason, quota metric). The engine swallows that body in
production; this script surfaces it — useful for diagnosing 429s / quota issues.

```bash
python -m diagnostics.check_gemini_api
```

In Azure, run it inside the container via a `command:` override on the Container
Apps Job: `["python", "-m", "diagnostics.check_gemini_api"]` — the bound Managed
Identity fetches the key from Key Vault.

## `check_sql_connection.py` — Azure SQL connectivity probe

`main()` walks through the full `--from-sql` auth chain one step at a time, so a
failure points at the exact broken link rather than a generic error:

1. `step_env_vars` — required env vars present (`AZURE_SQL_SERVER`,
   `AZURE_SQL_DATABASE`, `AZURE_CLIENT_ID`, …)
2. `step_odbc_driver` — ODBC Driver 18 for SQL Server installed
3. `step_token` — `DefaultAzureCredential` yields an AAD token
4. `step_connect` — pyodbc connects with the token
5. `step_identity_probe` — which principal the DB sees
6. `step_schema_probe` / `step_row_count` / `step_sample_rows` — the
   `SalesForce.Account_base` table is readable and shaped as expected

```bash
python -m diagnostics.check_sql_connection
```

Locally this will fail at the token/connect step without `az login` + the right
env vars — that's expected; the import/layout still resolves cleanly.
