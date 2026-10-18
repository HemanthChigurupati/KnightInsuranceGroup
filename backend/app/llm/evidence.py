"""Check the model's evidence against the real document text.

An excerpt counts only if it appears in the source (ignoring whitespace and case),
and the reported value appears inside that excerpt. This stops a model, or text
injected into a document, from inventing values with plausible-looking quotes.
"""

import re
from typing import Any

from app.core.models import SourceDoc


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def locate_excerpt(doc: SourceDoc, excerpt: str | None) -> str | None:
    """Return the locator ("email body", "PDF page 2") where the excerpt occurs, else None."""
    if not excerpt or not excerpt.strip():
        return None
    needle = _squash(excerpt)
    for index, page in enumerate(doc.pages, start=1):
        if needle in _squash(page):
            # Only broker emails and application PDFs are extracted from.
            return "email body" if doc.kind == "broker_email" else f"PDF page {index}"
    return None


def value_in_excerpt(value: Any, excerpt: str) -> bool:
    if value is None:
        return False
    hay = _squash(excerpt)
    if isinstance(value, bool):
        words = {"yes", "true", "y"} if value else {"no", "false", "n"}
        return bool(set(re.findall(r"[a-z]+", hay)) & words)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    # Whole-token match: "80" must not be accepted inside "180".
    pattern = r"(?<![0-9a-z])" + re.escape(_squash(str(value))) + r"(?![0-9a-z])"
    return re.search(pattern, hay) is not None
