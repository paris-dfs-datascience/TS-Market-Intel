"""
prompts.py — Thomas Scientific 80s Accounts Market Intelligence
21 signals × 7 industry categories (+ cross-segment).
Each prompt is category-aware so search language is tuned to the account type.
"""

import os
from datetime import datetime, timedelta
from accounts import ACCOUNT_ALIASES


def _entity_with_aliases(entity: str) -> str:
    """Append known aliases to entity name for better Gemini search coverage."""
    aliases = ACCOUNT_ALIASES.get(entity, ACCOUNT_ALIASES.get(entity.upper(), []))
    if aliases:
        alias_str = ", ".join(aliases[:3])  # cap at 3 to keep prompt concise
        return f"{entity} (also known as {alias_str})"
    return entity

# ── Recency window ─────────────────────────────────────────────────
# Override via: export DAYS_BACK=7
DAYS_BACK    = int(os.environ.get("DAYS_BACK",     "30"))
MIN_CAPEX_M  = int(os.environ.get("MIN_CAPEX_M",  "50"))   # minimum capital project value in $M


def _recency_instruction() -> str:
    """Compute date-aware recency instruction at call time — never stale."""
    cutoff = (datetime.today() - timedelta(days=DAYS_BACK)).strftime("%B %d, %Y")
    today  = datetime.today().strftime("%B %d, %Y")
    return (
        f"Only include results from the last {DAYS_BACK} days (on or after {cutoff}). "
        f"Today's date is {today}. "
        f"Focus on press releases, news articles, company announcements, SEC filings, and official publications. "
        f"Ignore results older than {DAYS_BACK} days."
    )


# Keep module-level alias for any importers that reference RECENCY_INSTRUCTION directly
RECENCY_INSTRUCTION = _recency_instruction()

ROLE = (
    "You are a market intelligence analyst for Thomas Scientific, "
    "a B2B scientific supply distributor. Your job is to identify sales signals "
    "that indicate upcoming demand for lab supplies, reagents, consumables, and scientific equipment."
)

JSON_INSTRUCTION = "Return ONLY a raw JSON array with no markdown, no explanation, no preamble."

# ── Category → triggers mapping (21 signals × 7 categories + cross-segment) ─
CATEGORY_TRIGGERS = {
    # Original 12 signals + new category-specific signals
    "Education & Research":      ["grant", "faculty", "capital", "contract", "expansion", "funding", "project",
                                   "breakthrough", "closure"],
    "BioPharma":                 ["grant", "capital", "contract", "pipeline", "expansion", "partnership", "funding",
                                   "project", "regulatory", "hiring",
                                   "ma", "spinoff", "closure"],
    "CDMO / CRO":                ["capital", "contract", "pipeline", "expansion", "partnership", "funding",
                                   "project", "regulatory", "hiring",
                                   "ma", "closure"],
    "Clinical / Mol Dx":         ["grant", "capital", "contract", "pipeline", "expansion", "partnership", "funding",
                                   "project", "regulatory",
                                   "volume", "competitive", "closure"],
    "Hospital & Health Systems": ["grant", "faculty", "capital", "contract", "pipeline", "expansion", "partnership",
                                   "funding", "project", "regulatory", "tender",
                                   "closure"],
    "Industrial":                ["capital", "expansion", "partnership", "funding", "project", "hiring",
                                   "production", "closure"],
    "Government":                ["grant", "capital", "contract", "expansion", "project", "tender",
                                   "mandate", "legislation", "closure"],
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

# ── New signal context dicts (from client meeting) ────────────────

_BREAKTHROUGH_CONTEXT = {
    "Education & Research":
        "Nobel Prize awards, major research breakthroughs, landmark publications in Nature/Science/Cell, "
        "presidential awards, or major honours recognising scientists at this institution",
}

_MA_CONTEXT = {
    "BioPharma":
        "mergers, acquisitions, or consolidations — including pending deals, closed transactions, "
        "or announcements of one company acquiring another in the pharma or biotech space",
    "CDMO / CRO":
        "mergers, acquisitions, or consolidations of contract manufacturing or CRO organisations — "
        "including capacity acquisitions, platform buyouts, or company mergers",
}

_SPINOFF_CONTEXT = {
    "BioPharma":
        "spin-off companies being created from a larger pharma, biotech, or university parent — "
        "including new company formations, carve-outs, or technology spinouts with new lab operations",
}

_PRODUCTION_CONTEXT = {
    "Industrial":
        "changes to production lines, new manufacturing site openings, shifts in production capacity, "
        "retooling of existing plants, or new product lines being manufactured",
}

_VOLUME_CONTEXT = {
    "Clinical / Mol Dx":
        "lab production volume increases, SKU expansions, new test menu additions, throughput growth, "
        "or scale-up of existing diagnostic testing capacity",
}

_COMPETITIVE_CONTEXT = {
    "Clinical / Mol Dx":
        "competitor wins on lab supply contracts, displacement of Thomas Scientific or a peer distributor "
        "on a line-item award, recompete losses, or competitive positioning shifts in reference lab procurement",
}

_MANDATE_CONTEXT = {
    "Government":
        "new government mandates triggering lab spending — wastewater surveillance programs, forensic lab "
        "expansions, foodborne illness testing requirements, environmental monitoring mandates, or public health "
        "emergency lab buildouts driven by legislation or regulation",
}

_LEGISLATION_CONTEXT = {
    "Government":
        "state or local budget appropriations, capital improvement bills, new funding legislation, "
        "or government budget announcements that allocate funds to lab infrastructure, public health, "
        "environmental testing, or scientific research",
}

_CLOSURE_CONTEXT = {
    "Education & Research":   "research facility closures, lab shutdowns, or programme terminations",
    "BioPharma":              "plant closures, R&D site shutdowns, or manufacturing facility decommissioning",
    "CDMO / CRO":             "contract manufacturing site closures or CRO programme shutdowns",
    "Clinical / Mol Dx":      "lab closures, testing site shutdowns, or diagnostic programme terminations",
    "Hospital & Health Systems": "hospital department closures, lab consolidations, or facility shutdowns",
    "Industrial":             "manufacturing plant closures, production line shutdowns, or facility decommissioning",
    "Government":             "government lab closures, programme terminations, or facility consolidations",
}


# ── Prompt builder ────────────────────────────────────────────────
def build_prompt(signal: str, entity: str, category: str,
                 recency_instruction: str = None) -> str:
    entity = _entity_with_aliases(entity)          # expand to include aliases
    # Use pre-computed instruction if provided (avoids redundant date math per signal)
    RECENCY_INSTRUCTION = recency_instruction or _recency_instruction()

    if signal == "grant":
        ctx = _GRANT_CONTEXT.get(category, "NIH, NSF, or government grant awards")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} at {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, recipient, department_or_lab, amount, agency, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this event was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
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
            f"summary, name, department, start_date, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this hire was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence noting new hires need to outfit labs with supplies and equipment. "
            f"If no results within the recency window, return []."
        )

    if signal == "capital":
        return (
            f"{ROLE} "
            f"Search for recent news about new research buildings, laboratory facilities, manufacturing plants, "
            f"or major capital construction projects at {entity} valued at ${MIN_CAPEX_M} million or more. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, project_name, location, value, timeline, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this project was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on the lab supply opportunity from new facility build-out. "
            f"Only include projects at or above ${MIN_CAPEX_M}M. If none, return []."
        )

    if signal == "contract":
        ctx = _CONTRACT_CONTEXT.get(category, "open RFPs or procurement bids for laboratory supplies or scientific equipment")
        return (
            f"{ROLE} "
            f"Search for recent news or postings about {ctx} at or involving {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, contract_name, estimated_value, deadline_or_expiration, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this contract or RFP was announced or posted, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
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
            f"summary, product_or_program, stage, therapeutic_or_application_area, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this pipeline event was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on why a lab supply sales rep should engage this account now. "
            f"If no results within the recency window, return []."
        )

    if signal == "expansion":
        return (
            f"{ROLE} "
            f"Search for recent news about {entity} announcing a new dedicated facility, new country or "
            f"region market entry with a named office or lab, or a capacity expansion with a disclosed "
            f"investment value ($5M or more) or a specific headcount addition (25 or more people) — "
            f"tied to manufacturing, laboratory, or research operations. "
            f"Exclude co-working arrangements, sales office openings with no lab component, "
            f"minor facility upgrades under $5M, and general 'expanding our presence' statements "
            f"without a named location, investment, or headcount figure. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, location, type_of_expansion, investment_value, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this expansion was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on the lab supply opportunity (new site = new procurement). "
            f"If no results within the recency window, return []."
        )

    if signal == "partnership":
        _PARTNERSHIP_SEARCH = {
            "BioPharma": (
                f"Search for recent news about {entity} entering new licensing deals, research collaborations, "
                f"co-development agreements, or joint ventures that involve new lab programs, manufacturing "
                f"capacity, or technology platforms — with a disclosed deal value of $5M or more, or a "
                f"named research facility or clinical program component. "
                f"Exclude routine vendor agreements, co-marketing deals, distribution partnerships, "
                f"and M&A transactions (covered separately)."
            ),
            "CDMO / CRO": (
                f"Search for recent news about {entity} entering new client partnerships, technology licensing "
                f"deals, or joint ventures that involve new manufacturing capacity, new analytical service "
                f"platforms, or multi-year service agreements with a disclosed value of $5M or more. "
                f"Exclude routine supplier agreements, co-marketing deals, and M&A transactions (covered separately)."
            ),
            "Clinical / Mol Dx": (
                f"Search for recent news about {entity} entering new co-development agreements, reference lab "
                f"service partnerships, diagnostic platform licensing deals, or joint ventures involving new "
                f"testing infrastructure — with a disclosed deal value or named facility or program component. "
                f"Exclude co-marketing arrangements, distribution agreements, and routine vendor contracts."
            ),
            "Hospital & Health Systems": (
                f"Search for recent news about {entity} entering new clinical research partnerships, academic "
                f"medical centre affiliations, technology licensing agreements for new clinical programs, or "
                f"joint ventures that open a new lab or diagnostic service line — with a disclosed investment "
                f"or named program. "
                f"Exclude co-marketing arrangements and routine supplier contracts."
            ),
            "Industrial": (
                f"Search for recent news about {entity} entering new joint ventures, technology licensing deals, "
                f"or manufacturing partnerships that involve new production infrastructure, new chemistry or "
                f"material platforms, or co-development programs with a disclosed deal value of $10M or more. "
                f"Exclude routine distribution agreements, supplier MoUs, or co-marketing deals."
            ),
        }
        search_phrase = _PARTNERSHIP_SEARCH.get(
            category,
            f"Search for recent news about {entity} entering new licensing deals, research collaborations, "
            f"or joint ventures involving new lab or manufacturing capacity — with a disclosed deal value "
            f"of $5M or more. Exclude routine vendor agreements, co-marketing deals, and M&A transactions."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, partner, deal_type, deal_value, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this partnership or deal was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how this deal signals new or expanded lab activity. "
            f"If no results within the recency window, return []."
        )

    if signal == "funding":
        _FUNDING_SEARCH = {
            "BioPharma": (
                f"Search for recent news about {entity} closing a new venture funding round (Series A or later), "
                f"a government program award (BARDA, NIH, DoD) specifically for R&D or manufacturing build-out, "
                f"or a strategic investment from a pharma partner — with a disclosed amount and a stated use of "
                f"proceeds tied to lab operations, clinical programs, or manufacturing capacity. "
                f"Exclude IPO filings, debt refinancings, bond issuances, revolving credit renewals, "
                f"and government contracts covered by the contract signal."
            ),
            "CDMO / CRO": (
                f"Search for recent news about {entity} closing a new private equity investment, receiving a "
                f"government manufacturing contract (>$10M) for capacity build-out, or a strategic investor "
                f"funding a new facility or technology platform — with a disclosed use of proceeds tied to "
                f"manufacturing capacity, instrument fleet, or new service capability. "
                f"Exclude routine credit renewals, bond issuances, and general corporate finance activities."
            ),
            "Clinical / Mol Dx": (
                f"Search for recent news about {entity} closing a new funding round (venture or strategic), "
                f"receiving a government diagnostic program award (CDC, BARDA, NIH), or a hospital system "
                f"investment for new testing infrastructure — with disclosed use of proceeds tied to new "
                f"assay development, instrument procurement, or lab expansion. "
                f"Exclude routine debt refinancings and general corporate finance activities."
            ),
            "Hospital & Health Systems": (
                f"Search for recent news about {entity} receiving a new philanthropic gift (>$5M), government "
                f"capital grant, or foundation award specifically for lab infrastructure, research programs, "
                f"or new clinical service lines — with a named program or facility as the recipient. "
                f"Exclude routine bond issuances for general operating capital or construction unrelated "
                f"to lab or research infrastructure."
            ),
            "Industrial": (
                f"Search for recent news about {entity} closing a new strategic investment, government "
                f"manufacturing contract (DoD, DOE, USDA >$10M), or private equity funding round — "
                f"with disclosed use of proceeds tied to new production infrastructure, R&D capability, "
                f"or lab or QC system build-out. "
                f"Exclude routine debt refinancings, bond issuances, and general operating credit renewals."
            ),
        }
        search_phrase = _FUNDING_SEARCH.get(
            category,
            f"Search for recent news about {entity} closing a new funding round, government program award, "
            f"or strategic investment — with a disclosed amount and use of proceeds tied to lab operations, "
            f"manufacturing, or research capacity. Exclude grants (covered separately), routine debt "
            f"refinancings, bond issuances, and general corporate finance activities."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, amount, funding_type, use_of_proceeds, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this funding was announced or closed, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how new capital translates to lab supply spending. "
            f"If no results within the recency window, return []."
        )

    if signal == "project":
        _PROJECT_SEARCH = {
            "Education & Research": (
                f"Search for recent announcements from {entity} about new multi-year research programs with "
                f"disclosed funding (>$1M), new research centre launches, large interdisciplinary science "
                f"initiatives, or government-funded research contracts awarded to the institution. "
                f"Exclude routine departmental seminars, single-investigator studies, or announcements "
                f"with no disclosed budget or scope."
            ),
            "BioPharma": (
                f"Search for recent announcements from {entity} about new R&D programs with disclosed investment "
                f"(>$10M), new clinical development platforms, manufacturing scale-up initiatives with named "
                f"facilities or headcount, or government-funded programs (BARDA, DoD, NIH) awarded to the company. "
                f"Exclude vague 'pipeline expansion' language or strategic direction statements without a "
                f"specific program, facility, or dollar commitment."
            ),
            "CDMO / CRO": (
                f"Search for recent announcements from {entity} about new large-scale client manufacturing "
                f"programs (>$5M contract value), new technology platform buildouts, multi-year service "
                f"agreements, or capacity expansion initiatives with named facility or headcount targets. "
                f"Exclude general capability statements or marketing announcements without a specific "
                f"contract, facility, or investment figure."
            ),
            "Clinical / Mol Dx": (
                f"Search for recent announcements from {entity} about new large-scale diagnostic programs "
                f"(>$5M), new reference lab service agreements, instrument fleet expansions, or government "
                f"or hospital contracts for expanded testing services. "
                f"Exclude general market positioning or analyst day commentary without a specific "
                f"contract, instrument, or investment figure."
            ),
            "Hospital & Health Systems": (
                f"Search for recent announcements from {entity} about new clinical programs with disclosed "
                f"capital investment (>$5M), new centre of excellence openings, large government or foundation "
                f"grants (>$1M) for clinical research, or major service line expansions with named facility "
                f"or headcount targets. "
                f"Exclude general patient care announcements or capital campaign mentions without "
                f"a specific lab or research infrastructure component."
            ),
            "Industrial": (
                f"Search for recent announcements from {entity} about new manufacturing programs with "
                f"disclosed investment (>$10M), new product lines requiring new production infrastructure, "
                f"government manufacturing contracts (DoD, DOE, USDA), or R&D scale-up initiatives "
                f"with named facility or headcount targets. "
                f"Exclude general business strategy statements, product roadmap teasers, or "
                f"announcements with no specific dollar or facility commitment."
            ),
            "Government": (
                f"Search for recent announcements from {entity} about new multi-year programs with disclosed "
                f"funding (federal >$10M, state >$1M), new laboratory infrastructure projects, government "
                f"contract awards for scientific services, or inter-agency science programs with named "
                f"budget allocations. "
                f"Exclude routine operational updates, RFI postings, or budget request documents "
                f"that have not yet been appropriated."
            ),
        }
        search_phrase = _PROJECT_SEARCH.get(
            category,
            f"Search for recent announcements from {entity} about new multi-year programs or projects "
            f"with disclosed funding or investment, named facilities or headcount targets, or government "
            f"contract awards — tied directly to lab, manufacturing, or research operations. "
            f"Exclude vague strategic direction statements with no specific dollar or program commitment."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, project_name, scope, timeline, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this project was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
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
            f"summary, product_or_site, regulatory_action, outcome, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this regulatory event occurred or was published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how this regulatory event affects lab supply demand "
            f"(approval = scale-up; warning letter = remediation supplies needed). "
            f"If no results within the recency window, return []."
        )

    if signal == "hiring":
        _HIRING_SEARCH = {
            "BioPharma": (
                f"Search for recent news about {entity} announcing 50 or more net new hires for R&D scientists, "
                f"clinical operations staff, or manufacturing personnel — such as a named site ramp, a new facility "
                f"staffing announcement, or a disclosed headcount addition indicating pipeline or capacity expansion. "
                f"Exclude generic job postings, LinkedIn listings, or articles with no specific headcount figure."
            ),
            "CDMO / CRO": (
                f"Search for recent news about {entity} announcing 50 or more net new hires for manufacturing, "
                f"scientific, or client-facing operations — such as a new site opening with named staffing targets, "
                f"a workforce expansion press release, or a capacity ramp with disclosed headcount. "
                f"Exclude generic job postings or articles with no specific headcount figure."
            ),
            "Industrial": (
                f"Search for recent news about {entity} announcing 50 or more net new hires for manufacturing, "
                f"engineering, lab operations, or QC roles — such as a plant staffing announcement, a new facility "
                f"workforce ramp, or a disclosed headcount addition for production expansion. "
                f"Exclude generic job postings or articles with no specific headcount figure."
            ),
        }
        search_phrase = _HIRING_SEARCH.get(
            category,
            f"Search for recent news about {entity} announcing 50 or more net new hires for scientific, "
            f"manufacturing, or lab operations roles — with a specific headcount figure disclosed in a "
            f"press release or company announcement. "
            f"Exclude generic job postings or articles with no specific headcount figure."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, role_or_department, headcount, location, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this hiring announcement was published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
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
            f"summary, tender_name, estimated_value, deadline, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this tender was published or posted, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on the direct sales opportunity for Thomas Scientific. "
            f"If no results within the recency window, return []."
        )

    # ── New signals from client meeting ──────────────────────────────

    if signal == "breakthrough":
        _BREAKTHROUGH_SEARCH = {
            "Education & Research": (
                f"Search for recent news about Nobel Prize awards, National Medal of Science, major government "
                f"honours, or peer-reviewed publications explicitly described as a first-in-class discovery or "
                f"breakthrough by the institution or a major science news outlet (not routine departmental press "
                f"releases) — involving researchers at {entity}. Also include $1M+ prize awards or named "
                f"endowed chair appointments tied to a research discovery. "
                f"Exclude generic faculty profile pieces or routine grant announcements."
            ),
        }
        search_phrase = _BREAKTHROUGH_SEARCH.get(
            category,
            f"Search for recent news about Nobel Prize awards, National Medal of Science, major government "
            f"honours, or peer-reviewed publications explicitly described as a first-in-class discovery or "
            f"breakthrough by the institution or a major science news outlet — involving researchers at {entity}. "
            f"Also include $1M+ prize awards or named endowed chair appointments tied to a research discovery. "
            f"Exclude generic faculty profile pieces or routine grant announcements."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, researcher_name, department_or_lab, award_or_discovery, significance, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this breakthrough or award was announced, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how this breakthrough signals high-impact lab activity and supply demand. "
            f"If no results within the recency window, return []."
        )

    if signal == "ma":
        ctx = _MA_CONTEXT.get(category, "mergers, acquisitions, or consolidations in this sector")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} involving {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, acquirer, target, deal_value, deal_status, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this M&A event was announced or reported, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how this M&A event creates lab supply opportunity "
            f"(integration = new procurement, consolidation = vendor rationalisation risk). "
            f"If no results within the recency window, return []."
        )

    if signal == "spinoff":
        ctx = _SPINOFF_CONTEXT.get(category, "spin-off companies or technology carve-outs creating new lab operations")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} from or involving {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, spinoff_name, parent_organisation, focus_area, funding_raised, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this spinoff was announced or incorporated, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on why a new spinoff lab is a greenfield sales opportunity. "
            f"If no results within the recency window, return []."
        )

    if signal == "production":
        ctx = _PRODUCTION_CONTEXT.get(category, "production line changes, new manufacturing sites, or capacity shifts")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} at {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, facility_or_line, change_type, location, investment_value, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this production change was announced or published, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how production changes drive new lab or QC supply demand. "
            f"If no results within the recency window, return []."
        )

    if signal == "volume":
        _VOLUME_SEARCH = {
            "Clinical / Mol Dx": (
                f"Search for recent news about {entity} announcing specific lab volume increases with disclosed "
                f"numbers (e.g. 'adding 10,000 tests/month,' 'expanding test menu by X assays'), new diagnostic "
                f"test codes added to the lab menu, new automation or instrumentation installed to increase "
                f"throughput, or a new reference lab contract that expands testing volume. "
                f"Exclude general earnings commentary or percentage growth projections without a concrete "
                f"operational event (new instrument, new test, new contract)."
            ),
        }
        search_phrase = _VOLUME_SEARCH.get(
            category,
            f"Search for recent news about {entity} announcing specific lab volume increases with disclosed "
            f"numbers, new test codes or product lines added, new automation installed to increase throughput, "
            f"or a new contract that expands testing or production volume. "
            f"Exclude general earnings commentary or growth projections without a concrete operational event."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, test_or_product_line, volume_change, driver, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this volume change was reported or announced, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how higher test volume directly increases consumable spend. "
            f"If no results within the recency window, return []."
        )

    if signal == "competitive":
        _COMPETITIVE_SEARCH = {
            "Clinical / Mol Dx": (
                f"Search for recent news about {entity} announcing a new preferred supplier agreement, a named "
                f"distributor partnership, or a supply chain consolidation event that names a specific lab supply "
                f"distributor (Fisher Scientific, VWR, Sigma-Aldrich, McKesson, Thermo Fisher, or Thomas Scientific) "
                f"as a primary or exclusive supplier for reagents, consumables, or laboratory equipment. "
                f"Also include announced GPO memberships or group purchasing agreements that affect "
                f"lab supply sourcing at this account. "
                f"Exclude general procurement strategy statements with no named distributor or contract."
            ),
        }
        search_phrase = _COMPETITIVE_SEARCH.get(
            category,
            f"Search for recent news about {entity} announcing a new preferred supplier agreement or named "
            f"distributor partnership for lab reagents, consumables, or equipment — naming a specific "
            f"distributor (Fisher Scientific, VWR, Sigma-Aldrich, McKesson, Thermo Fisher, or Thomas Scientific). "
            f"Also include GPO memberships or group purchasing agreements affecting lab supply sourcing. "
            f"Exclude general procurement strategy statements with no named distributor or contract."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, contract_or_award, incumbent, winner, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this contract award or competitive event was announced, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on the competitive risk or opportunity for Thomas Scientific. "
            f"If no results within the recency window, return []."
        )

    if signal == "mandate":
        ctx = _MANDATE_CONTEXT.get(category, "government mandated testing or lab spending requirements")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} involving {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, mandate_type, jurisdiction, funding_amount, effective_date, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this mandate was announced or passed, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how this mandate creates non-discretionary lab supply demand. "
            f"If no results within the recency window, return []."
        )

    if signal == "legislation":
        _LEGISLATION_SEARCH = {
            "Government": (
                f"Search for recent news about legislation or budget appropriations that explicitly name {entity} "
                f"or allocate a specific dollar amount (state: $1M or more, federal: $10M or more) to laboratory "
                f"infrastructure, public health testing, environmental monitoring, or scientific research programs "
                f"directly operated by or contracted to {entity}. "
                f"Exclude general appropriations bills without a named program or dollar allocation traceable "
                f"to lab or science spending at this entity."
            ),
        }
        search_phrase = _LEGISLATION_SEARCH.get(
            category,
            f"Search for recent news about legislation or budget appropriations that explicitly name {entity} "
            f"or allocate a specific dollar amount (state: $1M or more, federal: $10M or more) to laboratory "
            f"infrastructure, public health testing, environmental monitoring, or scientific research programs "
            f"at or contracted to {entity}. "
            f"Exclude general appropriations bills without a named program or dollar allocation traceable "
            f"to lab or science spending at this entity."
        )
        return (
            f"{ROLE} "
            f"{search_phrase} "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, bill_or_budget_name, jurisdiction, funding_amount, focus_area, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this bill or budget was passed or announced, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on how this legislation unlocks new lab supply spending. "
            f"If no results within the recency window, return []."
        )

    if signal == "closure":
        ctx = _CLOSURE_CONTEXT.get(category, "facility closures, lab shutdowns, or programme terminations")
        return (
            f"{ROLE} "
            f"Search for recent news about {ctx} at {entity}. "
            f"{RECENCY_INSTRUCTION} "
            f"{JSON_INSTRUCTION} "
            f"Each object must use these exact keys: "
            f"summary, facility_or_programme, closure_type, effective_date, reason, event_date, why_it_matters, source_url. "
            f"'event_date' = the date this closure was announced or reported, in format 'Month DD, YYYY' or 'Month YYYY' if exact date unknown; return null if not determinable. "
            f"'why_it_matters' = one sentence on the churn risk or competitive displacement opportunity for Thomas Scientific. "
            f"If no results within the recency window, return []."
        )

    raise ValueError(f"Unknown signal: {signal}")


# ── Output field display order ────────────────────────────────────
FIELD_MAPS = {
    # Original 12 signals
    "grant":       ["recipient", "department_or_lab", "agency", "amount", "event_date"],
    "faculty":     ["name", "department", "start_date", "event_date"],
    "capital":     ["project_name", "location", "value", "timeline", "event_date"],
    "contract":    ["contract_name", "estimated_value", "deadline_or_expiration", "event_date"],
    "pipeline":    ["product_or_program", "stage", "therapeutic_or_application_area", "event_date"],
    "expansion":   ["location", "type_of_expansion", "investment_value", "event_date"],
    "partnership": ["partner", "deal_type", "deal_value", "event_date"],
    "funding":     ["amount", "funding_type", "use_of_proceeds", "event_date"],
    "project":     ["project_name", "scope", "timeline", "event_date"],
    "regulatory":  ["product_or_site", "regulatory_action", "outcome", "event_date"],
    "hiring":      ["role_or_department", "headcount", "location", "event_date"],
    "tender":      ["tender_name", "estimated_value", "deadline", "event_date"],
    # New 9 signals from client meeting
    "breakthrough": ["researcher_name", "department_or_lab", "award_or_discovery", "event_date"],
    "ma":           ["acquirer", "target", "deal_value", "deal_status", "event_date"],
    "spinoff":      ["spinoff_name", "parent_organisation", "focus_area", "funding_raised", "event_date"],
    "production":   ["facility_or_line", "change_type", "location", "investment_value", "event_date"],
    "volume":       ["test_or_product_line", "volume_change", "driver", "event_date"],
    "competitive":  ["contract_or_award", "incumbent", "winner", "event_date"],
    "mandate":      ["mandate_type", "jurisdiction", "funding_amount", "effective_date", "event_date"],
    "legislation":  ["bill_or_budget_name", "jurisdiction", "funding_amount", "focus_area", "event_date"],
    "closure":      ["facility_or_programme", "closure_type", "effective_date", "reason", "event_date"],
}
