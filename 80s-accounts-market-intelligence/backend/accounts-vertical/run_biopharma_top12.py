"""
Run market intelligence for top 12 BioPharma accounts (async parallel execution).
Accounts: ABBVIE, ASTRAZENECA, ELI LILLY, MERCK, MODERNA, PFIZER,
          REGENERON, ROCHE GROUP, SANOFI, TAKEDA, VERTEX PHARMACEUTICAL, NOVONESIS

Usage:
    python run_biopharma_top12.py
    python run_biopharma_top12.py --signal pipeline
    python run_biopharma_top12.py --company PFIZER
    python run_biopharma_top12.py --output biopharma_top12_results.json
"""

import argparse
from runner import run_category

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

parser = argparse.ArgumentParser(description=f"Thomas Scientific — {CATEGORY} Top 12")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
args = parser.parse_args()

run_category(CATEGORY, args.output, args.signal, args.company, args.api_key,
             accounts_override=TOP12)
