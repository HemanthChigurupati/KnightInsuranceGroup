"""HTTP API with a fake model and a fresh store per test."""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_llm, get_store
from app.llm.client import LLMError
from app.main import app
from app.services.store import SubmissionStore
from helpers import FakeLLM


@pytest.fixture
def client():
    test_store = SubmissionStore()
    app.dependency_overrides[get_store] = lambda: test_store
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, samples_dir, packet, **form):
    files = [("files", (p.name, p.read_bytes())) for p in sorted((samples_dir / packet).iterdir())]
    return client.post("/api/submissions", files=files, data=form)


def rule_statuses(body):
    return [r["status"] for r in body["rules"]]


def test_upload_returns_full_result(client, samples_dir):
    resp = upload(client, samples_dir, "SUB-C")
    assert resp.status_code == 201
    body = resp.json()
    assert rule_statuses(body) == ["PASS", "FLAG", "UNKNOWN", "UNKNOWN", "FLAG"]
    assert body["overall_status"] == "NEEDS_REVIEW"
    radius = body["fields"]["operating_radius_miles"]
    assert radius["state"] == "CONFLICT" and radius["value"] is None
    assert {c["raw_value"] for c in radius["candidates"]} == {"180", "80"}
    assert [a["filename"] for a in body["attachments"]] == ["application.pdf", "broker_email.txt", "vehicles.csv"]


def test_get_returns_the_stored_result(client, samples_dir):
    sid = upload(client, samples_dir, "SUB-A").json()["submission_id"]
    resp = client.get(f"/api/submissions/{sid}")
    assert resp.status_code == 200 and resp.json()["overall_status"] == "READY_FOR_REVIEW"


def test_override_reruns_rules_and_keeps_original(client, samples_dir):
    sid = upload(client, samples_dir, "SUB-C").json()["submission_id"]
    resp = client.put(f"/api/submissions/{sid}/overrides/operating_radius_miles",
                      json={"value": "80", "note": "Confirmed by broker by phone"})
    assert resp.status_code == 200
    body = resp.json()
    assert rule_statuses(body) == ["PASS", "FLAG", "PASS", "UNKNOWN", "FLAG"]
    radius = body["fields"]["operating_radius_miles"]
    assert radius["state"] == "CONFLICT"  # original extraction kept
    assert radius["effective_value"] == 80 and radius["effective_state"] == "KNOWN"
    assert radius["override"]["note"] == "Confirmed by broker by phone"
    assert len(radius["candidates"]) == 2
    assert body["rules"][2]["inputs_used"]["operating_radius_miles"]["human_override"] is True
    # and the change is persisted
    assert rule_statuses(client.get(f"/api/submissions/{sid}").json())[2] == "PASS"


def test_override_does_not_call_the_model(samples_dir):
    llm = FakeLLM()
    test_store = SubmissionStore()
    app.dependency_overrides[get_store] = lambda: test_store
    app.dependency_overrides[get_llm] = lambda: llm
    try:
        c = TestClient(app)
        sid = upload(c, samples_dir, "SUB-C").json()["submission_id"]
        calls = len(llm.calls)
        c.put(f"/api/submissions/{sid}/overrides/operating_radius_miles", json={"value": 80})
        assert len(llm.calls) == calls
    finally:
        app.dependency_overrides.clear()


def test_remove_override_restores_result(client, samples_dir):
    sid = upload(client, samples_dir, "SUB-C").json()["submission_id"]
    client.put(f"/api/submissions/{sid}/overrides/operating_radius_miles", json={"value": "80"})
    body = client.delete(f"/api/submissions/{sid}/overrides/operating_radius_miles").json()
    assert body["fields"]["operating_radius_miles"]["override"] is None
    assert rule_statuses(body)[2] == "UNKNOWN"


def test_invalid_override_is_rejected(client, samples_dir):
    sid = upload(client, samples_dir, "SUB-C").json()["submission_id"]
    resp = client.put(f"/api/submissions/{sid}/overrides/operating_radius_miles", json={"value": "far away"})
    assert resp.status_code == 422
    assert "not usable" in resp.json()["detail"]


def test_unknown_field_and_submission_are_404(client, samples_dir):
    sid = upload(client, samples_dir, "SUB-A").json()["submission_id"]
    assert client.put(f"/api/submissions/{sid}/overrides/premium", json={"value": "1"}).status_code == 404
    assert client.get("/api/submissions/does-not-exist").status_code == 404


def test_packets_are_isolated(client, samples_dir):
    c_body = upload(client, samples_dir, "SUB-C").json()
    a_body = upload(client, samples_dir, "SUB-A").json()
    assert a_body["submission_id"] != c_body["submission_id"]
    assert a_body["fields"]["business_name"]["value"] == "Pacific Harbor Delivery Example LLC"
    assert "Metro Route" not in str(a_body)
    # overriding one packet does not touch the other
    client.put(f"/api/submissions/{c_body['submission_id']}/overrides/operating_radius_miles", json={"value": "80"})
    assert client.get(f"/api/submissions/{a_body['submission_id']}").json()["fields"]["operating_radius_miles"]["override"] is None


def test_paste_only_upload(client, samples_dir):
    text = (samples_dir / "SUB-A" / "broker_email.txt").read_text()
    body = client.post("/api/submissions", data={"email_text": text}).json()
    assert body["paste_only"] is True
    assert rule_statuses(body) == ["PASS", "UNKNOWN", "PASS", "PASS", "UNKNOWN"]


def test_empty_upload_is_400(client):
    resp = client.post("/api/submissions", data={"email_text": "  "})
    assert resp.status_code == 400
    assert "Upload the submission files" in resp.json()["detail"]


def test_two_emails_is_400(client, samples_dir):
    resp = upload(client, samples_dir, "SUB-A", email_text="Another email")
    assert resp.status_code == 400


def test_no_model_configured_still_returns_rules(samples_dir):
    app.dependency_overrides[get_store] = lambda: SubmissionStore()
    app.dependency_overrides[get_llm] = lambda: LLMError("No model configured for provider 'anthropic'.")
    try:
        body = upload(TestClient(app), samples_dir, "SUB-A").json()
        extract = next(s for s in body["stages"] if s["stage"] == "extract")
        assert extract["state"] == "failed" and "No model configured" in extract["message"]
        assert body["fields"]["schedule_vehicle_count"]["value"] == 3
        assert body["rules"][4]["status"] == "PASS"
    finally:
        app.dependency_overrides.clear()


def test_api_never_returns_the_key(samples_dir):
    from pydantic import SecretStr

    from app.config import Settings, get_settings

    secret = "sk-ant-should-never-appear"
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, llm_model="claude-opus-5-5", anthropic_api_key=SecretStr(secret))
    app.dependency_overrides[get_store] = lambda: SubmissionStore()
    try:
        c = TestClient(app)
        assert secret not in c.get("/api/health").text
    finally:
        app.dependency_overrides.clear()


def test_reanalysis_with_late_file_keeps_corrections(client, samples_dir):
    """Email + application first; loss runs arrive later and the packet is re-analysed."""
    early = [p for p in sorted((samples_dir / "SUB-A").iterdir()) if p.name != "loss_runs.pdf"]
    first = client.post("/api/submissions", files=[("files", (p.name, p.read_bytes())) for p in early]).json()
    assert first["fields"]["loss_runs_present"]["effective_value"] is False

    overrides = '{"garaging_state": {"value": "CA", "note": "Confirmed by phone"}}'
    second = upload(client, samples_dir, "SUB-A", overrides=overrides)
    assert second.status_code == 201
    body = second.json()
    assert body["fields"]["loss_runs_present"]["effective_value"] is True
    assert body["fields"]["garaging_state"]["override"]["note"] == "Confirmed by phone"
    assert body["fields"]["garaging_state"]["effective_value"] == "CA"


@pytest.mark.parametrize("overrides", ["not json", '{"no_such_field": {"value": 1}}', '{"operating_radius_miles": {"value": "far"}}'])
def test_bad_carried_overrides_are_rejected(client, samples_dir, overrides):
    assert upload(client, samples_dir, "SUB-A", overrides=overrides).status_code == 422
