import pytest

from app.core.models import FieldResult, FieldState, Override
from app.core.normalize import (
    comparison_key,
    is_absent,
    normalize_count,
    normalize_date,
    normalize_field,
    normalize_miles,
    normalize_state,
    normalize_text,
    normalize_yes_no,
)

K, M, I = FieldState.KNOWN, FieldState.MISSING, FieldState.INVALID


# --- absence -------------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "   ", "Not provided", "NOT PROVIDED.", "N/A", "TBD", "unknown"])
def test_absent_markers(raw):
    assert is_absent(raw)


@pytest.mark.parametrize("raw", ["No", "0", 0, False, "None"])
def test_real_answers_are_not_absent(raw):
    # "No", zero and False are answers, not absences.
    assert not is_absent(raw)


# --- text ----------------------------------------------------------------------

def test_text_trims_and_collapses_whitespace():
    assert normalize_text("  Pacific Harbor   Delivery Example LLC ") == (K, "Pacific Harbor Delivery Example LLC", None)


def test_text_not_provided_is_missing():
    assert normalize_text("Not provided").state is M


def test_text_comparison_is_case_and_punctuation_insensitive():
    # SUB-A: email ends with a period, PDF does not; this must not be a conflict.
    a = comparison_key("operations_description", "Local delivery of packaged retail goods.")
    b = comparison_key("operations_description", "local delivery of packaged retail goods")
    assert a == b
    assert comparison_key("business_name", "ACME LLC") == comparison_key("business_name", "acme llc")


# --- date ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    ["2026-11-01", "2026/11/01", "11/01/2026", "November 1, 2026", "Nov 1, 2026", "November 1st, 2026", "1 November 2026"],
)
def test_date_formats_normalise_to_iso(raw):
    assert normalize_date(raw) == (K, "2026-11-01", None)


@pytest.mark.parametrize("raw", ["2026-02-30", "2026-13-01", "next month", "11/01"])
def test_impossible_or_unclear_dates_are_invalid(raw):
    assert normalize_date(raw).state is I


def test_date_not_provided_is_missing():
    assert normalize_date("Not provided").state is M


def test_past_dates_are_allowed():
    # The field spec says there is no past or future date rule.
    assert normalize_date("1999-01-01") == (K, "1999-01-01", None)


# --- state ---------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["CA", "ca", "California", "CALIFORNIA", " california "])
def test_california_variants_all_become_ca(raw):
    assert normalize_state(raw) == (K, "CA", None)


@pytest.mark.parametrize("raw", ["Calif", "XX", "Ontario"])
def test_unknown_state_is_invalid(raw):
    assert normalize_state(raw).state is I


# --- vehicle count -------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("3", 3), (3, 3), ("3 vehicles", 3), ("3.0", 3), ("four", 4), ("0", 0), (0, 0)])
def test_counts(raw, expected):
    assert normalize_count(raw) == (K, expected, None)


@pytest.mark.parametrize("raw", ["3.5", "-1", "3 or 4", "several", True])
def test_bad_counts_are_invalid(raw):
    assert normalize_count(raw).state is I


def test_zero_vehicles_is_a_valid_count():
    assert normalize_count("0").value == 0


# --- radius --------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["150", 150, 150.0, "150.0", "150 miles", "150 mi"])
def test_150_variants_normalise_to_150(raw):
    result = normalize_miles(raw)
    assert result.state is K and result.value == 150


def test_150_point_1_is_kept_exactly():
    assert normalize_miles("150.1").value == 150.1


@pytest.mark.parametrize("raw", ["-5", "100-150 miles", "far", "200 km", True])
def test_bad_radius_is_invalid(raw):
    assert normalize_miles(raw).state is I


def test_radius_not_provided_is_missing():
    assert normalize_miles("Not provided").state is M


# --- yes / no ------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["Yes", "yes", "Y", "true", True, "Yes - paint supplies"])
def test_explicit_yes(raw):
    assert normalize_yes_no(raw) == (K, True, None)


@pytest.mark.parametrize("raw", ["No", "no.", "N", "false", False, "No, none carried"])
def test_explicit_no(raw):
    assert normalize_yes_no(raw) == (K, False, None)


@pytest.mark.parametrize("raw", ["Sometimes", "Occasionally", "None", "Not applicable", "maybe"])
def test_ambiguous_hazmat_is_invalid(raw):
    assert normalize_yes_no(raw).state is I


def test_absent_hazmat_is_missing_not_no():
    # The field spec: "absence is not No".
    assert normalize_yes_no("Not provided").state is M
    assert normalize_yes_no(None).state is M


# --- dispatch and models -------------------------------------------------------

def test_normalize_field_dispatches_by_name():
    assert normalize_field("garaging_state", "California").value == "CA"
    assert normalize_field("operating_radius_miles", "80").value == 80


def test_override_sets_effective_value_but_keeps_original():
    field = FieldResult(name="operating_radius_miles", state=FieldState.CONFLICT, value=None)
    field.override = Override(value=80, note="Confirmed by broker by phone")
    assert field.effective_value == 80
    assert field.effective_state is K
    assert field.state is FieldState.CONFLICT  # original state is preserved
    dumped = field.model_dump()
    assert dumped["effective_value"] == 80 and dumped["state"] == FieldState.CONFLICT


@pytest.mark.parametrize("raw, expected", [
    ("two", 2), ("Two vehicles", 2), ("2 (two)", 2), ("fifteen", 15), ("twenty-five", 25),
    ("one hundred fifty", 150), ("a hundred and fifty miles", 150), ("one thousand two hundred", 1200),
])
def test_numbers_written_in_words(raw, expected):
    assert normalize_count(raw) == (K, expected, None)
    assert normalize_miles(raw) == (K, expected, None)


@pytest.mark.parametrize("raw", ["two or three", "2 or three", "one and a half", "sometimes"])
def test_ambiguous_number_words_are_invalid(raw):
    assert normalize_count(raw).state is I
    assert normalize_miles(raw).state is I


@pytest.mark.parametrize("raw, expected", [
    ("11/01/26", "2026-11-01"), ("11/1/26", "2026-11-01"), ("Nov 1, 26", "2026-11-01"),
    ("1 Nov 26", "2026-11-01"), ("02/29/28", "2028-02-29"),
    ("2026/11/01", "2026-11-01"),  # year-first dates are not re-read as two-digit years
])
def test_two_digit_years_mean_20yy(raw, expected):
    assert normalize_date(raw) == (K, expected, None)


@pytest.mark.parametrize("raw", ["02/29/26", "November 26", "11/26"])
def test_two_digit_year_does_not_invent_dates(raw):
    assert normalize_date(raw).state is I
