"""
Run market intelligence for Super80 priority accounts.
Each account automatically uses its own category's signals.

Super80 accounts:
  AMAZON MARKET PLACE    → Government
  DEFENSE LOGISTICS (DLA)→ Government
  IQVIA                  → CDMO / CRO
  IVF STORE              → Clinical / Mol Dx
  LABCORP                → Clinical / Mol Dx
  TAKEDA                 → BioPharma
  TEMPUS                 → Clinical / Mol Dx

Usage:
    python run_super80.py
    python run_super80.py --signal pipeline
    python run_super80.py --company "TAKEDA"
    python run_super80.py --output super80_results.json
"""

import argparse
import os
import sys
import json
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from runner import (get_client, run_account, print_summary,
                    save_incremental, load_checkpoint, C)
from prompts import CATEGORY_TRIGGERS, DAYS_BACK
from accounts import SUPER80, get_category

DEFAULT_OUTPUT = "super80_results.json"

parser = argparse.ArgumentParser(description="Thomas Scientific — Super80 Priority Accounts")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
args = parser.parse_args()

client = get_client(args.api_key)

# Build run list
accounts = SUPER80
if args.company:
    query = args.company.upper()
    accounts = [a for a in SUPER80 if query in a.upper()]
    if not accounts:
        print(f"ERROR: '{args.company}' not found in Super80.")
        sys.exit(1)

# Resume from checkpoint
all_results = load_checkpoint(args.output) if args.output else []
completed = {r["account"].upper() for r in all_results}
if completed:
    print(f"\033[93m⚡ Resuming — {len(completed)} accounts already done.\033[0m")
pending = [a for a in accounts if a.upper() not in completed]

print(f"\n\033[1mThomas Scientific // Super80 Priority Accounts\033[0m")
print(f"\033[90m{len(pending)} accounts | Last {DAYS_BACK} days\033[0m")

for account in pending:
    category = get_category(account)
    signals = [args.signal] if args.signal else CATEGORY_TRIGGERS[category]
    run_account(client, account, category, signals,
                output_file=args.output, all_results=all_results)

print_summary(all_results)

if args.output:
    save_incremental(all_results, args.output)
    print(f"\033[90mFinal results saved to {args.output}\033[0m\n")
