"""
Run market intelligence for top 12 BioPharma accounts.
Accounts: ABBVIE, ASTRAZENECA, ELI LILLY, MERCK, MODERNA, PFIZER,
          REGENERON, ROCHE GROUP, SANOFI, TAKEDA, VERTEX PHARMACEUTICAL, NOVONESIS

Usage:
    python run_biopharma_top12.py
    python run_biopharma_top12.py --signal pipeline
    python run_biopharma_top12.py --output biopharma_top12_results.json
"""

import argparse
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from runner import get_client, run_account, print_summary, save_incremental, load_checkpoint, CALL_DELAY
from prompts import CATEGORY_TRIGGERS

CATEGORY = "BioPharma"
DEFAULT_OUTPUT = "biopharma_top12_results.json"

TOP12 = [
    "ABBVIE",
    "ASTRAZENECA",
    "ELI LILLY",
    "MERCK",
    "MODERNA THERAPEUTICS",
    "PFIZER",
    "REGENERON",
    "ROCHE GROUP",
    "SANOFI",
    "TAKEDA",
    "VERTEX PHARMACEUTICAL",
    "NOVONESIS",
]

parser = argparse.ArgumentParser(description="Thomas Scientific — BioPharma Top 12")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT)
parser.add_argument("--api-key", "-k", default=None)
args = parser.parse_args()

client = get_client(args.api_key)
signals = [args.signal] if args.signal else CATEGORY_TRIGGERS[CATEGORY]

all_results = load_checkpoint(args.output) if args.output else []
completed = {r["account"].upper() for r in all_results}
if completed:
    print(f"\033[93m⚡ Resuming — {len(completed)} accounts already done, skipping.\033[0m")
pending = [a for a in TOP12 if a.upper() not in completed]

print(f"\n\033[1mThomas Scientific // {CATEGORY} — Top 12\033[0m")
print(f"\033[90m{len(pending)} accounts | Signals: {', '.join(signals)}\033[0m")

for account in pending:
    run_account(client, account, CATEGORY, signals,
                output_file=args.output, all_results=all_results)

print_summary(all_results)

if args.output:
    save_incremental(all_results, args.output)
    print(f"\033[90mFinal results saved to {args.output}\033[0m\n")
