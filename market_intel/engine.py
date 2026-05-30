"""
engine.py — Async execution engine for the market-intel pipeline.

Exposes `run_category()`, called from main.py. All persistence is routed
through a `Sink` (see storage.py) so the same code works against the
local filesystem or Azure Blob Storage depending on env config.
"""

import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from functools import lru_cache

from dateutil import parser as _date_parser

import httpx

from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, HttpOptions, Tool
from .prompts import build_prompt, FIELD_MAPS, CATEGORY_TRIGGERS, DAYS_BACK, _recency_instruction
from .accounts import ACCOUNTS, PARENT_ID_MAP
from .storage import Sink

# ── Configurable via env vars ─────────────────────────────────────
MODEL          = os.environ.get("GEMINI_MODEL",       "gemini-2.5-flash")
TEMPERATURE    = float(os.environ.get("GEMINI_TEMPERATURE", "0.2"))
SEMAPHORE_SIZE = int(os.environ.get("SEMAPHORE_SIZE", "5"))   # max concurrent in-flight calls per account
MAX_RETRIES    = int(os.environ.get("MAX_RETRIES",    "3"))    # used by ai_summary path only; fetch_one uses the dual counters below
# fetch_one() retry budgets — three independent counters, each only tripped by its own failure mode.
# A single signal can therefore make up to 8 + 3 + 3 + 1 = 15 HTTP attempts if it alternates failure modes.
MAX_RATE_LIMIT_RETRIES = int(os.environ.get("MAX_RATE_LIMIT_RETRIES", "8"))  # 429 / "RATE" retries
MAX_TIMEOUT_RETRIES    = int(os.environ.get("MAX_TIMEOUT_RETRIES",    "3"))  # asyncio.TimeoutError / "timeout" / "deadline" retries
MAX_EMPTY_RETRIES      = int(os.environ.get("MAX_EMPTY_RETRIES",      "3"))  # empty-response (safety filter, etc.) retries
RATE_LIMIT_SLEEP_CAP   = int(os.environ.get("RATE_LIMIT_SLEEP_CAP",   "60")) # max seconds to sleep between rate-limit retries (cap on exponential and on Retry-After)
API_TIMEOUT_MS      = int(os.environ.get("API_TIMEOUT_MS",      "60000"))  # HTTP connection timeout (ms) — NOT wall-clock
SIGNAL_HARD_TIMEOUT = int(os.environ.get("SIGNAL_HARD_TIMEOUT", "120"))    # asyncio.wait_for wall-clock kill (secs)
# Note: API_TIMEOUT_MS only covers connection establishment; Gemini holds the connection open during
# Google Search grounding. SIGNAL_HARD_TIMEOUT is the real enforced limit via asyncio.wait_for().
# The 0.95× fallback below catches SDK exceptions that arrive just before the hard kill fires.

C = {
    "grant":       "\033[94m",
    "faculty":     "\033[96m",
    "capital":     "\033[92m",
    "contract":    "\033[93m",
    "pipeline":    "\033[95m",
    "expansion":   "\033[92m",
    "partnership": "\033[95m",
    "funding":     "\033[93m",
    "project":     "\033[91m",
    "regulatory":  "\033[31m",
    "hiring":      "\033[36m",
    "tender":      "\033[33m",
    "reset":  "\033[0m",
    "dim":    "\033[90m",
    "bold":   "\033[1m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "header": "\033[97m",
}


# ── Logger setup ──────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class _PlainFormatter(logging.Formatter):
    """Formatter that strips ANSI color codes — for non-TTY stdout (Azure) and file logs."""
    def format(self, record):
        return _ANSI_RE.sub("", super().format(record))


def setup_logger(log_file: str = None) -> logging.Logger:
    """Configure logger. Colors on interactive TTY; plain text in containers and files."""
    logger = logging.getLogger("thomas_intel")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    # Strip ANSI when stdout is not a terminal (docker, Azure Container Apps, CI, redirects)
    if sys.stdout.isatty():
        ch.setFormatter(logging.Formatter("%(message)s"))
    else:
        ch.setFormatter(_PlainFormatter("%(message)s"))
    logger.addHandler(ch)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(_PlainFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(fh)

    return logger


# Module-level logger (replaced per run_category call)
logger = logging.getLogger("thomas_intel")


# ── Helpers ───────────────────────────────────────────────────────

def _safe_name(s: str) -> str:
    """Sanitize a company or category name for use as a folder / blob prefix."""
    return re.sub(r"[^A-Z0-9_]+", "_", s.upper()).strip("_")


# Patterns the google-genai SDK tends to embed in 429 exception strings. The
# canonical one is the RetryInfo detail: '"retryDelay": "37s"'. The other two
# cover header-style and prose-style hints we've seen elsewhere.
_RETRY_AFTER_PATTERNS = (
    re.compile(r'retryDelay["\']?\s*[:=]\s*["\']?(\d+(?:\.\d+)?)s?', re.IGNORECASE),
    re.compile(r'retry[-_ ]?after["\']?\s*[:=]\s*["\']?(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'retry[\s_-]?in\s+(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b', re.IGNORECASE),
)


def _parse_retry_after(err_text: str) -> float | None:
    """Best-effort extract of a Retry-After hint from an exception string.

    Returns the suggested wait in seconds, or None if no hint is present.
    """
    for pat in _RETRY_AFTER_PATTERNS:
        m = pat.search(err_text)
        if m:
            try:
                return float(m.group(1))
            except (TypeError, ValueError):
                continue
    return None


_VERTICAL_API_OVERRIDES: dict[str, str] = {
    # "Clinical / Molecular Diagnostics" naive regex produces "Clinical_Molecular_Diagnostics"
    # but the SF API value is "Clinical_Mol_Dx" — override required.
    "Clinical / Molecular Diagnostics": "Clinical_Mol_Dx",
}


def _vertical_api_name(s: str) -> str:
    """Picklist-safe API name for a vertical — collapses non-alnum to `_`, with overrides."""
    return _VERTICAL_API_OVERRIDES.get(s, re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_"))


def _normalize_account(entry) -> tuple[str, str | None]:
    """Return (name, parent_id) from a plain string account or a CSV-sourced dict."""
    if isinstance(entry, dict):
        return entry["name"], entry.get("parent_id")
    return entry, None


@lru_cache(maxsize=1)
def _resolve_api_key(api_key: str = None) -> str:
    """Resolve the Gemini API key. Priority: CLI arg → env var → Azure Key Vault.

    When AZURE_KEY_VAULT_URL is set, fetches the secret named by
    GEMINI_API_KEY_SECRET_NAME (default: 'gemini-api-key') via DefaultAzureCredential —
    Managed Identity in Azure, `az login` locally.
    """
    if api_key:
        return api_key
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if vault_url:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        secret_name = os.environ.get("GEMINI_API_KEY_SECRET_NAME", "gemini-api-key")
        client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        return client.get_secret(secret_name).value
    logger.error(
        "ERROR: No Gemini API key found. Pass --api-key, set GEMINI_API_KEY, "
        "or set AZURE_KEY_VAULT_URL (with GEMINI_API_KEY_SECRET_NAME if not 'gemini-api-key')."
    )
    sys.exit(1)


def get_client(api_key: str = None):
    return genai.Client(
        api_key=_resolve_api_key(api_key),
        http_options=HttpOptions(api_version="v1alpha", timeout=API_TIMEOUT_MS),
    )


def parse_signals(raw: str) -> list:
    cleaned = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return []


_REDIRECT_HOST = "vertexaisearch.cloud.google.com"

# Placeholder written when no usable source URL survives resolution — keeps the
# CSV/JSON cell self-explanatory instead of blank.
NO_URL_PLACEHOLDER = "no URL provided by Gemini"


def _coerce_url(value) -> str:
    """Normalize a potentially-malformed source_url into a stripped string.

    Handles legacy data where some historical pipeline writes produced
    `source_url: [url1, url2, ...]` (list) or `source_url: null`. Returns the
    first non-empty string element if a list, the value itself if a string,
    or '' for anything else.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for v in value:
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _normalize_event_date(raw, fallback_iso_date: str) -> str:
    """Coerce Gemini's event_date into strict YYYY-MM-DD.

    Returns the fallback (the account's ingest date) whenever the input is
    missing, partial, or unparseable. Guarantees a sortable date string —
    never returns None, never returns free-form text.
    """
    if raw is None:
        return fallback_iso_date
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", "n/a", "unknown"):
        return fallback_iso_date
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass
    try:
        d1 = _date_parser.parse(s, default=datetime(1900, 1, 1), fuzzy=False, dayfirst=False)
        d2 = _date_parser.parse(s, default=datetime(2099, 12, 31), fuzzy=False, dayfirst=False)
        if d1 == d2:
            return d1.strftime("%Y-%m-%d")
    except (ValueError, OverflowError, TypeError):
        pass
    return fallback_iso_date


HTTP_USER_AGENT = "Mozilla/5.0 (compatible; ts-market-intel/1.0)"


def _hit_chunk_indices(summary: str, response_text: str, supports) -> list[int]:
    """Find which grounding-chunk indices were cited in support of a hit's summary.

    Locates the summary inside response_text (fuzzy fallback to first 80 chars),
    then walks `supports`. For any support whose segment overlaps the summary span,
    its `grounding_chunk_indices` are collected. Returns a deduplicated, order-preserved
    list — or `[]` if the summary isn't locatable (caller should fall back to all chunks).
    """
    if not summary or not response_text:
        return []
    s = summary.strip()
    start = response_text.find(s)
    if start == -1 and len(s) > 80:
        start = response_text.find(s[:80])
    if start == -1:
        return []
    end = start + len(s)
    seen: set[int] = set()
    ordered: list[int] = []
    for sup in supports or []:
        seg = getattr(sup, "segment", None)
        if seg is None:
            continue
        seg_start = getattr(seg, "start_index", None)
        seg_end = getattr(seg, "end_index", None)
        if seg_start is None or seg_end is None:
            continue
        if seg_start < end and seg_end > start:
            for idx in getattr(sup, "grounding_chunk_indices", []) or []:
                if idx not in seen:
                    seen.add(idx)
                    ordered.append(idx)
    return ordered


async def _resolve_redirect(url: str, http_client: "httpx.AsyncClient",
                             cache: dict) -> str | None:
    """Follow a Gemini grounding-redirect URL to its final article URL.

    Uses GET (not HEAD — some sites 405 HEAD). Reads the response's final `.url`
    after httpx follows redirects. Returns the final URL if it's no longer a
    redirect; None if resolution failed or still points at the redirect host.
    Cached per call site.
    """
    if not url:
        return None
    if url in cache:
        return cache[url]
    try:
        r = await http_client.get(url)
        final = str(r.url) if r.url else None
        if final and _REDIRECT_HOST not in final:
            cache[url] = final
            return final
    except Exception:
        pass
    cache[url] = None
    return None


async def _resolve_source_url(hit: dict, response_text: str, chunks,
                               supports, http_client: "httpx.AsyncClient",
                               redirect_cache: dict) -> str:
    """Pick the best real article URL for one signal hit.

    Strategy (decided 2026-05-25):
      1. Map the hit's `summary` text span in `response_text` to grounding-support
         segments; collect the union of chunk indices those supports cite.
         If supports can't be matched, fall back to scanning all chunks.
      2. Prefer the first non-redirect URI in the candidate chunk set.
      3. If only redirect URIs survive, HEAD-resolve the first one to its final
         article URL (cached per `redirect_cache`).
      4. If chunks yield nothing usable, fall back to the hit's original JSON URL.
    """
    chunks = list(chunks or [])
    raw_url = _coerce_url(hit.get("source_url"))

    # Step 1: narrow chunks via supports if possible; else use all chunks.
    indices = _hit_chunk_indices(hit.get("summary") or "", response_text, supports)
    if indices:
        candidate_chunks = [chunks[i] for i in indices if 0 <= i < len(chunks)]
    else:
        candidate_chunks = chunks

    # Step 2: prefer non-redirect URIs.
    redirect_chunks: list[str] = []
    for chunk in candidate_chunks:
        try:
            uri = chunk.web.uri
        except AttributeError:
            continue
        if not uri:
            continue
        if _REDIRECT_HOST not in uri:
            return uri
        redirect_chunks.append(uri)

    # Step 3: resolve redirect chunks via HTTP.
    for redirect_url in redirect_chunks:
        resolved = await _resolve_redirect(redirect_url, http_client, redirect_cache)
        if resolved:
            return resolved

    # Step 4: final fallback — Gemini's JSON URL (may be fabricated, but it's all we have).
    # If even that is blank, write a self-explanatory placeholder rather than "".
    return raw_url or NO_URL_PLACEHOLDER


def _has_signals(result: dict) -> bool:
    return any(v for v in result.get("signals", {}).values())


async def _generate_account_summary(client, result: dict) -> str | None:
    """Generate a ~60-word narrative summary of all signal hits for one account.

    Uses the same Gemini model but with no Google Search grounding (pure summarization),
    low temperature, and the same hard timeout. Returns None on any failure — callers
    should treat None as "skip ai_summary for this account."
    """
    lines = []
    for signal_type, hits in result.get("signals", {}).items():
        for hit in hits:
            summary = (hit.get("summary") or "").strip()
            if summary:
                lines.append(f"- [{signal_type}] {summary}")
    if not lines:
        return None

    body = "\n".join(lines)
    prompt = (
        f"You are summarising market intelligence signals for {result.get('account', '')} "
        f"({result.get('account_vertical', '')}). Below are signal summaries detected for "
        f"this account. Write ONE paragraph of approximately 60 words (do not exceed 60) "
        f"describing what is happening at this account and why it matters for a lab supply "
        f"sales team. No preamble, no bullets, no quotes — just the paragraph.\n\n"
        f"Signals:\n{body}"
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MODEL,
                    contents=prompt,
                    config=GenerateContentConfig(temperature=0.1),
                ),
                timeout=SIGNAL_HARD_TIMEOUT,
            )
            if _usage:
                await _usage.record("success", signal="_ai_summary",
                                    usage_meta=getattr(response, "usage_metadata", None),
                                    elapsed=0.0, hits=0,
                                    is_retry=(attempt > 1))
            text = (getattr(response, "text", None) or "").strip()
            return text or None
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(f"  {C['yellow']}⚠ ai_summary timed out after {SIGNAL_HARD_TIMEOUT}s — skipping.{C['reset']}")
            if _usage:
                await _usage.record("timeout", signal="_ai_summary")
            return None
        except Exception as e:
            err = str(e)
            transient = (
                "503" in err or "UNAVAILABLE" in err.upper()
                or "429" in err or "RATE" in err.upper()
            )
            if transient and attempt < MAX_RETRIES:
                wait = min(5 * (2 ** attempt), 120)  # 10s, 20s, 40s, capped at 120s
                logger.warning(
                    f"  {C['yellow']}⚠ ai_summary transient error (attempt {attempt}/{MAX_RETRIES}), "
                    f"retrying in {wait}s: {e}{C['reset']}"
                )
                await asyncio.sleep(wait)
                continue
            logger.warning(f"  {C['yellow']}⚠ ai_summary generation failed: {e}{C['reset']}")
            if _usage:
                await _usage.record("error", signal="_ai_summary",
                                    is_retry=(attempt > 1))
            return None
    return None


def print_signals(signal: str, signals: list):
    col = C.get(signal, "\033[96m")
    r, d, y, b = C["reset"], C["dim"], C["yellow"], C["bold"]
    if not signals:
        logger.info(f"  {d}[{signal}] No signals found.{r}")
        return
    logger.info(f"  {col}{b}[{signal.upper()}] {len(signals)} signal(s){r}")
    for i, s in enumerate(signals, 1):
        logger.info(f"    {b}[{i}] {s.get('summary', '')}{r}")
        meta = " · ".join(str(s[f]) for f in FIELD_MAPS.get(signal, []) if s.get(f))
        if meta:
            logger.info(f"        {d}{meta}{r}")
        if s.get("why_it_matters"):
            logger.info(f"        {y}↳ {s['why_it_matters']}{r}")
        if s.get("source_url"):
            logger.info(f"        {d}{s['source_url']}{r}")


# ── Usage tracker ─────────────────────────────────────────────────

class UsageTracker:
    """Accumulates token, cost, and outcome stats across an entire run."""

    # Gemini 2.5 Flash pricing (free tier: 1,500 grounded requests/day free, then $35/1K)
    INPUT_PRICE_PER_M  = 0.10   # $ per 1M input tokens
    OUTPUT_PRICE_PER_M = 0.40   # $ per 1M output tokens
    GROUNDING_PER_K    = 35.00  # $ per 1K grounding calls (after 1,500 RPD free tier)

    def __init__(self, total_accounts: int = 0):
        self.api_calls      = 0
        self.input_tokens   = 0
        self.output_tokens  = 0
        self.successes      = 0
        self.timeouts       = 0
        self.errors         = 0
        self.empty          = 0
        self.retries        = 0
        self.total_hits     = 0          # intelligence items returned
        self.accounts_done  = 0
        self.accounts_with_data = 0
        self.total_accounts = total_accounts   # for projection
        self.run_start      = time.time()
        self._lock          = asyncio.Lock()

        # Per-signal stats: {signal: {"calls":0, "hits":0, "timeouts":0, "empty":0, "total_time":0.0}}
        self.signal_stats: dict = {}

        # Per-account wall times for slowest-account report: [(account, elapsed)]
        self.account_times: list = []

    async def record(self, outcome: str, signal: str = None,
                     usage_meta=None, elapsed: float = 0.0,
                     hits: int = 0, is_retry: bool = False):
        """Thread-safe update."""
        async with self._lock:
            self.api_calls += 1
            # "retry" is a transient mid-flight attempt (429 / timeout / empty that we
            # retried). It counts toward api_calls (each is a real billed grounding call)
            # and retries, but never toward a terminal outcome — only the final
            # success/error/timeout/empty record per signal does that.
            if is_retry or outcome == "retry":
                self.retries += 1
            if outcome == "success":
                self.successes += 1
                self.total_hits += hits
            elif outcome == "timeout":
                self.timeouts += 1
            elif outcome == "empty":
                self.empty += 1
            elif outcome == "retry":
                pass
            else:
                self.errors += 1

            if usage_meta:
                self.input_tokens  += getattr(usage_meta, "prompt_token_count",     0) or 0
                self.output_tokens += getattr(usage_meta, "candidates_token_count", 0) or 0

            if signal:
                s = self.signal_stats.setdefault(signal, {
                    "calls": 0, "hits": 0, "timeouts": 0,
                    "empty": 0, "errors": 0, "total_time": 0.0
                })
                s["calls"]      += 1
                s["hits"]       += hits
                s["total_time"] += elapsed
                if outcome == "timeout": s["timeouts"] += 1
                if outcome == "empty":   s["empty"]    += 1
                if outcome == "error":   s["errors"]   += 1

    async def record_account(self, account: str, elapsed: float, hits: int):
        async with self._lock:
            self.accounts_done += 1
            self.account_times.append((account, elapsed))
            if hits > 0:
                self.accounts_with_data += 1

    # ── Derived metrics ───────────────────────────────────────────

    @property
    def token_cost(self):
        return (self.input_tokens  / 1_000_000 * self.INPUT_PRICE_PER_M +
                self.output_tokens / 1_000_000 * self.OUTPUT_PRICE_PER_M)

    @property
    def grounding_cost(self):
        return self.api_calls / 1_000 * self.GROUNDING_PER_K

    @property
    def total_cost(self):
        return self.token_cost + self.grounding_cost

    @property
    def resolved(self):
        """Terminal signal outcomes — the honest denominator for rates.

        Excludes transient retries (counted in api_calls/retries). One signal
        resolves to exactly one of success / error / timeout / empty.
        """
        return self.successes + self.errors + self.timeouts + self.empty

    @property
    def success_rate(self):
        return (self.successes / self.resolved * 100) if self.resolved else 0

    @property
    def cost_per_hit(self):
        return (self.total_cost / self.total_hits) if self.total_hits else 0.0

    @property
    def projected_cost(self):
        """Extrapolate total_cost to full account list based on accounts done so far."""
        if not self.accounts_done or not self.total_accounts:
            return 0.0
        return self.total_cost / self.accounts_done * self.total_accounts

    @property
    def elapsed(self):
        secs = int(time.time() - self.run_start)
        return f"{secs // 3600}h {(secs % 3600) // 60}m {secs % 60}s" if secs >= 3600 else f"{secs // 60}m {secs % 60}s"

    # ── Serialisation ─────────────────────────────────────────────

    def to_dict(self):
        slowest = sorted(self.account_times, key=lambda x: x[1], reverse=True)[:5]
        sig_rows = {
            sig: {
                "calls":    s["calls"],
                "hits":     s["hits"],
                "avg_time": round(s["total_time"] / s["calls"], 1) if s["calls"] else 0,
                "timeouts": s["timeouts"],
                "empty":    s["empty"],
                "errors":   s["errors"],
            }
            for sig, s in sorted(self.signal_stats.items(),
                                  key=lambda x: x[1]["total_time"] / max(x[1]["calls"], 1),
                                  reverse=True)
        }
        return {
            "api_calls":          self.api_calls,
            "input_tokens":       self.input_tokens,
            "output_tokens":      self.output_tokens,
            "token_cost":         round(self.token_cost,      4),
            "grounding_cost":     round(self.grounding_cost,  4),
            "total_cost":         round(self.total_cost,      4),
            "projected_cost":     round(self.projected_cost,  4),
            "cost_per_hit":       round(self.cost_per_hit,    4),
            "successes":          self.successes,
            "timeouts":           self.timeouts,
            "errors":             self.errors,
            "empty":              self.empty,
            "retries":            self.retries,
            "resolved":           self.resolved,
            "success_rate":       round(self.success_rate, 1),
            "total_hits":         self.total_hits,
            "accounts_done":      self.accounts_done,
            "accounts_with_data": self.accounts_with_data,
            "run_duration":       self.elapsed,
            "signal_breakdown":   sig_rows,
            "slowest_accounts":   [{"account": a, "elapsed_s": round(e, 1)} for a, e in slowest],
        }

    # ── Console report ────────────────────────────────────────────

    def print_report(self):
        b, r, d, y, g = C["bold"], C["reset"], C["dim"], C["yellow"], C.get("capital", "\033[92m")
        sep  = f"{b}{'═'*60}{r}"
        dash = f"  {d}{'─'*56}{r}"

        logger.info(f"\n{sep}")
        logger.info(f"{b}  USAGE REPORT{r}")
        logger.info(sep)

        # ── Cost breakdown ────────────────────────────────────────
        logger.info(f"  {b}Cost{r}")
        logger.info(f"  {'API / Grounding calls':<28} {self.api_calls:,}   {d}→ ${self.grounding_cost:.4f}{r}")
        logger.info(f"  {'Input tokens':<28} {self.input_tokens:,}   {d}→ ${self.input_tokens/1_000_000*self.INPUT_PRICE_PER_M:.4f}{r}")
        logger.info(f"  {'Output tokens':<28} {self.output_tokens:,}   {d}→ ${self.output_tokens/1_000_000*self.OUTPUT_PRICE_PER_M:.4f}{r}")
        logger.info(dash)
        logger.info(f"  {b}{'Estimated cost':<28} ${self.total_cost:.4f}{r}")
        if self.total_accounts and self.accounts_done:
            logger.info(f"  {d}{'Projected full run':<28} ${self.projected_cost:.4f}  ({self.total_accounts} accounts){r}")
        if self.total_hits:
            logger.info(f"  {d}{'Cost per signal hit':<28} ${self.cost_per_hit:.4f}{r}")

        # ── Run stats ─────────────────────────────────────────────
        # Rates are over `resolved` (terminal signal outcomes), not api_calls.
        # api_calls counts every HTTP attempt incl. retries — kept for cost only.
        denom = self.resolved or 1
        logger.info(f"\n  {b}Run Stats{r}")
        logger.info(f"  {'Duration':<28} {self.elapsed}")
        logger.info(f"  {'Success rate':<28} {y}{self.success_rate:.1f}%{r}  {d}({self.successes} / {self.resolved}){r}")
        logger.info(f"  {'API attempts (incl retries)':<28} {self.api_calls}  {d}(+{self.retries} retries){r}")
        if self.timeouts:
            logger.info(f"  {'Timeouts':<28} {self.timeouts}  {d}({self.timeouts/denom*100:.1f}%){r}")
        if self.errors:
            logger.info(f"  {'Errors':<28} {self.errors}  {d}({self.errors/denom*100:.1f}%){r}")
        if self.empty:
            logger.info(f"  {'Empty responses':<28} {self.empty}  {d}({self.empty/denom*100:.1f}%){r}")

        # ── Data density ──────────────────────────────────────────
        logger.info(f"\n  {b}Data Density{r}")
        logger.info(f"  {'Total signal hits':<28} {self.total_hits:,}")
        if self.accounts_done:
            logger.info(f"  {'Accounts with data':<28} {self.accounts_with_data} / {self.accounts_done}  {d}({self.accounts_with_data/self.accounts_done*100:.0f}%){r}")
            logger.info(f"  {'Avg hits per account':<28} {self.total_hits/self.accounts_done:.1f}")

        # ── Per-signal breakdown ──────────────────────────────────
        if self.signal_stats:
            logger.info(f"\n  {b}Signal Breakdown{r}  {d}(sorted by avg response time){r}")
            logger.info(f"  {d}  {'Signal':<14} {'Calls':>5}  {'AvgTime':>8}  {'Hits':>5}  {'T/O':>4}  {'Err':>4}{r}")
            logger.info(f"  {d}  {'─'*54}{r}")
            for sig, s in sorted(self.signal_stats.items(),
                                  key=lambda x: x[1]["total_time"] / max(x[1]["calls"], 1),
                                  reverse=True):
                avg = s["total_time"] / s["calls"] if s["calls"] else 0
                flag = f" {y}⚠{r}" if s["timeouts"] or s["errors"] else ""
                logger.info(f"  {d}  {sig:<14}{r} {s['calls']:>5}  {avg:>7.1f}s  {s['hits']:>5}  {s['timeouts']:>4}  {s['errors']:>4}{flag}")

        # ── Slowest accounts ──────────────────────────────────────
        if self.account_times:
            slowest = sorted(self.account_times, key=lambda x: x[1], reverse=True)[:5]
            logger.info(f"\n  {b}Slowest Accounts{r}")
            for acct, elapsed in slowest:
                logger.info(f"  {d}  {acct:<32} {elapsed:>6.1f}s{r}")

        logger.info(f"\n{sep}\n")


# Module-level tracker — reset at the start of each run_category call
_usage: UsageTracker = None


# ── Async run_account (parallel signals) ─────────────────────────

async def run_account_async(client, account: str, category: str, signals: list,
                             sink: Sink = None, usage_name: str = None,
                             sem: asyncio.Semaphore = None,
                             recency_instruction: str = None,
                             parent_id: str = None,
                             run_date: str = None) -> dict:
    b, h, r, d = C["bold"], C["header"], C["reset"], C["dim"]
    logger.info(f"\n{b}{'═'*60}{r}")
    logger.info(f"{h}{b}  {account}{r}  {d}[{category}]{r}")
    logger.info(f"{b}{'═'*60}{r}")

    result = {
        "account":          account,
        "account_vertical": _vertical_api_name(category),
        "Parent_ID":        parent_id if parent_id is not None else PARENT_ID_MAP.get(account.upper().strip()),
        "signals":          {},
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    }
    fallback_date = result["timestamp"][:10]  # 'YYYY-MM-DD' slice — used when event_date is missing/unparseable
    redirect_cache: dict[str, str] = {}       # Gemini-redirect -> resolved real URL, per account
    http_client: httpx.AsyncClient | None = None  # bound inside the async-with below; fetch_one reads via closure

    async def fetch_one(signal: str):
        """Fetch one signal concurrently — retries on 429, skips on other errors."""
        async with sem:
            prompt = build_prompt(signal, account, category, recency_instruction=recency_instruction)
            # Dual-counter retry policy. Each failure mode has its own budget and only
            # advances its own counter; the loop exits when a single counter exhausts.
            rate_limit_attempt = 0
            timeout_attempt = 0
            empty_attempt = 0
            total_attempt = 0
            while True:
                total_attempt += 1
                t_start = time.time()
                logger.info(f"  {C['dim']}→ [{signal}] starting (attempt {total_attempt})...{C['reset']}")
                try:
                    response = await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=MODEL,
                            contents=prompt,
                            config=GenerateContentConfig(
                                tools=[Tool(google_search=GoogleSearch())],
                                temperature=TEMPERATURE,
                            ),
                        ),
                        timeout=SIGNAL_HARD_TIMEOUT,  # hard wall-clock kill — fires even if Gemini holds connection open
                    )
                    elapsed = time.time() - t_start
                    logger.info(f"  {C['dim']}✓ [{signal}] done in {elapsed:.1f}s{C['reset']}")
                    if response.text is None:
                        candidates = getattr(response, "candidates", [])
                        finish_reason = candidates[0].finish_reason if candidates else "unknown"
                        empty_attempt += 1
                        if empty_attempt <= MAX_EMPTY_RETRIES:
                            logger.warning(f"  {C['yellow']}⚠ [{signal}] Empty response (finish_reason={finish_reason}, attempt {empty_attempt}/{MAX_EMPTY_RETRIES}).{C['reset']}")
                            if _usage: await _usage.record("retry", signal=signal, elapsed=elapsed)
                            await asyncio.sleep(3)  # brief pause before retry
                            continue  # retry — empty may be transient
                        logger.warning(f"  {C['yellow']}⚠ [{signal}] Empty response (finish_reason={finish_reason}) — max empty-response retries reached, skipping.{C['reset']}")
                        if _usage: await _usage.record("empty", signal=signal,
                                                        usage_meta=getattr(response, "usage_metadata", None),
                                                        elapsed=elapsed, hits=0,
                                                        is_retry=(empty_attempt > 1))
                        return signal, []
                    parsed = parse_signals(response.text)
                    # Extract grounding metadata for source_url recovery
                    try:
                        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
                    except (AttributeError, IndexError, TypeError):
                        chunks = []
                    try:
                        supports = response.candidates[0].grounding_metadata.grounding_supports or []
                    except (AttributeError, IndexError, TypeError):
                        supports = []
                    for hit in parsed:
                        hit["event_date"] = _normalize_event_date(hit.get("event_date"), fallback_date)
                        hit["source_url"] = await _resolve_source_url(
                            hit, response.text or "", chunks, supports,
                            http_client, redirect_cache,
                        )
                    if _usage: await _usage.record("success", signal=signal,
                                                    usage_meta=getattr(response, "usage_metadata", None),
                                                    elapsed=elapsed, hits=len(parsed),
                                                    is_retry=(total_attempt > 1))
                    return signal, parsed
                except (asyncio.TimeoutError, TimeoutError):
                    elapsed = time.time() - t_start
                    timeout_attempt += 1
                    if timeout_attempt <= MAX_TIMEOUT_RETRIES:
                        logger.warning(f"  {C['yellow']}⚠ TIMEOUT [{signal}] hard-killed after {elapsed:.1f}s (attempt {timeout_attempt}/{MAX_TIMEOUT_RETRIES}), retrying in 1s...{C['reset']}")
                        if _usage: await _usage.record("retry", signal=signal, elapsed=elapsed)
                        await asyncio.sleep(1)
                        continue
                    logger.warning(f"  {C['yellow']}⚠ TIMEOUT [{signal}] hard-killed after {elapsed:.1f}s — max timeout retries reached, skipping.{C['reset']}")
                    if _usage: await _usage.record("timeout", signal=signal, elapsed=elapsed)
                    return signal, []
                except Exception as e:
                    elapsed = time.time() - t_start
                    err = str(e)
                    err_lower = err.lower()
                    if "RESOURCE_EXHAUSTED" in err and (
                        "prepayment" in err_lower
                        or "plan and billing" in err_lower
                        or "billing details" in err_lower
                        or "exceeded your current quota" in err_lower
                    ):
                        logger.critical(f"  {C['red']}✘ Gemini quota exhausted (billing/plan limit). Check usage and top up at aistudio.google.com, then re-run.{C['reset']}")
                        raise RuntimeError("Gemini quota exhausted — check plan and billing at aistudio.google.com and re-run.")
                    elif "429" in err or "RATE" in err.upper():
                        rate_limit_attempt += 1
                        if rate_limit_attempt <= MAX_RATE_LIMIT_RETRIES:
                            retry_after = _parse_retry_after(err)
                            if retry_after is not None:
                                wait = min(retry_after, RATE_LIMIT_SLEEP_CAP)
                                wait_source = f"Retry-After {retry_after:.0f}s"
                            else:
                                base = min(5 * (2 ** rate_limit_attempt), RATE_LIMIT_SLEEP_CAP)
                                wait = base * random.uniform(0.75, 1.25)
                                wait_source = "exponential"
                            logger.warning(f"  {C['yellow']}⚠ Rate limit [{signal}], waiting {wait:.1f}s ({wait_source}, attempt {rate_limit_attempt}/{MAX_RATE_LIMIT_RETRIES})...{C['reset']}")
                            if _usage: await _usage.record("retry", signal=signal, elapsed=elapsed)
                            await asyncio.sleep(wait)
                            continue
                        logger.warning(f"  {C['yellow']}⚠ [{signal}] max rate-limit retries reached — skipping.{C['reset']}")
                        if _usage: await _usage.record("error", signal=signal, elapsed=elapsed)
                        return signal, []
                    elif "timeout" in err_lower or "deadline" in err_lower or elapsed >= (SIGNAL_HARD_TIMEOUT * 0.95):
                        timeout_attempt += 1
                        if timeout_attempt <= MAX_TIMEOUT_RETRIES:
                            logger.warning(f"  {C['yellow']}⚠ TIMEOUT [{signal}] after {elapsed:.1f}s (attempt {timeout_attempt}/{MAX_TIMEOUT_RETRIES}), retrying in 1s...{C['reset']}")
                            if _usage: await _usage.record("retry", signal=signal, elapsed=elapsed)
                            await asyncio.sleep(1)
                            continue
                        logger.warning(f"  {C['yellow']}⚠ TIMEOUT [{signal}] after {elapsed:.1f}s — max timeout retries reached, skipping.{C['reset']}")
                        if _usage: await _usage.record("timeout", signal=signal, elapsed=elapsed)
                        return signal, []
                    else:
                        logger.error(f"  {C['red']}ERROR [{signal}] after {elapsed:.1f}s: {e}{C['reset']}")
                        if _usage: await _usage.record("error", signal=signal, elapsed=elapsed)
                        return signal, []

    # Fire all signals concurrently — wait for all to finish.
    # The httpx client is open for the lifetime of the gather, used by
    # `_resolve_source_url` to follow Gemini redirect URLs to real article URLs.
    logger.info(f"  {d}Searching {len(signals)} signals in parallel...{r}")
    acct_start = time.time()
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(10.0),
        headers={"User-Agent": HTTP_USER_AGENT},
    ) as _client:
        http_client = _client
        pairs = await asyncio.gather(*[fetch_one(s) for s in signals])
    acct_elapsed = time.time() - acct_start
    logger.info(f"  {d}⏱ {account} completed in {acct_elapsed:.1f}s{r}")
    signal_map = dict(pairs)

    # Print in original signal order — deterministic, readable output
    acct_hits = 0
    for signal in signals:
        print_signals(signal, signal_map[signal])
        result["signals"][signal] = signal_map[signal]
        acct_hits += len(signal_map[signal])

    # Generate a 60-word narrative summary across all signals for this account.
    # No grounding — pure summarization. Skip when the account has no hits.
    result["ai_summary"] = await _generate_account_summary(client, result) if _has_signals(result) else None

    if _usage:
        await _usage.record_account(account, acct_elapsed, acct_hits)
        if sink and usage_name:
            try:
                await asyncio.to_thread(sink.write, usage_name, _usage.to_dict())
                logger.debug(f"  Usage report updated → {usage_name}")
            except Exception as ue:
                logger.warning(f"  ⚠ Could not write usage file: {ue}")

    # Write this account's results to its own folder: <SAFE_COMPANY>/results_YYYY-MM-DD.json
    if sink:
        account_path = f"{_safe_name(account)}/results_{run_date}.json"
        await asyncio.to_thread(sink.write, account_path, result)
        logger.info(f"  {d}✔ saved → {account_path}{r}")

    return result


# ── Summary printer ───────────────────────────────────────────────

def print_summary(all_results: list):
    b, r, d, y = C["bold"], C["reset"], C["dim"], C["yellow"]
    logger.info(f"\n{b}{'═'*60}{r}")
    logger.info(f"{b}  SUMMARY{r}")
    logger.info(f"{b}{'═'*60}{r}")
    total_signals = 0
    for res in all_results:
        counts = {s: len(v) for s, v in res["signals"].items()}
        total = sum(counts.values())
        total_signals += total
        if total > 0:
            count_str = "  ".join(f"{s}:{n}" for s, n in counts.items() if n > 0)
            vertical = res.get('account_vertical') or res.get('category', '')
            logger.info(f"  {b}{res['account']}{r}  {d}[{vertical}]{r}  {y}{count_str}{r}")
        else:
            vertical = res.get('account_vertical') or res.get('category', '')
            logger.info(f"  {d}{res['account']} [{vertical}] — no signals{r}")
    logger.info(f"\n  {b}Total signals: {total_signals}{r} across {len(all_results)} accounts\n")


# ── Main entry point ──────────────────────────────────────────────

def run_category(category: str, sink: Sink, signal_override: str = None,
                 api_key: str = None, limit: int = None,
                 accounts_override: list = None, run_date: str = None):
    """Run one category end-to-end through `sink`.

    Each account's result lands at `<SAFE_COMPANY>/results.json`. A per-run
    usage sidecar lands at `_usage/<category>.json`. A per-run log file lands
    at `_logs/<category>.log` (LocalSink only).

    accounts_override: if provided, use this list instead of ACCOUNTS[category].
                       Used for single-company and Super80 runs.
    run_date: UTC YYYY-MM-DD stamp for result filenames. Defaults to today (UTC)
              when not supplied. main() computes it once so every category of a
              --category all run shares a single date.
    """
    run_date = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)  # .env values take precedence over shell env vars
    except ImportError:
        pass

    cat_slug = _safe_name(category).lower()
    usage_name = f"_usage/{cat_slug}.json"
    log_name = f"_logs/{cat_slug}.log"

    # Only create a local log file when the sink supports it (LocalSink).
    # BlobSink relies on stdout — Azure Container App Logs captures it.
    global logger
    log_file = sink.log_path(log_name) if sink.supports_log_files else None
    logger = setup_logger(log_file)

    client = get_client(api_key)
    signals = [signal_override] if signal_override else CATEGORY_TRIGGERS.get(category, [])

    if not signals:
        logger.error(f"{C['red']}ERROR: No signals defined for category '{category}'. Check CATEGORY_TRIGGERS in prompts.py.{C['reset']}")
        sys.exit(1)

    # Validate all signal names are known before starting any API calls
    unknown = [s for s in signals if s not in FIELD_MAPS]
    if unknown:
        logger.error(f"{C['red']}ERROR: Unknown signal(s): {unknown}. Check FIELD_MAPS in prompts.py.{C['reset']}")
        sys.exit(1)

    # Use override list if provided, otherwise look up from ACCOUNTS
    if accounts_override is not None:
        accounts = accounts_override
    else:
        if category not in ACCOUNTS:
            logger.error(f"{C['red']}ERROR: Category '{category}' not found in accounts.py.{C['reset']}")
            sys.exit(1)
        accounts = ACCOUNTS[category]

    # Resume from checkpoint — each completed account has its own <SAFE_COMPANY>/results.json
    # accounts entries may be plain strings (legacy) or dicts {"name": ..., "parent_id": ...} (CSV/DB)
    resumed_results = []
    pending = []  # list of (name, parent_id)
    for entry in accounts:
        acct, pid = _normalize_account(entry)
        prior = sink.read(f"{_safe_name(acct)}/results_{run_date}.json")
        if prior and isinstance(prior, dict) and prior.get("account"):
            # Migrate old-schema blobs that used "category" instead of "account_vertical"
            if "category" in prior and "account_vertical" not in prior:
                prior["account_vertical"] = prior.pop("category")
            resumed_results.append(prior)
        else:
            pending.append((acct, pid))
    if resumed_results:
        logger.info(f"{C['yellow']}⚡ Resuming — {len(resumed_results)} accounts already done, skipping.{C['reset']}")

    # Apply limit after checkpoint resume so --limit 5 always means 5 new accounts
    if limit and limit > 0:
        pending = pending[:limit]
        logger.info(f"{C['yellow']}⚡ Limit set — running first {len(pending)} pending account(s).{C['reset']}")

    # Use account name for logging total count
    pending_names = [name for name, _ in pending]

    logger.info(f"\n{C['bold']}Thomas Scientific // {category}{C['reset']}")
    logger.info(f"{C['dim']}{len(pending_names)} accounts | Signals: {', '.join(signals)} | Last {DAYS_BACK} days{C['reset']}")

    # Compute recency instruction once for the entire run — not per signal
    recency_instr = _recency_instruction()

    # Initialise a fresh usage tracker for this run
    # Seed with already-completed accounts so resumed runs show accurate totals
    global _usage
    _usage = UsageTracker(total_accounts=len(accounts))
    for r in resumed_results:
        acct_hits = sum(len(v) for v in r.get("signals", {}).values())
        _usage.accounts_done += 1
        if acct_hits > 0:
            _usage.accounts_with_data += 1
        _usage.account_times.append((r["account"], 0.0))  # elapsed unknown for resumed accounts

    # Run all accounts sequentially, signals in parallel per account.
    # Each account's result is persisted inside run_account_async.
    fresh_results = []

    async def _run_all():
        sem = asyncio.Semaphore(SEMAPHORE_SIZE)
        for account, pid in pending:
            result = await run_account_async(client, account, category, signals,
                                             sink=sink, usage_name=usage_name,
                                             sem=sem, recency_instruction=recency_instr,
                                             parent_id=pid, run_date=run_date)
            fresh_results.append(result)

    try:
        asyncio.run(_run_all())
    except RuntimeError as e:
        if "credits depleted" in str(e).lower():
            logger.critical(str(e))
            sys.exit(1)
        raise
    # Allow aiohttp connector to close cleanly
    time.sleep(0.5)

    all_results = resumed_results + fresh_results
    print_summary(all_results)
    _usage.print_report()

    # Final usage sidecar write
    sink.write(usage_name, _usage.to_dict())
    logger.info(f"\033[90mUsage report saved to {usage_name}\033[0m")
    if log_file:
        logger.info(f"\033[90mLog saved to {log_file}\033[0m\n")
    return len(fresh_results)
