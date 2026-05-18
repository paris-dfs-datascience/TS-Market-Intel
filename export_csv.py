"""Export all results.json files under ./output to a single CSV."""
import csv, json, sys
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./output")
CSV_FILE   = OUTPUT_DIR / "market_intel_export.csv"

# SF import matches on picklist LABELS (not API values).
# Keys cover both the _vertical_api_name() output stored in JSON and older 'category' field values.
VERTICAL_LABELS: dict[str, str] = {
    "BioPharma":                  "BioPharma",
    "CDMO_CRO":                   "CDMO / CRO",
    "CDMO / CRO":                 "CDMO / CRO",
    "Clinical_Mol_Dx":            "Clinical / Molecular Diagnostics",
    "Clinical / Mol Dx":          "Clinical / Molecular Diagnostics",
    "Clinical / Molecular Diagnostics": "Clinical / Molecular Diagnostics",
    "Education_Research":         "Education & Research",
    "Education & Research":       "Education & Research",
    "Government":                 "Government",
    "Hospital_Health_Systems":    "Hospital & Health Systems",
    "Hospital & Health Systems":  "Hospital & Health Systems",
    "Industrial":                 "Industrial",
    # Non-SF verticals from older JSON files — map to nearest valid SF label
    "Resellers":                  "Industrial",
    "Advanced_Technology":        "Industrial",
    "Advanced Technology":        "Industrial",
    "International":              "BioPharma",
}

SIGNAL_TYPE_LABELS: dict[str, str] = {
    "grant":        "Grant",
    "faculty":      "Faculty Hire",
    "capital":      "Capital Project",
    "contract":     "Contract",
    "pipeline":     "Pipeline",
    "expansion":    "Expansion",
    "partnership":  "Partnership",
    "funding":      "Funding",
    "project":      "Project",
    "regulatory":   "Regulatory",
    "hiring":       "Hiring",
    "tender":       "Tender",
    "breakthrough": "Breakthrough",
    "ma":           "M&A",
    "spinoff":      "Spinoff",
    "production":   "Production",
    "volume":       "Volume",
    "competitive":  "Competitive",
    "mandate":      "Mandate",
    "legislation":  "Legislation",
    "closure":      "Closure",
}

COLS = ["account", "Parent_ID", "signal_type", "account_vertical",
        "summary", "why_it_matters", "event_date", "source_url", "ingested_at"]

rows = []
for result_path in sorted(OUTPUT_DIR.glob("*/results_*.json")):
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        continue

    account   = result.get("account", "")
    parent_id = result.get("Parent_ID", "")
    raw_vert  = result.get("account_vertical") or result.get("category", "")
    vertical  = VERTICAL_LABELS.get(raw_vert, raw_vert)
    ingested  = result.get("timestamp", "")
    try:
        ingested = datetime.fromisoformat(ingested).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    for signal_type, hits in result.get("signals", {}).items():
        sf_signal = SIGNAL_TYPE_LABELS.get(signal_type, signal_type)
        for hit in hits:
            rows.append({
                "account":          account,
                "Parent_ID":        parent_id,
                "signal_type":      sf_signal,
                "account_vertical": vertical,
                "summary":          hit.get("summary", ""),
                "why_it_matters":   hit.get("why_it_matters", ""),
                "event_date":       hit.get("event_date", ""),
                "source_url":       hit.get("source_url", ""),
                "ingested_at":      ingested,
            })

with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=COLS)
    writer.writeheader()
    writer.writerows(rows)

print(f"Exported {len(rows)} signal hits from "
      f"{len(list(OUTPUT_DIR.glob('*/results_*.json')))} accounts -> {CSV_FILE}")
