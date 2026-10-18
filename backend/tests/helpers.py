"""Builders for test data, so each test states only what matters to it."""

import json
import re
from typing import Any, Callable

from app.core.models import Candidate, Evidence, FieldResult, FieldState, SourceDoc
from app.core.normalize import normalize_field
from app.core.reconcile import reconcile_field
from app.llm.client import LLMError

EMAIL = "broker_email.txt"
PDF = "application.pdf"


def cand(name: str, source: str, raw: Any, verified: bool = True) -> Candidate:
    n = normalize_field(name, raw)
    locator = "email body" if source == EMAIL else "PDF page 1"
    return Candidate(
        source_file=source,
        raw_value=raw,
        state=n.state,
        value=n.value,
        note=n.note,
        evidence=Evidence(source_file=source, locator=locator, excerpt=f"{name}: {raw}"),
        verified=verified,
    )


def field(name: str, email: Any = None, pdf: Any = None) -> FieldResult:
    """Reconcile what the email and the application PDF said for one field."""
    return reconcile_field(name, [cand(name, EMAIL, email), cand(name, PDF, pdf)], [EMAIL, PDF])


def known(name: str, value: Any) -> FieldResult:
    return FieldResult(name=name, state=FieldState.KNOWN, value=value)


def state(name: str, s: FieldState) -> FieldResult:
    return FieldResult(name=name, state=s, value=None, reason=f"test {s.value}")


def pdf_doc(filename: str, heading: str, kind: str = "other", readable: bool = True) -> SourceDoc:
    return SourceDoc(
        filename=filename,
        kind=kind,
        readable=readable,
        pages=[f"{heading}\nFictional assessment document"] if readable else [],
        error=None if readable else "PDF could not be parsed",
    )


def csv_doc(text: str, filename: str = "vehicles.csv", readable: bool = True) -> SourceDoc:
    return SourceDoc(filename=filename, kind="vehicle_schedule", readable=readable, pages=[text] if readable else [])


def email_doc() -> SourceDoc:
    return SourceDoc(filename=EMAIL, kind="broker_email", readable=True, pages=["Hello"])


# --- fake models -----------------------------------------------------------------

FIELD_LABELS = {
    "business_name": "Business name",
    "requested_effective_date": "Requested effective date",
    "garaging_state": "Garaging state",
    "operations_description": "Operations(?: description)?",
    "declared_vehicle_count": "Declared vehicle count",
    "operating_radius_miles": "Operating radius miles",
    "hazardous_materials": "Hazardous materials",
}


def reply_json(fields: dict[str, tuple[Any, Any]]) -> str:
    """Build a model reply: {field: (value, excerpt)}."""
    return json.dumps({"fields": {k: {"value": v, "excerpt": e} for k, (v, e) in fields.items()}})


def document_of(user_prompt: str) -> tuple[str, str]:
    m = re.search(r'<document filename="([^"]+)">\n(.*)\n</document>', user_prompt, re.S)
    return m.group(1), m.group(2)


def perfect_reply(document_text: str) -> str:
    """What an ideal model would answer: label/value lines copied verbatim."""
    fields = {}
    for name, label in FIELD_LABELS.items():
        m = re.search(rf"^[ \t]*({label})[ \t]*:?[ \t]+(\S.*?)[ \t]*$", document_text, re.M | re.I)
        if m:
            line = m.group(0).strip()
            fields[name] = (m.group(2), line)
    return reply_json(fields)


def review_payload(user_prompt: str) -> dict:
    m = re.search(r"<review_results>\n(.*)\n</review_results>", user_prompt, re.S)
    return json.loads(m.group(1))


def narrative_reply(payload: dict) -> str:
    """A faithful narrative: names the applicant and status, and asks for every open item."""
    asks = "\n".join(f"- {item}" for item in payload["open_items"]) or "Nothing further is needed."
    return json.dumps({
        "summary": f"{payload['business_name']}: {payload['overall_status']}.",
        "draft_email": f"Subject: Submission follow-up\n\nPlease provide:\n{asks}\n\n[Underwriter name]",
    })


def default_respond(system: str, user: str) -> str:
    if "<document" in user:
        return perfect_reply(document_of(user)[1])
    return narrative_reply(review_payload(user))


class FakeLLM:
    """Test double for LLMClient. `respond(system, user) -> str` decides each reply."""

    name = "fake/test-model"

    def __init__(self, respond: Callable[[str, str], str] | None = None, error: str | None = None):
        self.respond = respond or default_respond
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.error:
            raise LLMError(self.error)
        return self.respond(system, user)


class ScriptedLLM(FakeLLM):
    """Returns the given replies in order (to test retries)."""

    def __init__(self, replies: list[str]):
        it = iter(replies)
        super().__init__(respond=lambda s, u: next(it))
