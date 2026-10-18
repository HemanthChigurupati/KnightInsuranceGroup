"""Turn one submission packet (uploaded files and/or pasted email) into SourceDocs.

Each call builds a fresh result, so nothing carries over between packets.
"""

from dataclasses import dataclass, field
from pathlib import PurePath

from app.core.models import Attachment, SourceDoc
from app.ingest.csv_reader import read_csv
from app.ingest.email_reader import read_email_file, read_pasted_email
from app.ingest.pdf_reader import read_pdf

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 20

_READERS = {
    ".txt": read_email_file,
    ".eml": read_email_file,
    ".pdf": read_pdf,
    ".csv": read_csv,
}


class IngestError(ValueError):
    """The packet as a whole cannot be processed (shown to the user as a failure)."""


@dataclass
class UploadedFile:
    filename: str
    data: bytes


@dataclass
class IngestResult:
    docs: list[SourceDoc]
    attachments: list[Attachment]
    paste_only: bool
    warnings: list[str] = field(default_factory=list)


def ingest(files: list[UploadedFile], pasted_email: str | None = None) -> IngestResult:
    pasted = (pasted_email or "").strip()
    if not files and not pasted:
        raise IngestError("Upload the submission files or paste the broker email.")
    if len(files) > MAX_FILES:
        raise IngestError(f"Too many files ({len(files)}); the limit is {MAX_FILES}.")

    docs: list[SourceDoc] = [read_pasted_email(pasted)] if pasted else []
    seen: set[str] = set()
    for upload in files:
        name = PurePath(upload.filename or "").name  # drop any client-side directory path
        if not name:
            raise IngestError("A file was uploaded without a name.")
        if name.lower() in seen:
            raise IngestError(f"Duplicate file name: {name}")
        seen.add(name.lower())
        if len(upload.data) > MAX_FILE_BYTES:
            raise IngestError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        docs.append(_read_one(name, upload.data))

    emails = [d for d in docs if d.kind == "broker_email"]
    if len(emails) > 1:
        raise IngestError(
            "More than one broker email was supplied ("
            + ", ".join(d.filename for d in emails)
            + "). Process one submission at a time."
        )

    warnings = [f"{d.filename}: {d.error}" for d in docs if not d.readable]
    if not emails:
        warnings.append("No broker email supplied; extraction uses the attachments only.")
    attachments = [
        Attachment(
            filename=d.filename,
            kind=d.kind,
            readable=d.readable,
            pages=len(d.pages) if d.filename.lower().endswith(".pdf") and d.readable else None,
            note=d.error,
        )
        for d in docs
    ]
    return IngestResult(docs=docs, attachments=attachments, paste_only=not files, warnings=warnings)


def _read_one(name: str, data: bytes) -> SourceDoc:
    if not data:
        return SourceDoc(filename=name, kind="unknown", readable=False, error="File is empty")
    reader = _READERS.get(PurePath(name).suffix.lower())
    if reader is None:
        return SourceDoc(
            filename=name, kind="unknown", readable=False,
            error="Unsupported file type (supported: .txt, .eml, .pdf, .csv)",
        )
    return reader(name, data)
