"""
NSF Awards API Client
Fetches grants and funding data from https://api.nsf.gov/services/v1/awards

Docs: https://resources.research.gov/common/webapi/awardapisearch-v1.htm

Key differences from NIH RePORTER:
  - GET requests (not POST)
  - Max 25 results per page (rpp), paginate via offset
  - No auth required
  - Returns JSON or XML
"""

import json
import time
import requests
from typing import Optional

BASE_URL = "https://api.nsf.gov/services/v1/awards.json"
RATE_LIMIT_DELAY = 1.0   # seconds between requests

# All available fields from the API
ALL_FIELDS = [
    "id", "title", "agency", "date", "startDate", "expDate",
    "awardee", "awardeeName", "awardeeAddress", "awardeeCity",
    "awardeeStateCode", "awardeeZipCode", "awardeeCountryCode", "awardeePhone",
    "ueiNumber", "parentUeiNumber",
    "estimatedTotalAmt", "fundsObligatedAmt", "fundsObligated",
    "piFirstName", "piLastName", "piEmail", "piMiddeInitial", "pdPIName",
    "coPDPI", "poName", "poEmail", "poPhone",
    "transType", "fundProgramName", "program",
    "dirAbbr", "divAbbr", "orgLongName", "orgLongName2", "orgUrl",
    "orgCodeDir", "orgCodeDiv",
    "cfdaNumber", "progEleCode", "progRefCode", "primaryProgram",
    "perfCity", "perfStateCode", "perfZipCode", "perfCountryCode",
    "abstractText", "projectOutComesReport",
    "activeAwd", "histAwd", "publicAccessMandate",
    "initAmendmentDate", "latestAmendmentDate",
]


def search_grants(
    keyword: Optional[str] = None,
    awardee_name: Optional[str] = None,
    awardee_state: Optional[str] = None,
    awardee_city: Optional[str] = None,
    pi_name: Optional[str] = None,
    po_name: Optional[str] = None,
    award_id: Optional[str] = None,
    date_start: Optional[str] = None,        # mm/dd/yyyy
    date_end: Optional[str] = None,          # mm/dd/yyyy
    start_date_start: Optional[str] = None,
    start_date_end: Optional[str] = None,
    amount_min: Optional[int] = None,
    amount_max: Optional[int] = None,
    trans_type: Optional[str] = None,
    fund_program: Optional[str] = None,
    active_only: bool = False,
    expired_only: bool = False,
    uei_number: Optional[str] = None,
    fields: Optional[list] = None,
    rpp: int = 25,
    offset: int = 1,
    sort_key: Optional[str] = None,
) -> dict:
    """
    Search NSF grants/awards.

    Args:
        keyword:          Free-text search (supports AND, OR, NOT)
        awardee_name:     Recipient organization name
        awardee_state:    State code, e.g. "MD", "NY"
        awardee_city:     City name
        pi_name:          Principal Investigator name
        po_name:          Program Officer name
        award_id:         Specific NSF award ID
        date_start:       Award date range start (mm/dd/yyyy)
        date_end:         Award date range end (mm/dd/yyyy)
        start_date_start: Project start date range start (mm/dd/yyyy)
        start_date_end:   Project start date range end (mm/dd/yyyy)
        amount_min:       Minimum obligated amount in dollars
        amount_max:       Maximum obligated amount in dollars
        trans_type:       e.g. "Standard Grant", "Cooperative Agreement", "Fellowship Award"
        fund_program:     Fund program name
        active_only:      Return only active awards
        expired_only:     Return only expired awards
        uei_number:       Unique Entity Identifier
        fields:           List of fields to return (default: all fields)
        rpp:              Results per page (max 25)
        offset:           Pagination offset, starts at 1
        sort_key:         Sort field: awardNumber, startDate, organization, etc.

    Returns:
        dict with 'response' containing 'award' list and 'totalCount'
    """
    params = {
        "rpp": min(rpp, 25),
        "offset": offset,
        "printFields": ",".join(fields if fields else ALL_FIELDS),
    }

    if keyword:           params["keyword"] = keyword
    if awardee_name:      params["awardeeName"] = awardee_name
    if awardee_state:     params["awardeeStateCode"] = awardee_state
    if awardee_city:      params["awardeeCity"] = awardee_city
    if pi_name:           params["pdPIName"] = pi_name
    if po_name:           params["poName"] = po_name
    if award_id:          params["id"] = award_id
    if date_start:        params["dateStart"] = date_start
    if date_end:          params["dateEnd"] = date_end
    if start_date_start:  params["startDateStart"] = start_date_start
    if start_date_end:    params["startDateEnd"] = start_date_end
    if amount_min:        params["fundsObligatedAmtFrom"] = amount_min
    if amount_max:        params["fundsObligatedAmtTo"] = amount_max
    if trans_type:        params["transType"] = trans_type
    if fund_program:      params["fundProgramName"] = fund_program
    if active_only:       params["ActiveAwards"] = "true"
    if expired_only:      params["ExpiredAwards"] = "true"
    if uei_number:        params["ueiNumber"] = uei_number
    if sort_key:          params["sortKey"] = sort_key

    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def search_all_grants(max_records: int = 500, **kwargs) -> list[dict]:
    """
    Paginate through all results for an NSF grants search query.

    Args:
        max_records: cap on total records to fetch
        **kwargs:    same arguments as search_grants()

    Returns:
        list of all award records
    """
    all_results = []
    offset = 1
    rpp = 25  # API max

    while len(all_results) < max_records:
        data = search_grants(offset=offset, rpp=rpp, **kwargs)
        response_body = data.get("response", {})
        awards = response_body.get("award", [])

        if not awards:
            break

        all_results.extend(awards)

        # Use totalCount when available (more reliable); fall back to page-size check
        total_count = response_body.get("totalCount") or response_body.get("total", 0)
        if total_count:
            if len(all_results) >= min(total_count, max_records):
                break
        elif len(awards) < rpp:
            # Last page heuristic — only used when totalCount unavailable
            break

        if len(all_results) >= max_records:
            break

        offset += rpp
        time.sleep(RATE_LIMIT_DELAY)

    return all_results[:max_records]


def save_to_json(data, filename: str):
    """Save results to a JSON file."""
    with open(filename, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Saved {len(data)} record(s) to {filename}")


# ─────────────────────────────────────────────────────────────────────────────
# Example usage
# ─────────────────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------
# Thomas Scientific — All Education & Hospital accounts
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    # Search name → canonical accounts.py name mapping
    # NSF API uses title case; _matched_account must match accounts.py exactly
    ACCOUNTS = {
        # Education & Research  (search_name: canonical_name)
        "Arizona State University":              "ARIZONA STATE UNIVERSITY",
        "Baylor College of Medicine":            "BAYLOR COLLEGE OF MEDICINE",
        "Broad Institute":                       "BROAD INSTITUTE",
        "Drexel University":                     "DREXEL UNIVERSITY",
        "Duke University":                       "DUKE UNIVERSITY",
        "Emory University":                      "EMORY UNIVERSITY",
        "Harvard University":                    "HARVARD UNIVERSITY",
        "Indiana University":                    "INDIANA UNIVERSITY",
        "Jackson Laboratory":                    "JACKSON LABS",
        "Johns Hopkins University":              "JOHNS HOPKINS UNIVERSITY",
        "Louisiana State University":            "LOUISIANA STATE UNIVERSITY",
        "Massachusetts Institute of Technology": "MASSACHUSETTS INSTITUTE OF TEC",
        "Michigan State University":             "MICHIGAN STATE UNIVERSITY",
        "New York University":                   "NEW YORK UNIVERSITY",
        "Ohio State University":                 "OHIO STATE UNIVERSITY",
        "Pennsylvania State University":         "PENN STATE UNIVERSITY",
        "Rockefeller University":                "ROCKEFELLER UNIVERSITY",
        "Stanford University":                   "STANFORD UNIVERSITY",
        "Temple University":                     "TEMPLE UNIVERSITY",
        "University of Arizona":                 "UNIVERSITY OF ARIZONA",
        "University of Cincinnati":              "UNIVERSITY OF CINCINNATI",
        "University of Connecticut":             "UNIVERSITY OF CONNECTICUT",
        "University of Illinois":                "UNIVERSITY OF ILLINOIS",
        "University of Maryland":                "UNIVERSITY OF MARYLAND",
        "University of Miami":                   "UNIVERSITY OF MIAMI",
        "University of Michigan":                "UNIVERSITY OF MICHIGAN",
        "University of Oregon":                  "UNIVERSITY OF OREGON",
        "University of Pennsylvania":            "UNIVERSITY OF PENNSYLVANIA",
        "University of Utah":                    "UNIVERSITY OF UTAH",
        "University of Washington":              "UNIVERSITY OF WASHINGTON",
        "Vanderbilt University":                 "VANDERBILT UNIVERSITY",
        "Weill Cornell Medicine":                "WEILL CORNELL MEDICAL COLLEGE",
        "Yale University":                       "YALE UNIVERSITY",
        "Coriell Institute":                     "CORIELL INSTITUTE",
        "ATCC":                                  "ATCC",
        # Hospital & Health Systems
        "Beth Israel Deaconess Medical Center":  "BETH ISRAEL",
        "Cedars-Sinai Medical Center":           "CEDAR SINAI MEDICAL CENTER",
        "Cincinnati Children's Hospital":        "CHILDRENS HOSP OF CINCINNATI",
        "Children's Hospital of Philadelphia":   "CHOP",
        "Dana-Farber Cancer Institute":          "DANA FARBER CANCER INSTITUTE",
        "H. Lee Moffitt Cancer Center":          "H LEE MOFFITT CANCER CENTER",
        "Hackensack University Medical Center":  "HACKENSACK UNIVERSITY MEDICAL",
        "Hospital for Special Surgery":          "HOSPITAL FOR SPECIAL SURGERY",
        "Kaiser Permanente":                     "KAISER PERMANENTE",
        "Mayo Clinic":                           "MAYO",
        "MD Anderson Cancer Center":             "MD ANDERSON",
        "Vanderbilt University Medical Center":  "VANDERBILT MEDICAL CENTER",
    }

    DATE_START = "01/01/2024"
    DATE_END   = "12/31/2026"
    MAX_PER_ACCOUNT = 100
    MAX_RETRIES = 3
    OUTPUT_FILE = "all_nsf_grants.json"

    print(f"Thomas Scientific // NSF Grant Fetch")
    print(f"Accounts: {len(ACCOUNTS)} | Period: {DATE_START}–{DATE_END} | Max per account: {MAX_PER_ACCOUNT}\n")

    all_grants = []
    for search_name, canonical_name in ACCOUNTS.items():
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                grants = search_all_grants(
                    awardee_name=search_name,
                    date_start=DATE_START,
                    date_end=DATE_END,
                    max_records=MAX_PER_ACCOUNT,
                )
                for g in grants:
                    g["_matched_account"] = canonical_name  # always canonical
                all_grants.extend(grants)
                print(f"  ✅  {canonical_name}: {len(grants)} grants")
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    wait = attempt * 5
                    print(f"  ⚠  {canonical_name}: error (attempt {attempt}/{MAX_RETRIES}), retrying in {wait}s — {e}")
                    time.sleep(wait)
                else:
                    print(f"  ❌  {canonical_name}: failed after {MAX_RETRIES} attempts — {e}")
        time.sleep(RATE_LIMIT_DELAY)

    save_to_json(all_grants, OUTPUT_FILE)
    print(f"\nTotal: {len(all_grants)} NSF grants across {len(ACCOUNTS)} accounts → {OUTPUT_FILE}")
