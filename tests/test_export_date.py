"""Regression coverage for run_export date filtering.

The empty-CSV incident was a date mismatch: the engine wrote results under one
date while the export read another. This pins the contract that run_export only
picks up `results_<date>.json` files matching the date it's asked for, and skips
every other date.
"""
from datetime import datetime, timedelta, timezone

from market_intel.export_csv import run_export
from market_intel.storage import get_sink


def _result(account, parent_id, vertical, summary):
    return {
        "account": account,
        "Parent_ID": parent_id,
        "account_vertical": vertical,
        "timestamp": "2026-05-30T10:00:00+00:00",
        "signals": {"grant": [{"summary": summary, "why_it_matters": "w",
                                "event_date": "2026-05-30", "source_url": "https://x"}]},
    }


def test_run_export_filters_to_requested_date(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    sink = get_sink()
    sink.write(f"ACME/results_{today}.json", _result("ACME", "001PARENT", "BioPharma", "fresh"))
    sink.write(f"STALE/results_{yesterday}.json", _result("STALE", "002PARENT", "BioPharma", "old"))

    main_rows, review_rows, main_key, review_key = run_export(sink, today)

    assert main_key == f"_export/market_intel_export_{today}.csv"
    assert main_rows == 1            # only ACME's single grant hit
    assert review_rows == 0          # both had Parent_ID; nothing diverted

    # Read the written CSV back through the filesystem to confirm STALE is absent.
    written = (tmp_path / "_export" / f"market_intel_export_{today}.csv").read_text(encoding="utf-8")
    assert "ACME" in written
    assert "STALE" not in written


def test_run_export_diverts_rows_missing_parent_id(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sink = get_sink()
    sink.write(f"HASID/results_{today}.json", _result("HASID", "001PARENT", "BioPharma", "ok"))
    sink.write(f"NOID/results_{today}.json", _result("NOID", "", "BioPharma", "needs review"))

    main_rows, review_rows, main_key, review_key = run_export(sink, today)

    assert main_rows == 1
    assert review_rows == 1
    assert review_key == f"_export/review_{today}.csv"
