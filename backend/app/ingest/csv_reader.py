"""Vehicle schedule CSV. Only decoded here; header check and row count live in core/derived.py."""

from app.core.models import SourceDoc
from app.ingest.text import decode_text


def read_csv(filename: str, data: bytes) -> SourceDoc:
    if b"\x00" in data:
        return SourceDoc(filename=filename, kind="vehicle_schedule", readable=False, error="File is not text")
    return SourceDoc(filename=filename, kind="vehicle_schedule", readable=True, pages=[decode_text(data)])
