"""
build_master_csv.py — Build master Salesforce CSV from all per-account output files.

Scans output/*/results.json, extracts all signals, and writes:
  - output/master_signals_salesforce.csv  (8 SF fields)
  - output/master_signals_full.csv        (all fields)

Re-run this after every new account to keep the master file up to date.

Usage:
    python build_master_csv.py
    python build_master_csv.py --output-dir ./output  # custom output dir
"""

import argparse
import csv
import json
import os
from pathlib import Path
from datetime import datetime

SF_FIELDS = [
    "account",
    "signal_type",
    "industry_category",
    "summary",
    "why_it_matters",
    "event_date",
    "source_url",
    "run_date",
]


def load_all_results(output_dir: Path) -> list[dict]:
    """Scan output/*/results.json and return flat list of all signal rows."""
    rows = []
    account_dirs = sorted([d for d in output_dir.iterdir() if d.is_dir() and not d.name.startswith("_")])

    for acct_dir in account_dirs:
        results_file = acct_dir / "results.json"
        if not results_file.exists():
            continue
        try:
            with open(results_file) as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠ Could not read {results_file}: {e}")
            continue

        account = data.get("account", acct_dir.name.replace("_", " "))
        for sig_type, sigs in data.get("signals", {}).items():
            for sig in sigs:
                rows.append(sig)

    return rows


def write_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    """Write rows to CSV with given fields."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Build master Salesforce CSV from all account outputs")
    parser.add_argument("--output-dir", default="output", help="Base output directory (default: ./output)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.exists():
        print(f"ERROR: output directory '{output_dir}' not found.")
        return

    print(f"Scanning {output_dir} for account results...")
    rows = load_all_results(output_dir)

    if not rows:
        print("No signals found. Run some accounts first.")
        return

    # Count by account
    from collections import Counter
    counts = Counter(r.get("account", "?") for r in rows)
    for acct, n in sorted(counts.items()):
        print(f"  {acct}: {n} signals")

    print(f"\nTotal: {len(rows)} signals across {len(counts)} accounts")

    # Write Salesforce CSV (8 fields)
    sf_path = output_dir / "master_signals_salesforce.csv"
    write_csv(rows, sf_path, SF_FIELDS)
    print(f"\n✅ Salesforce CSV → {sf_path}")

    # Write full CSV (all fields)
    all_fields = list(SF_FIELDS)
    extra_fields = sorted({k for r in rows for k in r.keys() if k not in SF_FIELDS})
    all_fields += extra_fields
    full_path = output_dir / "master_signals_full.csv"
    write_csv(rows, full_path, all_fields)
    print(f"✅ Full CSV        → {full_path}")
    print(f"\nLast updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
