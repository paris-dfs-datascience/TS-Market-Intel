"""
Run market intelligence for BioPharma accounts.
Signals: grant, capital, contract, pipeline, expansion, partnership, funding, project, regulatory, hiring

Usage:
    python run_biopharma.py
    python run_biopharma.py --signal pipeline
    python run_biopharma.py --company "PFIZER"
    python run_biopharma.py --output biopharma_results.json
"""

import argparse
from runner import run_category

CATEGORY = "BioPharma"
DEFAULT_OUTPUT = "biopharma_results.json"

parser = argparse.ArgumentParser(description=f"Thomas Scientific — {CATEGORY}")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
parser.add_argument("--limit", "-n", type=int, default=None, help="Max accounts to run")
args = parser.parse_args()

run_category(CATEGORY, args.output, args.signal, args.company, args.api_key, args.limit)
