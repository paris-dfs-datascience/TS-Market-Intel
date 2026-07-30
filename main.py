"""
main.py — Single entrypoint for the Thomas Scientific Market Intelligence pipeline.

Dispatches a run by category, company, or Super80 subset to engine.run_category().
Each account's results land at `<SAFE_COMPANY>/results.json` under the sink's root
(local `OUTPUT_DIR` or Azure Blob container — see storage.py).

Usage:
  python main.py --category biopharma
  python main.py --category all
  python main.py --categories "biopharma,cdmo_cro,education,hospital,industrial"
  python main.py --super80
  python main.py --company "YALE UNIVERSITY"
  python main.py --category biopharma --signal pipeline --limit 5
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from market_intel.accounts import ACCOUNTS, SUPER80, all_accounts_flat, load_accounts_from_csv
from market_intel.engine import run_category, setup_logger
from market_intel.storage import get_sink


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


# The two monthly scheduled runs, keyed by UTC day-of-month. Single source of truth for
# the scope of each run: the `--categories=` arg stored in each Azure scheduled job's
# template must match these strings, and --monthly-schedule reads them directly.
# Education & Research appears in both on purpose — it is re-run mid-month.
# Changing a set here means updating the Azure job args too (see DEPLOYMENT.md).
MONTHLY_SCHEDULE = {
    1:  "biopharma,cdmo_cro,education,hospital,industrial",
    15: "education,clinical_dx,government",
}


# Case-insensitive token → canonical category, accepting slugs and canonical names alike.
# --categories bypasses argparse `choices` (it arrives as one comma-joined string), so it
# resolves tokens through here instead.
_CATEGORY_LOOKUP = {c.lower(): c for c in CATEGORIES}
_CATEGORY_LOOKUP.update(CATEGORY_SLUGS)


def _resolve_category_list(value: str) -> list[str]:
    """Parse a comma-separated --categories value into canonical category names.

    Order-preserving and deduped. Hard-fails on an unrecognized token rather than
    skipping it — a typo in a scheduled job's args would otherwise silently shrink the
    month's run and still exit 0, which reads as a clean run that quietly missed a vertical.
    """
    names: list[str] = []
    unknown: list[str] = []
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        cat = _CATEGORY_LOOKUP.get(token.lower())
        if cat is None:
            unknown.append(token)
        elif cat not in names:
            names.append(cat)
    if unknown:
        logger.error(
            f"Unrecognized --categories value(s): {', '.join(unknown)}. "
            f"Valid slugs: {', '.join(CATEGORY_SLUGS)}."
        )
        sys.exit(1)
    if not names:
        logger.error("--categories was empty — pass at least one category slug or name.")
        sys.exit(1)
    return names


def _select_verticals(available: list[str], selected: list[str] | None,
                      source_label: str) -> list[str]:
    """Intersect the verticals a source produced with an explicit --categories list.

    Returns `available` unchanged when no --categories filter is in play. Ordered by the
    --categories list so the run order matches what was asked for.
    """
    if not selected:
        return available
    ordered = [v for v in selected if v in available]
    missing = [v for v in selected if v not in available]
    if missing:
        logger.warning(
            f"--categories asked for {', '.join(missing)}, but {source_label} returned "
            f"no accounts for those verticals — skipping them."
        )
    if not ordered:
        logger.error(f"None of the --categories verticals are present in {source_label}.")
        sys.exit(1)
    return ordered


def _run_verticals(verticals: list[str], sink, args, run_date: str,
                   accounts_by_vertical: dict | None = None) -> None:
    """Run each vertical in turn under one shared --total-limit budget.

    accounts_by_vertical: optional {vertical: [accounts]} from --from-sql / --from-csv.
    When omitted, run_category falls back to the baked-in ACCOUNTS list per vertical.
    """
    remaining = args.total_limit
    for vertical in verticals:
        if remaining is not None and remaining <= 0:
            break
        cat_limit = remaining if remaining is not None else args.limit
        override = accounts_by_vertical.get(vertical) if accounts_by_vertical else None
        ran = run_category(vertical, sink, signal_override=args.signal,
                           api_key=args.api_key, limit=cat_limit,
                           accounts_override=override, run_date=run_date)
        if remaining is not None:
            remaining -= ran


def _run_and_finalize(verticals: list[str], sink, args, run_date: str,
                      accounts_by_vertical: dict | None = None) -> None:
    """Run the verticals, then export — even when the run aborts partway through.

    The engine aborts the whole run on sustained Gemini quota exhaustion rather than
    skipping the signal, because skipping would checkpoint the account with empty signals
    and a later re-run would drop its data for good. Everything finished before the abort
    is already checkpointed, so the SF CSV should still be produced for it — otherwise an
    unattended monthly run that dies in its last vertical leaves no deliverable at all.

    Both abort flavors are caught: `RuntimeError` (engine re-raises the "quota exhausted
    after retries" one, which would otherwise surface as a raw traceback in the Azure
    logs) and `SystemExit` (engine's own sys.exit on "credits depleted"). The non-zero
    exit is preserved either way, so the Container Apps job still reports Failed.
    """
    aborted: BaseException | None = None
    try:
        _run_verticals(verticals, sink, args, run_date, accounts_by_vertical)
    except (RuntimeError, SystemExit) as e:
        aborted = e
        logger.critical(
            f"Run aborted before completing all verticals: {e} — exporting what "
            f"completed, then exiting non-zero."
        )
    _finalize_run(sink, run_date, api_key=args.api_key, fix_urls=aborted is None)
    if aborted is not None:
        sys.exit(1)


def _print_account_listing(source_label: str, accounts_by_vertical: dict) -> None:
    """--dry-run helper: print the accounts that would be processed, no signals fired.

    Tolerates both account shapes the loaders produce: SQL/CSV yield
    [{"name": ..., "parent_id": ...}, ...]; the --companies path yields plain
    name strings.
    """
    def _name(item):
        return item["name"] if isinstance(item, dict) else item
    total = sum(len(v) for v in accounts_by_vertical.values())
    print(f"\n[DRY RUN] Account retrieval from {source_label} — no signals will be generated.")
    print(f"{total} accounts across {len(accounts_by_vertical)} verticals:\n")
    for vertical, acct_list in accounts_by_vertical.items():
        print(f"  {vertical}  ({len(acct_list)})")
        for item in acct_list:
            print(f"    - {_name(item)}")
    print(f"\n[DRY RUN] Total: {total} accounts. Stopping before signal generation.")


def _finalize_run(sink, run_date: str, api_key: str | None = None,
                  fix_urls: bool = True) -> None:
    """End-of-run finalization for a full run: validate/repair source URLs, then
    auto-export the SF CSV. Both steps are best-effort — a failure is logged but
    never blocks the rest (the run itself already succeeded and is checkpointed).

    URL validation re-asks Gemini for any dead URL, so it adds time + a few API
    calls at the tail. Disable it without a code change by setting AUTO_FIX_URLS=0.

    fix_urls: pass False to skip URL repair and go straight to the export. Used on the
              quota-abort path — repair re-asks Gemini, and the quota is exactly what
              just died, so every dead URL would burn the full retry ladder for nothing.
    """
    if fix_urls and os.environ.get("AUTO_FIX_URLS", "1").strip().lower() not in ("0", "false", "no", "off"):
        from tools.backfill_results import run_url_backfill
        logger.info("Run complete — validating/repairing source URLs before export.")
        try:
            run_url_backfill(sink, run_date, api_key=api_key)
        except Exception as e:
            logger.error(f"Auto URL-fix failed (continuing to export): {e}")
    from market_intel.export_csv import run_export
    logger.info("Generating SF export CSV.")
    try:
        run_export(sink, run_date)
    except Exception as e:
        logger.error(f"Auto-export failed (run finished, but CSV not generated): {e}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Thomas Scientific Market Intelligence")
    p.add_argument("--category", choices=CATEGORY_CHOICES, default=None,
                   help="Industry vertical to run (slug or canonical name; default: all)")
    p.add_argument("--categories", default=None,
                   help="Comma-separated list of verticals to run, e.g. "
                        "'biopharma,cdmo_cro,education'. Slugs or canonical names, "
                        "case-insensitive. Runs them in the order given, then finalizes "
                        "(URL repair + SF CSV export) exactly like --category all. "
                        "Mutually exclusive with --category. Used by the scheduled Azure jobs.")
    p.add_argument("--monthly-schedule", action="store_true",
                   help="Pick the vertical set from the UTC day-of-month per MONTHLY_SCHEDULE "
                        "(1st: everything except Clinical/Mol Dx + Government; 15th: Education, "
                        "Clinical/Mol Dx, Government). Exits without running on any other day. "
                        "For driving both monthly runs from a single cron ('0 6 1,15 * *') when "
                        "two separate scheduled jobs aren't an option.")
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
    p.add_argument("--from-sql", action="store_true",
                   help="Load accounts from SalesForce.Account_base in Azure SQL via the "
                        "container's Managed Identity (no connection string secrets — token "
                        "auth via DefaultAzureCredential). Requires AZURE_SQL_SERVER and "
                        "AZURE_SQL_DATABASE env vars; uses the same MI bound for Key Vault. "
                        "Hard-fails if the connection or query fails. "
                        "Also enabled by ACCOUNTS_SOURCE=sql.")
    p.add_argument("--dry-run", action="store_true",
                   help="Load and print the accounts that would be processed, then exit "
                        "BEFORE any signals are generated (no Gemini calls, no cost). "
                        "Use with --from-sql / --from-csv to test account retrieval only.")
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

    if args.category and args.categories:
        logger.error(
            "Pass either --category (one vertical, or 'all') or --categories "
            "(a comma-separated list), not both."
        )
        sys.exit(1)
    category = args.category or "all"
    # Resolved up front so a bad --categories value fails before any Gemini call or
    # SQL connection — a scheduled run should die in the first second, not an hour in.
    selected = _resolve_category_list(args.categories) if args.categories else None

    if args.monthly_schedule:
        if args.category or args.categories:
            logger.error(
                "--monthly-schedule chooses the verticals itself — don't combine it with "
                "--category or --categories."
            )
            sys.exit(1)
        day = datetime.now(timezone.utc).day
        if day not in MONTHLY_SCHEDULE:
            scheduled_days = ", ".join(str(d) for d in MONTHLY_SCHEDULE)
            logger.info(
                f"--monthly-schedule: today is day {day} UTC and no run is scheduled for it "
                f"(scheduled days: {scheduled_days}). Exiting 0 without running anything."
            )
            return
        selected = _resolve_category_list(MONTHLY_SCHEDULE[day])
        logger.info(f"--monthly-schedule: day {day} UTC → {', '.join(selected)}")

    sink = get_sink()

    # One UTC date for the whole process. Threaded into every run_category (result
    # filenames + checkpoint reads) and the auto-export, so a run — including all
    # categories of --category all — stamps a single date and the export matches it.
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --export-csv: skip the engine entirely; just regenerate the SF CSV from existing results in the sink.
    if args.export_csv:
        from market_intel.export_csv import run_export
        run_export(sink, args.export_date)
        return

    # --analyze-dedup: one-off dedup analysis on an existing CSV. Skips the engine.
    if args.analyze_dedup:
        from tools.analyze_dedup import run as run_dedup
        run_dedup(args.analyze_dedup)
        return

    # --backfill: one-off re-processing of existing results JSONs. Skips the engine.
    if args.backfill:
        from tools.backfill_results import run_backfill
        run_backfill(sink, args.backfill, api_key=args.api_key)
        return

    # --fix-urls: one-off URL recovery (HEAD-validate + re-ask Gemini on 404s).
    if args.fix_urls:
        from tools.backfill_results import run_url_backfill
        run_url_backfill(sink, args.fix_urls, api_key=args.api_key)
        return

    # --from-sql (or ACCOUNTS_SOURCE=sql env var): load accounts from Azure SQL.
    # Single-company / --super80 modes still use the hardcoded ACCOUNTS dict; only the
    # bulk vertical-driven flows switch to SQL.
    use_sql = args.from_sql or os.environ.get("ACCOUNTS_SOURCE", "").lower() == "sql"
    if use_sql and not any([args.company, args.companies, args.super80]):
        from market_intel.accounts_sql import load_accounts_from_sql, SqlAccountsError
        try:
            sql_accounts = load_accounts_from_sql()
        except SqlAccountsError as e:
            logger.error(f"SQL account load failed (hard-fail by design): {e}")
            sys.exit(1)
        logger.info(
            f"Loaded {sum(len(v) for v in sql_accounts.values())} accounts from "
            f"SalesForce.Account_base across {len(sql_accounts)} verticals."
        )
        verticals = _select_verticals(list(sql_accounts), selected, "Azure SQL")
        if args.dry_run:
            _print_account_listing("Azure SQL (SalesForce.Account_base)",
                                   {v: sql_accounts[v] for v in verticals})
            return
        # End-of-run: validate/repair URLs, then auto-export the SF CSV — still exports if
        # the run aborts on quota partway through.
        _run_and_finalize(verticals, sink, args, run_date, sql_accounts)
        return

    # --from-csv (or ACCOUNTS_CSV_PATH env var): load accounts from Salesforce CSV export.
    # Runs all verticals found in the CSV; respects --limit, --total-limit, and --signal.
    csv_path = args.from_csv or os.environ.get("ACCOUNTS_CSV_PATH")
    if csv_path and not any([args.company, args.companies, args.super80]):
        csv_accounts = load_accounts_from_csv(csv_path)
        if not csv_accounts:
            logger.error(f"No Customer80/Super80 accounts found in '{csv_path}'. Check the CSV and SEGMENT_RAW_MAP.")
            sys.exit(1)
        verticals = _select_verticals(list(csv_accounts), selected, f"CSV ({csv_path})")
        if args.dry_run:
            _print_account_listing(f"CSV ({csv_path})",
                                   {v: csv_accounts[v] for v in verticals})
            return
        _run_verticals(verticals, sink, args, run_date, csv_accounts)
        return

    # --dry-run for the hardcoded-ACCOUNTS modes. The --from-sql / --from-csv
    # branches above already handled --dry-run and returned; this guarantees the
    # flag never fires signals in any remaining mode either.
    if args.dry_run:
        if args.companies:
            queries = {q.strip().upper() for q in args.companies.split(",") if q.strip()}
            listing: dict[str, list] = {}
            for acct, cat in all_accounts_flat():
                if acct.upper() in queries:
                    listing.setdefault(cat, []).append(acct)
            _print_account_listing("hardcoded ACCOUNTS (--companies)", listing)
        elif args.company:
            query = args.company.upper()
            listing = {}
            for acct, cat in all_accounts_flat():
                if acct.upper() == query:
                    listing.setdefault(cat, []).append(acct)
            _print_account_listing("hardcoded ACCOUNTS (--company)", listing)
        elif args.super80:
            listing = {cat: [a for a in accts if a in SUPER80]
                       for cat, accts in ACCOUNTS.items()}
            listing = {cat: accts for cat, accts in listing.items() if accts}
            _print_account_listing("hardcoded ACCOUNTS (--super80)", listing)
        elif selected:
            _print_account_listing("hardcoded ACCOUNTS (--categories)",
                                   {c: ACCOUNTS.get(c, []) for c in selected})
        elif category == "all":
            _print_account_listing("hardcoded ACCOUNTS (--category all)", dict(ACCOUNTS))
        else:
            cat = _resolve_category(category)
            _print_account_listing("hardcoded ACCOUNTS", {cat: ACCOUNTS.get(cat, [])})
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
                         api_key=args.api_key, limit=args.limit, run_date=run_date)
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
                         api_key=args.api_key, limit=args.limit, run_date=run_date)
        return

    if args.super80:
        for cat, accts in ACCOUNTS.items():
            priority = [a for a in accts if a in SUPER80]
            if not priority:
                continue
            run_category(cat, sink, signal_override=args.signal,
                         api_key=args.api_key, limit=args.limit,
                         accounts_override=priority, run_date=run_date)
        return

    # --categories: an explicit subset of verticals. Treated as a complete run — it
    # finalizes (URL repair + CSV export) like --category all, because that is what the
    # scheduled monthly jobs need to leave behind.
    if selected:
        logger.info(f"Running {len(selected)} vertical(s): {', '.join(selected)}")
        _run_and_finalize(selected, sink, args, run_date)
        return

    if category == "all":
        # End-of-run: validate/repair URLs, then auto-export the SF CSV — still exports if
        # the run aborts on quota partway through.
        _run_and_finalize(CATEGORIES, sink, args, run_date)
        return

    run_category(_resolve_category(category), sink,
                 signal_override=args.signal,
                 api_key=args.api_key, limit=args.limit, run_date=run_date)


if __name__ == "__main__":
    main()
