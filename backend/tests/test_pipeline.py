"""Whole pipeline on the real sample files, with a fake model (no network)."""

import pytest

from app.core.models import FieldState, OverallStatus, RuleStatus
from app.core.reconcile import apply_override
from app.ingest.loader import UploadedFile, ingest
from app.services.pipeline import apply_rules, process_submission
from helpers import FakeLLM, default_respond

PASS, FLAG, UNKNOWN = RuleStatus.PASS, RuleStatus.FLAG, RuleStatus.UNKNOWN
K, M, C, I = FieldState.KNOWN, FieldState.MISSING, FieldState.CONFLICT, FieldState.INVALID


def packet(samples_dir, name):
    return ingest([UploadedFile(p.name, p.read_bytes()) for p in sorted((samples_dir / name).iterdir())])


def statuses(result):
    return [r.status for r in result.rules]


def stage(result, name):
    return next(s for s in result.stages if s.stage == name)


# The expected-results table from plan.md, section 3.
EXPECTED = {
    "SUB-A": ([PASS, PASS, PASS, PASS, PASS], OverallStatus.READY_FOR_REVIEW),
    "SUB-B": ([FLAG, PASS, UNKNOWN, UNKNOWN, FLAG], OverallStatus.NEEDS_REVIEW),
    "SUB-C": ([PASS, FLAG, UNKNOWN, UNKNOWN, FLAG], OverallStatus.NEEDS_REVIEW),
}


@pytest.mark.parametrize("name", EXPECTED)
def test_samples_match_expected_table(samples_dir, name):
    result = process_submission(packet(samples_dir, name), FakeLLM())
    expected_rules, expected_overall = EXPECTED[name]
    assert statuses(result) == expected_rules
    assert result.overall_status is expected_overall
    assert [s.stage for s in result.stages] == ["ingest", "extract", "rules", "narrative"]
    assert stage(result, "extract").state == "ok"
    assert result.model_name == "fake/test-model"


def test_every_field_is_present_with_evidence_or_sources_checked(samples_dir):
    result = process_submission(packet(samples_dir, "SUB-B"), FakeLLM())
    assert len(result.fields) == 9
    for field in result.fields.values():
        has_evidence = any(c.evidence for c in field.candidates)
        assert has_evidence or field.sources_checked, field.name


def test_sub_c_evidence_in_rule_results(samples_dir):
    result = process_submission(packet(samples_dir, "SUB-C"), FakeLLM())
    r3 = result.rules[2]
    assert "disagree" in r3.reason
    assert {(e.source_file, e.locator) for e in r3.evidence} == {("broker_email.txt", "email body"), ("application.pdf", "PDF page 1")}
    r2 = result.rules[1]
    assert r2.inputs_used["declared_vehicle_count"]["value"] == 4
    assert r2.inputs_used["schedule_vehicle_count"]["value"] == 3


def test_r5_flag_shows_attachment_inventory(samples_dir):
    r5 = process_submission(packet(samples_dir, "SUB-B"), FakeLLM()).rules[4]
    assert r5.status is FLAG
    assert "application.pdf" in r5.evidence[0].observation and "vehicles.csv" in r5.evidence[0].observation


def test_override_then_rerun(samples_dir):
    result = process_submission(packet(samples_dir, "SUB-C"), FakeLLM())
    result.fields["operating_radius_miles"] = apply_override(result.fields["operating_radius_miles"], "80", "Confirmed by broker")
    apply_rules(result)
    assert statuses(result) == [PASS, FLAG, PASS, UNKNOWN, FLAG]
    assert result.fields["operating_radius_miles"].state is C  # original kept
    assert [s.stage for s in result.stages] == ["ingest", "extract", "rules", "narrative"]


def test_overrides_can_make_a_packet_ready(samples_dir):
    result = process_submission(packet(samples_dir, "SUB-B"), FakeLLM())
    for name, value in [("requested_effective_date", "2026-12-01"), ("operating_radius_miles", "90"),
                        ("hazardous_materials", "No"), ("loss_runs_present", "Yes")]:
        result.fields[name] = apply_override(result.fields[name], value, "Confirmed with broker")
    assert apply_rules(result).overall_status is OverallStatus.READY_FOR_REVIEW


# --- model failure: deterministic results survive -------------------------------------------

def test_model_down_keeps_deterministic_results(samples_dir):
    result = process_submission(packet(samples_dir, "SUB-A"), FakeLLM(error="Could not reach the Anthropic API (network error)."))
    assert stage(result, "extract").state == "failed"
    assert "network error" in stage(result, "extract").message
    assert result.fields["schedule_vehicle_count"].value == 3  # CSV still counted
    assert result.rules[4].status is PASS  # loss runs still detected
    # Unread is not the same as absent: R1 must not claim the name is missing.
    assert result.fields["business_name"].state is I
    assert "Extraction failed" in result.fields["business_name"].reason
    assert statuses(result) == [UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN, PASS]
    assert result.overall_status is OverallStatus.NEEDS_REVIEW


def test_no_model_configured(samples_dir):
    result = process_submission(packet(samples_dir, "SUB-A"), llm=None, llm_error="No model configured")
    assert stage(result, "extract").state == "failed"
    assert "No model configured" in stage(result, "extract").message
    assert result.model_name is None
    assert result.rules[4].status is PASS


def test_one_source_failing_is_reported(samples_dir):
    def flaky(system, user):
        if 'filename="application.pdf"' in user:
            return "not json"
        return default_respond(system, user)

    result = process_submission(packet(samples_dir, "SUB-C"), FakeLLM(flaky))
    assert stage(result, "extract").state == "failed"
    radius = result.fields["operating_radius_miles"]
    # Only the email was read, so the 180 vs 80 conflict cannot be seen; the reason says so.
    assert radius.state is K and "application.pdf" in radius.reason


# --- paste-only and partial packets -------------------------------------------------------------

def test_paste_only(samples_dir):
    text = (samples_dir / "SUB-A" / "broker_email.txt").read_text()
    result = process_submission(ingest([], pasted_email=text), FakeLLM())
    assert result.paste_only is True
    assert statuses(result) == [PASS, UNKNOWN, PASS, PASS, UNKNOWN]
    assert result.rules[4].reason == "No attachments supplied (paste-only mode)"


def test_csv_only_skips_extraction(samples_dir):
    csv = samples_dir / "SUB-A" / "vehicles.csv"
    llm = FakeLLM()
    result = process_submission(ingest([UploadedFile(csv.name, csv.read_bytes())]), llm)
    assert stage(result, "extract").state == "skipped"
    assert not any("<document" in user for _, user in llm.calls)  # nothing sent for extraction
    assert result.fields["schedule_vehicle_count"].value == 3


def test_each_run_gets_a_new_id(samples_dir):
    a = process_submission(packet(samples_dir, "SUB-A"), FakeLLM())
    b = process_submission(packet(samples_dir, "SUB-A"), FakeLLM())
    assert a.submission_id != b.submission_id


def test_duplicate_vin_reaches_open_items_and_draft_email_input(samples_dir):
    """The draft email is written from open_items, so the duplicate must be listed there."""
    from app.llm.narrative import narrative_payload

    files = [UploadedFile(p.name, p.read_bytes()) for p in sorted((samples_dir / "SUB-A").iterdir()) if p.name != "vehicles.csv"]
    files.append(UploadedFile("vehicles.csv", b"vehicle_id,vin,year,make,model\nV1,VIN111,2022,Ford,T\nV2,VIN111,2022,Ford,T\nV3,VIN333,2021,Ford,T\n"))
    result = process_submission(ingest(files), FakeLLM())

    assert result.rules[1].status is UNKNOWN  # R2: count cannot be trusted
    items = [i.text for i in result.open_items if i.rule_id == "R2"]
    assert len(items) == 1 and "the same VIN VIN111 for vehicles V1, V2" in items[0]
    assert items[0] in narrative_payload(result)["open_items"]
