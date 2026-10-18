from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import TypeAdapter, ValidationError

from app.api.schemas import OverrideRequest
from app.config import Settings, get_settings
from app.core.models import ALL_FIELDS, FieldState, SubmissionResult
from app.core.normalize import normalize_field
from app.core.reconcile import apply_override, remove_override
from app.ingest.loader import IngestError, UploadedFile, ingest
from app.llm.client import LLMClient, LLMError, get_llm_client
from app.services.pipeline import process_submission, rerun_after_override, write_narrative
from app.services.store import SubmissionStore, store

router = APIRouter(prefix="/api")
_OVERRIDES = TypeAdapter(dict[str, OverrideRequest])


def get_store() -> SubmissionStore:
    return store


def get_llm(settings: Settings = Depends(get_settings)) -> LLMClient | LLMError:
    """The configured model, or the reason there is none (shown as a failed extract stage)."""
    try:
        return get_llm_client(settings)
    except LLMError as exc:
        return exc


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness check plus which model is configured. Never returns a key."""
    return {
        "status": "ok",
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model or None,
            "configured": settings.llm_configured,
        },
    }


# Sync handlers: FastAPI runs them in a worker thread, so blocking model calls are fine.
@router.post("/submissions", status_code=status.HTTP_201_CREATED)
def create_submission(
    files: list[UploadFile] = File(default=[]),
    email_text: str | None = Form(default=None),
    overrides: str | None = Form(default=None, description='JSON {"field": {"value": ..., "note": ...}}'),
    llm: LLMClient | LLMError = Depends(get_llm),
    submissions: SubmissionStore = Depends(get_store),
) -> SubmissionResult:
    corrections = _parse_overrides(overrides)  # checked before any (billed) model call
    uploads = [UploadedFile(filename=f.filename or "", data=f.file.read()) for f in files]
    try:
        packet = ingest(uploads, pasted_email=email_text)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if isinstance(llm, LLMError):
        result = process_submission(packet, llm=None, llm_error=str(llm), overrides=corrections)
    else:
        result = process_submission(packet, llm=llm, overrides=corrections)
    return submissions.save(result)


def _parse_overrides(raw: str | None) -> dict[str, tuple]:
    """Corrections carried over when the same packet is re-analysed with more files."""
    if not raw:
        return {}
    try:
        parsed = _OVERRIDES.validate_json(raw)
    except ValidationError:
        raise HTTPException(status_code=422, detail="overrides must be a JSON object of {value, note}") from None
    out = {}
    for name, body in parsed.items():
        if name not in ALL_FIELDS:
            raise HTTPException(status_code=422, detail=f"Unknown field: {name}")
        if normalize_field(name, body.value).state is not FieldState.KNOWN:
            raise HTTPException(status_code=422, detail=f"Override for {name} is not usable")
        out[name] = (body.value, body.note.strip())
    return out


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: str, submissions: SubmissionStore = Depends(get_store)) -> SubmissionResult:
    return _load(submissions, submission_id)


@router.put("/submissions/{submission_id}/overrides/{field_name}")
def set_override(
    submission_id: str,
    field_name: str,
    body: OverrideRequest,
    submissions: SubmissionStore = Depends(get_store),
) -> SubmissionResult:
    result = _load(submissions, submission_id)
    field = _field(result, field_name)
    try:
        result.fields[field_name] = apply_override(field, body.value, body.note.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return submissions.save(rerun_after_override(result))


@router.delete("/submissions/{submission_id}/overrides/{field_name}")
def clear_override(
    submission_id: str, field_name: str, submissions: SubmissionStore = Depends(get_store)
) -> SubmissionResult:
    result = _load(submissions, submission_id)
    result.fields[field_name] = remove_override(_field(result, field_name))
    return submissions.save(rerun_after_override(result))


@router.post("/submissions/{submission_id}/narrative")
def regenerate_narrative(
    submission_id: str,
    llm: LLMClient | LLMError = Depends(get_llm),
    submissions: SubmissionStore = Depends(get_store),
) -> SubmissionResult:
    """Rewrite the summary and draft email from the current results (e.g. after overrides)."""
    result = _load(submissions, submission_id)
    if isinstance(llm, LLMError):
        return submissions.save(write_narrative(result, llm=None, llm_error=str(llm)))
    return submissions.save(write_narrative(result, llm=llm))


def _load(submissions: SubmissionStore, submission_id: str) -> SubmissionResult:
    result = submissions.get(submission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Submission not found (results are kept in memory only)")
    return result


def _field(result: SubmissionResult, field_name: str):
    if field_name not in ALL_FIELDS:
        raise HTTPException(status_code=404, detail=f"Unknown field: {field_name}")
    return result.fields[field_name]
