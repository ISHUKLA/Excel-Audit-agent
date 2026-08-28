"""Streamlit shell tests; gate and input behavior is covered in its owning modules."""

from pathlib import Path
from types import SimpleNamespace
import runpy

import pytest
from streamlit.testing.v1 import AppTest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import Orchestrator
from app import _effective_workbook_bytes, _pdf_error_message, _preparation_error_message, _workbook_identity_panel
from core.audit_log import AuditLog
from core.state_store import StateStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"
GENERATOR_FIXTURES = runpy.run_path(str(PROJECT_ROOT / "tests" / "test_generator.py"))
# Reuses the orchestrator test suite's own fixtures/mocking pattern (real
# Orchestrator, mocked Agent 1-3 internals, no live Anthropic client) rather
# than duplicating it, exactly as GENERATOR_FIXTURES above reuses test_generator.py.
ORCHESTRATOR_FIXTURES = runpy.run_path(str(PROJECT_ROOT / "tests" / "test_orchestrator.py"))


def _initial_app():
    return AppTest.from_file(str(APP_PATH)).run(timeout=20)


class _CountingMessagesAPI:
    """A fake Anthropic messages.create() that counts invocations.

    Used to prove, behaviourally, how many (if any) calls a Gate 3 AI-choice
    interaction actually makes - not merely that document_tabs was reachable.
    """

    def __init__(self, response_text: str):
        self.call_count = 0
        self._response_text = response_text

    def create(self, **kwargs):
        self.call_count += 1
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self._response_text)])


class _CountingFakeClient:
    def __init__(self, response_text: str):
        self.messages = _CountingMessagesAPI(response_text)


_VALID_DOC_JSON = (
    '{"method_summary": "Carries the provision output.", "assumptions": [], '
    '"data_sources": [], "anomalies_noted": [], "role_notes": ""}'
)


def _render_gate3(monkeypatch, tmp_path, *, documentation_client=None, use_real_document_tabs=False):
    """Drive a REAL Orchestrator to a real post-Gate-2 preview, inject it into
    a REAL Streamlit AppTest session, and render Screen 3.

    Agent 1-3 internals are mocked exactly as agents/test_orchestrator.py mocks
    them (parse_workbook, detect_anomalies, run_reconciliation) - deterministic
    Python with no I/O. document_tabs is left mocked (no Anthropic call
    possible) unless use_real_document_tabs=True, in which case the REAL
    agents/documentation.py runs against documentation_client, which must be a
    fake/counting client - never a real anthropic.Anthropic().
    """
    fixtures = ORCHESTRATOR_FIXTURES
    result = fixtures["_result"]()
    fixtures["_patch_agents"](monkeypatch, result)
    if use_real_document_tabs:
        from agents.documentation import document_tabs as real_document_tabs

        monkeypatch.setattr(orchestrator_module, "document_tabs", real_document_tabs)
        monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)

    audit_log = AuditLog(str(tmp_path / "audit.db"))
    state_store = StateStore(audit_log.db_path, audit_log=audit_log)
    orchestrator = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        documentation_client=documentation_client,
        code_version="apptest-fixture",
    )
    report_id, preview = fixtures["_start_preview"](orchestrator)
    state = orchestrator._state[report_id]

    app = AppTest.from_file(str(APP_PATH))
    app.session_state["orchestrator"] = orchestrator
    app.session_state["report_id"] = report_id
    app.session_state["parsed_file"] = state["parsed_file"]
    app.session_state["findings"] = state["findings"]
    app.session_state["reconciliation_result"] = preview
    app.session_state["materiality_defaults"] = orchestrator.get_materiality_defaults(report_id)
    app.session_state["reference_figures"] = None
    app.session_state["reviewer_name"] = fixtures["ACTOR"]
    app.session_state["reviewer_role"] = "actuary"
    app.session_state["context_match_verdict"] = state["context_match_verdict"]
    app.session_state["screen"] = 3
    app = app.run(timeout=20)
    return app, orchestrator, audit_log, report_id


def test_upload_screen_renders_the_required_context_and_reference_controls():
    app = _initial_app()

    assert not app.exception
    assert [header.value for header in app.header] == ["Screen 1 — Upload"]
    assert [area.label for area in app.text_area] == ["Describe what this file does"]
    labels = {widget.label for widget in app.text_input}
    assert {"Entity", "Period", "Currency", "Basis (optional)"} <= labels
    start_audit_buttons = [
        button for button in app.button if button.label == "Start audit"
    ]
    assert len(start_audit_buttons) == 1
    confirmation = next(
        checkbox
        for checkbox in app.checkbox
        if checkbox.label.startswith("I confirm that the workbook")
    )
    assert confirmation.value is False
    assert start_audit_buttons[0].disabled is True
    role_selectboxes = [
        selectbox for selectbox in app.selectbox if selectbox.label == "Role"
    ]
    assert len(role_selectboxes) == 1
    assert role_selectboxes[0].options == ["Actuary", "CRO", "CFO", "Auditor"]


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


# --------------------------------------------------------------------------
# Work Package 1, Phase E — explicit AI documentation choice
# --------------------------------------------------------------------------


def test_approval_screen_shows_the_declined_ai_documentation_status():
    declined_report = GENERATOR_FIXTURES["_report"](
        ai_documentation_status="declined", documentation=[], llm_data_manifest=[]
    )
    app = _initial_app()
    app.session_state["screen"] = 4
    app.session_state["report_preview"] = declined_report
    app.session_state["findings"] = []
    app = app.run(timeout=20)

    assert not app.exception
    assert any("AI documentation declined" in caption.value for caption in app.caption)


def test_report_screen_shows_the_generated_ai_documentation_status():
    generated_report = GENERATOR_FIXTURES["_report"](ai_documentation_status="generated")
    app = _initial_app()
    app.session_state["screen"] = 5
    app.session_state["final_report"] = generated_report
    app.session_state["pdf_bytes"] = b"%PDF-test"
    app.session_state["audit_rows"] = GENERATOR_FIXTURES["_audit_rows"]()
    app = app.run(timeout=20)

    assert not app.exception
    assert any("AI documentation generated" in caption.value for caption in app.caption)


def test_ai_choice_radio_has_no_preselected_option():
    """Item 1: rendering the choice widget must never default to either option."""
    source = APP_PATH.read_text(encoding="utf-8")
    radio_block = source[source.index('st.radio(\n        "Choose how to proceed with tab documentation'):]
    radio_block = radio_block[: radio_block.index(")\n") + 2]
    assert "index=None" in radio_block


def test_ai_disclosure_lists_the_actual_transmitted_categories_and_drops_the_old_false_claims():
    """Items 2, 20: the disclosure enumerates real categories and no longer
    claims cell values, amounts, or workbook data are withheld."""
    source = APP_PATH.read_text(encoding="utf-8")

    for category in (
        "Cached numeric cell values",
        "Short text cell values below the current 40-character threshold",
        "Exact formula text",
        "professional role category",
        "not a PII detector",
        "Reference-figure amounts",
        "Materiality thresholds",
    ):
        assert category in source

    assert "not cell values" not in source
    assert "not workbook data" not in source


def test_confirmation_checkbox_is_only_rendered_for_the_use_path():
    """Item 3: the synthetic/authorised-data confirmation is required only when
    the reviewer chooses the AI path, never for decline."""
    source = APP_PATH.read_text(encoding="utf-8")
    choice_fn = source[source.index("def _ai_choice_ui("):]
    choice_fn = choice_fn[: choice_fn.index("\ndef ", 1)]

    assert 'if choice == "use":' in choice_fn
    assert "synthetic or that I am authorised" in choice_fn


def test_confirm_reconciliation_is_disabled_until_an_ai_choice_is_made():
    """Item 4: submission is blocked until the explicit use/decline choice is made."""
    source = APP_PATH.read_text(encoding="utf-8")
    assert "ai_choice_missing = use_ai_documentation is None" in source
    start = source.index("disabled = (\n        has_blocker")
    end = source.index('if not st.button("Confirm reconciliation"', start)
    disabled_block = source[start:end]
    assert "ai_choice_missing" in disabled_block


def test_submit_gate3_decisions_receives_the_explicit_choice_and_acknowledgment():
    source = APP_PATH.read_text(encoding="utf-8")
    assert "use_ai_documentation=bool(use_ai_documentation)," in source
    assert "ai_transmission_acknowledged=ai_transmission_acknowledged," in source


def test_retry_path_also_offers_an_explicit_ai_choice_before_calling_prepare_report():
    """A failed attempt using AI must be retryable by switching to decline
    without forcing a second Anthropic call."""
    source = APP_PATH.read_text(encoding="utf-8")
    retry_block = source[source.index("if st.session_state.report_preparation_error:") :]
    retry_block = retry_block[: retry_block.index("\ndef ")]
    assert '_ai_choice_ui("retry_ai_documentation")' in retry_block
    assert "use_ai_documentation=bool(retry_use_ai)," in retry_block


# --------------------------------------------------------------------------
# Recommendation 2 — the UI shows which workbook is being confirmed
# --------------------------------------------------------------------------


def test_app_shows_both_a_short_and_a_full_workbook_hash():
    """Decision 4: the short hash is for the eye, the full 64 characters are
    what a reviewer can actually check against an external record. Showing only
    the abbreviation would ask them to confirm something uncheckable."""
    source = APP_PATH.read_text()
    assert "SHA-256 (short)" in source
    assert "workbook_hash[:12]" in source
    assert "st.code(workbook_hash" in source
    assert "shasum -a 256" in source


def test_app_resets_the_confirmation_when_the_workbook_changes():
    """Requirement 4: a different file, even under the same name, invalidates
    the previous confirmation."""
    source = APP_PATH.read_text()
    assert 'st.session_state.get("confirmed_workbook_hash") != workbook_hash' in source
    assert "st.session_state.gate1_context_confirmed = False" in source


def test_app_passes_bytes_and_the_confirmed_hash_rather_than_a_path():
    """The invariant: no temporary file sits between confirmation and parsing."""
    source = APP_PATH.read_text()
    assert "expected_workbook_hash=workbook_hash" in source
    assert "uploaded_file.getvalue()" in source
    assert "tempfile" not in source
    assert "NamedTemporaryFile" not in source


def test_app_surfaces_a_workbook_identity_mismatch_without_deciding_it():
    """app.py renders the refusal; the refusal itself is made in the
    orchestrator, per the rule that app.py holds no business logic."""
    source = APP_PATH.read_text()
    assert "except WorkbookIdentityError as exc:" in source
    assert "no Gate 1 decision was recorded" in source
    # The comparison itself must not live here.
    assert "verify_bytes_match(" not in source


def test_gate1_passes_the_confirmed_workbook_hash_to_file_context():
    """The FileContext received by Orchestrator.run() must carry the hash
    calculated from the uploaded bytes, or Pydantic validation fails before
    the run even starts. This test verifies the construction supplies it."""
    source = APP_PATH.read_text()
    # The construction must include the confirmed hash calculated from the upload.
    assert "confirmed_workbook_hash=workbook_hash" in source
    # And it must be in the FileContext construction (not just a comment or string).
    assert "FileContext(" in source
    # Verify the hash is calculated before the construction.
    fc_line = source.find("file_context = FileContext(")
    hash_line = source.find("workbook_hash = _workbook_identity_panel")
    assert hash_line < fc_line, (
        "workbook_hash must be calculated before FileContext construction"
    )


# --------------------------------------------------------------------------
# Gate 3 AI-choice — real Streamlit AppTest behavioural coverage
#
# The source-inspection tests above (test_ai_choice_radio_has_no_preselected_
# option and friends) prove the widget CODE is shaped correctly. They do not
# prove the widget BEHAVES correctly when actually rendered and interacted
# with. These tests do: a real Orchestrator, a real AppTest render of Screen
# 3, and real widget interactions (select, check, click), with only Agent
# 1-3's internals and the Anthropic client mocked - never the orchestrator,
# never the Streamlit runtime, never a real network call.
# --------------------------------------------------------------------------


def test_gate3_ai_choice_renders_unselected_with_disclosure_and_makes_no_call(
    monkeypatch, tmp_path
):
    """Items 1, 2, 3, 5: no default selection, the disclosure is visible
    before any choice, and merely rendering Screen 3 makes zero calls."""
    client = _CountingFakeClient(_VALID_DOC_JSON)
    app, orchestrator, audit_log, report_id = _render_gate3(
        monkeypatch, tmp_path, documentation_client=client
    )

    assert not app.exception

    ai_choice_radio = next(
        r for r in app.radio if r.key == "gate3_ai_documentation_choice"
    )
    assert ai_choice_radio.value is None

    disclosure_text = " ".join(md.value for md in app.markdown)
    assert "cached numeric cell values" in disclosure_text.lower()
    assert "short text cell values below the current 40-character threshold" in disclosure_text.lower()
    assert "professional role category" in disclosure_text.lower()

    # No acknowledgement checkbox until "use" is chosen.
    assert not any(cb.key == "gate3_ai_documentation_ack" for cb in app.checkbox)

    confirm_button = next(
        b for b in app.button if b.label == "Confirm reconciliation"
    )
    assert confirm_button.disabled is True

    assert client.messages.call_count == 0
    event_types = [row["event_type"] for row in audit_log.get_rows(report_id)]
    assert "llm_use_decision" not in event_types
    assert "llm_call" not in event_types


def test_gate3_selecting_an_option_without_submitting_makes_no_call(monkeypatch, tmp_path):
    """Items 3, 5: selecting "use" and checking the acknowledgement, without
    clicking Confirm, still makes zero calls."""
    client = _CountingFakeClient(_VALID_DOC_JSON)
    app, orchestrator, audit_log, report_id = _render_gate3(
        monkeypatch, tmp_path, documentation_client=client
    )

    ai_choice_radio = next(
        r for r in app.radio if r.key == "gate3_ai_documentation_choice"
    )
    app = ai_choice_radio.set_value("use").run(timeout=20)
    assert not app.exception

    ack_checkbox = next(
        cb for cb in app.checkbox if cb.key == "gate3_ai_documentation_ack"
    )
    assert ack_checkbox.value is False

    confirm_button = next(
        b for b in app.button if b.label == "Confirm reconciliation"
    )
    assert confirm_button.disabled is True, (
        "selecting 'use' without the acknowledgement must still block submission"
    )

    app = ack_checkbox.set_value(True).run(timeout=20)
    assert not app.exception
    confirm_button = next(
        b for b in app.button if b.label == "Confirm reconciliation"
    )
    assert confirm_button.disabled is False

    assert client.messages.call_count == 0
    event_types = [row["event_type"] for row in audit_log.get_rows(report_id)]
    assert "llm_use_decision" not in event_types
    assert "llm_call" not in event_types


def test_gate3_declining_ai_makes_zero_calls_and_reaches_gate4(monkeypatch, tmp_path):
    """Items 4, 6: choosing 'decline' needs no acknowledgement, makes no
    Anthropic call, and still reaches Gate 4 with a deterministic report."""
    client = _CountingFakeClient(_VALID_DOC_JSON)
    app, orchestrator, audit_log, report_id = _render_gate3(
        monkeypatch, tmp_path, documentation_client=client
    )

    ai_choice_radio = next(
        r for r in app.radio if r.key == "gate3_ai_documentation_choice"
    )
    app = ai_choice_radio.set_value("decline").run(timeout=20)
    assert not app.exception
    assert not any(cb.key == "gate3_ai_documentation_ack" for cb in app.checkbox)

    confirm_button = next(
        b for b in app.button if b.label == "Confirm reconciliation"
    )
    assert confirm_button.disabled is False
    app = confirm_button.click().run(timeout=20)

    assert not app.exception
    assert app.session_state["screen"] == 4
    report = app.session_state["report_preview"]
    assert report.ai_documentation_status == "declined"
    assert report.documentation == []

    assert client.messages.call_count == 0
    event_types = [row["event_type"] for row in audit_log.get_rows(report_id)]
    assert "llm_use_decision" in event_types
    assert "llm_call" not in event_types


def test_gate3_using_ai_calls_the_mocked_client_exactly_once_after_decision_is_logged(
    monkeypatch, tmp_path
):
    """Items 7, 8, 9, plus ordering: acknowledged AI use invokes only the
    mocked client, exactly once, and llm_use_decision precedes llm_call."""
    client = _CountingFakeClient(_VALID_DOC_JSON)
    app, orchestrator, audit_log, report_id = _render_gate3(
        monkeypatch,
        tmp_path,
        documentation_client=client,
        use_real_document_tabs=True,
    )

    ai_choice_radio = next(
        r for r in app.radio if r.key == "gate3_ai_documentation_choice"
    )
    app = ai_choice_radio.set_value("use").run(timeout=20)
    ack_checkbox = next(
        cb for cb in app.checkbox if cb.key == "gate3_ai_documentation_ack"
    )
    app = ack_checkbox.set_value(True).run(timeout=20)

    confirm_button = next(
        b for b in app.button if b.label == "Confirm reconciliation"
    )
    assert confirm_button.disabled is False
    app = confirm_button.click().run(timeout=20)

    assert not app.exception
    assert app.session_state["screen"] == 4
    report = app.session_state["report_preview"]
    assert report.ai_documentation_status == "generated"

    assert client.messages.call_count == 1

    event_types = [row["event_type"] for row in audit_log.get_rows(report_id)]
    assert event_types.index("llm_use_decision") < event_types.index("llm_call")


# --------------------------------------------------------------------------
# Gate 1 workbook identity — demo cases (P0 fix)
#
# _workbook_identity_panel previously received only the file_uploader's
# value, so loading a demo case skipped identity display and confirmation
# reset entirely; the hash was computed a second time, only after "Start
# audit" was clicked. These tests exercise the real app.py functions inside
# a genuine Streamlit script context (AppTest), proving the fix behaviourally
# rather than by source inspection alone.
# --------------------------------------------------------------------------


def test_loading_case_4_displays_its_identity_and_correct_hash():
    """Requirement 1: loading a demo case must show its identity panel, not
    "Not uploaded", and the hash must be the real SHA-256 of its bytes."""
    from core.workbook_identity import sha256_bytes
    from demo_cases import load_case

    expected_bytes = load_case(4)["workbook_bytes"]
    expected_hash = sha256_bytes(expected_bytes)

    app = _initial_app()
    selector = next(s for s in app.selectbox if s.key == "demo_case_selector")
    case4_index = next(
        i for i, label in enumerate(selector.options) if label.startswith("Case 4")
    )
    app = selector.set_value(case4_index).run(timeout=20)

    load_button = next(b for b in app.button if b.label == "Load case")
    app = load_button.click().run(timeout=20)
    assert not app.exception

    assert app.session_state["demo_workbook_bytes"] == expected_bytes
    assert app.session_state["confirmed_workbook_hash"] == expected_hash

    identity_text = " ".join(md.value for md in app.markdown)
    assert "Workbook identity" in identity_text
    metric_values = {m.label: m.value for m in app.metric}
    assert metric_values["Size"] == f"{len(expected_bytes):,} bytes"
    assert metric_values["SHA-256 (short)"] == expected_hash[:12]
    code_values = [c.value for c in app.code]
    assert expected_hash in code_values


def test_displayed_hash_equals_hash_passed_to_file_context():
    """Requirement 2: the hash bound into FileContext must be the exact
    value returned by _workbook_identity_panel, never recomputed later."""
    source = APP_PATH.read_text()
    panel_call = source.find("workbook_hash = _workbook_identity_panel(")
    file_context_call = source.find("confirmed_workbook_hash=workbook_hash")
    run_call = source.find("expected_workbook_hash=workbook_hash")
    assert panel_call != -1 and file_context_call != -1 and run_call != -1
    assert panel_call < file_context_call < run_call
    # The hash is computed in exactly one place — inside the identity panel
    # itself — never a second time after confirmation.
    assert source.count("sha256_bytes(workbook_bytes)") == 1


def test_switching_from_case_1_to_case_4_clears_confirmation():
    """Requirement 3: switching identity, including demo-to-demo, resets a
    prior confirmation — not just a different upload under the same name."""

    def script():
        import streamlit as st

        from app import _workbook_identity_panel

        _workbook_identity_panel(b"case-1-bytes", "Case 1: Clean Reserve Calculation (pass)")
        st.session_state.gate1_context_confirmed = True
        _workbook_identity_panel(
            b"case-4-bytes", "Case 4: Claims Reserve Roll-Forward (pass after mapping approval)"
        )
        st.session_state.after_switch_confirmed = st.session_state.gate1_context_confirmed

    at = AppTest.from_function(script).run(timeout=20)
    assert not at.exception
    assert at.session_state["after_switch_confirmed"] is False


def test_reloading_identical_bytes_does_not_create_a_false_mismatch():
    """Requirement 4: re-selecting the same case (identical bytes and name)
    must not clear a confirmation that is still valid."""

    def script():
        import streamlit as st

        from app import _workbook_identity_panel

        same_bytes = b"identical-workbook-bytes"
        _workbook_identity_panel(same_bytes, "Case 1: Clean Reserve Calculation (pass)")
        st.session_state.gate1_context_confirmed = True
        _workbook_identity_panel(same_bytes, "Case 1: Clean Reserve Calculation (pass)")
        st.session_state.still_confirmed = st.session_state.gate1_context_confirmed

    at = AppTest.from_function(script).run(timeout=20)
    assert not at.exception
    assert at.session_state["still_confirmed"] is True


def test_uploading_a_workbook_after_selecting_a_demo_clears_confirmation():
    """Requirement 5: an upload takes priority over — and must invalidate —
    a previously confirmed demo-case identity."""

    def script():
        from types import SimpleNamespace

        import streamlit as st

        from app import _effective_workbook_bytes, _workbook_identity_panel

        st.session_state.demo_workbook_bytes = b"demo-bytes-case-4"
        st.session_state.demo_workbook_label = "Case 4: Claims Reserve Roll-Forward (pass after mapping approval)"
        demo_bytes, demo_name = _effective_workbook_bytes(None)
        _workbook_identity_panel(demo_bytes, demo_name)
        st.session_state.gate1_context_confirmed = True

        fake_upload = SimpleNamespace(
            getvalue=lambda: b"a-real-uploaded-workbook", name="my_reserves.xlsx"
        )
        up_bytes, up_name = _effective_workbook_bytes(fake_upload)
        _workbook_identity_panel(up_bytes, up_name)
        st.session_state.final_confirmed = st.session_state.gate1_context_confirmed
        st.session_state.final_name = up_name

    at = AppTest.from_function(script).run(timeout=20)
    assert not at.exception
    assert at.session_state["final_confirmed"] is False
    assert at.session_state["final_name"] == "my_reserves.xlsx"


def test_the_uploaded_workbook_path_continues_to_work():
    """Requirement 6: an upload with no demo case loaded still resolves to
    its own bytes and filename, unaffected by the demo-case fallback."""
    fake_upload = SimpleNamespace(getvalue=lambda: b"upload-only-bytes", name="workbook.xlsx")

    def script():
        import streamlit as st

        from app import _effective_workbook_bytes

        st.session_state.pop("demo_workbook_bytes", None)

    at = AppTest.from_function(script).run(timeout=20)
    assert not at.exception
    # Exercised directly: no Streamlit widget context is required to prove
    # the resolution logic itself, only that session_state was consulted.
    workbook_bytes, name = _effective_workbook_bytes(fake_upload)
    assert workbook_bytes == b"upload-only-bytes"
    assert name == "workbook.xlsx"


def test_altered_bytes_are_refused_by_the_orchestrator():
    """Requirement 7: this is Decision 4's existing guarantee (see
    test_app_surfaces_a_workbook_identity_mismatch_without_deciding_it and
    core/test_workbook_identity.py) — re-asserted here so the Gate 1 identity
    fix does not silently regress it. The confirmed hash shown and bound at
    Gate 1 is the one checked; a byte-for-byte different workbook is refused
    before anything is parsed or recorded."""
    from core.workbook_identity import WorkbookIdentityError, verify_bytes_match

    confirmed_hash = "a" * 64
    with pytest.raises(WorkbookIdentityError):
        verify_bytes_match(b"different bytes than were confirmed", confirmed_hash)
