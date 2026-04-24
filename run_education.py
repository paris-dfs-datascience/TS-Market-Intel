"""
Run market intelligence for Education & Research accounts.
Signals: grant, faculty, capital, contract, expansion, funding, project

Usage:
    python run_education.py
    python run_education.py --signal grant
    python run_education.py --company "HARVARD UNIVERSITY"
    python run_education.py --output education_results.json
"""

import argparse
from runner import run_category

CATEGORY = "Education & Research"
DEFAULT_OUTPUT = "education_results.json"

parser = argparse.ArgumentParser(description=f"Thomas Scientific — {CATEGORY}")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
parser.add_argument("--limit", "-n", type=int, default=None, help="Max accounts to run")
args = parser.parse_args()

run_category(CATEGORY, args.output, args.signal, args.company, args.api_key, args.limit)
