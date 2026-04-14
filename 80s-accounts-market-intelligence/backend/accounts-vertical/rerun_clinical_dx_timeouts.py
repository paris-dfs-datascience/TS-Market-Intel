"""
rerun_clinical_dx_timeouts.py
Re-runs the 13 Clinical Dx signals that timed out at 120s, with a 180s hard limit.
Results merged back into clinical_dx_batch2_results.json.
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
HARD_TIMEOUT = 180   # seconds
CATEGORY     = "Clinical / Mol Dx"
BASE_FILE    = "clinical_dx_batch2_results.json"
OUTPUT_FILE  = "clinical_dx_batch2_rerun_results.json"
LOG_FILE     = "clinical_dx_batch2_rerun.log"

# ── Timed-out pairs from batch2 ───────────────────────────────────
TIMEOUT_PAIRS = [
    ("BIOMERIEUX",           "partnership"),
    ("BIOMERIEUX",           "funding"),
    ("BIOMERIEUX",           "volume"),
    ("BODE TECHNOLOGY GROUP","partnership"),
    ("EUROFINS",             "capital"),
    ("EUROFINS",             "volume"),
    ("EXACT SCIENCES",       "funding"),
    ("EXACT SCIENCES",       "regulatory"),
    ("GENE BY GENE",         "partnership"),
    ("GENE BY GENE",         "volume"),
    ("GRAIL",                "pipeline"),
    ("GRAIL",                "partnership"),
    ("GUARDANT HEALTH",      "project"),
]

# ── Logger ────────────────────────────────────────────────────────
_ansi = re.compile(r"\033\[[0-9;]*m")

def setup_logger():
    logger = logging.getLogger("rerun_cdx")
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

def parse_signals(raw):
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
async def run_one(client, account, signal, recency_instruction, sem):
    async with sem:
        prompt = build_prompt(signal, account, CATEGORY, recency_instruction=recency_instruction)
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
                return {"account": account, "signal": signal, "outcome": "empty", "elapsed": round(elapsed,1), "data": []}
            data = parse_signals(response.text)
            logger.info(f"  ✓ [{account}] [{signal}] done in {elapsed:.1f}s — {len(data)} hit(s)")
            return {"account": account, "signal": signal, "outcome": "success", "elapsed": round(elapsed,1), "data": data}
        except (asyncio.TimeoutError, TimeoutError):
            elapsed = time.time() - t_start
            logger.warning(f"  ✗ [{account}] [{signal}] TIMEOUT after {elapsed:.1f}s (>{HARD_TIMEOUT}s)")
            return {"account": account, "signal": signal, "outcome": "timeout", "elapsed": round(elapsed,1), "data": []}
        except Exception as e:
            elapsed = time.time() - t_start
            logger.error(f"  ✗ [{account}] [{signal}] ERROR after {elapsed:.1f}s: {e}")
            return {"account": account, "signal": signal, "outcome": "error", "elapsed": round(elapsed,1), "data": []}

# ── Merge results back into base file ────────────────────────────
def merge_into_base(rerun_results):
    base = json.load(open(BASE_FILE))
    # Index by account name
    acct_map = {r["account"].upper(): r for r in base}
    merged = 0
    for r in rerun_results:
        if r["outcome"] == "success" and r["data"]:
            key = r["account"].upper()
            if key in acct_map:
                acct_map[key]["signals"][r["signal"]] = r["data"]
                merged += 1
    updated = list(acct_map.values())
    # Atomic write
    tmp = BASE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(updated, f, indent=2)
    os.replace(tmp, BASE_FILE)
    return merged

# ── Main ──────────────────────────────────────────────────────────
async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set.")
        sys.exit(1)

    client = genai.Client(
        api_key=api_key,
        http_options=HttpOptions(api_version="v1alpha", timeout=250000),
    )
    recency = _recency_instruction()
    sem = asyncio.Semaphore(5)

    logger.info(f"\n{'═'*60}")
    logger.info(f"  CLINICAL DX TIMEOUT RE-RUN — {len(TIMEOUT_PAIRS)} pairs @ {HARD_TIMEOUT}s")
    logger.info(f"{'═'*60}")
    logger.info(f"  Started: {datetime.now().strftime('%H:%M:%S')}\n")

    tasks = [run_one(client, acct, sig, recency, sem) for acct, sig in TIMEOUT_PAIRS]
    results = await asyncio.gather(*tasks)

    # Save rerun results
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    # Merge successes back into base file
    merged = merge_into_base(results)

    # Summary
    successes = [r for r in results if r["outcome"] == "success"]
    timeouts  = [r for r in results if r["outcome"] == "timeout"]

    logger.info(f"\n{'═'*60}")
    logger.info(f"  SUMMARY")
    logger.info(f"{'═'*60}")
    logger.info(f"  Total pairs      {len(results)}")
    logger.info(f"  Successes        {len(successes)}  ({len(successes)/len(results)*100:.0f}%)")
    logger.info(f"  Timeouts         {len(timeouts)}")
    logger.info(f"  Merged into base {merged} signals")
    if successes:
        logger.info(f"  Max elapsed      {max(r['elapsed'] for r in successes):.1f}s")
    if timeouts:
        logger.info(f"\n  Still timed out at {HARD_TIMEOUT}s:")
        for r in timeouts:
            logger.info(f"    [{r['account']}] [{r['signal']}]")
    logger.info(f"\n  Base file updated → {BASE_FILE}")
    logger.info(f"  Rerun results    → {OUTPUT_FILE}")
    logger.info(f"{'═'*60}\n")

asyncio.run(main())
time.sleep(0.5)
