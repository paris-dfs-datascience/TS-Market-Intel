"""
NIH RePORTER API Client
Fetches grants and funding data from https://api.reporter.nih.gov

Endpoints:
  POST /v2/projects/search   — search grants/projects
  POST /v2/publications/search — search linked publications

Docs: https://api.reporter.nih.gov
"""

import json
import time
import requests
from typing import Optional

BASE_URL = "https://api.reporter.nih.gov/v2"
RATE_LIMIT_DELAY = 1.0  # seconds between requests (API recommendation)


def search_grants(
    fiscal_years: Optional[list[int]] = None,
    org_names: Optional[list[str]] = None,
    pi_names: Optional[list[dict]] = None,
    activity_codes: Optional[list[str]] = None,
    agencies: Optional[list[str]] = None,
    opportunity_numbers: Optional[list[str]] = None,
    project_nums: Optional[list[str]] = None,
    award_amount_min: Optional[int] = None,
    award_amount_max: Optional[int] = None,
    project_start_date: Optional[dict] = None,
    project_end_date: Optional[dict] = None,
    keywords: Optional[list[str]] = None,
    limit: int = 50,
    offset: int = 0,
    sort_field: str = "project_start_date",
    sort_order: str = "desc",
) -> dict:
    """
    Search NIH grants/projects.

    Args:
        fiscal_years:       e.g. [2023, 2024]
        org_names:          e.g. ["JOHNS HOPKINS UNIVERSITY"]
        pi_names:           e.g. [{"last_name": "Smith", "first_name": "John"}]
        activity_codes:     e.g. ["R01", "R21", "K99"]
        agencies:           e.g. ["NCI", "NHLBI"]
        opportunity_numbers: e.g. ["PA-20-185"]
        project_nums:       e.g. ["1R01CA123456-01"]
        award_amount_min:   minimum award in dollars
        award_amount_max:   maximum award in dollars
        project_start_date: e.g. {"from_date": "2023-01-01", "to_date": "2024-12-31"}
        project_end_date:   same format as project_start_date
        keywords:           free-text keyword search terms
        limit:              results per page (max 500)
        offset:             pagination offset (max 14999)
        sort_field:         field to sort by
        sort_order:         "asc" or "desc"

    Returns:
        dict with keys: meta (total, offset, limit) and results (list of grants)
    """
    criteria = {}

    if fiscal_years:
        criteria["fiscal_years"] = fiscal_years
    if org_names:
        criteria["org_names"] = org_names
    if pi_names:
        criteria["pi_names"] = pi_names
    if activity_codes:
        criteria["activity_codes"] = activity_codes
    if agencies:
        criteria["agencies"] = agencies
    if opportunity_numbers:
        criteria["opportunity_numbers"] = opportunity_numbers
    if project_nums:
        criteria["project_nums"] = project_nums
    if award_amount_min is not None or award_amount_max is not None:
        criteria["award_amount_range"] = {
            "min_amount": award_amount_min or 0,
            "max_amount": award_amount_max or 999_999_999,
        }
    if project_start_date:
        criteria["project_start_date"] = project_start_date
    if project_end_date:
        criteria["project_end_date"] = project_end_date
    if keywords:
        criteria["advanced_text_search"] = {
            "operator": "and",
            "search_field": "all",
            "search_text": " ".join(keywords),
        }

    payload = {
        "criteria": criteria,
        "offset": offset,
        "limit": min(limit, 500),
        "sort_field": sort_field,
        "sort_order": sort_order,
    }

    resp = requests.post(f"{BASE_URL}/projects/search", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def search_all_grants(max_records: int = 500, **kwargs) -> list[dict]:
    """
    Paginate through all results for a grants search query.

    Args:
        max_records: cap on total records to fetch (to avoid runaway loops)
        **kwargs:    same arguments as search_grants()

    Returns:
        list of all grant records
    """
    all_results = []
    offset = 0
    limit = min(kwargs.pop("limit", 500), 500)

    while len(all_results) < max_records:
        data = search_grants(offset=offset, limit=limit, **kwargs)
        results = data.get("results", [])
        total = data.get("meta", {}).get("total", 0)

        all_results.extend(results)

        if offset + limit >= total or offset + limit >= 15000:
            break
        if len(all_results) >= max_records:
            break

        offset += limit
        time.sleep(RATE_LIMIT_DELAY)

    return all_results[:max_records]


def search_publications(
    pmids: Optional[list[int]] = None,
    core_project_nums: Optional[list[str]] = None,
    appl_ids: Optional[list[int]] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Search publications linked to NIH-funded projects.

    Args:
        pmids:              PubMed IDs, e.g. [33298401]
        core_project_nums:  e.g. ["R01CA123456"]
        appl_ids:           NIH application IDs
        limit:              results per page (max 500)
        offset:             pagination offset (max 9999)

    Returns:
        dict with meta and results
    """
    criteria = {}
    if pmids:
        criteria["pmids"] = pmids
    if core_project_nums:
        criteria["core_project_nums"] = core_project_nums
    if appl_ids:
        criteria["appl_ids"] = appl_ids

    payload = {"criteria": criteria, "offset": offset, "limit": min(limit, 500)}
    resp = requests.post(f"{BASE_URL}/publications/search", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def save_to_json(data, filename: str):
    """Save results to a JSON file."""
    with open(filename, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Saved {len(data) if isinstance(data, list) else 1} record(s) to {filename}")


# ---------------------------------------------------------------------------
# Thomas Scientific — All Education & Hospital accounts
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    ACCOUNTS = [
        # Education & Research
        "ARIZONA STATE UNIVERSITY", "BAYLOR COLLEGE OF MEDICINE", "BROAD INSTITUTE",
        "DREXEL UNIVERSITY", "DUKE UNIVERSITY", "EMORY UNIVERSITY",
        "HARVARD UNIVERSITY", "INDIANA UNIVERSITY", "JACKSON LABORATORY",
        "JOHNS HOPKINS UNIVERSITY", "LOUISIANA STATE UNIVERSITY",
        "MASSACHUSETTS INSTITUTE OF TECHNOLOGY", "MICHIGAN STATE UNIVERSITY",
        "NEW YORK UNIVERSITY", "OHIO STATE UNIVERSITY", "PENN STATE UNIVERSITY",
        "ROCKEFELLER UNIVERSITY", "STANFORD UNIVERSITY", "TEMPLE UNIVERSITY",
        "UNIVERSITY OF ARIZONA", "UNIVERSITY OF CINCINNATI",
        "UNIVERSITY OF CONNECTICUT", "UNIVERSITY OF ILLINOIS",
        "UNIVERSITY OF MARYLAND", "UNIVERSITY OF MIAMI", "UNIVERSITY OF MICHIGAN",
        "UNIVERSITY OF OREGON", "UNIVERSITY OF PENNSYLVANIA",
        "UNIVERSITY OF UTAH", "UNIVERSITY OF WASHINGTON",
        "VANDERBILT UNIVERSITY", "WEILL CORNELL MEDICAL COLLEGE", "YALE UNIVERSITY",
        "CORIELL INSTITUTE", "ATCC",
        # Hospital & Health Systems
        "BETH ISRAEL DEACONESS MEDICAL CENTER", "CEDARS-SINAI MEDICAL CENTER",
        "CHILDRENS HOSPITAL OF CINCINNATI", "CHILDRENS HOSPITAL OF PHILADELPHIA",
        "DANA-FARBER CANCER INSTITUTE", "H LEE MOFFITT CANCER CENTER",
        "HACKENSACK UNIVERSITY MEDICAL CENTER", "HOSPITAL FOR SPECIAL SURGERY",
        "KAISER PERMANENTE", "MAYO CLINIC", "MD ANDERSON CANCER CENTER",
        "VANDERBILT UNIVERSITY MEDICAL CENTER",
    ]

    FISCAL_YEARS = [2024, 2025, 2026]
    MAX_PER_ACCOUNT = 100
    OUTPUT_FILE = "all_nih_grants.json"

    print(f"Thomas Scientific // NIH Grant Fetch")
    print(f"Accounts: {len(ACCOUNTS)} | Years: {FISCAL_YEARS} | Max per account: {MAX_PER_ACCOUNT}\n")

    all_grants = []
    for org in ACCOUNTS:
        try:
            grants = search_all_grants(
                org_names=[org],
                fiscal_years=FISCAL_YEARS,
                max_records=MAX_PER_ACCOUNT,
            )
            for g in grants:
                g["_matched_account"] = org
            all_grants.extend(grants)
            print(f"  ✅  {org}: {len(grants)} grants")
        except Exception as e:
            print(f"  ❌  {org}: {e}")
        time.sleep(RATE_LIMIT_DELAY)

    save_to_json(all_grants, OUTPUT_FILE)
    print(f"\nTotal: {len(all_grants)} NIH grants across {len(ACCOUNTS)} accounts → {OUTPUT_FILE}")
