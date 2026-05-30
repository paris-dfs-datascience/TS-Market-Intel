# `tools/` — one-off maintenance scripts

Operational tools that mutate or analyze already-written results. They are not part
of the normal run path — invoke them through `main.py` flags, or directly as a
module from the repo root (`python -m tools.<name>`). They import the core package
absolutely (`from market_intel.engine import …`).

## `backfill_results.py` — re-process existing result JSONs

Two idempotent passes over `<COMPANY>/results_<DATE>.json` files in the sink:

- **`run_backfill(sink, date_str, api_key=None)`** (`--backfill <DATE|all>`) —
  normalizes every signal hit's `event_date` to strict `YYYY-MM-DD`, and generates
  `ai_summary` for any account that has signal hits but no summary yet. Files that
  already contain `ai_summary` are skipped.
- **`run_url_backfill(sink, date_str, api_key=None)`** (`--fix-urls <DATE|all>`) —
  HEAD-validates every `source_url`; for any URL that 4xx's or fails to load,
  re-asks Gemini (with grounding) for the canonical URL and writes it back. Files
  tagged `urls_fixed` (version `_URLS_FIXED_VERSION`) are skipped.

```bash
python main.py --backfill 2026-05-14        # via the CLI
python main.py --backfill all
python -m tools.backfill_results 2026-05-14 # standalone, for testing
```

## `analyze_dedup.py` — duplicate-signal analysis on an export CSV

`run(date_str)` (`--analyze-dedup <DATE>`) reads
`_export/market_intel_export_<DATE>.csv` and detects near-duplicate signal hits
using two passes:

1. **Lexical** — Jaccard token overlap (`JACCARD_AUTO` / `JACCARD_TAG`).
2. **Semantic** — cosine similarity over Gemini embeddings
   (`EMBED_MODEL = gemini-embedding-001`, batched), with `COSINE_AUTO` /
   `COSINE_TAG` thresholds.

A union-find groups matches into clusters; high-confidence pairs are auto-rolled-up
(`rollup_4a`), borderline pairs are tagged for human review (`build_4b_csv`). It
writes `dedup_4a_*`, `dedup_4b_*`, and `dedup_analysis_*` artifacts back to
`_export/` and exits without touching the engine.

```bash
python main.py --analyze-dedup 2026-05-19
python -m tools.analyze_dedup 2026-05-19
```
