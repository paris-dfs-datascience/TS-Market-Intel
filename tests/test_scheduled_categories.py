"""Pins the --categories contract and the monthly schedule's vertical coverage.

`thomas-intel-job` runs on cron `0 6 1,15 * *` with `--monthly-schedule`, which resolves
the vertical set from `MONTHLY_SCHEDULE` in `main.py` by UTC day-of-month — so that dict is
the only thing standing between "a vertical exists" and "a vertical actually gets run".
A vertical added to or renamed in `ACCOUNTS` without a matching edit there would silently
stop being covered by either monthly run, and the run would still exit 0. These tests fail
loudly instead. See DEPLOYMENT.md, "Scheduled monthly runs".
"""
import pytest

from main import CATEGORIES, MONTHLY_SCHEDULE, _resolve_category_list, _select_verticals


# The expected contents of MONTHLY_SCHEDULE, spelled out independently so that an
# accidental edit to the dict has to be a deliberate edit here too.
DAY_1_ARG = "biopharma,cdmo_cro,education,hospital,industrial"
DAY_15_ARG = "education,clinical_dx,government"

# Deliberately re-run mid-month alongside the 1st-of-month sweep.
REPEATED = ["Education & Research"]


def test_monthly_schedule_has_the_expected_shape():
    """Only the 1st and 15th are scheduled days, with the intended scope on each."""
    assert MONTHLY_SCHEDULE == {1: DAY_1_ARG, 15: DAY_15_ARG}


def test_first_of_month_arg_resolves_to_all_but_clinical_and_government():
    assert _resolve_category_list(DAY_1_ARG) == [
        "BioPharma",
        "CDMO / CRO",
        "Education & Research",
        "Hospital & Health Systems",
        "Industrial",
    ]


def test_fifteenth_arg_resolves_to_education_clinical_government():
    assert _resolve_category_list(DAY_15_ARG) == [
        "Education & Research",
        "Clinical / Molecular Diagnostics",
        "Government",
    ]


def test_the_two_runs_cover_every_vertical():
    """No vertical may fall through the gap between the 1st and the 15th."""
    covered = set(_resolve_category_list(DAY_1_ARG)) | set(_resolve_category_list(DAY_15_ARG))
    assert covered == set(CATEGORIES), (
        f"verticals covered by neither scheduled run: {set(CATEGORIES) - covered}"
    )


def test_education_is_the_only_vertical_run_twice():
    """Anything else appearing on both dates is double-spend on Gemini calls."""
    both = set(_resolve_category_list(DAY_1_ARG)) & set(_resolve_category_list(DAY_15_ARG))
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
    """A typo in MONTHLY_SCHEDULE must kill the run immediately, not quietly shrink it."""
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
