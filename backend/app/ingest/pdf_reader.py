"""PDF attachments: text per page, so evidence can point at "PDF page N".

The brief guarantees selectable text; scanned PDFs (no text layer) are reported
as unreadable rather than OCR'd.
"""

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.derived import classify_pdf
from app.core.models import SourceDoc


def read_pdf(filename: str, data: bytes) -> SourceDoc:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return _unreadable(filename, "PDF is password protected")
        pages = [_page_text(page) for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        return _unreadable(filename, f"PDF could not be parsed: {exc}")

    if not any(p.strip() for p in pages):
        return _unreadable(filename, "PDF has no selectable text (scanned PDFs are out of scope)")

    doc = SourceDoc(filename=filename, kind="other", readable=True, pages=pages)
    doc.kind = classify_pdf(doc)
    return doc


def _page_text(page) -> str:
    # Layout mode keeps table labels and values on one line ("Operating radius miles   80").
    try:
        text = page.extract_text(extraction_mode="layout")
    except Exception:  # layout mode is newer and stricter; fall back to plain extraction
        text = ""
    if not text.strip():
        text = page.extract_text() or ""
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _unreadable(filename: str, error: str) -> SourceDoc:
    return SourceDoc(filename=filename, kind="unknown", readable=False, error=error)
