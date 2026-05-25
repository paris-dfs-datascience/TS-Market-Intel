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
import os
import sys

from accounts import ACCOUNTS, SUPER80, all_accounts_flat, load_accounts_from_csv
from engine import run_category, setup_logger
from storage import get_sink


logger = setup_logger()


CATEGORIES = list(ACCOUNTS.keys())

# Slug → canonical category name. Also accepts the canonical names directly.
CATEGORY_SLUGS = {
    "education":   "Education & Research",
    "biopharma":   "BioPharma",
    "cdmo_cro":    "CDMO / CRO",
    "clinical_dx": "Clinical / Molecular Diagnostics",
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
    p.add_argument("--companies", default=None,
                   help="Comma-separated list of company names to run (case-insensitive)")
    p.add_argument("--limit", type=int, default=None,
                   help="Limit to first N pending accounts per category (useful for testing)")
    p.add_argument("--total-limit", type=int, default=None,
                   help="Cap total accounts run across all categories (use with --category all)")
    p.add_argument("--super80", action="store_true",
                   help="Run only the Super80 priority accounts across verticals")
    p.add_argument("--export-csv", action="store_true",
                   help="Run the SF CSV export only (reads result JSONs from the configured sink; "
                        "writes _export/market_intel_export_<DATE>.csv). Skips the engine.")
    p.add_argument("--export-date", default=None, metavar="YYYY-MM-DD",
                   help="Date to filter result JSONs by (default: today UTC). "
                        "Only meaningful with --export-csv.")
    p.add_argument("--api-key", default=None,
                   help="Gemini API key (overrides GEMINI_API_KEY env var)")
    p.add_argument("--from-csv", default=None, metavar="PATH",
                   help="Load accounts from a CSV export of SalesForce.Account_base "
                        "(filters Customer80/Super80, maps segment_raw to prompt verticals). "
                        "Overrides --category all when set. Env var: ACCOUNTS_CSV_PATH.")
    p.add_argument("--analyze-dedup", default=None, metavar="DATE",
                   help="One-off dedup analysis on _export/market_intel_export_<DATE>.csv. "
                        "Writes dedup_4a_*, dedup_4b_*, dedup_analysis_* artifacts back to "
                        "_export/ and exits. Skips the engine.")
    p.add_argument("--backfill", default=None, metavar="DATE",
                   help="One-off backfill on existing results_<DATE>.json files: normalize "
                        "event_date to YYYY-MM-DD, generate ai_summary for any account "
                        "missing it. Pass 'all' to backfill every dated result in the sink. "
                        "Idempotent — files already containing ai_summary are skipped.")
    p.add_argument("--fix-urls", default=None, metavar="DATE",
                   help="HEAD-validate every source_url in results_<DATE>.json files; for "
                        "any URL that 4xx's or fails to load, re-ask Gemini (with grounding) "
                        "for the canonical URL and write back. Idempotent — files with "
                        "urls_fixed=true are skipped.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    sink = get_sink()

    # --export-csv: skip the engine entirely; just regenerate the SF CSV from existing results in the sink.
    if args.export_csv:
        from export_csv import run_export
        run_export(sink, args.export_date)
        return

    # --analyze-dedup: one-off dedup analysis on an existing CSV. Skips the engine.
    if args.analyze_dedup:
        from analyze_dedup import run as run_dedup
        run_dedup(args.analyze_dedup)
        return

    # --backfill: one-off re-processing of existing results JSONs. Skips the engine.
    if args.backfill:
        from backfill_results import run_backfill
        run_backfill(sink, args.backfill, api_key=args.api_key)
        return

    # --fix-urls: one-off URL recovery (HEAD-validate + re-ask Gemini on 404s).
    if args.fix_urls:
        from backfill_results import run_url_backfill
        run_url_backfill(sink, args.fix_urls, api_key=args.api_key)
        return

    # --from-csv (or ACCOUNTS_CSV_PATH env var): load accounts from Salesforce CSV export.
    # Runs all verticals found in the CSV; respects --limit, --total-limit, and --signal.
    csv_path = args.from_csv or os.environ.get("ACCOUNTS_CSV_PATH")
    if csv_path and not any([args.company, args.companies, args.super80]):
        csv_accounts = load_accounts_from_csv(csv_path)
        if not csv_accounts:
            logger.error(f"No Customer80/Super80 accounts found in '{csv_path}'. Check the CSV and SEGMENT_RAW_MAP.")
            sys.exit(1)
        remaining = args.total_limit
        for vertical, acct_list in csv_accounts.items():
            if remaining is not None and remaining <= 0:
                break
            cat_limit = remaining if remaining is not None else args.limit
            ran = run_category(vertical, sink, signal_override=args.signal,
                               api_key=args.api_key, limit=cat_limit,
                               accounts_override=acct_list)
            if remaining is not None:
                remaining -= ran
        return

    if args.companies:
        queries = [q.strip().upper() for q in args.companies.split(",") if q.strip()]
        flat = all_accounts_flat()
        # Group matched accounts by category so each category runs in one batch
        by_cat: dict[str, list[str]] = {}
        missing = []
        for query in queries:
            matches = [(acct, cat) for acct, cat in flat if acct.upper() == query]
            if not matches:
                missing.append(query)
            for acct, cat in matches:
                by_cat.setdefault(cat, []).append(acct)
        if missing:
            logger.error(
                f"No account exactly matches: {', '.join(missing)}. "
                f"Names are case-insensitive but must be complete."
            )
            sys.exit(1)
        for cat, accts in by_cat.items():
            run_category(cat, sink, signal_override=args.signal,
                         accounts_override=accts,
                         api_key=args.api_key, limit=args.limit)
        return

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
        remaining = args.total_limit
        for cat in CATEGORIES:
            if remaining is not None and remaining <= 0:
                break
            cat_limit = remaining if remaining is not None else args.limit
            ran = run_category(cat, sink, signal_override=args.signal,
                               api_key=args.api_key, limit=cat_limit)
            if remaining is not None:
                remaining -= ran
        # Auto-export: generate the SF-import CSV from every account result just written.
        from export_csv import run_export
        logger.info("All categories complete — generating SF export CSV.")
        try:
            run_export(sink)
        except Exception as e:
            logger.error(f"Auto-export failed (run finished, but CSV not generated): {e}")
        return

    run_category(_resolve_category(args.category), sink,
                 signal_override=args.signal,
                 api_key=args.api_key, limit=args.limit)


if __name__ == "__main__":
    main()
