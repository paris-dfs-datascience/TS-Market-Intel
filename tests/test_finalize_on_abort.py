"""Pins that a quota-aborted run still produces the Salesforce export CSV.

`engine.py` aborts the whole run on sustained Gemini quota exhaustion rather than skipping
the signal — skipping would checkpoint the account with empty signals and a later re-run
would drop its data for good. But the abort propagates out of `run_category`, so without
`main._run_and_finalize` it also skips `_finalize_run`, and an unattended monthly run that
dies in its last vertical leaves no CSV at all despite hours of completed, checkpointed work.

Two abort flavors matter, and they arrive differently:
  - `RuntimeError` — engine's "quota exhausted after retries" (its own handler only
    clean-exits on "credits depleted", so this one is re-raised);
  - `SystemExit`  — engine's `sys.exit(1)` on "prepaid credits depleted".
"""
from types import SimpleNamespace

import pytest

import main


QUOTA_ERROR = "Gemini quota exhausted after retries — check plan/billing/limits"


def _args():
    """The only attrs _run_verticals / _run_and_finalize read off the argparse namespace."""
    return SimpleNamespace(api_key=None, total_limit=None, limit=None, signal=None)


@pytest.fixture
def finalize_spy(monkeypatch):
    """Capture how _finalize_run was called, without touching Gemini or the sink."""
    calls = []
    monkeypatch.setattr(
        main, "_finalize_run",
        lambda sink, run_date, api_key=None, fix_urls=True: calls.append(
            {"run_date": run_date, "fix_urls": fix_urls}
        ),
    )
    return calls


def _raise(exc):
    def _fake(*_a, **_kw):
        raise exc
    return _fake


def test_runtime_error_abort_still_exports_then_exits_nonzero(monkeypatch, finalize_spy):
    monkeypatch.setattr(main, "_run_verticals", _raise(RuntimeError(QUOTA_ERROR)))

    with pytest.raises(SystemExit) as exc:
        main._run_and_finalize(["BioPharma"], None, _args(), "2026-08-01")

    assert exc.value.code == 1, "the Azure job must still report Failed"
    assert len(finalize_spy) == 1, "the export must run even though the run aborted"
    assert finalize_spy[0]["run_date"] == "2026-08-01"


def test_system_exit_abort_still_exports_then_exits_nonzero(monkeypatch, finalize_spy):
    """engine.py sys.exit(1)s on 'credits depleted' — not an Exception subclass."""
    monkeypatch.setattr(main, "_run_verticals", _raise(SystemExit(1)))

    with pytest.raises(SystemExit) as exc:
        main._run_and_finalize(["BioPharma"], None, _args(), "2026-08-01")

    assert exc.value.code == 1
    assert len(finalize_spy) == 1


def test_abort_skips_url_repair(monkeypatch, finalize_spy):
    """URL repair re-asks Gemini, and the quota is exactly what just died."""
    monkeypatch.setattr(main, "_run_verticals", _raise(RuntimeError(QUOTA_ERROR)))

    with pytest.raises(SystemExit):
        main._run_and_finalize(["BioPharma"], None, _args(), "2026-08-01")

    assert finalize_spy[0]["fix_urls"] is False


def test_clean_run_finalizes_with_url_repair_and_does_not_exit(monkeypatch, finalize_spy):
    monkeypatch.setattr(main, "_run_verticals", lambda *_a, **_kw: None)

    main._run_and_finalize(["BioPharma", "Government"], None, _args(), "2026-08-01")

    assert len(finalize_spy) == 1
    assert finalize_spy[0]["fix_urls"] is True


def test_finalize_run_honors_fix_urls_false(monkeypatch):
    """The switch must actually gate the backfill import, not just the log line."""
    called = {"backfill": False, "export": False}

    import market_intel.export_csv as export_csv
    monkeypatch.setattr(export_csv, "run_export",
                        lambda *_a, **_kw: called.__setitem__("export", True))
    monkeypatch.setenv("AUTO_FIX_URLS", "1")   # env says yes; the arg must still win

    import tools.backfill_results as backfill
    monkeypatch.setattr(backfill, "run_url_backfill",
                        lambda *_a, **_kw: called.__setitem__("backfill", True))

    main._finalize_run(None, "2026-08-01", api_key=None, fix_urls=False)

    assert called["backfill"] is False, "fix_urls=False must skip URL repair"
    assert called["export"] is True, "the CSV export must still run"
