"""Pins the --categories contract and the monthly Azure schedule's coverage.

The two scheduled Container Apps Jobs (`thomas-intel-job-m1`, `thomas-intel-job-m15`)
each carry a hardcoded `--categories=...` arg in their job template — see DEPLOYMENT.md.
Nothing in the Python reads those strings, so a vertical added to or renamed in
`ACCOUNTS` would silently stop being covered by either run. These tests fail loudly
instead, and the fix is to update both the job args and the constants below.
"""
import pytest

from main import CATEGORIES, MONTHLY_SCHEDULE, _resolve_category_list, _select_verticals


# Verbatim copies of the `--categories=` args stored in each scheduled job's template.
# These are duplicated on purpose: MONTHLY_SCHEDULE is Python, the job args are Azure
# state, and the two drifting apart is the failure this file exists to catch.
JOB_M1_ARG = "biopharma,cdmo_cro,education,hospital,industrial"
JOB_M15_ARG = "education,clinical_dx,government"

# Deliberately re-run mid-month alongside the 1st-of-month sweep.
REPEATED = ["Education & Research"]


def test_monthly_schedule_matches_the_deployed_job_args():
    """MONTHLY_SCHEDULE (used by --monthly-schedule) and the job args must agree."""
    assert MONTHLY_SCHEDULE == {1: JOB_M1_ARG, 15: JOB_M15_ARG}


def test_first_of_month_arg_resolves_to_all_but_clinical_and_government():
    assert _resolve_category_list(JOB_M1_ARG) == [
        "BioPharma",
        "CDMO / CRO",
        "Education & Research",
        "Hospital & Health Systems",
        "Industrial",
    ]


def test_fifteenth_arg_resolves_to_education_clinical_government():
    assert _resolve_category_list(JOB_M15_ARG) == [
        "Education & Research",
        "Clinical / Molecular Diagnostics",
        "Government",
    ]


def test_the_two_runs_cover_every_vertical():
    """No vertical may fall through the gap between the 1st and the 15th."""
    covered = set(_resolve_category_list(JOB_M1_ARG)) | set(_resolve_category_list(JOB_M15_ARG))
    assert covered == set(CATEGORIES), (
        f"verticals covered by neither scheduled run: {set(CATEGORIES) - covered}"
    )


def test_education_is_the_only_vertical_run_twice():
    """Anything else appearing in both args is double-spend on Gemini calls."""
    both = set(_resolve_category_list(JOB_M1_ARG)) & set(_resolve_category_list(JOB_M15_ARG))
    assert both == set(REPEATED)


@pytest.mark.parametrize("value", ["biopharma", "BioPharma", "  BIOPHARMA  "])
def test_tokens_are_case_and_whitespace_insensitive(value):
    assert _resolve_category_list(value) == ["BioPharma"]


def test_duplicate_tokens_collapse_and_order_is_preserved():
    assert _resolve_category_list("government,education,government") == [
        "Government", "Education & Research",
    ]


@pytest.mark.parametrize("value", ["clinical", "biopharma,nope", "", " , "])
def test_unrecognized_or_empty_value_exits_nonzero(value):
    """A typo in a job arg must kill the run immediately, not quietly shrink it."""
    with pytest.raises(SystemExit) as exc:
        _resolve_category_list(value)
    assert exc.value.code == 1


def test_select_verticals_filters_and_reorders_a_source_listing():
    available = ["Industrial", "BioPharma", "Government"]
    assert _select_verticals(available, ["Government", "BioPharma"], "test") == [
        "Government", "BioPharma",
    ]


def test_select_verticals_passes_through_when_no_filter():
    available = ["Industrial", "BioPharma"]
    assert _select_verticals(available, None, "test") == available


def test_select_verticals_exits_when_nothing_overlaps():
    with pytest.raises(SystemExit) as exc:
        _select_verticals(["Industrial"], ["Government"], "test")
    assert exc.value.code == 1
