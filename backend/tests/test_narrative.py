"""Open items (code) and the AI narrative (fake model): what the broker is asked for."""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_llm, get_store
from app.core.reconcile import apply_override
from app.ingest.loader import UploadedFile, ingest
from app.llm.client import LLMError
from app.llm.narrative import generate_narrative, narrative_payload
from app.llm.prompts import NARRATIVE_SYSTEM
from app.main import app
from app.services.pipeline import process_submission, rerun_after_override, write_narrative
from app.services.store import SubmissionStore
from helpers import FakeLLM, ScriptedLLM, review_payload


def run(samples_dir, name, llm=None):
    files = [UploadedFile(p.name, p.read_bytes()) for p in sorted((samples_dir / name).iterdir())]
    return process_submission(ingest(files), llm or FakeLLM())


def item_texts(result):
    return [i.text for i in result.open_items]


def stage(result, name):
    return next(s for s in result.stages if s.stage == name)


# --- open items: computed by code -------------------------------------------------------

def test_sub_a_has_no_open_items(samples_dir):
    assert run(samples_dir, "SUB-A").open_items == []


def test_sub_b_open_items(samples_dir):
    texts = item_texts(run(samples_dir, "SUB-B"))
    assert texts == [
        "Requested effective date was not provided",
        "Operating radius (miles) was not provided",
        "Hazardous materials answer was not provided",
        "No loss runs or loss history report is attached",
    ]


def test_sub_c_open_items_quote_both_sources(samples_dir):
    items = run(samples_dir, "SUB-C").open_items
    by_rule = {i.rule_id: i.text for i in items}
    assert by_rule["R2"] == "Declared vehicle count (4) does not match the vehicle schedule (3 vehicles)"
    assert "broker_email.txt says '180'" in by_rule["R3"] and "application.pdf says '80'" in by_rule["R3"]
    assert "'Yes'" in by_rule["R4"] and "'No'" in by_rule["R4"]
    assert by_rule["R5"] == "No loss runs or loss history report is attached"


def test_override_removes_the_open_item(samples_dir):
    result = run(samples_dir, "SUB-C")
    result.fields["operating_radius_miles"] = apply_override(result.fields["operating_radius_miles"], "80")
    rerun_after_override(result)
    assert "R3" not in {i.rule_id for i in result.open_items}


def test_radius_above_threshold_item():
    from helpers import field, known
    from app.core.open_items import build_open_items
    from app.core.rules import evaluate_rules

    fields = {name: known(name, v) for name, v in [
        ("business_name", "A"), ("requested_effective_date", "2026-11-01"), ("garaging_state", "CA"),
        ("operations_description", "x"), ("declared_vehicle_count", 1), ("schedule_vehicle_count", 1),
        ("hazardous_materials", False), ("loss_runs_present", True)]}
    fields["operating_radius_miles"] = field("operating_radius_miles", email="180")
    items = build_open_items(fields, evaluate_rules(fields))
    assert [i.text for i in items] == ["Operating radius of 180 miles is above 150 miles"]


def test_unruled_missing_field_is_still_asked_for(samples_dir):
    text = (samples_dir / "SUB-A" / "broker_email.txt").read_text().replace("Garaging state: CA", "Garaging state: Not provided")
    result = process_submission(ingest([], pasted_email=text), FakeLLM())
    assert "Garaging state was not provided" in item_texts(result)


def test_paste_only_item(samples_dir):
    text = (samples_dir / "SUB-A" / "broker_email.txt").read_text()
    texts = item_texts(process_submission(ingest([], pasted_email=text), FakeLLM()))
    assert "No attachments were supplied, so the loss runs report could not be checked" in texts
    assert "No vehicle schedule (CSV) was supplied" in texts


# --- narrative: what the model is given ----------------------------------------------------

def test_model_gets_structured_results_not_raw_documents(samples_dir):
    llm = FakeLLM()
    run(samples_dir, "SUB-C", llm)
    system, user = llm.calls[-1]
    assert system == NARRATIVE_SYSTEM
    assert "<document" not in user
    payload = review_payload(user)
    assert payload["overall_status"] == "NEEDS_REVIEW"
    assert payload["open_items"] == item_texts(run(samples_dir, "SUB-C"))
    assert {r["status"] for r in payload["rules"]} == {"PASS", "FLAG", "UNKNOWN"}


def test_payload_marks_human_overrides(samples_dir):
    result = run(samples_dir, "SUB-C")
    result.fields["operating_radius_miles"] = apply_override(result.fields["operating_radius_miles"], "80", "Phoned broker")
    radius = next(f for f in narrative_payload(rerun_after_override(result))["fields"] if f["field"] == "Operating radius (miles)")
    assert radius == {"field": "Operating radius (miles)", "state": "KNOWN", "value": 80,
                      "human_override": {"note": "Phoned broker", "original_state": "CONFLICT"}}


def test_prompt_forbids_coverage_decisions_and_injection():
    assert "Do not approve, decline, quote, price or bind coverage" in NARRATIVE_SYSTEM
    assert "untrusted data" in NARRATIVE_SYSTEM
    assert "asking for every" in NARRATIVE_SYSTEM


# --- narrative: success, retry, failure ---------------------------------------------------------

def test_narrative_is_stored_and_labelled(samples_dir):
    result = run(samples_dir, "SUB-B")
    assert result.narrative_source == "llm" and result.narrative_stale is False
    assert result.summary == "Canyon Parcel Example LLC: NEEDS_REVIEW."
    assert result.draft_email.startswith("Subject:")
    for item in item_texts(result):
        assert item in result.draft_email
    assert stage(result, "narrative").message == "Written by fake/test-model"


def test_ready_packet_email_asks_for_nothing(samples_dir):
    assert "Nothing further is needed." in run(samples_dir, "SUB-A").draft_email


def test_bad_reply_is_retried_once(samples_dir):
    result = run(samples_dir, "SUB-A")
    good = '{"summary": "ok", "draft_email": "Subject: x"}'
    reply = generate_narrative(ScriptedLLM(["not json", good]), result)
    assert reply.summary == "ok"


@pytest.mark.parametrize("bad", ['{"summary": "only one key"}', '{"summary": " ", "draft_email": "x"}', "nope"])
def test_unusable_reply_twice_raises(samples_dir, bad):
    result = run(samples_dir, "SUB-A")
    with pytest.raises(LLMError, match="after one retry"):
        generate_narrative(ScriptedLLM([bad, bad]), result)


def test_model_failure_never_produces_fake_text(samples_dir):
    result = run(samples_dir, "SUB-B", FakeLLM(error="The model call timed out."))
    assert result.summary is None and result.draft_email is None
    assert result.narrative_source == "unavailable"
    assert stage(result, "narrative").state == "failed"
    assert "timed out" in stage(result, "narrative").message
    assert len(result.open_items) > 0  # the deterministic list is still available to show


def test_override_marks_narrative_stale_and_regenerate_clears_it(samples_dir):
    result = run(samples_dir, "SUB-C")
    result.fields["operating_radius_miles"] = apply_override(result.fields["operating_radius_miles"], "80")
    rerun_after_override(result)
    assert result.narrative_stale is True
    write_narrative(result, FakeLLM())
    assert result.narrative_stale is False
    assert "180" not in result.draft_email  # the resolved conflict is no longer asked about


def test_failed_regeneration_keeps_previous_text(samples_dir):
    result = run(samples_dir, "SUB-C")
    before = result.draft_email
    result.fields["operating_radius_miles"] = apply_override(result.fields["operating_radius_miles"], "80")
    rerun_after_override(result)
    write_narrative(result, FakeLLM(error="Rate limited by the Anthropic API. Try again shortly."))
    assert result.draft_email == before
    assert result.narrative_source == "llm" and result.narrative_stale is True
    assert stage(result, "narrative").state == "failed"


# --- API ------------------------------------------------------------------------------------------

def test_api_regenerate_after_override(samples_dir):
    llm = FakeLLM()
    test_store = SubmissionStore()
    app.dependency_overrides[get_store] = lambda: test_store
    app.dependency_overrides[get_llm] = lambda: llm
    try:
        c = TestClient(app)
        files = [("files", (p.name, p.read_bytes())) for p in sorted((samples_dir / "SUB-C").iterdir())]
        sid = c.post("/api/submissions", files=files).json()["submission_id"]
        body = c.put(f"/api/submissions/{sid}/overrides/operating_radius_miles", json={"value": "80"}).json()
        assert body["narrative_stale"] is True
        calls = len(llm.calls)
        body = c.post(f"/api/submissions/{sid}/narrative").json()
        assert len(llm.calls) == calls + 1
        assert body["narrative_stale"] is False and "180" not in body["draft_email"]
    finally:
        app.dependency_overrides.clear()


def test_api_regenerate_without_model_reports_failure(samples_dir):
    test_store = SubmissionStore()
    app.dependency_overrides[get_store] = lambda: test_store
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    try:
        c = TestClient(app)
        files = [("files", (p.name, p.read_bytes())) for p in sorted((samples_dir / "SUB-B").iterdir())]
        sid = c.post("/api/submissions", files=files).json()["submission_id"]
        app.dependency_overrides[get_llm] = lambda: LLMError("No model configured for provider 'anthropic'.")
        body = c.post(f"/api/submissions/{sid}/narrative").json()
        narrative = next(s for s in body["stages"] if s["stage"] == "narrative")
        assert narrative["state"] == "failed" and "No model configured" in narrative["message"]
        assert body["draft_email"]  # previous text kept
    finally:
        app.dependency_overrides.clear()
