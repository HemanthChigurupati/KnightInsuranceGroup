"""Ask the model what each document says, one document per call.

Sources are extracted separately (email, then each application PDF) so the model
never sees two sources together and cannot pick a winner; core/reconcile.py
compares them in code.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.core.models import EXTRACTED_FIELDS, Candidate, Evidence, SourceDoc
from app.core.normalize import normalize_field
from app.llm.client import LLMClient, LLMError
from app.llm.evidence import locate_excerpt, value_in_excerpt
from app.llm.jsonreply import parse_json_object
from app.llm.prompts import EXTRACTION_SYSTEM, extraction_user_prompt, retry_user_prompt

EXTRACTABLE_KINDS = ("broker_email", "application")


class FieldAnswer(BaseModel):
    value: str | int | float | bool | None = None
    excerpt: str | None = None


class ExtractionReply(BaseModel):
    fields: dict[str, FieldAnswer]


@dataclass
class SourceExtraction:
    filename: str
    candidates: dict[str, Candidate] = field(default_factory=dict)
    error: str | None = None  # set when this source could not be extracted


@dataclass
class ExtractionResult:
    sources: list[SourceExtraction]
    sources_checked: list[str]  # documents whose extraction succeeded

    @property
    def errors(self) -> list[str]:
        return [f"{s.filename}: {s.error}" for s in self.sources if s.error]

    def candidates_for(self, name: str) -> list[Candidate]:
        return [s.candidates[name] for s in self.sources if name in s.candidates]


def parse_reply(text: str) -> ExtractionReply:
    data = parse_json_object(text)
    try:
        return ExtractionReply.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"JSON did not match the required shape ({exc.error_count()} error(s))") from None


def _candidate(doc: SourceDoc, name: str, answer: FieldAnswer) -> Candidate:
    locator = locate_excerpt(doc, answer.excerpt)
    verified = locator is not None and value_in_excerpt(answer.value, answer.excerpt or "")
    normalized = normalize_field(name, answer.value)
    note = normalized.note
    if not verified:
        note = "Excerpt not found in the document, so this value is not used"
    return Candidate(
        source_file=doc.filename,
        raw_value=answer.value,
        state=normalized.state,
        value=normalized.value,
        note=note,
        evidence=Evidence(
            source_file=doc.filename,
            locator=locator or "not found in document",
            excerpt=answer.excerpt,
        ),
        verified=verified,
    )


def extract_source(llm: LLMClient, doc: SourceDoc) -> SourceExtraction:
    out = SourceExtraction(filename=doc.filename)
    user = extraction_user_prompt(doc.filename, doc.text)
    try:
        reply_text = llm.complete(EXTRACTION_SYSTEM, user)
        try:
            reply = parse_reply(reply_text)
        except ValueError as first_error:
            reply_text = llm.complete(EXTRACTION_SYSTEM, retry_user_prompt(user, str(first_error)))
            reply = parse_reply(reply_text)
    except LLMError as exc:
        out.error = str(exc)
        return out
    except ValueError as exc:
        out.error = f"Model reply unusable after one retry: {exc}"
        return out

    for name in EXTRACTED_FIELDS:  # unknown keys from the model are ignored
        answer = reply.fields.get(name)
        if answer is None or answer.value is None:
            continue  # not mentioned in this document
        out.candidates[name] = _candidate(doc, name, answer)
    return out


def extraction_targets(docs: list[SourceDoc]) -> list[SourceDoc]:
    return [d for d in docs if d.kind in EXTRACTABLE_KINDS and d.readable]


def extract_all(llm: LLMClient, docs: list[SourceDoc]) -> ExtractionResult:
    sources = [extract_source(llm, d) for d in extraction_targets(docs)]
    checked = [s.filename for s in sources if not s.error]
    return ExtractionResult(sources=sources, sources_checked=checked)
