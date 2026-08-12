"""Streamlit shell tests; gate and input behavior is covered in its owning modules."""

from pathlib import Path
import runpy

from streamlit.testing.v1 import AppTest

from app import _pdf_error_message, _preparation_error_message

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"
GENERATOR_FIXTURES = runpy.run_path(str(PROJECT_ROOT / "tests" / "test_generator.py"))


def _initial_app():
    return AppTest.from_file(str(APP_PATH)).run(timeout=20)


def test_upload_screen_renders_the_required_context_and_reference_controls():
    app = _initial_app()

    assert not app.exception
    assert [header.value for header in app.header] == ["Screen 1 — Upload"]
    assert [area.label for area in app.text_area] == ["Describe what this file does"]
    labels = {widget.label for widget in app.text_input}
    assert {"Entity", "Period", "Currency", "Basis (optional)"} <= labels
    assert [button.label for button in app.button] == ["Start audit"]
    confirmation = next(
        checkbox
        for checkbox in app.checkbox
        if checkbox.label.startswith("I confirm that the workbook")
    )
    assert confirmation.value is False
    assert app.button[0].disabled is True
    assert app.selectbox[0].options == ["Actuary", "CRO", "CFO", "Auditor"]


def test_pdf_download_is_unavailable_when_gate_4_has_not_completed():
    app = _initial_app()
    app.session_state["screen"] = 5
    app = app.run(timeout=20)

    assert not app.exception
    assert any("PDF remains unavailable until Gate 4 completes" in error.value for error in app.error)
    assert not app.get("download_button")


def test_app_contains_no_reconciliation_or_gate_implementation_imports():
    source = APP_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "apply_thresholds",
        "compute_verdict",
        "run_reconciliation",
        "context_gate",
        "findings_review_gate",
        "reconciliation_gate",
        "approval_record_gate",
    ):
        assert forbidden not in source


def test_app_uses_current_report_and_approval_vocabulary():
    source = APP_PATH.read_text(encoding="utf-8")

    assert "Record approval and generate report" in source
    assert "Named approval record" in source
    assert "translation_and_reconciliation_verdict" in source
    assert "submit_approval_record" in source
    for retired in ("submit_signoff", "screen_4_signoff", "signed_by", "attested_by"):
        assert retired not in source


def test_provider_failure_message_is_retryable_and_does_not_echo_raw_content():
    message = _preparation_error_message(
        RuntimeError("provider response containing details that should stay out of the UI")
    )

    assert "Retry report preparation" in message
    assert "Gate 3 will not be replayed" in message
    assert "provider response containing details" not in message

    pdf_message = _pdf_error_message(RuntimeError("renderer internals"))
    assert "Retry PDF generation" in pdf_message
    assert "approval record will not be replayed" in pdf_message
    assert "renderer internals" not in pdf_message


def test_approval_screen_has_no_pdf_export_and_requires_both_identity_fields():
    app = _initial_app()
    app.session_state["screen"] = 4
    app.session_state["report_preview"] = GENERATOR_FIXTURES["_report"]()
    app.session_state["findings"] = []
    app = app.run(timeout=20)

    assert not app.exception
    assert [header.value for header in app.header] == [
        "Screen 4 — Gate 4: Named approval record"
    ]
    record_button = next(
        button for button in app.button if button.label == "Record approval and generate report"
    )
    assert record_button.disabled is True
    assert not app.get("download_button")


def test_report_screen_exposes_download_badges_traceability_and_integrity_check():
    report = GENERATOR_FIXTURES["_report"]()
    app = _initial_app()
    app.session_state["screen"] = 5
    app.session_state["final_report"] = report
    app.session_state["pdf_bytes"] = b"%PDF-test"
    app.session_state["audit_rows"] = GENERATOR_FIXTURES["_audit_rows"]()
    app = app.run(timeout=20)

    assert not app.exception
    assert [header.value for header in app.header] == ["Screen 5 — Report"]
    assert len(app.get("download_button")) == 1
    assert any(button.label == "Verify evidence integrity" for button in app.button)
    assert any("full index in the PDF" in caption.value for caption in app.caption)
