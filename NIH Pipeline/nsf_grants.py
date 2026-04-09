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
        awards = data.get("response", {}).get("award", [])

        if not awards:
            break

        all_results.extend(awards)

        # NSF API doesn't always return totalCount reliably — stop when empty page
        if len(awards) < rpp:
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

    ACCOUNTS = [
        # Education & Research
        "Arizona State University", "Baylor College of Medicine", "Broad Institute",
        "Drexel University", "Duke University", "Emory University",
        "Harvard University", "Indiana University", "Jackson Laboratory",
        "Johns Hopkins University", "Louisiana State University",
        "Massachusetts Institute of Technology", "Michigan State University",
        "New York University", "Ohio State University", "Pennsylvania State University",
        "Rockefeller University", "Stanford University", "Temple University",
        "University of Arizona", "University of Cincinnati",
        "University of Connecticut", "University of Illinois",
        "University of Maryland", "University of Miami", "University of Michigan",
        "University of Oregon", "University of Pennsylvania",
        "University of Utah", "University of Washington",
        "Vanderbilt University", "Weill Cornell Medicine", "Yale University",
        "Coriell Institute", "ATCC",
        # Hospital & Health Systems
        "Beth Israel Deaconess Medical Center", "Cedars-Sinai Medical Center",
        "Cincinnati Children's Hospital", "Children's Hospital of Philadelphia",
        "Dana-Farber Cancer Institute", "H. Lee Moffitt Cancer Center",
        "Hackensack University Medical Center", "Hospital for Special Surgery",
        "Kaiser Permanente", "Mayo Clinic", "MD Anderson Cancer Center",
        "Vanderbilt University Medical Center",
    ]

    DATE_START = "01/01/2024"
    DATE_END   = "12/31/2026"
    MAX_PER_ACCOUNT = 100
    OUTPUT_FILE = "all_nsf_grants.json"

    print(f"Thomas Scientific // NSF Grant Fetch")
    print(f"Accounts: {len(ACCOUNTS)} | Period: {DATE_START}–{DATE_END} | Max per account: {MAX_PER_ACCOUNT}\n")

    all_grants = []
    for org in ACCOUNTS:
        try:
            grants = search_all_grants(
                awardee_name=org,
                date_start=DATE_START,
                date_end=DATE_END,
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
    print(f"\nTotal: {len(all_grants)} NSF grants across {len(ACCOUNTS)} accounts → {OUTPUT_FILE}")
