import pytest

from app.core.models import FieldState
from app.core.reconcile import apply_override, reconcile_field, remove_override
from helpers import EMAIL, PDF, cand, field

K, M, C, I = FieldState.KNOWN, FieldState.MISSING, FieldState.CONFLICT, FieldState.INVALID


def test_agreeing_sources_are_known():
    f = field("declared_vehicle_count", email="3", pdf="3")
    assert (f.state, f.value) == (K, 3)
    assert len(f.candidates) == 2  # evidence from both sources is kept


def test_ca_vs_california_is_not_a_conflict():
    # SUB-A: email "CA", application "California"
    f = field("garaging_state", email="CA", pdf="California")
    assert (f.state, f.value) == (K, "CA")


def test_harmless_number_formats_agree():
    assert field("operating_radius_miles", email="150", pdf="150.0").state is K


def test_trailing_period_and_case_do_not_conflict():
    f = field("operations_description", email="Local delivery of packaged retail goods.", pdf="local delivery of packaged retail goods")
    assert f.state is K


def test_source_conflict_keeps_both_values_and_sets_null():
    # SUB-C: email 180, application 80
    f = field("operating_radius_miles", email="180", pdf="80")
    assert f.state is C
    assert f.value is None
    assert {c.value for c in f.candidates} == {180, 80}
    assert all(c.evidence for c in f.candidates)
    assert "180" in f.reason and "80" in f.reason


def test_hazmat_yes_vs_no_is_a_conflict():
    assert field("hazardous_materials", email="Yes", pdf="No").state is C


def test_missing_mention_is_not_a_conflict():
    # field_specification.json: "Missing mentions are not conflicting values."
    f = field("operating_radius_miles", email="120", pdf="Not provided")
    assert (f.state, f.value) == (K, 120)


def test_both_missing_lists_sources_checked():
    f = field("requested_effective_date", email="Not provided", pdf="Not provided")
    assert f.state is M
    assert f.sources_checked == [EMAIL, PDF]
    assert EMAIL in f.reason and PDF in f.reason


def test_invalid_only_is_invalid():
    f = field("requested_effective_date", email="2026-02-30", pdf=None)
    assert f.state is I


def test_known_plus_invalid_is_unresolved_not_silently_preferred():
    f = field("declared_vehicle_count", email="3", pdf="3 or 4")
    assert f.state is I
    assert f.value is None


def test_unverified_candidate_is_shown_but_not_used():
    candidates = [cand("operating_radius_miles", EMAIL, "500", verified=False), cand("operating_radius_miles", PDF, "80")]
    f = reconcile_field("operating_radius_miles", candidates, [EMAIL, PDF])
    assert (f.state, f.value) == (K, 80)
    assert len(f.candidates) == 2
    assert "unverified" in f.reason


def test_override_resolves_conflict_and_keeps_original():
    f = field("operating_radius_miles", email="180", pdf="80")
    corrected = apply_override(f, "80", note="Confirmed by broker by phone")
    assert corrected.effective_state is K and corrected.effective_value == 80
    assert corrected.state is C and corrected.value is None  # original kept
    assert len(corrected.candidates) == 2  # original evidence kept
    assert corrected.override.note == "Confirmed by broker by phone"
    assert f.override is None  # input not mutated


def test_override_value_is_normalised():
    f = field("garaging_state", email="Not provided", pdf="Not provided")
    assert apply_override(f, "california").effective_value == "CA"


def test_unusable_override_is_rejected():
    f = field("operating_radius_miles", email="180", pdf="80")
    with pytest.raises(ValueError):
        apply_override(f, "far away")


def test_remove_override_restores_extracted_state():
    f = apply_override(field("operating_radius_miles", email="180", pdf="80"), "80")
    restored = remove_override(f)
    assert restored.override is None and restored.effective_state is C
