"""
Batch Account Intel — Thomas Scientific 80s Accounts
Runs category-appropriate signals per account based on the 12×7 matrix.

Usage:
    python run_all_accounts.py
    python run_all_accounts.py --category "BioPharma"
    python run_all_accounts.py --category "Clinical / Mol Dx"
    python run_all_accounts.py --company "PFIZER"
    python run_all_accounts.py --company "PFIZER" --signal pipeline
    python run_all_accounts.py --super80
    python run_all_accounts.py --output results.json
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, HttpOptions, Tool
from prompts import build_prompt, FIELD_MAPS, CATEGORY_TRIGGERS, DAYS_BACK
from accounts import ACCOUNTS, SUPER80, get_category, all_accounts_flat

MODEL = "gemini-2.5-flash"

CATEGORIES = list(ACCOUNTS.keys())

SIGNAL_COLORS = {
    "grant":       "\033[94m",   # blue
    "faculty":     "\033[96m",   # cyan
    "capital":     "\033[92m",   # green
    "contract":    "\033[93m",   # yellow
    "pipeline":    "\033[95m",   # magenta
    "expansion":   "\033[92m",   # green
    "partnership": "\033[95m",   # magenta
    "funding":     "\033[93m",   # yellow
    "project":     "\033[91m",   # red
    "regulatory":  "\033[31m",   # dark red
    "hiring":      "\033[36m",   # teal
    "tender":      "\033[33m",   # orange
}

COLORS = {
    **SIGNAL_COLORS,
    "reset":  "\033[0m",
    "dim":    "\033[90m",
    "bold":   "\033[1m",
    "yellow": "\033[93m",
    "header": "\033[97m",
}


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
    c = COLORS.get(signal, "\033[96m")
    r = COLORS["reset"]
    d = COLORS["dim"]
    y = COLORS["yellow"]
    b = COLORS["bold"]

    if not signals:
        print(f"  {d}[{signal}] No signals found.{r}")
        return

    print(f"  {c}{b}[{signal.upper()}] {len(signals)} signal(s){r}")
    for i, s in enumerate(signals, 1):
        print(f"    {b}[{i}] {s.get('summary', '')}{r}")
        meta = " · ".join(str(s[f]) for f in FIELD_MAPS.get(signal, []) if s.get(f))
        if meta:
            print(f"        {d}{meta}{r}")
        if s.get("why_it_matters"):
            print(f"        {y}↳ {s['why_it_matters']}{r}")
        if s.get("source_url"):
            print(f"        {d}{s['source_url']}{r}")


def save_incremental(all_results: list, output_file: str):
    """Write current results to disk after every account."""
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)


def load_checkpoint(output_file: str) -> list:
    """Load previously saved results so a resumed run skips completed accounts."""
    if output_file and os.path.exists(output_file):
        try:
            with open(output_file) as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return []


def run_account(client, account: str, category: str, signals: list,
                output_file: str = None, all_results: list = None,
                retry: int = 3, retry_delay: int = 10) -> dict:
    b = COLORS["bold"]
    h = COLORS["header"]
    r = COLORS["reset"]
    d = COLORS["dim"]

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
                        temperature=0.2,
                    ),
                )
                found = parse_signals(response.text)
                break
            except Exception as e:
                err = str(e)
                # Hard stop on depleted credits — no point retrying
                if "RESOURCE_EXHAUSTED" in err and "prepayment" in err:
                    print(f"\r  \033[91m✘ Credits depleted. Top up at aistudio.google.com and re-run.\033[0m")
                    result["signals"][signal] = []
                    # Save whatever we have before exiting
                    if output_file and all_results is not None:
                        all_results.append(result)
                        save_incremental(all_results, output_file)
                        print(f"\033[90m  Progress saved to {output_file}\033[0m")
                    sys.exit(1)
                # Rate limit — wait and retry
                elif "429" in err or "RATE" in err.upper():
                    wait = retry_delay * attempt
                    print(f"\r  \033[93m⚠ Rate limit [{signal}], waiting {wait}s (attempt {attempt}/{retry})...\033[0m", end="", flush=True)
                    time.sleep(wait)
                else:
                    print(f"\r  \033[91mERROR [{signal}]: {e}\033[0m")
                    break

        print(f"\r", end="")
        print_signals(signal, found)
        result["signals"][signal] = found

    # ── Incremental save after every account ─────────────────────
    if output_file and all_results is not None:
        all_results.append(result)
        save_incremental(all_results, output_file)
        d_color = COLORS["dim"]
        r_color = COLORS["reset"]
        print(f"  {d_color}✔ saved → {output_file}{r_color}")

    return result


def print_summary(all_results: list):
    b = COLORS["bold"]
    r = COLORS["reset"]
    d = COLORS["dim"]
    y = COLORS["yellow"]

    print(f"\n{b}{'═'*60}{r}")
    print(f"{b}  SUMMARY{r}")
    print(f"{b}{'═'*60}{r}")

    total_signals = 0
    for result in all_results:
        counts = {s: len(v) for s, v in result["signals"].items()}
        total = sum(counts.values())
        total_signals += total
        if total > 0:
            count_str = "  ".join(f"{s}:{n}" for s, n in counts.items() if n > 0)
            print(f"  {b}{result['account']}{r}  {d}[{result['category']}]{r}  {y}{count_str}{r}")
        else:
            print(f"  {d}{result['account']} [{result['category']}] — no signals{r}")

    print(f"\n  {b}Total signals: {total_signals}{r} across {len(all_results)} accounts\n")


def main():
    all_signals = list(FIELD_MAPS.keys())

    parser = argparse.ArgumentParser(description="Thomas Scientific — 80s Accounts Market Intel")
    parser.add_argument("--category", "-cat", default=None,
                        choices=CATEGORIES,
                        help="Run for one industry category")
    parser.add_argument("--signal", "-s", default=None,
                        choices=all_signals,
                        help="Override: run a single signal for all selected accounts")
    parser.add_argument("--company", "-c", default=None,
                        help="Run for a single account (substring match)")
    parser.add_argument("--super80", action="store_true",
                        help="Run Super80 priority accounts only")
    parser.add_argument("--output", "-o", default=None,
                        help="Save results to JSON file")
    parser.add_argument("--api-key", "-k", default=None)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Add GEMINI_API_KEY=your_key to your .env file.")
        sys.exit(1)

    client = genai.Client(
        api_key=api_key,
        http_options=HttpOptions(api_version="v1alpha")
    )

    # ── Build run list: [(account, category, [signals])] ─────────
    run_list = []

    if args.company:
        query = args.company.upper()
        for acct, cat in all_accounts_flat():
            if query in acct.upper():
                signals = [args.signal] if args.signal else CATEGORY_TRIGGERS[cat]
                run_list.append((acct, cat, signals))
        if not run_list:
            print(f"ERROR: No accounts matched '{args.company}'.")
            sys.exit(1)

    elif args.super80:
        for acct in SUPER80:
            cat = get_category(acct)
            signals = [args.signal] if args.signal else CATEGORY_TRIGGERS[cat]
            run_list.append((acct, cat, signals))

    elif args.category:
        for acct in ACCOUNTS[args.category]:
            signals = [args.signal] if args.signal else CATEGORY_TRIGGERS[args.category]
            run_list.append((acct, args.category, signals))

    else:
        for acct, cat in all_accounts_flat():
            signals = [args.signal] if args.signal else CATEGORY_TRIGGERS[cat]
            run_list.append((acct, cat, signals))

    # ── Resume from checkpoint if output file already exists ─────
    all_results = load_checkpoint(args.output) if args.output else []
    completed = {r["account"].upper() for r in all_results}
    if completed:
        print(f"\033[93m⚡ Resuming — {len(completed)} accounts already done, skipping.\033[0m")
    pending = [(a, c, s) for a, c, s in run_list if a.upper() not in completed]

    print(f"\n\033[1mThomas Scientific // 80s Accounts Market Intel\033[0m")
    print(f"\033[90m{len(pending)} accounts to run | Last {DAYS_BACK} days\033[0m")

    if not pending:
        print("\033[92mAll accounts already completed.\033[0m")
    else:
        for acct, cat, signals in pending:
            run_account(client, acct, cat, signals,
                        output_file=args.output,
                        all_results=all_results)

    print_summary(all_results)

    # Final save if no --output was given (print-only mode)
    if args.output:
        save_incremental(all_results, args.output)
        print(f"\033[90mFinal results saved to {args.output}\033[0m\n")


if __name__ == "__main__":
    main()
