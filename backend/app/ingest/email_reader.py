"""Broker email: an uploaded .txt / .eml file, or text pasted into the UI."""

from email import policy
from email.parser import BytesParser

from app.core.models import SourceDoc
from app.ingest.text import decode_text

PASTED_EMAIL_NAME = "pasted email"


def read_email_file(filename: str, data: bytes) -> SourceDoc:
    if filename.lower().endswith(".eml"):
        text = _eml_to_text(data)
    else:
        text = decode_text(data)
    return _email_doc(filename, text)


def read_pasted_email(text: str) -> SourceDoc:
    return _email_doc(PASTED_EMAIL_NAME, text)


def _email_doc(filename: str, text: str) -> SourceDoc:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return SourceDoc(filename=filename, kind="broker_email", readable=False, error="Email is empty")
    return SourceDoc(filename=filename, kind="broker_email", readable=True, pages=[text])


def _eml_to_text(data: bytes) -> str:
    """Keep the headers a reader would see, then the plain-text body."""
    msg = BytesParser(policy=policy.default).parsebytes(data)
    headers = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Subject", "Date") if msg[h])
    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content() if body_part else ""
    return f"{headers}\n\n{body}"
