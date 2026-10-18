"""Run one submission packet through every stage and record what happened.

  ingest  -> extract (LLM, per source) -> evidence check -> normalise -> derive
          -> reconcile -> overrides -> rules + open items -> narrative (LLM)

A failed stage never hides the deterministic results that remain available.
"""

import uuid
from typing import Any

from app.core.derived import loss_runs_present, schedule_vehicle_count
from app.core.models import (
    EXTRACTED_FIELDS,
    FieldResult,
    FieldState,
    StageStatus,
    SubmissionResult,
)
from app.core.open_items import build_open_items
from app.core.reconcile import apply_override, reconcile_field
from app.core.rules import evaluate_rules, overall_status
from app.ingest.loader import IngestResult
from app.llm.client import LLMClient, LLMError
from app.llm.extraction import ExtractionResult, extract_all, extraction_targets
from app.llm.narrative import generate_narrative


def process_submission(
    packet: IngestResult,
    llm: LLMClient | None,
    llm_error: str | None = None,
    overrides: dict[str, tuple[Any, str]] | None = None,
) -> SubmissionResult:
    """overrides: {field: (value, note)} carried over when a packet is re-analysed with more files."""
    result = SubmissionResult(
        submission_id=uuid.uuid4().hex,
        paste_only=packet.paste_only,
        attachments=packet.attachments,
        model_name=llm.name if llm else None,
    )
    result.stages.append(
        StageStatus(stage="ingest", state="ok", message="; ".join(packet.warnings) or "All files read")
    )

    extraction, extract_stage = _extract(packet, llm, llm_error)
    result.stages.append(extract_stage)

    for name in EXTRACTED_FIELDS:
        result.fields[name] = _extracted_field(name, extraction, extract_stage)
    result.fields["schedule_vehicle_count"] = schedule_vehicle_count(packet.docs)
    result.fields["loss_runs_present"] = loss_runs_present(packet.docs)
    for name, (value, note) in (overrides or {}).items():
        result.fields[name] = apply_override(result.fields[name], value, note)

    apply_rules(result)
    return write_narrative(result, llm, llm_error)


def apply_rules(result: SubmissionResult) -> SubmissionResult:
    """(Re)evaluate R1-R5 and the open items from the current fields, including overrides. No LLM call."""
    result.rules = evaluate_rules(result.fields, paste_only=result.paste_only)
    result.overall_status = overall_status(result.rules)
    result.open_items = build_open_items(result.fields, result.rules)
    result.stages = [s for s in result.stages if s.stage != "rules"]
    result.stages.insert(
        min(2, len(result.stages)),
        StageStatus(stage="rules", state="ok", message=f"Overall: {result.overall_status.value}"),
    )
    return result


def rerun_after_override(result: SubmissionResult) -> SubmissionResult:
    """Rules change immediately; an existing AI narrative is kept but marked out of date."""
    apply_rules(result)
    if result.narrative_source == "llm":
        result.narrative_stale = True
    return result


def write_narrative(result: SubmissionResult, llm: LLMClient | None, llm_error: str | None = None) -> SubmissionResult:
    """Generate the summary and draft email. On failure, say so; never substitute fake AI text.

    A previous successful narrative is kept (still marked stale) if regeneration fails.
    """
    result.stages = [s for s in result.stages if s.stage != "narrative"]
    if llm is None:
        stage = StageStatus(stage="narrative", state="failed", message=f"Model unavailable: {llm_error or 'not configured'}")
    else:
        try:
            reply = generate_narrative(llm, result)
        except LLMError as exc:
            stage = StageStatus(stage="narrative", state="failed", message=str(exc))
        else:
            result.summary, result.draft_email = reply.summary, reply.draft_email
            result.narrative_source, result.narrative_stale = "llm", False
            result.model_name = llm.name
            stage = StageStatus(stage="narrative", state="ok", message=f"Written by {llm.name}")
    if stage.state == "failed" and result.narrative_source != "llm":
        result.narrative_source = "unavailable"
    result.stages.append(stage)
    return result


def _extract(packet: IngestResult, llm: LLMClient | None, llm_error: str | None) -> tuple[ExtractionResult, StageStatus]:
    empty = ExtractionResult(sources=[], sources_checked=[])
    if not extraction_targets(packet.docs):
        return empty, StageStatus(stage="extract", state="skipped", message="No readable broker email or application to extract from")
    if llm is None:
        return empty, StageStatus(stage="extract", state="failed", message=f"Model unavailable: {llm_error or 'not configured'}")

    extraction = extract_all(llm, packet.docs)
    if not extraction.errors:
        names = ", ".join(extraction.sources_checked)
        return extraction, StageStatus(stage="extract", state="ok", message=f"Extracted with {llm.name} from {names}")
    return extraction, StageStatus(stage="extract", state="failed", message="; ".join(extraction.errors))


def _extracted_field(name: str, extraction: ExtractionResult, stage: StageStatus) -> FieldResult:
    field = reconcile_field(name, extraction.candidates_for(name), extraction.sources_checked)
    if stage.state != "failed":
        return field
    # Some or all sources could not be read by the model: "not found" is not the same as "absent".
    failed = [s.filename for s in extraction.sources if s.error] or ["all sources"]
    note = f"Extraction failed for {', '.join(failed)}"
    if field.state is FieldState.MISSING:
        return field.model_copy(update={"state": FieldState.INVALID, "reason": f"{note}; value unknown"})
    return field.model_copy(update={"reason": f"{field.reason}. {note}, so other sources were not compared"})
