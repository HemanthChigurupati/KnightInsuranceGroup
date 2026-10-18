"""Rule logic, tested without any model calls.

Covers the brief's required cases: missing information, a source conflict,
unequal vehicle counts and the 150-mile threshold.
"""

import pytest

from app.core.derived import loss_runs_present, schedule_vehicle_count
from app.core.models import FieldState, OverallStatus, RuleStatus
from app.core.reconcile import apply_override
from app.core.rules import (
    PASTE_ONLY_REASON,
    evaluate_rules,
    overall_status,
    r1_identity,
    r2_vehicle_count,
    r3_radius,
    r4_hazmat,
    r5_loss_runs,
)
from helpers import csv_doc, email_doc, field, known, pdf_doc, state

PASS, FLAG, UNKNOWN = RuleStatus.PASS, RuleStatus.FLAG, RuleStatus.UNKNOWN
K, M, C, I = FieldState.KNOWN, FieldState.MISSING, FieldState.CONFLICT, FieldState.INVALID
NAME, DATE = "business_name", "requested_effective_date"


# --- R1 Required identity and effective date --------------------------------------

def test_r1_pass():
    assert r1_identity(known(NAME, "Acme LLC"), known(DATE, "2026-11-01")).status is PASS


@pytest.mark.parametrize("name_state,date_state", [(M, K), (K, M), (M, M)])
def test_r1_flag_when_absent(name_state, date_state):
    name = known(NAME, "Acme") if name_state is K else state(NAME, M)
    date = known(DATE, "2026-11-01") if date_state is K else state(DATE, M)
    result = r1_identity(name, date)
    assert result.status is FLAG
    assert "Missing" in result.reason


@pytest.mark.parametrize("bad", [I, C])
def test_r1_unknown_when_invalid_or_conflicting(bad):
    assert r1_identity(known(NAME, "Acme"), state(DATE, bad)).status is UNKNOWN


@pytest.mark.parametrize("other", [I, C])
def test_r1_missing_takes_precedence_over_unresolved(other):
    # Brief: a missing required field produces FLAG even if the other is conflicting or invalid.
    assert r1_identity(state(NAME, other), state(DATE, M)).status is FLAG


def test_r1_names_differing_only_in_case_pass():
    name = field(NAME, email="Metro Route Example LLC", pdf="METRO ROUTE EXAMPLE LLC")
    assert r1_identity(name, known(DATE, "2026-11-01")).status is PASS


def test_r1_conflicting_names_are_unknown():
    name = field(NAME, email="Metro Route Example LLC", pdf="Other Name LLC")
    assert r1_identity(name, known(DATE, "2026-11-01")).status is UNKNOWN


# --- R2 Vehicle count reconciliation --------------------------------------------------

def test_r2_pass_when_equal():
    assert r2_vehicle_count(known("declared_vehicle_count", 3), known("schedule_vehicle_count", 3)).status is PASS


def test_r2_flag_when_counts_differ():
    # SUB-C: declared 4, CSV has 3
    result = r2_vehicle_count(known("declared_vehicle_count", 4), known("schedule_vehicle_count", 3))
    assert result.status is FLAG
    assert "4" in result.reason and "3" in result.reason


def test_r2_zero_equals_zero_passes():
    assert r2_vehicle_count(known("declared_vehicle_count", 0), known("schedule_vehicle_count", 0)).status is PASS


@pytest.mark.parametrize("bad", [M, I, C])
def test_r2_unknown_when_declared_unresolved(bad):
    assert r2_vehicle_count(state("declared_vehicle_count", bad), known("schedule_vehicle_count", 3)).status is UNKNOWN


@pytest.mark.parametrize("bad", [M, I, C])
def test_r2_unknown_when_schedule_unresolved(bad):
    assert r2_vehicle_count(known("declared_vehicle_count", 3), state("schedule_vehicle_count", bad)).status is UNKNOWN


def test_r2_unknown_when_csv_cannot_be_parsed():
    schedule = schedule_vehicle_count([csv_doc("not,a,vehicle,file\n")])
    assert r2_vehicle_count(known("declared_vehicle_count", 3), schedule).status is UNKNOWN


# --- R3 Operating radius ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [("150", PASS), ("150.0", PASS), (150, PASS), ("149.9", PASS), ("0", PASS), ("80", PASS),
     ("150.1", FLAG), ("151", FLAG), ("180", FLAG)],
)
def test_r3_threshold(raw, expected):
    assert r3_radius(field("operating_radius_miles", email=raw, pdf=raw)).status is expected


@pytest.mark.parametrize("raw", ["-5", "far", "Not provided"])
def test_r3_unknown_for_negative_nonnumeric_or_missing(raw):
    assert r3_radius(field("operating_radius_miles", email=raw)).status is UNKNOWN


def test_r3_conflict_is_unknown_before_threshold():
    # SUB-C: email 180 would FLAG on its own, but the conflict must come first.
    assert r3_radius(field("operating_radius_miles", email="180", pdf="80")).status is UNKNOWN


def test_r3_override_resolves_conflict():
    radius = apply_override(field("operating_radius_miles", email="180", pdf="80"), "80", "Confirmed by broker")
    result = r3_radius(radius)
    assert result.status is PASS
    assert result.inputs_used["operating_radius_miles"]["human_override"] is True
    assert len(result.evidence) == 2  # original evidence still reported


# --- R4 Hazardous materials --------------------------------------------------------------

def test_r4_explicit_no_passes():
    assert r4_hazmat(field("hazardous_materials", email="No", pdf="No")).status is PASS


def test_r4_explicit_yes_flags():
    assert r4_hazmat(field("hazardous_materials", email="Yes")).status is FLAG


@pytest.mark.parametrize("email,pdf", [("Not provided", "Not provided"), ("Sometimes", None), ("Yes", "No")])
def test_r4_unknown_for_missing_ambiguous_or_conflicting(email, pdf):
    assert r4_hazmat(field("hazardous_materials", email=email, pdf=pdf)).status is UNKNOWN


# --- R5 Loss runs attachment ---------------------------------------------------------------

def test_r5_pass_with_report():
    f = loss_runs_present([pdf_doc("loss_runs.pdf", "Loss runs report", "loss_runs")])
    assert r5_loss_runs(f, paste_only=False).status is PASS


def test_r5_flag_when_only_promised():
    f = loss_runs_present([email_doc(), pdf_doc("application.pdf", "Commercial auto application", "application")])
    assert r5_loss_runs(f, paste_only=False).status is FLAG


def test_r5_unknown_when_unreadable():
    f = loss_runs_present([email_doc(), pdf_doc("scan.pdf", "", "unknown", readable=False)])
    assert r5_loss_runs(f, paste_only=False).status is UNKNOWN


def test_r5_paste_only_is_unknown():
    result = r5_loss_runs(loss_runs_present([email_doc()]), paste_only=True)
    assert result.status is UNKNOWN
    assert result.reason == PASTE_ONLY_REASON


def test_r5_paste_only_override_is_respected():
    f = apply_override(loss_runs_present([email_doc()]), "Yes", "Report received separately")
    assert r5_loss_runs(f, paste_only=True).status is PASS


# --- Overall ----------------------------------------------------------------------------------

def test_overall_ready_only_when_all_pass():
    all_pass = [r1_identity(known(NAME, "A"), known(DATE, "2026-11-01"))] * 5
    assert overall_status(all_pass) is OverallStatus.READY_FOR_REVIEW
    one_unknown = all_pass[:4] + [r4_hazmat(state("hazardous_materials", M))]
    assert overall_status(one_unknown) is OverallStatus.NEEDS_REVIEW


def test_overall_with_no_results_is_not_ready():
    assert overall_status([]) is OverallStatus.NEEDS_REVIEW


# --- The three sample packets, built from their documented values ----------------------------
# Values typed from the sample email and application PDF; the LLM is not involved here.

APP = pdf_doc("application.pdf", "Commercial auto application", "application")
LOSS = pdf_doc("loss_runs.pdf", "Loss runs report", "loss_runs")

PACKETS = {
    "SUB-A": dict(
        email={NAME: "Pacific Harbor Delivery Example LLC", DATE: "2026-11-01", "garaging_state": "CA",
               "operations_description": "Local delivery of packaged retail goods.", "declared_vehicle_count": "3",
               "operating_radius_miles": "150", "hazardous_materials": "No"},
        pdf={NAME: "Pacific Harbor Delivery Example LLC", DATE: "2026-11-01", "garaging_state": "California",
             "operations_description": "Local delivery of packaged retail goods", "declared_vehicle_count": "3",
             "operating_radius_miles": "150", "hazardous_materials": "No"},
        attachments=[APP, LOSS],
        expected=[PASS, PASS, PASS, PASS, PASS],
        overall=OverallStatus.READY_FOR_REVIEW,
    ),
    "SUB-B": dict(
        email={NAME: "Canyon Parcel Example LLC", DATE: "Not provided", "garaging_state": "CA",
               "operations_description": "Local delivery of packaged retail goods.", "declared_vehicle_count": "2",
               "operating_radius_miles": "Not provided", "hazardous_materials": "Not provided"},
        pdf={NAME: "Canyon Parcel Example LLC", DATE: "Not provided", "garaging_state": "California",
             "operations_description": "Local delivery of packaged retail goods", "declared_vehicle_count": "2",
             "operating_radius_miles": "Not provided", "hazardous_materials": "Not provided"},
        attachments=[APP],
        expected=[FLAG, PASS, UNKNOWN, UNKNOWN, FLAG],
        overall=OverallStatus.NEEDS_REVIEW,
    ),
    "SUB-C": dict(
        email={NAME: "Metro Route Example LLC", DATE: "2026-11-01", "garaging_state": "CA",
               "operations_description": "Local delivery of packaged retail goods.", "declared_vehicle_count": "4",
               "operating_radius_miles": "180", "hazardous_materials": "Yes"},
        pdf={NAME: "Metro Route Example LLC", DATE: "2026-11-01", "garaging_state": "California",
             "operations_description": "Local delivery of packaged retail goods", "declared_vehicle_count": "4",
             "operating_radius_miles": "80", "hazardous_materials": "No"},
        attachments=[APP],
        expected=[PASS, FLAG, UNKNOWN, UNKNOWN, FLAG],
        overall=OverallStatus.NEEDS_REVIEW,
    ),
}


@pytest.mark.parametrize("packet", PACKETS)
def test_sample_packets_match_expected_table(packet, samples_dir):
    p = PACKETS[packet]
    csv = csv_doc((samples_dir / packet / "vehicles.csv").read_text())
    docs = [email_doc(), *p["attachments"], csv]
    fields = {name: field(name, email=p["email"][name], pdf=p["pdf"][name]) for name in p["email"]}
    fields["schedule_vehicle_count"] = schedule_vehicle_count(docs)
    fields["loss_runs_present"] = loss_runs_present(docs)

    assert fields["garaging_state"].state is K  # CA vs California never conflicts
    results = evaluate_rules(fields)
    assert [r.status for r in results] == p["expected"]
    assert overall_status(results) is p["overall"]


def test_paste_only_gives_unknown_for_r2_and_r5():
    docs = [email_doc()]
    fields = {name: field(name, email=v) for name, v in PACKETS["SUB-A"]["email"].items()}
    fields["schedule_vehicle_count"] = schedule_vehicle_count(docs)
    fields["loss_runs_present"] = loss_runs_present(docs)
    statuses = [r.status for r in evaluate_rules(fields, paste_only=True)]
    assert statuses == [PASS, UNKNOWN, PASS, PASS, UNKNOWN]
