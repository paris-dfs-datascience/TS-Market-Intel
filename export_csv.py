"""Export all results_<DATE>.json files from the configured sink to a single CSV.

Reads result JSONs via the Sink abstraction (LocalSink or BlobSink), filters to
a single date (default: today, UTC), translates verticals + signal types to the
exact Salesforce picklist labels, and writes the CSV back to the sink at
`_export/market_intel_export_<DATE>.csv`.

Run standalone:
    python export_csv.py                  # uses get_sink() and today's date
    python export_csv.py 2026-05-19       # specific date

Or call run_export(sink, date_str) programmatically (main.py does this when
`--export-csv` is passed or after `--category all` completes).
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage import Sink

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

# Skip sink keys under these "directory" prefixes — they're sidecars, not account results.
_SIDECAR_PREFIXES = ("_usage/", "_logs/", "_export/")


def _rows_to_csv_text(rows: list[dict]) -> str:
    """Build CSV text with a UTF-8 BOM so Excel renders Unicode correctly."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLS)
    writer.writeheader()
    writer.writerows(rows)
    return "﻿" + buf.getvalue()


def run_export(sink: "Sink", date_str: str | None = None) -> tuple[int, str]:
    """Iterate sink for `<COMPANY>/results_<date>.json` keys, build CSV, write back to sink.

    Returns (row_count, output_key).
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = f"results_{date_str}.json"

    rows: list[dict] = []
    accounts_seen: set[str] = set()
    for key in sink.list(""):
        if key.startswith(_SIDECAR_PREFIXES):
            continue
        if not key.endswith(suffix):
            continue
        result = sink.read(key)
        if not result:
            continue

        account   = result.get("account", "")
        parent_id = result.get("Parent_ID", "") or result.get("parent_id", "")
        raw_vert  = result.get("account_vertical") or result.get("category", "")
        vertical  = VERTICAL_LABELS.get(raw_vert, raw_vert)
        ingested  = result.get("timestamp", "")
        try:
            ingested = datetime.fromisoformat(ingested).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

        accounts_seen.add(account)

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

        ai_summary_text = (result.get("ai_summary") or "").strip()
        if ai_summary_text:
            rows.append({
                "account":          account,
                "Parent_ID":        parent_id,
                "signal_type":      "ai_summary",
                "account_vertical": vertical,
                "summary":          ai_summary_text,
                "why_it_matters":   "",
                "event_date":       date_str,
                "source_url":       "",
                "ingested_at":      ingested,
            })

    csv_text = _rows_to_csv_text(rows)
    out_key = f"_export/market_intel_export_{date_str}.csv"
    sink.write_text(out_key, csv_text)
    print(f"Exported {len(rows)} signal hits from {len(accounts_seen)} accounts -> {out_key}")
    return len(rows), out_key


if __name__ == "__main__":
    from storage import get_sink
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_export(get_sink(), date_arg)
