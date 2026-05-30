"""Regression coverage for UsageTracker metrics honesty.

A transient "retry" (429 / timeout / empty that we retried mid-flight) must count
toward api_calls and retries, but never toward a terminal outcome. Rates are
reported over `resolved` (success + error + timeout + empty), not over api_calls.
This pins the fix that previously let retries inflate the error count and crater
the displayed success rate.
"""
import asyncio

from market_intel.engine import UsageTracker


def _run(coro):
    return asyncio.run(coro)


def test_retries_excluded_from_errors_and_counted_separately():
    async def scenario():
        t = UsageTracker(total_accounts=1)
        # One signal: three transient retries, then a terminal success.
        for _ in range(3):
            await t.record("retry", signal="grant", elapsed=0.1)
        await t.record("success", signal="grant", elapsed=0.5, hits=2)
        return t

    t = _run(scenario())
    assert t.retries == 3
    assert t.errors == 0
    assert t.successes == 1
    assert t.api_calls == 4          # every attempt is a real billed call
    assert t.total_hits == 2


def test_resolved_is_terminal_only_and_drives_rates():
    async def scenario():
        t = UsageTracker(total_accounts=4)
        await t.record("retry", signal="grant", elapsed=0.1)   # transient
        await t.record("success", signal="grant", elapsed=0.5, hits=1)
        await t.record("error", signal="pipeline", elapsed=0.2)
        await t.record("timeout", signal="capital", elapsed=0.3)
        await t.record("empty", signal="faculty", elapsed=0.1)
        return t

    t = _run(scenario())
    # resolved excludes the transient retry
    assert t.resolved == t.successes + t.errors + t.timeouts + t.empty == 4
    assert t.api_calls == 5
    assert t.retries == 1
    # success_rate is over resolved, not api_calls (1/4 = 25%, not 1/5 = 20%)
    assert abs(t.success_rate - 25.0) < 1e-9


def test_per_signal_errors_only_bump_on_terminal_outcome():
    async def scenario():
        t = UsageTracker()
        # A retry on a signal must not register as a per-signal error.
        await t.record("retry", signal="grant", elapsed=0.1)
        await t.record("error", signal="grant", elapsed=0.2)
        return t

    t = _run(scenario())
    grant = t.signal_stats["grant"]
    assert grant["calls"] == 2       # retry + terminal both count as calls
    assert grant["errors"] == 1      # only the terminal error
    assert grant["timeouts"] == 0
    assert grant["empty"] == 0


def test_to_dict_reports_resolved():
    async def scenario():
        t = UsageTracker()
        await t.record("retry", signal="grant", elapsed=0.1)
        await t.record("success", signal="grant", elapsed=0.5, hits=1)
        return t

    t = _run(scenario())
    d = t.to_dict()
    assert d["resolved"] == 1
    assert d["retries"] == 1
    assert d["successes"] == 1
    assert d["errors"] == 0
