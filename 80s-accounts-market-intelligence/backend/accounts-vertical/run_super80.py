"""
Run market intelligence for Super80 priority accounts (async parallel execution).
Each account uses its own category's signals automatically.

Usage:
    python run_super80.py
    python run_super80.py --signal pipeline
    python run_super80.py --company TAKEDA
    python run_super80.py --output super80_results.json
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
from accounts import SUPER80, get_category

DEFAULT_OUTPUT = "super80_results.json"

parser = argparse.ArgumentParser(description="Thomas Scientific — Super80 Priority Accounts")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
args = parser.parse_args()

# Filter to a single account if specified
accounts = SUPER80
if args.company:
    query = args.company.upper()
    accounts = [a for a in SUPER80 if query in a.upper()]
    if not accounts:
        print(f"ERROR: '{args.company}' not found in Super80.")
        sys.exit(1)

# Group accounts by category — run_category() handles one category at a time
by_category = defaultdict(list)
for account in accounts:
    by_category[get_category(account)].append(account)

# Run each category group using async runner (all signals in parallel per account)
for category, cat_accounts in by_category.items():
    run_category(category, args.output, args.signal, args.company, args.api_key,
                 accounts_override=cat_accounts)
