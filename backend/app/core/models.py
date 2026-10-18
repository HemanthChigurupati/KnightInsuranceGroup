"""Shared data model for the whole pipeline.

Field names and state names follow field_specification.json and rules.json,
which form the evaluation contract.
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

# Values the LLM extracts from the email and the application PDF.
EXTRACTED_FIELDS: tuple[str, ...] = (
    "business_name",
    "requested_effective_date",
    "garaging_state",
    "operations_description",
    "declared_vehicle_count",
    "operating_radius_miles",
    "hazardous_materials",
)

# Values derived by code from the attachments themselves.
DERIVED_FIELDS: tuple[str, ...] = (
    "loss_runs_present",
    "schedule_vehicle_count",
)

ALL_FIELDS: tuple[str, ...] = EXTRACTED_FIELDS + DERIVED_FIELDS


class FieldState(str, Enum):
    KNOWN = "KNOWN"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"


class RuleStatus(str, Enum):
    PASS = "PASS"
    FLAG = "FLAG"
    UNKNOWN = "UNKNOWN"


class OverallStatus(str, Enum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Evidence(BaseModel):
    """Where a value came from. Absent values carry an observation, never an invented excerpt."""

    source_file: str
    locator: str  # e.g. "email body", "PDF page 1", "CSV data rows 1-3 (header excluded)"
    excerpt: str | None = None  # verbatim text from the source
    observation: str | None = None  # used for derived values and absences


class Candidate(BaseModel):
    """One source's answer for one field, before sources are reconciled."""

    source_file: str
    raw_value: Any = None
    state: FieldState  # KNOWN, MISSING or INVALID after normalisation
    value: Any = None  # normalised value when state is KNOWN
    note: str | None = None  # why it is MISSING or INVALID
    evidence: Evidence | None = None
    verified: bool = True  # False when the excerpt was not found in the source text


class Override(BaseModel):
    """A human correction. The extracted value and evidence stay on the FieldResult."""

    value: Any
    note: str = ""


class FieldResult(BaseModel):
    name: str
    state: FieldState
    value: Any = None  # null unless state is KNOWN
    reason: str | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    sources_checked: list[str] = Field(default_factory=list)
    override: Override | None = None

    @computed_field
    @property
    def effective_value(self) -> Any:
        return self.override.value if self.override else self.value

    @computed_field
    @property
    def effective_state(self) -> FieldState:
        return FieldState.KNOWN if self.override else self.state


class RuleResult(BaseModel):
    rule_id: str
    name: str
    status: RuleStatus
    reason: str
    inputs_used: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class StageStatus(BaseModel):
    stage: Literal["ingest", "extract", "rules", "narrative"]
    state: Literal["ok", "failed", "skipped"]
    message: str = ""


AttachmentKind = Literal[
    "broker_email", "application", "loss_runs", "vehicle_schedule", "other", "unknown"
]


class SourceDoc(BaseModel):
    """One supplied file (or the pasted email) as plain text, produced by ingest/."""

    filename: str
    kind: AttachmentKind
    readable: bool
    pages: list[str] = Field(default_factory=list)  # PDF: one entry per page; email/CSV: one entry
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.pages)


class Attachment(BaseModel):
    filename: str
    kind: AttachmentKind
    readable: bool
    pages: int | None = None
    note: str | None = None


class OpenItem(BaseModel):
    """Something the underwriter needs from the broker, derived by code from the rule results."""

    rule_id: str | None  # None for fields no rule covers (e.g. garaging_state)
    text: str


class SubmissionResult(BaseModel):
    submission_id: str
    paste_only: bool = False
    attachments: list[Attachment] = Field(default_factory=list)
    fields: dict[str, FieldResult] = Field(default_factory=dict)
    rules: list[RuleResult] = Field(default_factory=list)
    overall_status: OverallStatus | None = None
    open_items: list[OpenItem] = Field(default_factory=list)
    summary: str | None = None
    draft_email: str | None = None
    narrative_source: Literal["pending", "llm", "unavailable"] = "pending"
    narrative_stale: bool = False  # True after an override until the narrative is regenerated
    model_name: str | None = None
    stages: list[StageStatus] = Field(default_factory=list)
