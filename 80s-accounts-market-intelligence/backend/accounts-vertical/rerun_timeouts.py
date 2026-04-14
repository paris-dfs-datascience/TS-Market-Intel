"""
rerun_timeouts.py — Re-run specific account+signal pairs that timed out.
Runs with a higher timeout (150s) to measure actual completion time.
Results saved to rerun_timeouts_results.json + timing report to rerun_timeouts_timing.json
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
from prompts import build_prompt, _recency_instruction

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

# ── Config ────────────────────────────────────────────────────────
MODEL        = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
TEMPERATURE  = float(os.environ.get("GEMINI_TEMPERATURE", "0.2"))
HARD_TIMEOUT = 150  # seconds — higher than batch run's 90s to see if signals complete
CATEGORY     = "BioPharma"
OUTPUT_FILE  = "rerun_timeouts_results.json"
TIMING_FILE  = "rerun_timeouts_timing.json"
LOG_FILE     = "rerun_timeouts.log"

# ── Exact account+signal pairs that timed out in batch4 ──────────
TIMEOUT_PAIRS = [
    ("GENEZEN",                    "contract"),
    ("GENEZEN",                    "pipeline"),
    ("GENEZEN",                    "spinoff"),
    ("GRANULES PHARMACEUTICALS INC","pipeline"),
    ("LOXO ONCOLOGY",              "partnership"),
    ("LOXO ONCOLOGY",              "regulatory"),
    ("MANNKIND CORPORATION",       "regulatory"),
    ("MYRIAD",                     "grant"),
    ("MYRIAD",                     "pipeline"),
    ("MYRIAD",                     "partnership"),
    ("PIRAMAL",                    "partnership"),
    ("PIRAMAL",                    "funding"),
    ("PIRAMAL",                    "regulatory"),
    ("PIRAMAL",                    "ma"),
    ("PIRAMAL",                    "spinoff"),
    ("SEPIA THERAPEUTICS",         "spinoff"),
    ("STALLERGENE GREER",          "pipeline"),
]

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
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(PlainFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)
    return logger

logger = setup_logger()

# ── Main ──────────────────────────────────────────────────────────

async def run_one(client, account, signal, recency_instruction, sem):
    async with sem:
        prompt = build_prompt(signal, account, CATEGORY, recency_instruction=recency_instruction)
        t_start = time.time()
        logger.info(f"  → [{account}] [{signal}] starting...")
        outcome = "unknown"
        elapsed = 0.0
        result_data = []
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
                outcome = "empty"
                logger.warning(f"  ⚠ [{account}] [{signal}] empty response after {elapsed:.1f}s")
            else:
                cleaned = re.sub(r"```json\s*", "", response.text, flags=re.IGNORECASE)
                cleaned = re.sub(r"```\s*", "", cleaned).strip()
                try:
                    parsed = json.loads(cleaned)
                    result_data = parsed if isinstance(parsed, list) else []
                except Exception:
                    match = re.search(r"\[[\s\S]*\]", cleaned)
                    result_data = json.loads(match.group(0)) if match else []
                outcome = "success"
                logger.info(f"  ✓ [{account}] [{signal}] done in {elapsed:.1f}s — {len(result_data)} hit(s)")
        except (asyncio.TimeoutError, TimeoutError):
            elapsed = time.time() - t_start
            outcome = "timeout"
            logger.warning(f"  ✗ [{account}] [{signal}] TIMEOUT after {elapsed:.1f}s (>{HARD_TIMEOUT}s limit)")
        except Exception as e:
            elapsed = time.time() - t_start
            outcome = "error"
            logger.error(f"  ✗ [{account}] [{signal}] ERROR after {elapsed:.1f}s: {e}")

        return {
            "account":  account,
            "signal":   signal,
            "outcome":  outcome,
            "elapsed":  round(elapsed, 1),
            "hits":     len(result_data),
            "data":     result_data,
        }


async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("ERROR: GEMINI_API_KEY not set.")
        sys.exit(1)

    client = genai.Client(
        api_key=api_key,
        http_options=HttpOptions(api_version="v1alpha", timeout=200000),  # 200s — above asyncio.wait_for hard limit
    )
    recency_instruction = _recency_instruction()
    sem = asyncio.Semaphore(5)  # max 5 concurrent re-runs

    logger.info(f"\n{'═'*60}")
    logger.info(f"  TIMEOUT RE-RUN — {len(TIMEOUT_PAIRS)} pairs @ {HARD_TIMEOUT}s limit")
    logger.info(f"{'═'*60}")
    logger.info(f"  Started: {datetime.now().strftime('%H:%M:%S')}\n")

    tasks = [run_one(client, acct, sig, recency_instruction, sem)
             for acct, sig in TIMEOUT_PAIRS]
    results = await asyncio.gather(*tasks)

    # ── Save results ─────────────────────────────────────────────
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    # ── Timing report ─────────────────────────────────────────────
    successes = [r for r in results if r["outcome"] == "success"]
    timeouts  = [r for r in results if r["outcome"] == "timeout"]
    empties   = [r for r in results if r["outcome"] == "empty"]

    timing_report = {
        "total_pairs":   len(results),
        "successes":     len(successes),
        "timeouts":      len(timeouts),
        "empties":       len(empties),
        "recommendation": f"Raise timeout to {int(max(r['elapsed'] for r in successes) + 15)}s" if successes else "Signals still timing out — consider removing from run",
        "by_signal": {},
        "by_account": {},
        "results": results,
    }

    # Group by signal
    for r in results:
        s = timing_report["by_signal"].setdefault(r["signal"], {"outcomes": [], "elapsed": []})
        s["outcomes"].append(r["outcome"])
        s["elapsed"].append(r["elapsed"])
    for sig, d in timing_report["by_signal"].items():
        d["avg_elapsed"] = round(sum(d["elapsed"]) / len(d["elapsed"]), 1)
        d["max_elapsed"] = max(d["elapsed"])

    # Group by account
    for r in results:
        a = timing_report["by_account"].setdefault(r["account"], {"outcomes": [], "elapsed": []})
        a["outcomes"].append(r["outcome"])
        a["elapsed"].append(r["elapsed"])

    with open(TIMING_FILE, "w") as f:
        json.dump(timing_report, f, indent=2)

    # ── Console summary ────────────────────────────────────────────
    logger.info(f"\n{'═'*60}")
    logger.info(f"  RE-RUN SUMMARY")
    logger.info(f"{'═'*60}")
    logger.info(f"  {'Total pairs':<25} {len(results)}")
    logger.info(f"  {'Successes':<25} {len(successes)}  ({len(successes)/len(results)*100:.0f}%)")
    logger.info(f"  {'Timeouts (>{HARD_TIMEOUT}s)':<25} {len(timeouts)}")
    logger.info(f"  {'Empty responses':<25} {len(empties)}")
    logger.info(f"  {'New signal hits':<25} {sum(r['hits'] for r in successes)}")

    if successes:
        max_t = max(r["elapsed"] for r in successes)
        logger.info(f"\n  Slowest successful: {max_t:.1f}s")
        logger.info(f"  Recommended timeout: {int(max_t + 15)}s")

    logger.info(f"\n  Signal breakdown:")
    for sig, d in sorted(timing_report["by_signal"].items(), key=lambda x: x[1]["max_elapsed"], reverse=True):
        logger.info(f"    {sig:<16} avg {d['avg_elapsed']:>6.1f}s  max {d['max_elapsed']:>6.1f}s  outcomes: {d['outcomes']}")

    if timeouts:
        logger.info(f"\n  Still timed out at {HARD_TIMEOUT}s:")
        for r in timeouts:
            logger.info(f"    [{r['account']}] [{r['signal']}]")

    logger.info(f"\n  Results → {OUTPUT_FILE}")
    logger.info(f"  Timing  → {TIMING_FILE}")
    logger.info(f"{'═'*60}\n")


asyncio.run(main())
time.sleep(0.5)
