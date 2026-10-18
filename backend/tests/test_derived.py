from app.core.derived import classify_pdf, loss_runs_present, schedule_vehicle_count
from app.core.models import FieldState
from helpers import csv_doc, email_doc, pdf_doc

K, M, C, I = FieldState.KNOWN, FieldState.MISSING, FieldState.CONFLICT, FieldState.INVALID
HEADER = "vehicle_id,vin,year,make,model\n"


# --- schedule_vehicle_count ------------------------------------------------------

def test_counts_sample_csvs(samples_dir):
    for packet, expected in (("SUB-A", 3), ("SUB-B", 2), ("SUB-C", 3)):
        text = (samples_dir / packet / "vehicles.csv").read_text()
        f = schedule_vehicle_count([csv_doc(text)])
        assert (f.state, f.value) == (K, expected), packet


def test_evidence_uses_row_range_locator():
    f = schedule_vehicle_count([csv_doc(HEADER + "A,1,2022,X,Y\nB,2,2023,X,Y\nC,3,2024,X,Y\n")])
    assert f.candidates[0].evidence.locator == "CSV data rows 1-3 (header excluded)"


def test_blank_rows_are_not_counted():
    f = schedule_vehicle_count([csv_doc(HEADER + "A,1,2022,X,Y\n\n , , , , \nB,2,2023,X,Y\n")])
    assert f.value == 2


def test_header_only_is_zero_vehicles():
    f = schedule_vehicle_count([csv_doc(HEADER)])
    assert (f.state, f.value) == (K, 0)


def test_wrong_header_is_invalid():
    assert schedule_vehicle_count([csv_doc("id,name\n1,x\n")]).state is I


def test_empty_file_is_invalid():
    assert schedule_vehicle_count([csv_doc("")]).state is I


def test_unreadable_csv_is_invalid():
    assert schedule_vehicle_count([csv_doc("", readable=False)]).state is I


def test_no_csv_is_missing():
    f = schedule_vehicle_count([email_doc()])
    assert f.state is M
    assert f.sources_checked == ["uploaded attachment inventory"]


def test_two_schedules_that_disagree_conflict():
    a = csv_doc(HEADER + "A,1,2022,X,Y\n", filename="a.csv")
    b = csv_doc(HEADER + "A,1,2022,X,Y\nB,2,2023,X,Y\n", filename="b.csv")
    assert schedule_vehicle_count([a, b]).state is C


# --- document classification ----------------------------------------------------

def test_classify_by_heading():
    assert classify_pdf(pdf_doc("x.pdf", "Loss runs report")) == "loss_runs"
    assert classify_pdf(pdf_doc("x.pdf", "Loss History Report")) == "loss_runs"
    assert classify_pdf(pdf_doc("x.pdf", "Commercial auto application")) == "application"
    assert classify_pdf(pdf_doc("x.pdf", "Driver list")) == "other"


# --- loss_runs_present -----------------------------------------------------------

def test_readable_loss_runs_report_is_present():
    docs = [email_doc(), pdf_doc("application.pdf", "Commercial auto application", "application"),
            pdf_doc("loss_runs.pdf", "Loss runs report", "loss_runs"), csv_doc(HEADER)]
    f = loss_runs_present(docs)
    assert (f.state, f.value) == (K, True)
    assert f.candidates[0].evidence.excerpt == "Loss runs report"


def test_zero_loss_report_still_counts():
    # Brief: "A readable report stating zero losses still counts."
    doc = pdf_doc("history.pdf", "Loss runs report", "loss_runs")
    doc.pages = ["Loss runs report\nReported claims 0\nTotal incurred losses $0"]
    assert loss_runs_present([doc]).value is True


def test_promise_in_email_does_not_count():
    # SUB-B/SUB-C: the email promises loss runs later; only application + CSV are attached.
    docs = [email_doc(), pdf_doc("application.pdf", "Commercial auto application", "application"), csv_doc(HEADER)]
    f = loss_runs_present(docs)
    assert (f.state, f.value) == (K, False)
    observation = f.candidates[0].evidence.observation
    assert "application.pdf" in observation and "vehicles.csv" in observation  # attachment inventory shown


def test_unreadable_attachment_is_undetermined():
    docs = [email_doc(), pdf_doc("scan.pdf", "", "unknown", readable=False)]
    f = loss_runs_present(docs)
    assert f.state is I and f.value is None


def test_loss_named_file_without_loss_heading_is_undetermined():
    docs = [email_doc(), pdf_doc("loss_history.pdf", "Summary", "other")]
    assert loss_runs_present(docs).state is I


def test_readable_report_wins_over_an_unreadable_extra():
    docs = [pdf_doc("loss_runs.pdf", "Loss runs report", "loss_runs"), pdf_doc("scan.pdf", "", "unknown", readable=False)]
    assert loss_runs_present(docs).value is True


def test_paste_only_has_no_attachments():
    f = loss_runs_present([email_doc()])
    assert f.state is M


HEADER = "vehicle_id,vin,year,make,model\n"


def test_same_vin_under_different_ids_is_invalid():
    f = schedule_vehicle_count([csv_doc(HEADER + "V1,1FTFW1E50NFA00001,2022,Ford,T\nV2,1ftfw-1e50nfa00001,2022,Ford,T\nV3,XYZ3,2021,Ford,T\n")])
    assert f.state is FieldState.INVALID and f.value is None
    assert "the same VIN 1FTFW1E50NFA00001 for vehicles V1, V2" in f.reason


def test_same_vehicle_id_twice_is_invalid():
    f = schedule_vehicle_count([csv_doc(HEADER + "V1,AAA1,2022,Ford,T\nv1,BBB2,2022,Ford,T\n")])
    assert f.state is FieldState.INVALID
    assert "vehicle ID V1 more than once (data rows 1, 2)" in f.reason


def test_blank_vins_are_not_duplicates():
    f = schedule_vehicle_count([csv_doc(HEADER + "V1,,2022,Ford,T\nV2,,2022,Ford,T\n")])
    assert (f.state, f.value) == (FieldState.KNOWN, 2)
