"""Fields computed by code from the attachments themselves (no LLM).

schedule_vehicle_count: nonblank data rows after the CSV header.
loss_runs_present:      decided by what is actually attached, never by what the email claims.
"""

import csv
import io
import re

from app.core.models import Candidate, Evidence, FieldResult, FieldState, SourceDoc

INVENTORY = "uploaded attachment inventory"
EXPECTED_CSV_HEADER = ["vehicle_id", "vin", "year", "make", "model"]

# --- document classification ----------------------------------------------------

_LOSS_RUNS_TITLE = re.compile(r"\bloss[\s-]*runs?\b|\bloss\s+history\b|\bloss\s+experience\b|\bclaims?\s+history\b", re.I)
_APPLICATION_TITLE = re.compile(r"\bapplication\b", re.I)
_LOSS_HINT_IN_NAME = re.compile(r"loss|claim|history", re.I)
_TITLE_LINES = 3  # a document "is" a loss runs report if its heading says so


def title_lines(doc: SourceDoc) -> list[str]:
    """First few meaningful lines of page 1 (page-number-only lines are skipped)."""
    first_page = doc.pages[0] if doc.pages else ""
    lines = [line.strip() for line in first_page.splitlines()]
    return [line for line in lines if line and not line.isdigit()][:_TITLE_LINES]


def classify_pdf(doc: SourceDoc) -> str:
    """'loss_runs', 'application' or 'other', from the document heading."""
    for line in title_lines(doc):
        if _LOSS_RUNS_TITLE.search(line):
            return "loss_runs"
        if _APPLICATION_TITLE.search(line):
            return "application"
    return "other"


HEADING_LOCATOR = "PDF page 1"  # only readable PDFs reach the heading checks


def _matching_title(doc: SourceDoc) -> str | None:
    return next((line for line in title_lines(doc) if _LOSS_RUNS_TITLE.search(line)), None)


# --- schedule_vehicle_count ------------------------------------------------------


def _count_csv(doc: SourceDoc) -> Candidate:
    if not doc.readable:
        return Candidate(
            source_file=doc.filename,
            state=FieldState.INVALID,
            note=f"CSV could not be read: {doc.error or 'unreadable'}",
            evidence=Evidence(source_file=doc.filename, locator="whole file", observation="File could not be read"),
        )
    try:
        rows = list(csv.reader(io.StringIO(doc.text)))
    except csv.Error as exc:
        return Candidate(
            source_file=doc.filename,
            state=FieldState.INVALID,
            note=f"CSV could not be parsed: {exc}",
            evidence=Evidence(source_file=doc.filename, locator="whole file", observation=f"Parse error: {exc}"),
        )

    header = [cell.strip().casefold() for cell in rows[0]] if rows else []
    if header != EXPECTED_CSV_HEADER:
        return Candidate(
            source_file=doc.filename,
            state=FieldState.INVALID,
            note=f"Unexpected CSV header: {','.join(header) or '(empty file)'}",
            evidence=Evidence(
                source_file=doc.filename,
                locator="CSV header row",
                observation=f"Expected {','.join(EXPECTED_CSV_HEADER)}",
            ),
        )

    records = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    count = len(records)
    if duplicates := _duplicate_identifiers(records):
        # Same vehicle listed twice, or a typo in an ID/VIN: we cannot tell which, so no count.
        return Candidate(
            source_file=doc.filename,
            state=FieldState.INVALID,
            note=(
                f"{doc.filename} lists {'; '.join(duplicates)}. "
                "Please confirm whether these are duplicate entries or an error in the VIN / vehicle ID"
            ),
            evidence=Evidence(
                source_file=doc.filename,
                locator=f"CSV data rows 1-{count} (header excluded)",
                observation=f"{count} vehicle record(s), but they contain duplicate identifiers",
            ),
        )
    locator = f"CSV data rows 1-{count} (header excluded)" if count else "CSV data rows (header excluded)"
    return Candidate(
        source_file=doc.filename,
        raw_value=count,
        state=FieldState.KNOWN,
        value=count,
        evidence=Evidence(
            source_file=doc.filename,
            locator=locator,
            observation=f"{count} nonblank vehicle record(s) after the header",
        ),
    )


def _duplicate_identifiers(records: list[list[str]]) -> list[str]:
    """Describe VINs and vehicle IDs that occur on more than one row.

    VINs compare ignoring case, spaces and dashes. Blank cells are not duplicates.
    """
    seen_vin: dict[str, list[str]] = {}
    seen_id: dict[str, tuple[str, list[str]]] = {}
    for number, row in enumerate(records, start=1):
        vehicle_id = row[0].strip() if row else ""
        vin = row[1].strip() if len(row) > 1 else ""
        if key := re.sub(r"[\s-]", "", vin).upper():
            seen_vin.setdefault(key, []).append(vehicle_id or f"row {number}")
        if vehicle_id:
            seen_id.setdefault(vehicle_id.casefold(), (vehicle_id, []))[1].append(str(number))
    found = [
        f"the same VIN {vin} for vehicles {', '.join(labels)}"
        for vin, labels in seen_vin.items() if len(labels) > 1
    ]
    found += [
        f"vehicle ID {vehicle_id} more than once (data rows {', '.join(rows_)})"
        for vehicle_id, rows_ in seen_id.values() if len(rows_) > 1
    ]
    return found


def schedule_vehicle_count(docs: list[SourceDoc]) -> FieldResult:
    schedules = [d for d in docs if d.kind == "vehicle_schedule"]
    if not schedules:
        return FieldResult(
            name="schedule_vehicle_count",
            state=FieldState.MISSING,
            reason="No vehicle schedule (CSV) was supplied",
            sources_checked=[INVENTORY],
        )

    candidates = [_count_csv(d) for d in schedules]
    checked = [d.filename for d in schedules]
    known = [c for c in candidates if c.state is FieldState.KNOWN]
    common = dict(name="schedule_vehicle_count", candidates=candidates, sources_checked=checked)

    if len(known) < len(candidates):
        reasons = "; ".join(c.note for c in candidates if c.note)
        return FieldResult(state=FieldState.INVALID, reason=reasons, **common)
    if len({c.value for c in known}) > 1:
        detail = "; ".join(f"{c.source_file}: {c.value}" for c in known)
        return FieldResult(state=FieldState.CONFLICT, reason=f"Vehicle schedules disagree: {detail}", **common)
    return FieldResult(
        state=FieldState.KNOWN, value=known[0].value, reason=f"Counted from {', '.join(checked)}", **common
    )


# --- loss_runs_present -----------------------------------------------------------


def loss_runs_present(docs: list[SourceDoc]) -> FieldResult:
    attachments = [d for d in docs if d.kind not in ("broker_email", "vehicle_schedule")]
    inventory = [d.filename for d in docs if d.kind != "broker_email"]
    common = dict(name="loss_runs_present", sources_checked=[INVENTORY, *inventory])

    if not inventory:  # paste-only, or only an email file
        return FieldResult(state=FieldState.MISSING, reason="No attachments were supplied", **common)

    found: list[Candidate] = []
    undetermined: list[Candidate] = []
    for doc in attachments:
        if doc.readable and (title := _matching_title(doc)):
            found.append(
                Candidate(
                    source_file=doc.filename,
                    raw_value=True,
                    state=FieldState.KNOWN,
                    value=True,
                    evidence=Evidence(source_file=doc.filename, locator=HEADING_LOCATOR, excerpt=title),
                )
            )
        elif not doc.readable:
            undetermined.append(
                Candidate(
                    source_file=doc.filename,
                    state=FieldState.INVALID,
                    note="Attachment could not be read, so its document type is unknown",
                    evidence=Evidence(source_file=doc.filename, locator="whole file", observation=doc.error or "Unreadable"),
                )
            )
        elif doc.kind in ("other", "unknown") and _LOSS_HINT_IN_NAME.search(doc.filename):
            undetermined.append(
                Candidate(
                    source_file=doc.filename,
                    state=FieldState.INVALID,
                    note="File name suggests loss history but the document heading does not say so",
                    evidence=Evidence(
                        source_file=doc.filename,
                        locator=HEADING_LOCATOR,
                        observation="Heading: " + (" / ".join(title_lines(doc)) or "(no text)"),
                    ),
                )
            )

    if found:
        names = ", ".join(c.source_file for c in found)
        return FieldResult(
            state=FieldState.KNOWN, value=True, reason=f"Loss runs report attached: {names}",
            candidates=found + undetermined, **common,
        )
    if undetermined:
        names = ", ".join(c.source_file for c in undetermined)
        return FieldResult(
            state=FieldState.INVALID, reason=f"Cannot determine whether {names} is a loss runs report",
            candidates=undetermined, **common,
        )

    listed = ", ".join(f"{d.filename} ({d.kind.replace('_', ' ')})" for d in docs if d.kind != "broker_email")
    absence = Candidate(
        source_file=INVENTORY,
        raw_value=False,
        state=FieldState.KNOWN,
        value=False,
        evidence=Evidence(
            source_file=INVENTORY,
            locator="attachment list",
            observation=f"Inspected: {listed}. None is a loss runs or loss history report.",
        ),
    )
    return FieldResult(
        state=FieldState.KNOWN, value=False, reason="No loss runs report among the attachments",
        candidates=[absence], **common,
    )
