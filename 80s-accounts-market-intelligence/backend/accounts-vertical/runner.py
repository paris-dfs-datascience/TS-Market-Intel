"""
runner.py — Shared core logic for all category run scripts.
Imported by run_biopharma.py, run_education.py, etc.
Do not run directly.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, HttpOptions, Tool
from prompts import build_prompt, FIELD_MAPS, CATEGORY_TRIGGERS, DAYS_BACK
from accounts import ACCOUNTS, get_category

# ── Configurable via env vars ─────────────────────────────────────
# Override any of these in your shell or .env file:
#   export GEMINI_MODEL=gemini-2.5-flash
#   export CALL_DELAY=2          (paid plan with higher RPM)
#   export GEMINI_TEMPERATURE=0.3
MODEL       = os.environ.get("GEMINI_MODEL",       "gemini-flash-latest")
CALL_DELAY  = int(os.environ.get("CALL_DELAY",    "6"))   # secs between calls; 6 = ~10 RPM free tier
TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0.2"))

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


# ── Helpers ───────────────────────────────────────────────────────

def get_client(api_key: str = None):
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        print("ERROR: Add GEMINI_API_KEY=your_key to your .env file.")
        sys.exit(1)
    return genai.Client(api_key=key, http_options=HttpOptions(api_version="v1alpha"))


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
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)


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
        print(f"  {d}[{signal}] No signals found.{r}")
        return
    print(f"  {col}{b}[{signal.upper()}] {len(signals)} signal(s){r}")
    for i, s in enumerate(signals, 1):
        print(f"    {b}[{i}] {s.get('summary', '')}{r}")
        meta = " · ".join(str(s[f]) for f in FIELD_MAPS.get(signal, []) if s.get(f))
        if meta:
            print(f"        {d}{meta}{r}")
        if s.get("why_it_matters"):
            print(f"        {y}↳ {s['why_it_matters']}{r}")
        if s.get("source_url"):
            print(f"        {d}{s['source_url']}{r}")


def run_account(client, account: str, category: str, signals: list,
                output_file: str = None, all_results: list = None,
                retry: int = 3, retry_delay: int = 10) -> dict:
    b, h, r, d = C["bold"], C["header"], C["reset"], C["dim"]
    print(f"\n{b}{'═'*60}{r}")
    print(f"{h}{b}  {account}{r}  {d}[{category}]{r}")
    print(f"{b}{'═'*60}{r}")

    result = {
        "account":   account,
        "category":  category,
        "signals":   {},
        "timestamp": datetime.now().isoformat(),
    }

    for signal in signals:
        print(f"  {d}Searching [{signal}]...{r}", end="", flush=True)
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
                    print(f"\r  {C['yellow']}⚠ [{signal}] Empty response (finish_reason={finish_reason}, parts={part_types}). Skipping.{C['reset']}")
                    break
                found = parse_signals(response.text)
                time.sleep(CALL_DELAY)
                break
            except Exception as e:
                err = str(e)
                if "RESOURCE_EXHAUSTED" in err and "prepayment" in err:
                    print(f"\r  {C['red']}✘ Credits depleted. Top up at aistudio.google.com and re-run.{C['reset']}")
                    result["signals"][signal] = []
                    if output_file and all_results is not None:
                        all_results.append(result)
                        save_incremental(all_results, output_file)
                        print(f"{d}  Progress saved to {output_file}{r}")
                    sys.exit(1)
                elif "429" in err or "RATE" in err.upper():
                    wait = min(retry_delay * attempt, 120)  # cap at 2 minutes
                    print(f"\r  {C['yellow']}⚠ Rate limit [{signal}], waiting {wait}s (attempt {attempt}/{retry})...{C['reset']}", end="", flush=True)
                    time.sleep(wait)
                else:
                    print(f"\r  {C['red']}ERROR [{signal}]: {e}{C['reset']}")
                    break

        print(f"\r", end="")
        print_signals(signal, found)
        result["signals"][signal] = found

    if output_file and all_results is not None:
        all_results.append(result)
        # Save every 5 accounts to reduce disk I/O — always save on first and last
        if len(all_results) % 5 == 0 or len(all_results) == 1:
            save_incremental(all_results, output_file)
            print(f"  {d}✔ saved → {output_file} ({len(all_results)} accounts){r}")

    return result


def print_summary(all_results: list):
    b, r, d, y = C["bold"], C["reset"], C["dim"], C["yellow"]
    print(f"\n{b}{'═'*60}{r}")
    print(f"{b}  SUMMARY{r}")
    print(f"{b}{'═'*60}{r}")
    total_signals = 0
    for res in all_results:
        counts = {s: len(v) for s, v in res["signals"].items()}
        total = sum(counts.values())
        total_signals += total
        if total > 0:
            count_str = "  ".join(f"{s}:{n}" for s, n in counts.items() if n > 0)
            print(f"  {b}{res['account']}{r}  {d}[{res['category']}]{r}  {y}{count_str}{r}")
        else:
            print(f"  {d}{res['account']} [{res['category']}] — no signals{r}")
    print(f"\n  {b}Total signals: {total_signals}{r} across {len(all_results)} accounts\n")


def run_category(category: str, output_file: str, signal_override: str = None,
                 company_filter: str = None, api_key: str = None, limit: int = None):
    """Main entry point called by each category script."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    client = get_client(api_key)
    signals = [signal_override] if signal_override else CATEGORY_TRIGGERS.get(category, [])

    if not signals:
        print(f"{C['red']}ERROR: No signals defined for category '{category}'. Check CATEGORY_TRIGGERS in prompts.py.{C['reset']}")
        sys.exit(1)

    # Validate all signal names are known before starting any API calls
    unknown = [s for s in signals if s not in FIELD_MAPS]
    if unknown:
        print(f"{C['red']}ERROR: Unknown signal(s): {unknown}. Check FIELD_MAPS in prompts.py.{C['reset']}")
        sys.exit(1)

    if category not in ACCOUNTS:
        print(f"{C['red']}ERROR: Category '{category}' not found in accounts.py.{C['reset']}")
        sys.exit(1)

    accounts = ACCOUNTS[category]

    # Filter to single company if specified
    if company_filter:
        query = company_filter.upper()
        accounts = [a for a in accounts if query in a.upper()]
        if not accounts:
            print(f"ERROR: No accounts matched '{company_filter}' in {category}.")
            sys.exit(1)

    # Resume from checkpoint
    all_results = load_checkpoint(output_file) if output_file else []
    completed = {r["account"].upper() for r in all_results}
    if completed:
        print(f"{C['yellow']}⚡ Resuming — {len(completed)} accounts already done, skipping.{C['reset']}")
    pending = [a for a in accounts if a.upper() not in completed]

    # Apply limit after checkpoint resume so --limit 5 always means 5 new accounts
    if limit and limit > 0:
        pending = pending[:limit]
        print(f"{C['yellow']}⚡ Limit set — running first {len(pending)} pending account(s).{C['reset']}")

    print(f"\n{C['bold']}Thomas Scientific // {category}{C['reset']}")
    print(f"{C['dim']}{len(pending)} accounts | Signals: {', '.join(signals)} | Last {DAYS_BACK} days{C['reset']}")

    for account in pending:
        run_account(client, account, category, signals,
                    output_file=output_file, all_results=all_results)

    print_summary(all_results)

    if output_file:
        save_incremental(all_results, output_file)
        print(f"\033[90mFinal results saved to {output_file}\033[0m\n")
