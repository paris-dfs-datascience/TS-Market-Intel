"""
Run market intelligence for CDMO / CRO accounts.
Signals: capital, contract, pipeline, expansion, partnership, funding, project, regulatory, hiring

Usage:
    python run_cdmo_cro.py
    python run_cdmo_cro.py --signal regulatory
    python run_cdmo_cro.py --company "CHARLES RIVER LABS"
    python run_cdmo_cro.py --output cdmo_cro_results.json
"""

import argparse
from runner import run_category

CATEGORY = "CDMO / CRO"
DEFAULT_OUTPUT = "cdmo_cro_results.json"

parser = argparse.ArgumentParser(description=f"Thomas Scientific — {CATEGORY}")
parser.add_argument("--signal", "-s", default=None, help="Run a single signal only")
parser.add_argument("--company", "-c", default=None, help="Run for a single account")
parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output JSON file")
parser.add_argument("--api-key", "-k", default=None)
args = parser.parse_args()

run_category(CATEGORY, args.output, args.signal, args.company, args.api_key)
