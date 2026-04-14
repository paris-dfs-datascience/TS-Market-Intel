"""
runner.py — Shared core logic for all category run scripts.
Imported by run_biopharma.py, run_education.py, etc.
Do not run directly.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, HttpOptions, Tool
from prompts import build_prompt, FIELD_MAPS, CATEGORY_TRIGGERS, DAYS_BACK, _recency_instruction
from accounts import ACCOUNTS, get_category

# ── Configurable via env vars ─────────────────────────────────────
# Override any of these in your shell or .env file:
#   export GEMINI_MODEL=gemini-2.5-flash
#   export CALL_DELAY=2          (paid plan with higher RPM)
#   export GEMINI_TEMPERATURE=0.3
#   export SEMAPHORE_SIZE=13     (max concurrent API calls per account)
#   export MAX_RETRIES=3         (attempts per signal before giving up)
#   export SAVE_FREQUENCY=10     (save checkpoint every N accounts)
#   export API_TIMEOUT_MS=60000  (Gemini API timeout in milliseconds)
MODEL          = os.environ.get("GEMINI_MODEL",       "gemini-flash-latest")
CALL_DELAY     = int(os.environ.get("CALL_DELAY",     "6"))    # secs between calls; 6 = ~10 RPM free tier
TEMPERATURE    = float(os.environ.get("GEMINI_TEMPERATURE", "0.2"))
SEMAPHORE_SIZE = int(os.environ.get("SEMAPHORE_SIZE", "13"))   # covers BioPharma's 13 signals max
MAX_RETRIES    = int(os.environ.get("MAX_RETRIES",    "3"))    # attempts per signal before giving up
SAVE_FREQUENCY = int(os.environ.get("SAVE_FREQUENCY", "1"))   # checkpoint every N accounts (default: every account)
API_TIMEOUT_MS = int(os.environ.get("API_TIMEOUT_MS", "60000"))  # Gemini API timeout (ms)

SIGNAL_COLORS = {
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
}
C = {
    **SIGNAL_COLORS,
    "reset":  "\033[0m",
    "dim":    "\033[90m",
    "bold":   "\033[1m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "header": "\033[97m",
}


# ── Logger setup ──────────────────────────────────────────────────

def setup_logger(log_file: str = None) -> logging.Logger:
    """Configure logger with color console + optional plain file handler."""
    logger = logging.getLogger("thomas_intel")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # Console handler — keeps ANSI colors intact
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    # File handler — plain text, no ANSI codes, with timestamps
    if log_file:
        # Strip ANSI codes for file output
        class PlainFormatter(logging.Formatter):
            _ansi = re.compile(r"\033\[[0-9;]*m")
            def format(self, record):
                record.msg = self._ansi.sub("", str(record.msg))
                return super().format(record)

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(PlainFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(fh)

    return logger


# Module-level logger (replaced per run_category call)
logger = logging.getLogger("thomas_intel")


# ── Helpers ───────────────────────────────────────────────────────

def get_client(api_key: str = None):
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.error("ERROR: Add GEMINI_API_KEY=your_key to your .env file.")
        sys.exit(1)
    return genai.Client(api_key=key, http_options=HttpOptions(api_version="v1alpha", timeout=API_TIMEOUT_MS))


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


def save_incremental(all_results: list, output_file: str):
    """Atomic write: dump to a temp file then rename to avoid corruption on crash."""
    tmp = output_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(all_results, f, indent=2)
    os.replace(tmp, output_file)  # atomic on POSIX; safe on Windows too


def load_checkpoint(output_file: str) -> list:
    if output_file and os.path.exists(output_file):
        try:
            with open(output_file) as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, IOError):
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

    # Gemini 2.0 Flash pricing
    INPUT_PRICE_PER_M  = 0.10   # $ per 1M input tokens
    OUTPUT_PRICE_PER_M = 0.40   # $ per 1M output tokens
    GROUNDING_PER_K    = 35.00  # $ per 1K grounding (Google Search) calls

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


# ── Sync run_account (kept for backward compatibility) ────────────

def run_account(client, account: str, category: str, signals: list,
                output_file: str = None, all_results: list = None,
                retry: int = 3, retry_delay: int = 10) -> dict:
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

    for signal in signals:
        logger.info(f"  {d}Searching [{signal}]...{r}")
        found = []
        for attempt in range(1, retry + 1):
            try:
                prompt = build_prompt(signal, account, category)
                response = client.models.generate_content(
                    model=MODEL,
                    contents=prompt,
                    config=GenerateContentConfig(
                        tools=[Tool(google_search=GoogleSearch())],
                        temperature=TEMPERATURE,
                    ),
                )
                if response.text is None:
                    candidates = getattr(response, "candidates", [])
                    finish_reason = candidates[0].finish_reason if candidates else "unknown"
                    parts = candidates[0].content.parts if candidates and candidates[0].content else []
                    part_types = [type(p).__name__ for p in parts] if parts else []
                    logger.warning(f"  {C['yellow']}⚠ [{signal}] Empty response (finish_reason={finish_reason}, parts={part_types}). Skipping.{C['reset']}")
                    break
                found = parse_signals(response.text)
                time.sleep(CALL_DELAY)
                break
            except Exception as e:
                err = str(e)
                if "RESOURCE_EXHAUSTED" in err and "prepayment" in err:
                    logger.critical(f"  {C['red']}✘ Credits depleted. Top up at aistudio.google.com and re-run.{C['reset']}")
                    result["signals"][signal] = []
                    if output_file and all_results is not None:
                        all_results.append(result)
                        save_incremental(all_results, output_file)
                        logger.info(f"{d}  Progress saved to {output_file}{r}")
                    sys.exit(1)
                elif "429" in err or "RATE" in err.upper():
                    wait = min(5 * (2 ** attempt), 120)  # exponential: 10s, 20s, 40s, capped at 120s
                    logger.warning(f"  {C['yellow']}⚠ Rate limit [{signal}], waiting {wait}s (attempt {attempt}/{retry})...{C['reset']}")
                    time.sleep(wait)
                else:
                    logger.error(f"  {C['red']}ERROR [{signal}]: {e}{C['reset']}")
                    break

        print_signals(signal, found)
        result["signals"][signal] = found

    if output_file and all_results is not None:
        all_results.append(result)
        if len(all_results) % SAVE_FREQUENCY == 0 or len(all_results) == 1:
            save_incremental(all_results, output_file)
            logger.info(f"  {d}✔ saved → {output_file} ({len(all_results)} accounts){r}")

    return result


# ── Async run_account (parallel signals) ─────────────────────────

async def run_account_async(client, account: str, category: str, signals: list,
                             output_file: str = None, all_results: list = None,
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
                        timeout=120,  # hard wall-clock kill — fires even if Gemini holds connection open
                    )
                    elapsed = time.time() - t_start
                    logger.info(f"  {C['dim']}✓ [{signal}] done in {elapsed:.1f}s{C['reset']}")
                    if response.text is None:
                        candidates = getattr(response, "candidates", [])
                        finish_reason = candidates[0].finish_reason if candidates else "unknown"
                        logger.warning(f"  {C['yellow']}⚠ [{signal}] Empty response (finish_reason={finish_reason}). Skipping.{C['reset']}")
                        if _usage: await _usage.record("empty", signal=signal,
                                                        usage_meta=getattr(response, "usage_metadata", None),
                                                        elapsed=elapsed, hits=0,
                                                        is_retry=(attempt > 1))
                        return signal, []
                    parsed = parse_signals(response.text)
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
                        sys.exit(1)
                    elif "429" in err or "RATE" in err.upper():
                        wait = min(5 * (2 ** attempt), 120)  # exponential: 10s, 20s, 40s, capped at 120s
                        logger.warning(f"  {C['yellow']}⚠ Rate limit [{signal}], waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...{C['reset']}")
                        if _usage: await _usage.record("error", signal=signal, elapsed=elapsed, is_retry=True)
                        await asyncio.sleep(wait)
                    elif isinstance(e, (asyncio.TimeoutError, TimeoutError)) or "timeout" in err.lower() or "deadline" in err.lower() or elapsed >= 89:
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
        result["signals"][signal] = signal_map[signal]
        acct_hits += len(signal_map[signal])

    if _usage:
        await _usage.record_account(account, acct_elapsed, acct_hits)
        # Flush partial usage report to disk after every account (synchronous — simple and reliable)
        if output_file:
            try:
                usage_file = output_file.replace(".json", "_usage.json")
                usage_snapshot = _usage.to_dict()
                with open(usage_file, "w") as f:
                    json.dump(usage_snapshot, f, indent=2)
                logger.debug(f"  Usage report updated → {usage_file}")
            except Exception as ue:
                logger.warning(f"  ⚠ Could not write usage file: {ue}")

    if output_file and all_results is not None:
        all_results.append(result)
        if len(all_results) % SAVE_FREQUENCY == 0 or len(all_results) == 1:
            # Run file I/O in a background thread — keeps event loop unblocked
            await asyncio.to_thread(save_incremental, all_results[:], output_file)
            logger.info(f"  {d}✔ saved → {output_file} ({len(all_results)} accounts){r}")

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

def run_category(category: str, output_file: str, signal_override: str = None,
                 company_filter: str = None, api_key: str = None, limit: int = None,
                 accounts_override: list = None):
    """Main entry point called by each category script.

    accounts_override: if provided, use this list instead of ACCOUNTS[category].
                       Useful for sub-lists like Top12 or Super80.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)  # .env values take precedence over shell env vars
    except ImportError:
        pass

    # Set up logger — writes to console + log file alongside the JSON output
    global logger
    log_file = output_file.replace(".json", ".log") if output_file else None
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

    # Filter to single company if specified
    if company_filter:
        query = company_filter.upper()
        accounts = [a for a in accounts if query in a.upper()]
        if not accounts:
            logger.error(f"ERROR: No accounts matched '{company_filter}' in {category}.")
            sys.exit(1)

    # Resume from checkpoint
    all_results = load_checkpoint(output_file) if output_file else []
    completed = {r["account"].upper() for r in all_results}
    if completed:
        logger.info(f"{C['yellow']}⚡ Resuming — {len(completed)} accounts already done, skipping.{C['reset']}")
    pending = [a for a in accounts if a.upper() not in completed]

    # Apply limit after checkpoint resume so --limit 5 always means 5 new accounts
    if limit and limit > 0:
        pending = pending[:limit]
        logger.info(f"{C['yellow']}⚡ Limit set — running first {len(pending)} pending account(s).{C['reset']}")

    logger.info(f"\n{C['bold']}Thomas Scientific // {category}{C['reset']}")
    logger.info(f"{C['dim']}{len(pending)} accounts | Signals: {', '.join(signals)} | Last {DAYS_BACK} days{C['reset']}")

    # Compute recency instruction once for the entire run — not per signal
    recency_instr = _recency_instruction()

    # Initialise a fresh usage tracker for this run
    # Seed with already-checkpointed accounts so resumed runs show accurate totals
    global _usage
    _usage = UsageTracker(total_accounts=len(accounts))
    for r in all_results:
        # Count prior accounts as done with data (no timing/token data available for them)
        acct_hits = sum(len(v) for v in r.get("signals", {}).values())
        _usage.accounts_done += 1
        if acct_hits > 0:
            _usage.accounts_with_data += 1
        _usage.account_times.append((r["account"], 0.0))  # elapsed unknown for resumed accounts

    # Run all accounts sequentially, signals in parallel per account
    async def _run_all():
        sem = asyncio.Semaphore(SEMAPHORE_SIZE)
        for account in pending:
            await run_account_async(client, account, category, signals,
                                    output_file=output_file, all_results=all_results,
                                    sem=sem, recency_instruction=recency_instr)

    asyncio.run(_run_all())
    # Allow aiohttp connector to close cleanly
    time.sleep(0.5)

    print_summary(all_results)
    _usage.print_report()

    # Save usage sidecar file
    if output_file:
        usage_file = output_file.replace(".json", "_usage.json")
        with open(usage_file, "w") as f:
            json.dump(_usage.to_dict(), f, indent=2)
        logger.info(f"\033[90mUsage report saved to {usage_file}\033[0m")

    if output_file:
        save_incremental(all_results, output_file)
        logger.info(f"\033[90mFinal results saved to {output_file}\033[0m")
        if log_file:
            logger.info(f"\033[90mLog saved to {log_file}\033[0m\n")
