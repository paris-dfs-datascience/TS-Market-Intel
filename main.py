"""
main.py — Single entrypoint for the Thomas Scientific Market Intelligence pipeline.

Dispatches a run by category, company, or Super80 subset to engine.run_category().
Each account's results land at `<SAFE_COMPANY>/results.json` under the sink's root
(local `OUTPUT_DIR` or Azure Blob container — see storage.py).

Usage:
  python main.py --category biopharma
  python main.py --category all
  python main.py --super80
  python main.py --company "YALE UNIVERSITY"
  python main.py --category biopharma --signal pipeline --limit 5
"""

import argparse
import sys

from accounts import ACCOUNTS, SUPER80, all_accounts_flat
from engine import run_category, setup_logger
from storage import get_sink


logger = setup_logger()


CATEGORIES = list(ACCOUNTS.keys())

# Slug → canonical category name. Also accepts the canonical names directly.
CATEGORY_SLUGS = {
    "education":   "Education & Research",
    "biopharma":   "BioPharma",
    "cdmo_cro":    "CDMO / CRO",
    "clinical_dx": "Clinical / Mol Dx",
    "hospital":    "Hospital & Health Systems",
    "industrial":  "Industrial",
    "government":  "Government",
}
CATEGORY_CHOICES = list(CATEGORY_SLUGS.keys()) + CATEGORIES + ["all"]


def _resolve_category(value: str) -> str:
    return CATEGORY_SLUGS.get(value, value)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Thomas Scientific Market Intelligence")
    p.add_argument("--category", choices=CATEGORY_CHOICES, default="all",
                   help="Industry vertical to run (slug or canonical name; default: all)")
    p.add_argument("--signal", default=None,
                   help="Run a single signal type only (e.g. grant, pipeline)")
    p.add_argument("--company", default=None,
                   help="Run a single company by exact name (case-insensitive)")
    p.add_argument("--limit", type=int, default=None,
                   help="Limit to first N pending accounts (useful for testing)")
    p.add_argument("--super80", action="store_true",
                   help="Run only the Super80 priority accounts across verticals")
    p.add_argument("--api-key", default=None,
                   help="Gemini API key (overrides GEMINI_API_KEY env var)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    sink = get_sink()

    if args.company:
        # Exact, case-insensitive match — avoids silent over-match on substrings
        query = args.company.upper()
        matches = [(acct, cat) for acct, cat in all_accounts_flat()
                   if acct.upper() == query]
        if not matches:
            logger.error(
                f"No account exactly matches '{args.company}'. "
                f"Names are case-insensitive but must be complete (e.g. 'YALE UNIVERSITY')."
            )
            sys.exit(1)
        for acct, cat in matches:
            run_category(cat, sink, signal_override=args.signal,
                         accounts_override=[acct],
                         api_key=args.api_key, limit=args.limit)
        return

    if args.super80:
        for cat, accts in ACCOUNTS.items():
            priority = [a for a in accts if a in SUPER80]
            if not priority:
                continue
            run_category(cat, sink, signal_override=args.signal,
                         api_key=args.api_key, limit=args.limit,
                         accounts_override=priority)
        return

    if args.category == "all":
        for cat in CATEGORIES:
            run_category(cat, sink, signal_override=args.signal,
                         api_key=args.api_key, limit=args.limit)
        return

    run_category(_resolve_category(args.category), sink,
                 signal_override=args.signal,
                 api_key=args.api_key, limit=args.limit)


if __name__ == "__main__":
    main()
