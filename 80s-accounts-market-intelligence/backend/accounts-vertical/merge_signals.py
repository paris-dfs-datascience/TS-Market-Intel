"""
merge_signals.py — Integrates 80s Accounts signals with NIH/NSF pipeline data.

Sources:
  - 80s Accounts: government_results.json, biopharma_top12_results.json (+ others as they complete)
  - NIH Pipeline: nih_grants_nci_r01_2024.json, jhu_nsf_grants_all.json

Output: unified_signals.json

Usage:
    python merge_signals.py
    python merge_signals.py --output my_output.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    from accounts import ACCOUNTS as MASTER_ACCOUNTS, ACCOUNT_ALIASES
except ImportError:
    MASTER_ACCOUNTS = {}
    ACCOUNT_ALIASES = {}

# ── Paths ─────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
NIH_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(BASE))), "NIH Pipeline")

ACCOUNTS_SOURCES = [
    os.path.join(BASE, "government_results.json"),
    os.path.join(BASE, "biopharma_results.json"),       # full 52-account BioPharma run
    os.path.join(BASE, "biopharma_top12_results.json"), # top 12 fallback (deduplicated below)
    os.path.join(BASE, "hospital_results.json"),
    os.path.join(BASE, "education_results.json"),
    os.path.join(BASE, "cdmo_cro_results.json"),
    os.path.join(BASE, "industrial_results.json"),
    os.path.join(BASE, "clinical_dx_results.json"),
]

NIH_SOURCES = [
    os.path.join(NIH_BASE, "all_nih_grants.json"),       # Full pull — all Education & Hospital accounts
    os.path.join(NIH_BASE, "nih_grants_nci_r01_2024.json"),  # Legacy fallback
]

NSF_SOURCES = [
    os.path.join(NIH_BASE, "all_nsf_grants.json"),       # Full pull — all Education accounts
    os.path.join(NIH_BASE, "jhu_nsf_grants_all.json"),   # Legacy fallback
    os.path.join(NIH_BASE, "nsf_grants_jhu_2024.json"),  # Legacy fallback
]

# ── Account name mapping for NIH/NSF → our account names ──────────
# Maps substrings in NIH/NSF institution names to our account names
INSTITUTION_MAP = {
    "JOHNS HOPKINS":             ("JOHNS HOPKINS UNIVERSITY",   "Education & Research"),
    "OHIO STATE":                ("OHIO STATE UNIVERSITY",       "Education & Research"),
    "EMORY":                     ("EMORY UNIVERSITY",            "Education & Research"),
    "YALE":                      ("YALE UNIVERSITY",             "Education & Research"),
    "HARVARD":                   ("HARVARD UNIVERSITY",          "Education & Research"),
    "STANFORD":                  ("STANFORD UNIVERSITY",         "Education & Research"),
    "MIT":                       ("MASSACHUSETTS INSTITUTE OF TEC", "Education & Research"),
    "MASSACHUSETTS INSTITUTE":   ("MASSACHUSETTS INSTITUTE OF TEC", "Education & Research"),
    "MICHIGAN STATE":            ("MICHIGAN STATE UNIVERSITY",   "Education & Research"),
    "UNIVERSITY OF MICHIGAN":    ("UNIVERSITY OF MICHIGAN",      "Education & Research"),
    "UNIVERSITY OF PENNSYLVANIA":("UNIVERSITY OF PENNSYLVANIA",  "Education & Research"),
    "PENN STATE":                ("PENN STATE UNIVERSITY",       "Education & Research"),
    "UNIVERSITY OF MARYLAND":    ("UNIVERSITY OF MARYLAND",      "Education & Research"),
    "UNIVERSITY OF WASHINGTON":  ("UNIVERSITY OF WASHINGTON",    "Education & Research"),
    "UNIVERSITY OF ILLINOIS":    ("UNIVERSITY OF ILLINOIS",      "Education & Research"),
    "UNIVERSITY OF ARIZONA":     ("UNIVERSITY OF ARIZONA",       "Education & Research"),
    "INDIANA UNIVERSITY":        ("INDIANA UNIVERSITY",          "Education & Research"),
    "VANDERBILT":                ("VANDERBILT UNIVERSITY",       "Education & Research"),
    "NEW YORK UNIVERSITY":       ("NEW YORK UNIVERSITY",         "Education & Research"),
    "DUKE":                      ("DUKE UNIVERSITY",             "Education & Research"),
    "BAYLOR":                    ("BAYLOR COLLEGE OF MEDICINE",  "Education & Research"),
    "ROCKEFELLER":               ("ROCKEFELLER UNIVERSITY",      "Education & Research"),
    "WEILL CORNELL":             ("WEILL CORNELL MEDICAL COLLEGE","Education & Research"),
    "MAYO":                      ("MAYO",                        "Hospital & Health Systems"),
    "MD ANDERSON":               ("MD ANDERSON",                 "Hospital & Health Systems"),
    "DANA FARBER":               ("DANA FARBER CANCER INSTITUTE","Hospital & Health Systems"),
    "CEDAR SINAI":               ("CEDAR SINAI MEDICAL CENTER",  "Hospital & Health Systems"),
    "MOFFITT":                   ("H LEE MOFFITT CANCER CENTER", "Hospital & Health Systems"),
    "KAISER":                    ("KAISER PERMANENTE",           "Hospital & Health Systems"),
    "NIH":                       ("NIH",                         "Government"),
}


def normalize(name: str) -> str:
    return name.upper().strip()


def match_institution(inst_name: str):
    """Match NIH/NSF institution name to our account. Returns (account, category) or None."""
    upper = inst_name.upper()

    # 1. Try INSTITUTION_MAP keyword match (fast path, existing logic)
    for keyword, (account, category) in INSTITUTION_MAP.items():
        if keyword.upper() in upper:
            return account, category

    # 2. Try alias reverse lookup — check if inst_name contains any known alias
    for account, aliases in ACCOUNT_ALIASES.items():
        for alias in aliases:
            if alias.upper() in upper:
                for cat, accts in MASTER_ACCOUNTS.items():
                    if account.upper() in [a.upper() for a in accts]:
                        return account, cat

    return None


def load_accounts_signals() -> list:
    """Load all 80s accounts JSON files into unified signal records."""
    records = []
    for path in ACCOUNTS_SOURCES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ⚠ Skipping {os.path.basename(path)} — could not load: {e}")
            continue
        source_file = os.path.basename(path)
        for entry in data:
            account = entry.get("account", "")
            category = entry.get("category", "")
            timestamp = entry.get("timestamp", "")
            for signal_type, signals in entry.get("signals", {}).items():
                for sig in signals:
                    records.append({
                        "account":      account,
                        "category":     category,
                        "source":       "80s_accounts",
                        "source_file":  source_file,
                        "signal_type":  signal_type,
                        "summary":      sig.get("summary", ""),
                        "why_it_matters": sig.get("why_it_matters", ""),
                        "amount":       sig.get("amount") or sig.get("value") or sig.get("investment_size") or "",
                        "counterparty": sig.get("counterparty") or sig.get("recipient") or sig.get("agency") or "",
                        "pi":           sig.get("pi") or "",
                        "title":        sig.get("summary", "")[:120],
                        "start_date":   "",
                        "end_date":     "",
                        "source_url":   sig.get("source_url", ""),
                        "timestamp":    timestamp,
                        "raw":          sig,
                    })
    return records


def get_category_for_account(account: str) -> str:
    """Look up category for an account name using INSTITUTION_MAP or direct match."""
    upper = account.upper()
    # Try direct match in INSTITUTION_MAP
    for keyword, (mapped_account, category) in INSTITUTION_MAP.items():
        if keyword.upper() in upper:
            return category
    # Check if it's a hospital keyword
    hospital_keywords = ["HOSPITAL", "MEDICAL CENTER", "CANCER CENTER", "CLINIC", "HEALTH SYSTEM"]
    if any(k in upper for k in hospital_keywords):
        return "Hospital & Health Systems"
    return "Education & Research"


def _parse_date(date_str: str, fmt: str = None):
    """Parse a date string, returning a datetime or None on failure."""
    if not date_str:
        return None
    s = str(date_str).strip()[:10]  # trim to YYYY-MM-DD or MM/DD/YY
    try:
        if fmt:
            return datetime.strptime(s, fmt)
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


TODAY = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def load_nih_signals() -> list:
    """Load NIH grant records into unified signal records.
    Deduplicates across files. Skips expired grants (project_end_date < today).
    """
    records = []
    seen_appl_ids = set()
    skipped_expired = 0

    for path in NIH_SOURCES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ⚠ Skipping {os.path.basename(path)} — could not load: {e}")
            continue

        for grant in data:
            # Skip expired grants — project has ended, lab is no longer actively funded
            end_date = _parse_date(str(grant.get("project_end_date") or "")[:10])
            if end_date and end_date < TODAY:
                skipped_expired += 1
                continue

            # Deduplicate by application ID; fall back to title+org hash if no ID
            appl_id = grant.get("appl_id", "")
            if not appl_id:
                appl_id = f"hash:{grant.get('project_title','')[:60]}|{grant.get('organization',{}).get('org_name','')}"
            if appl_id in seen_appl_ids:
                continue
            seen_appl_ids.add(appl_id)

            # Use _matched_account if present (new files), else fuzzy match
            if grant.get("_matched_account"):
                account = grant["_matched_account"]
                category = get_category_for_account(account)
            else:
                org_name = grant.get("organization", {}).get("org_name", "")
                match = match_institution(org_name)
                account, category = match if match else (org_name, "Education & Research")

            pi_raw = grant.get("contact_pi_name", "")
            pi = pi_raw.title().strip(", ") if pi_raw else ""
            amount = grant.get("award_amount", "")
            amount_str = f"${amount:,}" if isinstance(amount, int) else str(amount)
            activity = grant.get("activity_code", "")
            fiscal_year = grant.get("fiscal_year", "")

            records.append({
                "account":        account,
                "category":       category,
                "source":         "NIH",
                "source_file":    os.path.basename(path),
                "signal_type":    "grant",
                "summary":        grant.get("project_title", ""),
                "why_it_matters": (
                    f"Active NIH {activity} grant (FY{fiscal_year}) — {amount_str} — "
                    f"signals ongoing lab supply demand for this institution."
                ),
                "amount":         amount_str,
                "counterparty":   grant.get("agency_code", "NIH"),
                "pi":             pi,
                "title":          grant.get("project_title", ""),
                "start_date":     str(grant.get("project_start_date") or "")[:10],
                "end_date":       str(grant.get("project_end_date") or "")[:10],
                "source_url":     grant.get("project_detail_url", ""),
                "timestamp":      datetime.now().isoformat(),
                "raw":            grant,
            })
    print(f"  ({skipped_expired} expired NIH grants filtered out)")
    return records


def load_nsf_signals() -> list:
    """Load NSF grant records into unified signal records.
    Deduplicates across files. Skips expired grants (expDate < today).
    """
    records = []
    seen_ids = set()
    skipped_expired = 0

    for path in NSF_SOURCES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ⚠ Skipping {os.path.basename(path)} — could not load: {e}")
            continue

        for grant in data:
            # Skip expired grants — project has ended, lab is no longer actively funded
            end_date = _parse_date(grant.get("expDate", ""), fmt="%m/%d/%Y")
            if end_date and end_date < TODAY:
                skipped_expired += 1
                continue

            # Deduplicate by grant ID; fall back to title+awardee hash if no ID
            gid = grant.get("id", "")
            if not gid:
                gid = f"hash:{grant.get('title','')[:60]}|{grant.get('awardeeName','')}"
            if gid in seen_ids:
                continue
            seen_ids.add(gid)

            # Use _matched_account if present (new files), else fuzzy match
            if grant.get("_matched_account"):
                account = grant["_matched_account"]
                category = get_category_for_account(account)
            else:
                awardee = grant.get("awardeeName", "")
                match = match_institution(awardee)
                account, category = match if match else (awardee, "Education & Research")

            amount_raw = grant.get("estimatedTotalAmt") or grant.get("fundsObligatedAmt") or "0"
            try:
                amount_str = f"${int(float(amount_raw)):,}"
            except (ValueError, TypeError):
                amount_str = str(amount_raw)

            records.append({
                "account":        account,
                "category":       category,
                "source":         "NSF",
                "source_file":    os.path.basename(path),
                "signal_type":    "grant",
                "summary":        grant.get("title", ""),
                "why_it_matters": (
                    f"Active NSF award — {amount_str} — "
                    f"signals active research lab with ongoing consumable and equipment needs. "
                    f"Project runs until {grant.get('expDate', 'unknown')}."
                ),
                "amount":         amount_str,
                "counterparty":   "NSF",
                "pi":             grant.get("pdPIName", ""),
                "title":          grant.get("title", ""),
                "start_date":     grant.get("startDate", ""),
                "end_date":       grant.get("expDate", ""),
                "source_url":     grant.get("orgUrl", ""),
                "timestamp":      datetime.now().isoformat(),
                "raw":            grant,
            })
    print(f"  ({skipped_expired} expired NSF grants filtered out)")
    return records


def seed_from_master() -> dict:
    """Initialise grouped dict from the master ACCOUNTS list — account-first approach.
    Every account appears in the output regardless of whether signals exist yet."""
    grouped = {}
    for category, accts in MASTER_ACCOUNTS.items():
        for acct in accts:
            grouped[acct] = {
                "account":      acct,
                "category":     category,
                "sources":      [],
                "signals":      [],
                "signal_count": 0,
                "status":       "pending",   # pending = not yet run
            }
    return grouped


def attach_signals(grouped: dict, records: list) -> dict:
    """Attach signal records to the account-first grouped dict."""
    for r in records:
        acct = r["account"]
        if acct not in grouped:
            # Signal came from an account not in master list — add it anyway
            grouped[acct] = {
                "account":      acct,
                "category":     r.get("category", "Unknown"),
                "sources":      [],
                "signals":      [],
                "signal_count": 0,
                "status":       "complete",
            }
        grouped[acct]["signals"].append(r)
        grouped[acct]["signal_count"] += 1
        src = r["source"]
        if src not in grouped[acct]["sources"]:
            grouped[acct]["sources"].append(src)
        grouped[acct]["status"] = "complete"
    return grouped


def main(output_file: str):
    print("Seeding from master account list...")
    grouped = seed_from_master()
    print(f"  {len(grouped)} accounts loaded as base")

    print("Loading 80s Accounts signals...")
    accounts_records = load_accounts_signals()
    print(f"  {len(accounts_records)} signals from 80s Accounts")

    print("Loading NIH grant signals...")
    nih_records = load_nih_signals()
    print(f"  {len(nih_records)} signals from NIH")

    print("Loading NSF grant signals...")
    nsf_records = load_nsf_signals()
    print(f"  {len(nsf_records)} signals from NSF")

    all_records = accounts_records + nih_records + nsf_records
    attach_signals(grouped, all_records)
    print(f"\nTotal: {len(all_records)} signals across {len(grouped)} accounts\n")

    # Summary by category
    from collections import Counter
    cat_counts = Counter(v["category"] for v in grouped.values())
    for cat, n in sorted(cat_counts.items()):
        cat_signals = sum(v["signal_count"] for v in grouped.values() if v["category"] == cat)
        pending    = sum(1 for v in grouped.values() if v["category"] == cat and v["status"] == "pending")
        print(f"  {cat}: {n} accounts ({pending} pending), {cat_signals} signals")

    # Accounts with signals from multiple sources
    multi = [(k, v) for k, v in grouped.items() if len(v["sources"]) > 1]
    if multi:
        print(f"\nAccounts with signals from multiple sources ({len(multi)}):")
        for account, data in sorted(multi, key=lambda x: x[1]["signal_count"], reverse=True):
            print(f"  {account}: {data['signal_count']} signals [{', '.join(data['sources'])}]")

    pending_count  = sum(1 for v in grouped.values() if v["status"] == "pending")
    complete_count = sum(1 for v in grouped.values() if v["status"] == "complete")

    output = {
        "generated_at":    datetime.now().isoformat(),
        "total_signals":   len(all_records),
        "total_accounts":  len(grouped),
        "accounts_complete": complete_count,
        "accounts_pending":  pending_count,
        "sources": {
            "80s_accounts": len(accounts_records),
            "NIH":          len(nih_records),
            "NSF":          len(nsf_records),
        },
        "accounts":    grouped,
        "all_signals": all_records,
    }

    # Remove raw field from output to keep file lean
    for sig in output["all_signals"]:
        sig.pop("raw", None)
    for acct in output["accounts"].values():
        for sig in acct["signals"]:
            sig.pop("raw", None)

    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nSaved → {output_file}")
    print(f"File size: {os.path.getsize(output_file)/1024:.0f} KB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge 80s Accounts + NIH/NSF signals")
    parser.add_argument("--output", "-o", default="unified_signals.json")
    args = parser.parse_args()
    main(args.output)
