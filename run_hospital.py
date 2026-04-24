"""
Run market intelligence for Hospital & Health Systems accounts.
Signals: grant, faculty, capital, contract, pipeline, expansion, partnership, funding, project, regulatory, tender

Usage:
    python run_hospital.py
    python run_hospital.py --signal tender
    python run_hospital.py --company "MAYO"
    python run_hospital.py --output hospital_results.json
"""

import argparse
from runner import run_category

CATEGORY = "Hospital & Health Systems"
DEFAULT_OUTPUT = "hospital_results.json"

parser = argparse.ArgumentParser(description=f"Thomas Scientific — {CATEGORY}")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
parser.add_argument("--limit", "-n", type=int, default=None, help="Max accounts to run (e.g. 5)")
args = parser.parse_args()

run_category(CATEGORY, args.output, args.signal, args.company, args.api_key, args.limit)
