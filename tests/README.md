# `tests/` — pytest suite

Real unit tests (distinct from `diagnostics/`, which hit live services). Run from
the repo root so `market_intel` is importable:

```bash
pytest -q
```

This directory is intentionally **not** copied into the Docker image.

## `test_metrics_honesty.py`

Pins the `UsageTracker` accounting rules in `market_intel/engine.py`:

- a transient `retry` counts toward `api_calls` and `retries` but **not** toward
  `errors` or any terminal outcome;
- `resolved` = `successes + errors + timeouts + empty` (excludes retries);
- `success_rate` is computed over `resolved`, not `api_calls`;
- per-signal `errors` only increment on a terminal `error`, not on a retry;
- `to_dict()` reports `resolved`.

Regression guard for the bug where retries inflated the error count and tanked the
displayed success rate.

## `test_export_date.py`

Pins the date-filtering contract in `market_intel/export_csv.run_export`:

- only `results_<date>.json` files matching the requested date are exported; other
  dates are skipped;
- rows missing a `Parent_ID` are diverted to `_export/review_<DATE>.csv` instead of
  the import file.

Uses a `LocalSink` in a `tmp_path` (via `OUTPUT_DIR` + `get_sink()`). Regression
guard for the empty-CSV incident caused by an engine-write / export-read date
mismatch.

## `test_scheduled_categories.py`

Pins `--categories` parsing in `main.py` and the coverage of the two scheduled Azure
jobs (`thomas-intel-job-m1` / `-m15`, see DEPLOYMENT.md):

- the two `--categories=` job args resolve to the intended vertical sets;
- together they cover **every** vertical in `ACCOUNTS` — a newly added vertical that
  neither run picks up fails here;
- Education & Research is the only vertical run twice (anything else is double-spend);
- tokens are case/whitespace-insensitive and deduped, order preserved;
- an unrecognized or empty value exits non-zero rather than quietly shrinking the run;
- `MONTHLY_SCHEDULE` (the `--monthly-schedule` fallback) matches the deployed job args.

The job args are Azure state and can't be read from Python, so they're duplicated as
constants here — drift between the two is exactly what this file catches.

## `test_finalize_on_abort.py`

Pins `main._run_and_finalize`: a run that aborts on sustained Gemini quota exhaustion must
still write the Salesforce export CSV for the accounts that completed, then exit non-zero.

- both abort flavors are handled — `RuntimeError` ("quota exhausted after retries") and
  `SystemExit` (the engine's own `sys.exit(1)` on "prepaid credits depleted");
- the abort path passes `fix_urls=False`, since URL repair re-asks Gemini and the quota is
  what just died — and `_finalize_run` genuinely skips the backfill, even with
  `AUTO_FIX_URLS=1` set;
- a clean run finalizes once with `fix_urls=True` and does not exit.

Without this, an unattended monthly run that died in its last vertical would leave no CSV
at all despite hours of completed, checkpointed work.

> **Running the suite while a snapshot folder is extracted in the repo root:** pass
> `--ignore=<snapshot-dir>` (e.g. `pytest -q --ignore=TS-Market-Intel-main-30072026`).
> An extracted copy of the repo contains its own `tests/` with identical module names, and
> pytest collects both, failing with `import file mismatch`.
