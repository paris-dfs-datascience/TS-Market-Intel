"""
prompts.py — Thomas Scientific 80s Accounts Market Intelligence
12 signals × 7 industry categories.
Each prompt is category-aware so search language is tuned to the account type.
"""

from datetime import datetime, timedelta

# ── Recency window ────────────────────────────────────────────────
DAYS_BACK = 7
cutoff = (datetime.today() - timedelta(days=DAYS_BACK)).strftime("%B %d, %Y")
TODAY = datetime.today().strftime("%B %d, %Y")

RECENCY_INSTRUCTION = (
    f"Only include results from the last {DAYS_BACK} days (on or after {cutoff}). "
    f"Today's date is {TODAY}. "
    f"Focus on press releases, news articles, company announcements, SEC filings, and official publications. "
    f"Ignore results older than {DAYS_BACK} days."
)

ROLE = (
    "You are a market intelligence analyst for Thomas Scientific, "
    "a B2B scientific supply distributor. Your job is to identify sales signals "
    "that indicate upcoming demand for lab supplies, reagents, consumables, and scientific equipment."
)

JSON_INSTRUCTION = "Return ONLY a raw JSON array with no markdown, no explanation, no preamble."

# ── Category → triggers mapping (from 12×7 matrix) ───────────────
CATEGORY_TRIGGERS = {
    "Education & Research":      ["grant", "faculty", "capital", "contract", "expansion", "funding", "project"],
    "BioPharma":                 ["grant", "capital", "contract", "pipeline", "expansion", "partnership", "funding", "project", "regulatory", "hiring"],
    "CDMO / CRO":                ["capital", "contract", "pipeline", "expansion", "partnership", "funding", "project", "regulatory", "hiring"],
    "Clinical / Mol Dx":         ["grant", "capital", "contract", "pipeline", "expansion", "partnership", "funding", "project", "regulatory"],
    "Hospital & Health Systems": ["grant", "faculty", "capital", "contract", "pipeline", "expansion", "partnership", "funding", "project", "regulatory", "tender"],
    "Industrial":                ["capital", "expansion", "partnership", "funding", "project", "hiring"],
    "Government":                ["grant", "capital", "contract", "expansion", "project", "tender"],
}

# ── Category-specific prompt context injections ───────────────────
_GRANT_CONTEXT = {
    "Education & Research":
        "NIH, NSF, or DoD grants awarded to professors, principal investigators, or research labs",
    "BioPharma":
        "NIH, BARDA, SBIR/STTR, or government grant awards to the company for R&D programs",
    "Clinical / Mol Dx":
        "NIH, CDC, or BARDA grants awarded for diagnostic assay development or clinical research",
    "Hospital & Health Systems":
        "NIH clinical research grants, NCI/NIA cancer center designations, or foundation research awards",
    "Government":
        "DARPA, DoD, NIH, or other federal agency grant awards or contract funding",
}

_PIPELINE_CONTEXT = {
    "BioPharma":
        "new drug discoveries, IND filings, clinical trial initiations, NDA/BLA submissions, or FDA approvals",
    "CDMO / CRO":
        "new client manufacturing programs, new drug substance or drug product contracts, or technology platform expansions",
    "Clinical / Mol Dx":
        "new diagnostic assay launches, 510(k) submissions, LDT launches, or CE-IVD markings",
    "Hospital & Health Systems":
        "new clinical programs, investigator-initiated trials, or new treatment protocols being adopted",
}

_REGULATORY_CONTEXT = {
    "BioPharma":
        "FDA NDA/BLA/sNDA approvals, IND clearances, Priority Review designations, Breakthrough Therapy designations, or FDA Warning Letters",
    "CDMO / CRO":
        "FDA manufacturing site inspections, Form 483 observations, EMA GMP compliance reports, or cGMP certification outcomes",
    "Clinical / Mol Dx":
        "FDA 510(k) clearances, PMA approvals, De Novo classifications, CAP/CLIA accreditation changes, or CE-IVD markings",
    "Hospital & Health Systems":
        "Joint Commission accreditation decisions, CMS certification changes, CAP laboratory accreditation, or Magnet nursing designation",
}

_CONTRACT_CONTEXT = {
    "Education & Research":
        "open RFPs, competitive bids, or expiring procurement contracts for laboratory supplies, scientific equipment, or research consumables",
    "BioPharma":
        "open RFPs or procurement bids for lab reagents, consumables, raw materials, or scientific equipment",
    "CDMO / CRO":
        "new client manufacturing contracts, technology transfer agreements, or capacity reservation deals",
    "Clinical / Mol Dx":
        "open bids or RFPs for laboratory equipment, reagents, or reference lab service agreements",
    "Hospital & Health Systems":
        "GPO contract awards, hospital supply chain RFPs, or lab equipment procurement tenders",
    "Government":
        "GSA schedule awards, federal procurement solicitations, or lab supply contract vehicles",
}

_HIRING_CONTEXT = {
    "BioPharma":
        "large-scale hiring announcements for R&D scientists, clinical operations staff, or manufacturing personnel — indicating pipeline or capacity ramp-up",
    "CDMO / CRO":
        "new site staffing announcements, manufacturing workforce expansion, or scientific headcount growth signaling new client capacity",
    "Industrial":
        "manufacturing plant staffing announcements, engineering hires, or lab operations headcount growth",
}

_TENDER_CONTEXT = {
    "Hospital & Health Systems":
        "hospital equipment procurement tenders, GPO competitive bids, or lab supply purchasing agreements open for bidding",
    "Government":
        "GSA schedule solicitations, federal lab supply procurement notices, DoD or VA equipment tenders",
}

_FACULTY_CONTEXT = {
    "Education & Research":
        "new faculty hires, incoming professors, or newly appointed researchers in biology, chemistry, immunology, biochemistry, neuroscience, or medical research",
    "Hospital & Health Systems":
        "new department chairs, division chiefs, recruited physician-scientists, or newly appointed research directors",
}


# ── Prompt builder ────────────────────────────────────────────────
def build_prompt(signal: str, entity: str, category: str) -> str:

    if signal == "grant":
        ctx = _GRANT_CONTEXT.get(category, "NIH, NSF, or government grant awards")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} at {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, recipient, department_or_lab, amount, agency, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on why a lab supply sales rep should act on this. "
            f"If no results within the recency window, return []."
        )

    if signal == "faculty":
        ctx = _FACULTY_CONTEXT.get(category, "new faculty or research leadership hires")
        return (
            f"{ROLE} "
            f"Search for recent announcements about {ctx} at {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, name, department, start_date, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence noting new hires need to outfit labs with supplies and equipment. "
            f"If no results within the recency window, return []."
        )

    if signal == "capital":
        return (
            f"{ROLE} "
            f"Search for recent news about new research buildings, laboratory facilities, manufacturing plants, "
            f"or major capital construction projects at {entity} valued at $50 million or more. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, project_name, location, value, timeline, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on the lab supply opportunity from new facility build-out. "
            f"Only include projects at or above $50M. If none, return []."
        )

    if signal == "contract":
        ctx = _CONTRACT_CONTEXT.get(category, "open RFPs or procurement bids for laboratory supplies or scientific equipment")
        return (
            f"{ROLE} "
            f"Search for recent news or postings about {ctx} at or involving {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, contract_name, estimated_value, deadline_or_expiration, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on the bid or supply opportunity for Thomas Scientific. "
            f"If no results within the recency window, return []."
        )

    if signal == "pipeline":
        ctx = _PIPELINE_CONTEXT.get(category, "new product launches, R&D breakthroughs, or regulatory submissions")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} at {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, product_or_program, stage, therapeutic_or_application_area, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on why a lab supply sales rep should engage this account now. "
            f"If no results within the recency window, return []."
        )

    if signal == "expansion":
        return (
            f"{ROLE} "
            f"Search for recent news about {entity} expanding operations — new manufacturing facilities, "
            f"new laboratory sites, geographic expansion, capacity scale-up, new plants, or facility upgrades. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, location, type_of_expansion, investment_value, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on the lab supply opportunity (new site = new procurement). "
            f"If no results within the recency window, return []."
        )

    if signal == "partnership":
        return (
            f"{ROLE} "
            f"Search for recent news about {entity} entering new partnerships, licensing deals, collaborations, "
            f"joint ventures, or M&A activity (acquisitions or being acquired). "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, partner, deal_type, deal_value, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on how this deal signals new or expanded lab activity. "
            f"If no results within the recency window, return []."
        )

    if signal == "funding":
        return (
            f"{ROLE} "
            f"Search for recent news about {entity} raising capital — venture funding rounds, grants, "
            f"government contracts, IPO, bond issuances, follow-on offerings, or large contract awards. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, amount, funding_type, use_of_proceeds, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on how new capital translates to lab supply spending. "
            f"If no results within the recency window, return []."
        )

    if signal == "project":
        return (
            f"{ROLE} "
            f"Search for recent announcements from {entity} about new research programs, large-scale projects, "
            f"government contracts, manufacturing scale-up initiatives, or strategic initiatives. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, project_name, scope, timeline, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence connecting the project to demand for scientific supplies or equipment. "
            f"If no results within the recency window, return []."
        )

    if signal == "regulatory":
        ctx = _REGULATORY_CONTEXT.get(category, "FDA approvals, regulatory clearances, or compliance outcomes")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} involving {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, product_or_site, regulatory_action, outcome, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on how this regulatory event affects lab supply demand "
            f"(approval = scale-up; warning letter = remediation supplies needed). "
            f"If no results within the recency window, return []."
        )

    if signal == "hiring":
        ctx = _HIRING_CONTEXT.get(category, "large-scale scientific or manufacturing hiring announcements")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} at {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, role_or_department, headcount, location, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on how headcount expansion signals new lab supply demand. "
            f"If no results within the recency window, return []."
        )

    if signal == "tender":
        ctx = _TENDER_CONTEXT.get(category, "public procurement tenders or lab equipment bids")
        return (
            f"{ROLE} "
            f"Search for recent news or postings about {ctx} at or involving {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, tender_name, estimated_value, deadline, why_it_matters, source_url. "
            f"'why_it_matters' = one sentence on the direct sales opportunity for Thomas Scientific. "
            f"If no results within the recency window, return []."
        )

    raise ValueError(f"Unknown signal: {signal}")


# ── Output field display order ────────────────────────────────────
FIELD_MAPS = {
    "grant":       ["recipient", "department_or_lab", "agency", "amount"],
    "faculty":     ["name", "department", "start_date"],
    "capital":     ["project_name", "location", "value", "timeline"],
    "contract":    ["contract_name", "estimated_value", "deadline_or_expiration"],
    "pipeline":    ["product_or_program", "stage", "therapeutic_or_application_area"],
    "expansion":   ["location", "type_of_expansion", "investment_value"],
    "partnership": ["partner", "deal_type", "deal_value"],
    "funding":     ["amount", "funding_type", "use_of_proceeds"],
    "project":     ["project_name", "scope", "timeline"],
    "regulatory":  ["product_or_site", "regulatory_action", "outcome"],
    "hiring":      ["role_or_department", "headcount", "location"],
    "tender":      ["tender_name", "estimated_value", "deadline"],
}
