"""
rerun_timeouts.py — Generic timeout rerun tool.

Auto-parses a run log file to find all TIMEOUT lines, extracts the
account+signal pairs, and reruns them with a higher timeout.
Successful results are merged back into the original results JSON.

Usage:
    python3 rerun_timeouts.py <log_file> [--timeout 180] [--concurrency 5]

Examples:
    python3 rerun_timeouts.py biopharma_batch4_results.log
    python3 rerun_timeouts.py clinical_dx_batch2_results.log --timeout 180
    python3 rerun_timeouts.py industrial_results.log --timeout 200 --concurrency 3
"""

import argparse
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
from prompts import build_prompt, _recency_instruction

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

# ── Arg parsing ───────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Re-run timed-out signals from a batch log.")
parser.add_argument("log_file", help="Path to the .log file from a batch run")
parser.add_argument("--timeout", type=int, default=180, help="Hard timeout in seconds (default: 180)")
parser.add_argument("--concurrency", type=int, default=5, help="Max concurrent API calls (default: 5)")
args = parser.parse_args()

LOG_FILE_IN   = args.log_file
HARD_TIMEOUT  = args.timeout
CONCURRENCY   = args.concurrency
BASE_JSON     = LOG_FILE_IN.replace(".log", ".json")
OUTPUT_JSON   = LOG_FILE_IN.replace(".log", "_rerun_results.json")
LOG_FILE_OUT  = LOG_FILE_IN.replace(".log", "_rerun.log")

MODEL       = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0.2"))

# ── Logger ────────────────────────────────────────────────────────
_ansi = re.compile(r"\033\[[0-9;]*m")

def setup_logger():
    logger = logging.getLogger("rerun")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)
    class PlainFormatter(logging.Formatter):
        def format(self, record):
            record.msg = _ansi.sub("", str(record.msg))
            return super().format(record)
    fh = logging.FileHandler(LOG_FILE_OUT, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(PlainFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)
    return logger

logger = setup_logger()

# ── Parse log for TIMEOUT pairs ───────────────────────────────────
def parse_timeout_pairs(log_path: str) -> list[tuple[str, str, str]]:
    """
    Reads log file and extracts (account, signal, category) for every TIMEOUT line.
    Returns deduplicated list — each pair appears once even if logged twice.
    """
    current_account = None
    current_category = None
    pairs = []
    seen = set()

    # Match: "  ACCOUNT NAME  [Category]"
    acct_re  = re.compile(r"^\s+([A-Z][A-Z0-9 &/.,'\-]+)\s+\[([^\]]+)\]\s*$")
    # Match: "⚠ TIMEOUT [signal] ..."  or  "hard-killed"
    timeout_re = re.compile(r"TIMEOUT\s+\[([a-z_]+)\]|hard-killed after.*\[([a-z_]+)\]")

    # Strip ANSI from log lines
    with open(log_path, encoding="utf-8") as f:
        for raw_line in f:
            line = _ansi.sub("", raw_line).strip()
            # Strip log prefix "HH:MM:SS [LEVEL] "
            line = re.sub(r"^\d{2}:\d{2}:\d{2} \[\w+\] ", "", line)

            acct_m = acct_re.match(line)
            if acct_m:
                current_account  = acct_m.group(1).strip()
                current_category = acct_m.group(2).strip()

            to_m = timeout_re.search(line)
            if to_m and current_account:
                signal = (to_m.group(1) or to_m.group(2)).strip()
                key = (current_account, signal)
                if key not in seen:
                    seen.add(key)
                    pairs.append((current_account, signal, current_category))

    return pairs

# ── Parse signals ─────────────────────────────────────────────────
def parse_signals(raw: str) -> list:
    cleaned = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, list) else []
    except Exception:
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            try: return json.loads(match.group(0))
            except: pass
    return []

# ── Run one pair ──────────────────────────────────────────────────
async def run_one(client, account, signal, category, recency, sem):
    async with sem:
        prompt = build_prompt(signal, account, category, recency_instruction=recency)
        t_start = time.time()
        logger.info(f"  → [{account}] [{signal}] starting...")
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
                timeout=HARD_TIMEOUT,
            )
            elapsed = time.time() - t_start
            if response.text is None:
                logger.warning(f"  ⚠ [{account}] [{signal}] empty after {elapsed:.1f}s")
                return {"account": account, "signal": signal, "category": category,
                        "outcome": "empty", "elapsed": round(elapsed, 1), "data": []}
            data = parse_signals(response.text)
            logger.info(f"  ✓ [{account}] [{signal}] done in {elapsed:.1f}s — {len(data)} hit(s)")
            return {"account": account, "signal": signal, "category": category,
                    "outcome": "success", "elapsed": round(elapsed, 1), "data": data}
        except (asyncio.TimeoutError, TimeoutError):
            elapsed = time.time() - t_start
            logger.warning(f"  ✗ [{account}] [{signal}] TIMEOUT after {elapsed:.1f}s (>{HARD_TIMEOUT}s)")
            return {"account": account, "signal": signal, "category": category,
                    "outcome": "timeout", "elapsed": round(elapsed, 1), "data": []}
        except Exception as e:
            elapsed = time.time() - t_start
            logger.error(f"  ✗ [{account}] [{signal}] ERROR after {elapsed:.1f}s: {e}")
            return {"account": account, "signal": signal, "category": category,
                    "outcome": "error", "elapsed": round(elapsed, 1), "data": []}

# ── Merge successes back into base JSON ───────────────────────────
def merge_into_base(rerun_results: list) -> int:
    if not os.path.exists(BASE_JSON):
        logger.warning(f"  Base JSON not found: {BASE_JSON} — skipping merge.")
        return 0
    with open(BASE_JSON) as f:
        base = json.load(f)
    acct_map = {r["account"].upper(): r for r in base}
    merged = 0
    for r in rerun_results:
        if r["outcome"] == "success" and r["data"]:
            key = r["account"].upper()
            if key in acct_map:
                acct_map[key]["signals"][r["signal"]] = r["data"]
                merged += 1
    updated = list(acct_map.values())
    tmp = BASE_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(updated, f, indent=2)
    os.replace(tmp, BASE_JSON)
    return merged

# ── Main ──────────────────────────────────────────────────────────
async def main():
    # Parse log
    if not os.path.exists(LOG_FILE_IN):
        logger.error(f"Log file not found: {LOG_FILE_IN}")
        sys.exit(1)

    pairs = parse_timeout_pairs(LOG_FILE_IN)
    if not pairs:
        logger.info("No TIMEOUT lines found in log. Nothing to re-run.")
        sys.exit(0)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set.")
        sys.exit(1)

    client = genai.Client(
        api_key=api_key,
        http_options=HttpOptions(api_version="v1alpha", timeout=(HARD_TIMEOUT + 60) * 1000),
    )
    recency = _recency_instruction()
    sem = asyncio.Semaphore(CONCURRENCY)

    logger.info(f"\n{'═'*60}")
    logger.info(f"  TIMEOUT RE-RUN")
    logger.info(f"{'═'*60}")
    logger.info(f"  Log parsed:    {LOG_FILE_IN}")
    logger.info(f"  Pairs found:   {len(pairs)}")
    logger.info(f"  Hard timeout:  {HARD_TIMEOUT}s")
    logger.info(f"  Concurrency:   {CONCURRENCY}")
    logger.info(f"  Started:       {datetime.now().strftime('%H:%M:%S')}\n")

    for acct, sig, cat in pairs:
        logger.info(f"  {acct} → [{sig}]  ({cat})")
    logger.info("")

    tasks = [run_one(client, acct, sig, cat, recency, sem) for acct, sig, cat in pairs]
    results = await asyncio.gather(*tasks)

    # Save rerun output
    with open(OUTPUT_JSON, "w") as f:
        json.dump(results, f, indent=2)

    # Merge back into base
    merged = merge_into_base(results)

    # Summary
    successes = [r for r in results if r["outcome"] == "success"]
    timeouts  = [r for r in results if r["outcome"] == "timeout"]
    errors    = [r for r in results if r["outcome"] == "error"]

    logger.info(f"\n{'═'*60}")
    logger.info(f"  SUMMARY")
    logger.info(f"{'═'*60}")
    logger.info(f"  Total pairs      {len(results)}")
    logger.info(f"  Successes        {len(successes)}  ({len(successes)/len(results)*100:.0f}%)")
    logger.info(f"  Timeouts         {len(timeouts)}")
    logger.info(f"  Errors           {len(errors)}")
    logger.info(f"  New hits         {sum(r['hits'] if 'hits' in r else len(r['data']) for r in successes)}")
    logger.info(f"  Merged into base {merged} signals → {BASE_JSON}")
    if successes:
        logger.info(f"  Max elapsed      {max(r['elapsed'] for r in successes):.1f}s")
        logger.info(f"  Recommended timeout: {int(max(r['elapsed'] for r in successes) + 15)}s")
    if timeouts:
        logger.info(f"\n  Still timed out at {HARD_TIMEOUT}s:")
        for r in timeouts:
            logger.info(f"    [{r['account']}] [{r['signal']}]")
    logger.info(f"\n  Rerun results → {OUTPUT_JSON}")
    logger.info(f"  Rerun log     → {LOG_FILE_OUT}")
    logger.info(f"{'═'*60}\n")


asyncio.run(main())
time.sleep(0.5)
