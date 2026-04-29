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
import re
import sys
import time
from datetime import datetime
from functools import lru_cache

from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, HttpOptions, Tool
from prompts import build_prompt, FIELD_MAPS, CATEGORY_TRIGGERS, DAYS_BACK, _recency_instruction
from accounts import ACCOUNTS
from storage import Sink

# ── Configurable via env vars ─────────────────────────────────────
MODEL          = os.environ.get("GEMINI_MODEL",       "gemini-2.5-flash")
TEMPERATURE    = float(os.environ.get("GEMINI_TEMPERATURE", "0.2"))
SEMAPHORE_SIZE = int(os.environ.get("SEMAPHORE_SIZE", "13"))   # max concurrent in-flight calls per account
MAX_RETRIES    = int(os.environ.get("MAX_RETRIES",    "3"))    # attempts per signal before giving up
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


async def resolve_url(url: str, timeout: int = 6) -> str:
    """Resolve a Gemini grounding redirect URL to its final destination.

    Resolution order:
      1. Parse the 'url' query param directly from the token string — no HTTP
         request needed, works for both open and paywalled sources.
      2. Follow the HTTP redirect chain with a full Chrome User-Agent — works
         for open sources and government/news sites.
      3. If redirect fails (403/timeout), return the original token unchanged.
         Caller is responsible for further fallback (grounding chunks or Google Search).
    """
    if not url or "vertexaisearch" not in url:
        return url

    # ── Step 1: extract encoded destination from the token itself ──────────
    from urllib.parse import urlparse, parse_qs
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "url" in params:
            candidate = params["url"][0]
            if candidate.startswith("http"):
                return candidate
    except Exception:
        pass

    # ── Step 2: follow the HTTP redirect chain ─────────────────────────────
    import urllib.request
    import urllib.error
    import ssl

    def _follow():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            })
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            return resp.url
        except urllib.error.HTTPError as e:
            logger.debug(f"  [resolve_url] HTTPError {e.code} {e.reason} → {url[:80]}")
            return url
        except urllib.error.URLError as e:
            logger.debug(f"  [resolve_url] URLError {e.reason} → {url[:80]}")
            return url
        except Exception as e:
            logger.debug(f"  [resolve_url] {type(e).__name__}: {e} → {url[:80]}")
            return url

    try:
        return await asyncio.wait_for(asyncio.to_thread(_follow), timeout=timeout + 1)
    except Exception:
        return url


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
            if is_retry:
                self.retries += 1
            if outcome == "success":
                self.successes += 1
                self.total_hits += hits
            elif outcome == "timeout":
                self.timeouts += 1
            elif outcome == "empty":
                self.empty += 1
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
    def success_rate(self):
        return (self.successes / self.api_calls * 100) if self.api_calls else 0

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
        logger.info(f"\n  {b}Run Stats{r}")
        logger.info(f"  {'Duration':<28} {self.elapsed}")
        logger.info(f"  {'Success rate':<28} {y}{self.success_rate:.1f}%{r}  {d}({self.successes} / {self.api_calls}){r}")
        if self.retries:
            logger.info(f"  {'Rate-limit retries':<28} {self.retries}")
        if self.timeouts:
            logger.info(f"  {'Timeouts':<28} {self.timeouts}  {d}({self.timeouts/self.api_calls*100:.1f}%){r}")
        if self.errors:
            logger.info(f"  {'Errors':<28} {self.errors}  {d}({self.errors/self.api_calls*100:.1f}%){r}")
        if self.empty:
            logger.info(f"  {'Empty responses':<28} {self.empty}  {d}({self.empty/self.api_calls*100:.1f}%){r}")

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
                             recency_instruction: str = None) -> dict:
    b, h, r, d = C["bold"], C["header"], C["reset"], C["dim"]
    logger.info(f"\n{b}{'═'*60}{r}")
    logger.info(f"{h}{b}  {account}{r}  {d}[{category}]{r}")
    logger.info(f"{b}{'═'*60}{r}")

    result = {
        "account":   account,
        "category":  category,
        "signals":   {},
        "timestamp": datetime.now().isoformat(),
    }

    async def fetch_one(signal: str):
        """Fetch one signal concurrently — retries on 429, skips on other errors."""
        async with sem:
            prompt = build_prompt(signal, account, category, recency_instruction=recency_instruction)
            for attempt in range(1, MAX_RETRIES + 1):
                t_start = time.time()
                logger.info(f"  {C['dim']}→ [{signal}] starting (attempt {attempt})...{C['reset']}")
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
                        logger.warning(f"  {C['yellow']}⚠ [{signal}] Empty response (finish_reason={finish_reason}, attempt {attempt}/{MAX_RETRIES}).{C['reset']}")
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(3)  # brief pause before retry
                            continue  # retry — empty may be transient
                        # All retries exhausted — record and skip
                        if _usage: await _usage.record("empty", signal=signal,
                                                        usage_meta=getattr(response, "usage_metadata", None),
                                                        elapsed=elapsed, hits=0,
                                                        is_retry=(attempt > 1))
                        return signal, []
                    parsed = parse_signals(response.text)
                    # Extract all grounding chunk URIs — used as fallback candidates
                    # when the primary source_url returns 403 or fails to resolve
                    grounding_uris = []
                    try:
                        for chunk in response.candidates[0].grounding_metadata.grounding_chunks:
                            if hasattr(chunk, "web") and chunk.web.uri:
                                grounding_uris.append(chunk.web.uri)
                    except Exception:
                        pass
                    for sig in parsed:
                        sig["_grounding_urls"] = grounding_uris
                    if _usage: await _usage.record("success", signal=signal,
                                                    usage_meta=getattr(response, "usage_metadata", None),
                                                    elapsed=elapsed, hits=len(parsed),
                                                    is_retry=(attempt > 1))
                    return signal, parsed
                except (asyncio.TimeoutError, TimeoutError):
                    elapsed = time.time() - t_start
                    logger.warning(f"  {C['yellow']}⚠ TIMEOUT [{signal}] hard-killed after {elapsed:.1f}s — skipping.{C['reset']}")
                    if _usage: await _usage.record("timeout", signal=signal, elapsed=elapsed)
                    return signal, []
                except Exception as e:
                    elapsed = time.time() - t_start
                    err = str(e)
                    if "RESOURCE_EXHAUSTED" in err and "prepayment" in err:
                        logger.critical(f"  {C['red']}✘ Credits depleted. Top up at aistudio.google.com and re-run.{C['reset']}")
                        raise RuntimeError("API credits depleted — top up at aistudio.google.com and re-run.")
                    elif "429" in err or "RATE" in err.upper():
                        wait = min(5 * (2 ** attempt), 120)  # exponential: 10s, 20s, 40s, capped at 120s
                        logger.warning(f"  {C['yellow']}⚠ Rate limit [{signal}], waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...{C['reset']}")
                        if _usage: await _usage.record("error", signal=signal, elapsed=elapsed, is_retry=True)
                        await asyncio.sleep(wait)
                        continue  # retry the for loop — do NOT fall through to timeout/error handlers
                    elif isinstance(e, (asyncio.TimeoutError, TimeoutError)) or "timeout" in err.lower() or "deadline" in err.lower() or elapsed >= (SIGNAL_HARD_TIMEOUT * 0.95):
                        logger.warning(f"  {C['yellow']}⚠ TIMEOUT [{signal}] after {elapsed:.1f}s — skipping.{C['reset']}")
                        if _usage: await _usage.record("timeout", signal=signal, elapsed=elapsed)
                        return signal, []
                    else:
                        logger.error(f"  {C['red']}ERROR [{signal}] after {elapsed:.1f}s: {e}{C['reset']}")
                        if _usage: await _usage.record("error", signal=signal, elapsed=elapsed)
                        return signal, []
            logger.warning(f"  {C['yellow']}⚠ [{signal}] max retries reached — skipping.{C['reset']}")
            if _usage: await _usage.record("error", signal=signal)
            return signal, []

    # Fire all signals concurrently — wait for all to finish
    logger.info(f"  {d}Searching {len(signals)} signals in parallel...{r}")
    acct_start = time.time()
    pairs = await asyncio.gather(*[fetch_one(s) for s in signals])
    acct_elapsed = time.time() - acct_start
    logger.info(f"  {d}⏱ {account} completed in {acct_elapsed:.1f}s{r}")
    signal_map = dict(pairs)

    # Print in original signal order — deterministic, readable output
    acct_hits = 0
    for signal in signals:
        print_signals(signal, signal_map[signal])
        # Resolve Gemini redirect URLs to real article URLs
        enriched = []
        for sig in signal_map[signal]:
            # Inject Salesforce-ready fields
            sig["account"]           = account
            sig["signal_type"]       = signal
            sig["industry_category"] = category
            sig["run_date"]          = result["timestamp"]
            # Resolve Gemini redirect URLs to real article URLs
            if sig.get("source_url"):
                resolved = await resolve_url(sig["source_url"])
                # If primary token failed, try other grounding chunks for same signal
                if "vertexaisearch" in resolved:
                    primary = sig["source_url"]
                    for fallback in sig.get("_grounding_urls", []):
                        if fallback == primary:
                            continue
                        candidate = await resolve_url(fallback)
                        if "vertexaisearch" not in candidate:
                            resolved = candidate
                            break
                # Last resort: Google Search URL built from account + summary
                if "vertexaisearch" in resolved:
                    import urllib.parse
                    summary_snippet = sig.get("summary", "")[:120].strip()
                    query = f"{account} {summary_snippet}"
                    resolved = "https://www.google.com/search?q=" + urllib.parse.quote(query)
                sig["source_url"] = resolved
            sig.pop("_grounding_urls", None)  # clean up — don't persist to output
            enriched.append(sig)
        result["signals"][signal] = enriched
        acct_hits += len(enriched)

    if _usage:
        await _usage.record_account(account, acct_elapsed, acct_hits)
        if sink and usage_name:
            try:
                await asyncio.to_thread(sink.write, usage_name, _usage.to_dict())
                logger.debug(f"  Usage report updated → {usage_name}")
            except Exception as ue:
                logger.warning(f"  ⚠ Could not write usage file: {ue}")

    # Write this account's results to its own folder: <SAFE_COMPANY>/results.json + results.csv
    if sink:
        account_path = f"{_safe_name(account)}/results.json"
        await asyncio.to_thread(sink.write, account_path, result)
        logger.info(f"  {d}✔ saved → {account_path}{r}")

        # Also write a Salesforce-ready CSV alongside the JSON
        import csv, io
        SF_FIELDS = ["account", "signal_type", "industry_category", "summary",
                     "why_it_matters", "event_date", "source_url", "run_date"]
        rows = []
        for sig_type, sigs in result.get("signals", {}).items():
            for sig in sigs:
                rows.append({f: sig.get(f, "") for f in SF_FIELDS})
        if rows:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=SF_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            csv_path = f"{_safe_name(account)}/results.csv"
            await asyncio.to_thread(sink.write, csv_path, buf.getvalue())
            logger.info(f"  {d}✔ saved → {csv_path}  ({len(rows)} signals){r}")

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
            logger.info(f"  {b}{res['account']}{r}  {d}[{res['category']}]{r}  {y}{count_str}{r}")
        else:
            logger.info(f"  {d}{res['account']} [{res['category']}] — no signals{r}")
    logger.info(f"\n  {b}Total signals: {total_signals}{r} across {len(all_results)} accounts\n")


# ── Main entry point ──────────────────────────────────────────────

def run_category(category: str, sink: Sink, signal_override: str = None,
                 api_key: str = None, limit: int = None,
                 accounts_override: list = None):
    """Run one category end-to-end through `sink`.

    Each account's result lands at `<SAFE_COMPANY>/results.json`. A per-run
    usage sidecar lands at `_usage/<category>.json`. A per-run log file lands
    at `_logs/<category>.log` (LocalSink only).

    accounts_override: if provided, use this list instead of ACCOUNTS[category].
                       Used for single-company and Super80 runs.
    """
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
    resumed_results = []
    pending = []
    for acct in accounts:
        prior = sink.read(f"{_safe_name(acct)}/results.json")
        if prior and isinstance(prior, dict) and prior.get("account"):
            resumed_results.append(prior)
        else:
            pending.append(acct)
    if resumed_results:
        logger.info(f"{C['yellow']}⚡ Resuming — {len(resumed_results)} accounts already done, skipping.{C['reset']}")

    # Apply limit after checkpoint resume so --limit 5 always means 5 new accounts
    if limit and limit > 0:
        pending = pending[:limit]
        logger.info(f"{C['yellow']}⚡ Limit set — running first {len(pending)} pending account(s).{C['reset']}")

    logger.info(f"\n{C['bold']}Thomas Scientific // {category}{C['reset']}")
    logger.info(f"{C['dim']}{len(pending)} accounts | Signals: {', '.join(signals)} | Last {DAYS_BACK} days{C['reset']}")

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
        for account in pending:
            result = await run_account_async(client, account, category, signals,
                                             sink=sink, usage_name=usage_name,
                                             sem=sem, recency_instruction=recency_instr)
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
