"""AI-written underwriter summary and draft broker email.

The model receives only the structured review results (never the raw documents),
and the open items it must ask about are computed by code beforehand.
"""

import json

from pydantic import BaseModel, ValidationError, field_validator

from app.core.models import SubmissionResult
from app.core.open_items import FIELD_LABELS
from app.llm.client import LLMClient, LLMError
from app.llm.jsonreply import parse_json_object
from app.llm.prompts import NARRATIVE_SYSTEM, narrative_user_prompt, retry_user_prompt


class NarrativeReply(BaseModel):
    summary: str
    draft_email: str

    @field_validator("summary", "draft_email")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


def narrative_payload(result: SubmissionResult) -> dict:
    fields = result.fields
    return {
        "business_name": fields["business_name"].effective_value if "business_name" in fields else None,
        "overall_status": result.overall_status.value if result.overall_status else None,
        "attachments": [f"{a.filename} ({a.kind.replace('_', ' ')}{'' if a.readable else ', unreadable'})" for a in result.attachments],
        "rules": [{"rule": f"{r.rule_id} {r.name}", "status": r.status.value, "reason": r.reason} for r in result.rules],
        "open_items": [item.text for item in result.open_items],
        "fields": [
            {
                "field": FIELD_LABELS[f.name],
                "state": f.effective_state.value,
                "value": f.effective_value,
                **({"human_override": {"note": f.override.note, "original_state": f.state.value}} if f.override else {}),
            }
            for f in fields.values()
        ],
    }


def generate_narrative(llm: LLMClient, result: SubmissionResult) -> NarrativeReply:
    """Raises LLMError when the model fails or its reply is unusable after one retry."""
    user = narrative_user_prompt(json.dumps(narrative_payload(result), indent=1, default=str))
    reply = llm.complete(NARRATIVE_SYSTEM, user)
    try:
        return _parse(reply)
    except ValueError as first_error:
        reply = llm.complete(NARRATIVE_SYSTEM, retry_user_prompt(user, str(first_error)))
    try:
        return _parse(reply)
    except ValueError as exc:
        raise LLMError(f"Model reply unusable after one retry: {exc}") from None


def _parse(text: str) -> NarrativeReply:
    try:
        return NarrativeReply.model_validate(parse_json_object(text))
    except ValidationError as exc:
        raise ValueError(f"JSON did not match the required shape ({exc.error_count()} error(s))") from None
