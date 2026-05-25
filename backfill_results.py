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

import httpx

from engine import (
    HTTP_USER_AGENT,
    MODEL,
    SEMAPHORE_SIZE,
    SIGNAL_HARD_TIMEOUT,
    _REDIRECT_HOST,
    _coerce_url,
    _generate_account_summary,
    _has_signals,
    _normalize_event_date,
    _resolve_source_url,
    get_client,
)
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

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


# ── URL backfill (v2 source_url fix on historical files) ──────────────────

# Bumped to 2 in hotfix v2.2 because the previous _check incorrectly accepted
# `vertexaisearch.cloud.google.com` redirect URLs as alive without HTTP-validating
# them. All 480 files written under marker `True` need to be re-processed under
# the new logic that resolves + HEAD-checks redirects. Bump again on any future
# correctness fix that changes what URLs the script considers "fixed".
_URLS_FIXED_VERSION = 2


async def _is_url_alive(url: str, http_client: "httpx.AsyncClient") -> bool:
    """True iff fetching `url` resolves to a 2xx response. Treats any exception or
    final non-2xx (after redirects) as dead. Uses GET with follow_redirects so that
    sites serving 405 on HEAD or 301-chaining marketing redirects are handled.
    """
    if not url:
        return False
    try:
        r = await http_client.get(url)
        return 200 <= r.status_code < 300
    except Exception:
        return False


async def _reask_gemini_for_url(client, account: str, signal_type: str, hit: dict,
                                 http_client: "httpx.AsyncClient",
                                 redirect_cache: dict) -> str:
    """Ask Gemini, with Google Search grounding, for the canonical URL for one hit.

    Returns the resolved URL (real article, not a redirect) or "" on failure /
    if Gemini answers UNKNOWN. Reuses `_resolve_source_url` so chunk-extraction +
    redirect-resolution logic stays in one place.
    """
    prompt = (
        f"Find the canonical source URL for this market intelligence event.\n"
        f"Account: {account}\n"
        f"Signal type: {signal_type}\n"
        f"Event date: {hit.get('event_date', 'unknown')}\n"
        f"Summary: {hit.get('summary', '')}\n"
        f"Why it matters: {hit.get('why_it_matters', '')}\n\n"
        f"Use Google Search to locate the original published article or press "
        f"release that reported this event. Return ONLY the URL on one line — "
        f"no JSON, no markdown, no commentary. If you cannot find the article, "
        f"return the single word UNKNOWN."
    )
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=GenerateContentConfig(
                    tools=[Tool(google_search=GoogleSearch())],
                    temperature=0.1,
                ),
            ),
            timeout=SIGNAL_HARD_TIMEOUT,
        )
    except Exception:
        return ""

    text = (getattr(response, "text", "") or "").strip()
    if text.upper().startswith("UNKNOWN"):
        text = ""

    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
    except (AttributeError, IndexError, TypeError):
        chunks = []
    try:
        supports = response.candidates[0].grounding_metadata.grounding_supports or []
    except (AttributeError, IndexError, TypeError):
        supports = []

    # Reuse the v2 resolver. Treat Gemini's plain-text answer as the "hit.source_url"
    # so the chunks-always-win logic still gets first shot.
    pseudo_hit = {"summary": hit.get("summary", ""), "source_url": text}
    return await _resolve_source_url(
        pseudo_hit, text, chunks, supports, http_client, redirect_cache,
    )


async def _fix_urls_one(sink: "Sink", key: str, client,
                        http_client: "httpx.AsyncClient",
                        sem: asyncio.Semaphore,
                        redirect_cache: dict, stats: dict) -> None:
    """HEAD-validate every source_url in one result file; re-ask Gemini on 404s."""
    async with sem:
        result = await asyncio.to_thread(sink.read, key)
        if not isinstance(result, dict) or "signals" not in result:
            stats["skipped_invalid"] += 1
            return
        if result.get("urls_fixed") == _URLS_FIXED_VERSION:
            stats["skipped_already_done"] += 1
            return

        stats["files"] += 1
        account = result.get("account", "")
        all_hits: list[tuple[str, dict]] = []  # (signal_type, hit)
        for signal_type, hits in result.get("signals", {}).items():
            if not isinstance(hits, list):
                continue
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                url = _coerce_url(hit.get("source_url"))
                if not url:
                    continue
                # Migrate legacy list-shaped source_url to a single string
                # in-place so the writeback at the end of the function captures
                # the cleanup (no future code needs to keep tolerating lists).
                if hit.get("source_url") != url:
                    hit["source_url"] = url
                all_hits.append((signal_type, hit))

        # Phase 1: HEAD-validate all URLs concurrently. For Gemini-redirect URLs,
        # resolve to the underlying article URL first (those tokens are short-lived
        # — a 6-day-old redirect may already 404 even though the resolver returned
        # it at write time). If resolution succeeds, replace the hit's source_url
        # with the stable real URL in-place, then HEAD-check it. If resolution
        # fails OR the resolved URL doesn't 2xx, the URL is dead → Phase 2 re-asks
        # Gemini for the canonical article URL.
        async def _check(hit: dict) -> bool:
            url = _coerce_url(hit.get("source_url"))
            if not url:
                return False
            if _REDIRECT_HOST in url:
                resolved = await _resolve_redirect(url, http_client, redirect_cache)
                if not resolved:
                    return False
                hit["source_url"] = resolved
                return await _is_url_alive(resolved, http_client)
            return await _is_url_alive(url, http_client)

        statuses = await asyncio.gather(*[_check(h) for _st, h in all_hits])
        stats["checked"] += len(all_hits)

        # Phase 2: for dead URLs, re-ask Gemini for the real URL.
        for (signal_type, hit), alive in zip(all_hits, statuses):
            if alive:
                stats["alive"] += 1
                continue
            new_url = await _reask_gemini_for_url(
                client, account, signal_type, hit, http_client, redirect_cache,
            )
            if new_url and await _is_url_alive(new_url, http_client):
                hit["source_url"] = new_url
                stats["dead_fixed"] += 1
            else:
                hit["source_url"] = ""
                stats["dead_nulled"] += 1

        result["urls_fixed"] = _URLS_FIXED_VERSION
        await asyncio.to_thread(sink.write, key, result)


def run_url_backfill(sink: "Sink", date_str: str, api_key: str | None = None) -> None:
    """HEAD-validate every source_url in `results_<date_str>.json` files; for any
    that 4xx, re-ask Gemini (with grounding) for the canonical URL and write back.

    Idempotent — files with `urls_fixed: true` are skipped.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    client = get_client(api_key)

    keys = [k for k in sink.list("") if _matches_date(k, date_str)]
    if not keys:
        print(f"No matching results files found for date='{date_str}'. Nothing to fix.")
        return

    print(f"Fixing URLs in {len(keys)} result file(s) for date='{date_str}' "
          f"with concurrency={SEMAPHORE_SIZE}...")

    stats = {
        "files":                0,
        "checked":              0,
        "alive":                0,
        "dead_fixed":           0,
        "dead_nulled":          0,
        "skipped_already_done": 0,
        "skipped_invalid":      0,
    }

    async def _run() -> None:
        sem = asyncio.Semaphore(SEMAPHORE_SIZE)
        redirect_cache: dict[str, str] = {}
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": HTTP_USER_AGENT},
        ) as http_client:
            await asyncio.gather(*[
                _fix_urls_one(sink, k, client, http_client, sem, redirect_cache, stats)
                for k in keys
            ])

    asyncio.run(_run())

    print(
        f"URL backfill complete. "
        f"files={stats['files']} "
        f"checked={stats['checked']} "
        f"alive={stats['alive']} "
        f"dead_fixed={stats['dead_fixed']} "
        f"dead_nulled={stats['dead_nulled']} "
        f"skipped_already_done={stats['skipped_already_done']} "
        f"skipped_invalid={stats['skipped_invalid']}"
    )


if __name__ == "__main__":
    from storage import get_sink
    if len(sys.argv) != 2:
        print("Usage: python backfill_results.py <YYYY-MM-DD|all>")
        sys.exit(1)
    run_backfill(get_sink(), sys.argv[1], api_key=os.environ.get("GEMINI_API_KEY"))
