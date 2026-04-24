"""
Run market intelligence for Clinical / Mol Dx accounts.
Signals: grant, capital, contract, pipeline, expansion, partnership, funding, project, regulatory

Usage:
    python run_clinical_dx.py
    python run_clinical_dx.py --signal pipeline
    python run_clinical_dx.py --company "LABCORP"
    python run_clinical_dx.py --output clinical_dx_results.json
"""

import argparse
from runner import run_category

CATEGORY = "Clinical / Mol Dx"
DEFAULT_OUTPUT = "clinical_dx_results.json"

parser = argparse.ArgumentParser(description=f"Thomas Scientific — {CATEGORY}")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
parser.add_argument("--limit", "-n", type=int, default=None, help="Max accounts to run (e.g. 5)")
args = parser.parse_args()

run_category(CATEGORY, args.output, args.signal, args.company, args.api_key, args.limit)
