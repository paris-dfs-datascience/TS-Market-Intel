"""
backfill_results.py — One-time re-processor for existing `<COMPANY>/results_<DATE>.json`
files in the configured sink.

Applies these mutations to each result file:
  - normalize every signal hit's `event_date` to strict YYYY-MM-DD (falls back to the
    account's ingest date if Gemini's original date was unparseable)
  - generate `ai_summary` (60-word narrative) for any account that has at least one
    signal hit but no ai_summary yet

Source URLs: for files written before the live pipeline started extracting grounding
chunks, redirect URLs cannot be recovered retroactively (grounding metadata was never
saved). They are left as-is — the live pipeline handles new writes correctly.

Idempotent: files that already contain `ai_summary` are skipped entirely. Re-runs are
safe and free of side effects beyond the initial pass.

Usage:
    python main.py --backfill 2026-05-14      # single date
    python main.py --backfill all             # every results_*.json in the sink

Run standalone (outside main.py) for testing:
    python backfill_results.py 2026-05-14
    python backfill_results.py all
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import TYPE_CHECKING

from engine import (
    SEMAPHORE_SIZE,
    _generate_account_summary,
    _has_signals,
    _normalize_event_date,
    get_client,
)

if TYPE_CHECKING:
    from storage import Sink


_RESULTS_RE = re.compile(r"results_(\d{4}-\d{2}-\d{2})\.json$")
_SIDECAR_PREFIXES = ("_usage/", "_logs/", "_export/")


def _matches_date(key: str, date_str: str) -> bool:
    """True if `key` ends with results_<date>.json (or any date when date_str=='all')."""
    if key.startswith(_SIDECAR_PREFIXES):
        return False
    m = _RESULTS_RE.search(key)
    if not m:
        return False
    if date_str == "all":
        return True
    return m.group(1) == date_str


async def _backfill_one(sink: "Sink", key: str, client, sem: asyncio.Semaphore,
                        stats: dict) -> None:
    """Read, normalize, summarize, write — for a single results JSON."""
    async with sem:
        result = await asyncio.to_thread(sink.read, key)
        if not isinstance(result, dict) or "signals" not in result:
            stats["skipped_invalid"] += 1
            return

        # Idempotency: if ai_summary already set, skip entirely.
        if result.get("ai_summary"):
            stats["skipped_already_done"] += 1
            return

        fallback_date = (result.get("timestamp") or "")[:10] or "1970-01-01"

        # Normalize event_date on every hit. No grounding chunks available for old files
        # → leave source_url as-is (cannot recover real URL retroactively).
        changed = False
        for _signal_type, hits in result.get("signals", {}).items():
            for hit in hits:
                old = hit.get("event_date")
                new = _normalize_event_date(old, fallback_date)
                if new != old:
                    hit["event_date"] = new
                    changed = True

        # Generate ai_summary if and only if the account has at least one signal hit.
        if _has_signals(result):
            summary = await _generate_account_summary(client, result)
            if summary:
                result["ai_summary"] = summary
                changed = True
                stats["summaries_generated"] += 1
        else:
            # Mark no-signal accounts as processed so re-runs skip them quickly.
            result["ai_summary"] = None

        if changed or "ai_summary" in result:
            await asyncio.to_thread(sink.write, key, result)
            stats["written"] += 1
        else:
            stats["unchanged"] += 1


def run_backfill(sink: "Sink", date_str: str, api_key: str | None = None) -> None:
    """Backfill every `results_<date_str>.json` in `sink`, or every dated result when
    `date_str == 'all'`.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    client = get_client(api_key)

    keys = [k for k in sink.list("") if _matches_date(k, date_str)]
    if not keys:
        print(f"No matching results files found for date='{date_str}'. Nothing to backfill.")
        return

    print(f"Backfilling {len(keys)} result file(s) for date='{date_str}' "
          f"with concurrency={SEMAPHORE_SIZE}...")

    stats = {
        "written": 0,
        "summaries_generated": 0,
        "unchanged": 0,
        "skipped_already_done": 0,
        "skipped_invalid": 0,
    }

    async def _run() -> None:
        sem = asyncio.Semaphore(SEMAPHORE_SIZE)
        await asyncio.gather(*[_backfill_one(sink, k, client, sem, stats) for k in keys])

    asyncio.run(_run())

    print(
        f"Backfill complete. "
        f"written={stats['written']} "
        f"summaries={stats['summaries_generated']} "
        f"unchanged={stats['unchanged']} "
        f"skipped_already_done={stats['skipped_already_done']} "
        f"skipped_invalid={stats['skipped_invalid']}"
    )


if __name__ == "__main__":
    from storage import get_sink
    if len(sys.argv) != 2:
        print("Usage: python backfill_results.py <YYYY-MM-DD|all>")
        sys.exit(1)
    run_backfill(get_sink(), sys.argv[1], api_key=os.environ.get("GEMINI_API_KEY"))
