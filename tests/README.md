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
