"""
run_all_accounts.py — Run market intelligence for all (or filtered) accounts.

Delegates to runner.run_category() for async parallel signal execution.
All signals for each account fire concurrently — ~11× faster than the old sync version.

Usage:
    python run_all_accounts.py                              # all categories, per-category output files
    python run_all_accounts.py --category "BioPharma"      # single category
    python run_all_accounts.py --company "PFIZER"          # single account across all categories
    python run_all_accounts.py --company "PFIZER" --signal pipeline
    python run_all_accounts.py --super80                   # Super80 priority accounts only
    python run_all_accounts.py --output results.json       # write all results to one file
"""

import argparse
import sys
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from runner import run_category
from accounts import ACCOUNTS, SUPER80, get_category, all_accounts_flat

CATEGORIES = list(ACCOUNTS.keys())

parser = argparse.ArgumentParser(description="Thomas Scientific — 80s Accounts Market Intel")
parser.add_argument("--category", "-cat", default=None, choices=CATEGORIES,
                    help="Run for one industry category")
parser.add_argument("--signal", "-s", default=None,
                    help="Run a single signal only")
parser.add_argument("--company", "-c", default=None,
                    help="Run for a single account (substring match)")
parser.add_argument("--super80", action="store_true",
                    help="Run Super80 priority accounts only")
parser.add_argument("--output", "-o", default=None,
                    help="Write all results to one file (default: per-category files)")
parser.add_argument("--api-key", "-k", default=None)
args = parser.parse_args()


def _output_for(category: str) -> str:
    """Return per-category output file, or shared --output if specified."""
    if args.output:
        return args.output
    safe = category.lower().replace(" ", "_").replace("/", "_")
    return f"{safe}_results.json"


if args.company:
    # Find which categories this company belongs to, run each
    by_cat = defaultdict(list)
    for acct, cat in all_accounts_flat():
        if args.company.upper() in acct.upper():
            by_cat[cat].append(acct)
    if not by_cat:
        print(f"ERROR: No accounts matched '{args.company}'.")
        sys.exit(1)
    for cat, accts in by_cat.items():
        run_category(cat, _output_for(cat), args.signal, args.company, args.api_key)

elif args.super80:
    # Group Super80 accounts by category, run each group
    by_cat = defaultdict(list)
    for acct in SUPER80:
        by_cat[get_category(acct)].append(acct)
    for cat, accts in by_cat.items():
        run_category(cat, _output_for(cat), args.signal, None, args.api_key,
                     accounts_override=accts)

elif args.category:
    # Single category
    run_category(args.category, _output_for(args.category), args.signal, None, args.api_key)

else:
    # All categories sequentially (each using async parallel signals internally)
    for cat in CATEGORIES:
        run_category(cat, _output_for(cat), args.signal, None, args.api_key)
