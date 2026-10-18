import io

import pytest
from pypdf import PdfWriter

from app.core.derived import loss_runs_present, schedule_vehicle_count
from app.core.models import FieldState
from app.ingest.loader import MAX_FILE_BYTES, IngestError, UploadedFile, ingest


def packet(samples_dir, name: str) -> list[UploadedFile]:
    return [UploadedFile(p.name, p.read_bytes()) for p in sorted((samples_dir / name).iterdir())]


def doc_of(result, kind: str):
    """The first ingested document of this kind, or None."""
    return next((d for d in result.docs if d.kind == kind), None)


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# --- sample packets ------------------------------------------------------------------

def test_sub_a_files_are_classified(samples_dir):
    result = ingest(packet(samples_dir, "SUB-A"))
    kinds = {a.filename: a.kind for a in result.attachments}
    assert kinds == {
        "application.pdf": "application",
        "broker_email.txt": "broker_email",
        "loss_runs.pdf": "loss_runs",
        "vehicles.csv": "vehicle_schedule",
    }
    assert all(a.readable for a in result.attachments)
    assert result.paste_only is False
    assert result.warnings == []


@pytest.mark.parametrize("name", ["SUB-B", "SUB-C"])
def test_packets_without_loss_runs(samples_dir, name):
    result = ingest(packet(samples_dir, name))
    assert "loss_runs" not in {a.kind for a in result.attachments}
    assert loss_runs_present(result.docs).value is False


def test_pdf_text_keeps_labels_and_values_on_one_line(samples_dir):
    app = doc_of(ingest(packet(samples_dir, "SUB-C")), "application")
    assert len(app.pages) == 1
    lines = [" ".join(line.split()) for line in app.pages[0].splitlines()]
    assert "Operating radius miles 80" in lines
    assert "Hazardous materials No" in lines


def test_email_text_is_read(samples_dir):
    email = doc_of(ingest(packet(samples_dir, "SUB-C")), "broker_email")
    assert email.filename == "broker_email.txt"
    assert "Operating radius miles: 180" in email.text


def test_derived_fields_from_real_files(samples_dir):
    docs = ingest(packet(samples_dir, "SUB-A")).docs
    assert schedule_vehicle_count(docs).value == 3
    f = loss_runs_present(docs)
    assert (f.state, f.value) == (FieldState.KNOWN, True)
    assert f.candidates[0].evidence.excerpt == "Loss runs report"


def test_attachment_inventory_reports_pdf_pages(samples_dir):
    result = ingest(packet(samples_dir, "SUB-A"))
    pages = {a.filename: a.pages for a in result.attachments}
    assert pages["application.pdf"] == 1 and pages["vehicles.csv"] is None


# --- paste-only -------------------------------------------------------------------------

def test_pasted_email_only(samples_dir):
    text = (samples_dir / "SUB-A" / "broker_email.txt").read_text()
    result = ingest([], pasted_email=text)
    assert result.paste_only is True
    assert doc_of(result, "broker_email").filename == "pasted email"
    assert [a.kind for a in result.attachments] == ["broker_email"]


def test_pasted_email_with_attachments_is_not_paste_only(samples_dir):
    files = [f for f in packet(samples_dir, "SUB-A") if f.filename != "broker_email.txt"]
    result = ingest(files, pasted_email="Business name: Example LLC")
    assert result.paste_only is False
    assert doc_of(result, "broker_email").filename == "pasted email"


def test_eml_file_is_read():
    eml = b"From: broker@example.com\r\nSubject: Test\r\nContent-Type: text/plain\r\n\r\nDeclared vehicle count: 2\r\n"
    email = doc_of(ingest([UploadedFile("submission.eml", eml)]), "broker_email")
    assert "Subject: Test" in email.text and "Declared vehicle count: 2" in email.text


# --- broken and unusual files --------------------------------------------------------------

def test_corrupt_pdf_is_unreadable_not_a_crash():
    result = ingest([UploadedFile("loss_runs.pdf", b"%PDF-1.4 this is not really a pdf")])
    att = result.attachments[0]
    assert att.readable is False and att.note
    assert loss_runs_present(result.docs).state is FieldState.INVALID  # -> R5 UNKNOWN
    assert result.warnings


def test_scanned_pdf_without_text_is_unreadable():
    att = ingest([UploadedFile("scan.pdf", blank_pdf())]).attachments[0]
    assert att.readable is False
    assert "no selectable text" in att.note


def test_header_only_csv_counts_zero():
    result = ingest([UploadedFile("vehicles.csv", b"vehicle_id,vin,year,make,model\n")])
    assert schedule_vehicle_count(result.docs).value == 0


def test_cp1252_csv_is_decoded():
    data = "vehicle_id,vin,year,make,model\nA-01,X1,2022,Citroën,Van\n".encode("cp1252")
    result = ingest([UploadedFile("vehicles.csv", data)])
    assert schedule_vehicle_count(result.docs).value == 1


def test_binary_csv_is_unreadable():
    result = ingest([UploadedFile("vehicles.csv", b"\x00\x01\x02")])
    assert schedule_vehicle_count(result.docs).state is FieldState.INVALID  # -> R2 UNKNOWN


def test_unsupported_file_is_listed_as_unreadable():
    att = ingest([UploadedFile("loss_history.docx", b"PK\x03\x04")]).attachments[0]
    assert (att.kind, att.readable) == ("unknown", False)
    assert "Unsupported" in att.note


def test_empty_file_is_unreadable():
    assert ingest([UploadedFile("application.pdf", b"")]).attachments[0].readable is False


def test_directory_parts_are_stripped_from_names():
    result = ingest([UploadedFile("../../etc/vehicles.csv", b"vehicle_id,vin,year,make,model\n")])
    assert result.attachments[0].filename == "vehicles.csv"


def test_no_email_warns_but_continues(samples_dir):
    files = [f for f in packet(samples_dir, "SUB-A") if f.filename != "broker_email.txt"]
    result = ingest(files)
    assert doc_of(result, "broker_email") is None
    assert any("No broker email" in w for w in result.warnings)


# --- whole-packet failures -------------------------------------------------------------------

def test_nothing_supplied_is_an_error():
    with pytest.raises(IngestError):
        ingest([], pasted_email="   ")


def test_two_emails_is_an_error(samples_dir):
    files = packet(samples_dir, "SUB-A")
    with pytest.raises(IngestError, match="More than one broker email"):
        ingest(files, pasted_email="another email")


def test_duplicate_names_is_an_error():
    f = UploadedFile("vehicles.csv", b"vehicle_id,vin,year,make,model\n")
    with pytest.raises(IngestError, match="Duplicate"):
        ingest([f, f])


def test_oversized_file_is_an_error():
    with pytest.raises(IngestError, match="larger than"):
        ingest([UploadedFile("big.pdf", b"0" * (MAX_FILE_BYTES + 1))])


def test_each_call_is_independent(samples_dir):
    a = ingest(packet(samples_dir, "SUB-A"))
    c = ingest(packet(samples_dir, "SUB-C"))
    assert "Pacific Harbor" in doc_of(a, "broker_email").text
    assert "Pacific Harbor" not in doc_of(c, "broker_email").text
    assert "loss_runs.pdf" not in {x.filename for x in c.attachments}
