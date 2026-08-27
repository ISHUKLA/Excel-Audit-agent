"""Tests for core/models.py — that every model instantiates, and that the
distinctions the models exist to preserve cannot be collapsed by accident."""

import hashlib
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

import core.models
from core.models import (
    AccountingProvenance,
    AccountMapping,
    AnomalyFinding,
    ArtifactReference,
    AuditLogRow,
    AuditReport,
    CellRecord,
    ControlTotalCheck,
    DerivationStep,
    FileContext,
    LLMDataManifestEntry,
    MappingReviewDecision,
    ParsedFile,
    RecalculationEngineProfile,
    RecalculationEvidence,
    RecalculationPolicy,
    ReconciliationLine,
    ReconciliationResult,
    ReferenceFigureLine,
    ReferenceFigures,
    StateSnapshot,
    TabDocumentation,
    TraceabilityEntry,
    WorkbookMeta,
)

NOW = datetime(2026, 8, 10, 9, 30, tzinfo=timezone.utc)
ZERO_HASH = "0" * 64


# --------------------------------------------------------------------------
# builders — dummy data, kept minimal so each test's own point stays visible
# --------------------------------------------------------------------------


def a_file_context(**overrides):
    defaults = dict(
        filename="provisions_q4.xlsx",
        description="Q4 technical provisions roll-forward",
        user_role="actuary",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        confirmed_workbook_hash="a" * 64,
        uploaded_at=NOW,
    )
    return FileContext(**{**defaults, **overrides})


def a_cell(**overrides):
    defaults = dict(
        cell_ref="Provisions!C5",
        formula="=SUM(C1:C4)",
        cached_value=1250.0,
        data_type="number",
        number_format="#,##0.00",
        is_error=False,
        error_type=None,
        is_stale=False,
        calculation_freshness="fresh",
    )
    return CellRecord(**{**defaults, **overrides})


def a_workbook_meta(**overrides):
    defaults = dict(
        calc_mode="automatic",
        workbook_hash="a" * 64,
        app_version="Microsoft Excel",
        fully_calculated_on_load=True,
    )
    return WorkbookMeta(**{**defaults, **overrides})


def a_parsed_file(**overrides):
    defaults = dict(
        tab_names=["Provisions"],
        cells={"Provisions!C5": a_cell()},
        named_ranges={},
        external_links=[],
        has_vba=False,
        workbook_meta=a_workbook_meta(),
        tab_dependency_graph={"Provisions": []},
        cell_dependency_graph={"Provisions!C5": ["Provisions!C1"]},
        warnings=[],
    )
    return ParsedFile(**{**defaults, **overrides})


def a_reference_line(**overrides):
    defaults = dict(
        line_id="GL-001",
        account_number="4100",
        label="Technical provisions",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        ledger_source="SAP FI extract, Q4 close",
        debit_credit="credit",
        amount=1250.0,
        version="2026-01-15",
        evidence_ref="GL_extract_Q4.csv row 3",
    )
    return ReferenceFigureLine(**{**defaults, **overrides})


def a_derivation_step(**overrides):
    defaults = dict(
        cell_ref="Provisions!C5",
        formula="=SUM(C1:C4)",
        depends_on=["Provisions!C1", "Provisions!C2"],
        resolved_value=1250.0,
        is_supported=True,
    )
    return DerivationStep(**{**defaults, **overrides})


def a_recon_line(**overrides):
    defaults = dict(
        check_type="excel_vs_python",
        label="Technical provisions",
        source_value=1250.0,
        target_value=1250.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        pct_threshold=0.01,
        absolute_threshold=100.0,
        threshold_is_default=True,
        completeness="complete",
        reconstruction_coverage_pct=100.0,
        unsupported_elements=[],
        derivation=[a_derivation_step()],
        mapping_id=None,
        calculation_evidence_status="fresh",
    )
    return ReconciliationLine(**{**defaults, **overrides})


def a_mapping(**overrides):
    defaults = dict(
        mapping_id="MAP-001",
        python_output_cell_ref="Provisions!C5",
        reference_line_id="GL-001",
        mapping_type="one_to_one",
        suggested_by="fuzzy_match",
        suggested_confidence=94.0,
        approved_by="Isaac Shukla",
        approved_at=NOW,
        approval_note=None,
        is_approved=True,
    )
    return AccountMapping(**{**defaults, **overrides})


def an_audit_report(**overrides):
    defaults = dict(
        file_context=a_file_context(),
        reference_figures=None,
        authoritative_outputs=["Provisions!C5"],
        parsed_file=a_parsed_file(),
        findings=[],
        mappings=[],
        reconciliation=[],
        unmatched_reference_items=[],
        unmapped_python_outputs=[],
        context_match_verdict="not_checked",
        traceability_index=[],
        documentation=[],
        llm_data_manifest=[],
        translation_and_reconciliation_verdict="pass",
        internal_verdict="pass",
        external_verdict="not_performed",
        workbook_hash="a" * 64,
        code_version="0.1.0",
        validation_run_id="run-001",
        disclaimer="This report does not validate the actuarial model.",
        independence_disclosure=(
            "All four gates were completed by the same individual. "
            "No independent review occurred."
        ),
        report_approval_name=None,
        report_approval_at=None,
        report_approval_role=None,
        generated_at=NOW,
        report_id="RPT-001",
        audit_log_verification_note="Re-verify with AuditLog.verify_chain(report_id).",
    )
    return AuditReport(**{**defaults, **overrides})


def a_recalculation_engine_profile(**overrides):
    defaults = dict(
        profile_id="libreoffice-macos-26.2.5.2-x86_64",
        engine_family="libreoffice",
        exact_version="26.2.5.2",
        operating_system="macos",
        architecture="x86_64",
        supported_extensions=[".xlsx"],
        status="candidate",
    )
    return RecalculationEngineProfile(**{**defaults, **overrides})


def a_recalculation_policy(**overrides):
    defaults = dict(
        policy_id="test-policy",
        policy_version="1.0.0",
        profiles=[a_recalculation_engine_profile()],
    )
    return RecalculationPolicy(**{**defaults, **overrides})


def an_artifact_reference(**overrides):
    defaults = dict(
        artifact_kind="confirmed_source",
        relative_path="workbooks/test.xlsx",
        sha256="a" * 64,
        byte_size=1024,
    )
    return ArtifactReference(**{**defaults, **overrides})


def a_recalculation_evidence(**overrides):
    source_artifact = ArtifactReference(
        artifact_kind="confirmed_source",
        relative_path="source.xlsx",
        sha256="b" * 64,
        byte_size=1024,
    )
    recalc_artifact = ArtifactReference(
        artifact_kind="recalculated_output",
        relative_path="recalculated.xlsx",
        sha256="c" * 64,
        byte_size=1024,
    )
    defaults = dict(
        source_workbook_hash="b" * 64,
        recalculated_workbook_hash="c" * 64,
        engine_profile_id="test-profile",
        engine_family="libreoffice",
        detected_engine_version="26.2.5.2",
        policy_id="test-policy",
        policy_hash="d" * 64,
        started_at=datetime(2026, 1, 1, 10, 0),
        completed_at=datetime(2026, 1, 1, 10, 5),
        formula_count_before=100,
        formula_count_after=100,
        formula_manifest_hash_before="e" * 64,
        formula_manifest_hash_after="e" * 64,
        source_artifact=source_artifact,
        recalculated_artifact=recalc_artifact,
        external_data_refresh_status="not_performed",
        warnings=[],
    )
    return RecalculationEvidence(**{**defaults, **overrides})


# --------------------------------------------------------------------------
# every model instantiates
# --------------------------------------------------------------------------


def test_every_model_instantiates_with_dummy_data():
    """Smoke test: every model in core.models accepts a plausible instance.

    The step numbers its models 1-17, but item 11b (ReconciliationResult) makes
    18. Rather than hardcode either number, this checks the list against what
    the module actually defines, so a model added later cannot slip in
    untested."""
    models = [
        a_file_context(),
        a_reference_line(),
        ReferenceFigures(
            source_label="Q4 trial balance extract",
            entity="Acme Life SA",
            period="2025-Q4",
            currency="EUR",
            basis="IFRS 17",
            control_total=1250.0,
            lines=[a_reference_line()],
            uploaded_at=NOW,
        ),
        a_mapping(),
        MappingReviewDecision(mapping_id="MAP-001", action="approve"),
        a_cell(),
        a_workbook_meta(),
        a_derivation_step(),
        AccountingProvenance(
            reference_line_id="GL-001",
            account_number="4100",
            ledger_source="SAP FI extract, Q4 close",
            entity="Acme Life SA",
            period="2025-Q4",
            currency="EUR",
            evidence_ref="GL_extract_Q4.csv row 3",
            mapping_id="MAP-001",
            approved_by="Isaac Shukla",
        ),
        a_parsed_file(),
        AnomalyFinding(
            finding_id="F-001",
            severity="warning",
            tab="Provisions",
            cell_ref="Provisions!C7",
            description="Hardcoded 1.05 inside a formula",
            raw_value="=C6*1.05",
        ),
        a_recon_line(),
        ReconciliationResult(
            lines=[a_recon_line()],
            mappings=[a_mapping()],
            unmatched_reference_items=[],
            unmapped_python_outputs=[],
        ),
        TraceabilityEntry(
            report_figure_label="Technical provisions",
            report_value=1250.0,
            derivation=[a_derivation_step()],
            trace_status="traced",
        ),
        TabDocumentation(
            tab_name="Provisions",
            method_summary="Rolls forward opening provisions.",
            assumptions=["Discount rate held flat"],
            data_sources=["Claims extract"],
            anomalies_noted=[],
            role_notes="For the actuary: check the discount rate source.",
        ),
        LLMDataManifestEntry(
            tab_name="Provisions",
            cell_refs_included=["Provisions!C5"],
            cell_refs_excluded=["Provisions!A1"],
            exclusion_reasons={"Provisions!A1": "free text over length threshold, possible PII"},
            sent_at=NOW,
            prompt_char_count=812,
        ),
        AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type="gate_decision",
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        ),
        StateSnapshot(
            report_id="RPT-001",
            gate_name="gate_2_findings_review",
            captured_at=NOW,
            state_json='{"findings": []}',
            state_hash="d" * 64,
        ),
        ControlTotalCheck(
            status="match",
            declared_total=1250.0,
            signed_line_total=1250.0,
            difference=0.0,
        ),
        an_audit_report(),
        a_recalculation_engine_profile(),
        a_recalculation_policy(),
        an_artifact_reference(),
        a_recalculation_evidence(),
    ]
    instantiated = {type(m).__name__ for m in models}
    defined = {
        name
        for name, obj in vars(core.models).items()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    }
    assert instantiated == defined, f"models never instantiated: {defined - instantiated}"


# --------------------------------------------------------------------------
# CellRecord — formula and cached value are never an either/or
# --------------------------------------------------------------------------


def test_cell_record_holds_formula_and_cached_value_together():
    cell = a_cell(formula="=SUM(C1:C4)", cached_value=1250.0)
    assert cell.formula == "=SUM(C1:C4)"
    assert cell.cached_value == 1250.0


def test_cell_record_is_stale_when_formula_has_no_cached_value():
    """A formula that was never recalculated: the formula survives, the value
    is None, and is_stale says why — three separate facts, none lost."""
    cell = a_cell(
        formula="=SUM(C1:C4)", cached_value=None, is_stale=True, calculation_freshness="stale"
    )
    assert cell.formula is not None
    assert cell.cached_value is None
    assert cell.is_stale is True


def test_cell_record_cached_value_accepts_text_and_boolean():
    assert a_cell(cached_value="n/a", data_type="text").cached_value == "n/a"
    assert a_cell(cached_value=True, data_type="boolean").cached_value is True


def test_cell_record_rejects_unknown_data_type():
    with pytest.raises(ValidationError):
        a_cell(data_type="currency")


# --------------------------------------------------------------------------
# ReconciliationLine — "incomplete" is its own state, both passes coexist
# --------------------------------------------------------------------------


def test_partial_reconstruction_is_incomplete_not_pass():
    line = a_recon_line(
        verdict="incomplete",
        completeness="partial",
        target_value=None,
        delta=None,
        delta_pct=None,
        reconstruction_coverage_pct=62.5,
        unsupported_elements=["Provisions!C9 uses VLOOKUP (unsupported)"],
    )
    assert line.verdict == "incomplete"
    assert line.verdict != "pass"
    assert line.completeness == "partial"
    assert line.reconstruction_coverage_pct == 62.5


def test_both_check_types_exist_and_stay_distinct():
    internal = a_recon_line(check_type="excel_vs_python")
    external = a_recon_line(
        check_type="python_vs_accounts",
        mapping_id="MAP-001",
        source_value=1250.0,
        target_value=1249.0,
        delta=1.0,
        delta_pct=0.0008,
    )
    assert internal.check_type != external.check_type
    assert internal.mapping_id is None
    assert external.mapping_id == "MAP-001"


def test_reconciliation_line_carries_both_thresholds():
    line = a_recon_line(pct_threshold=0.005, absolute_threshold=50.0, threshold_is_default=False)
    assert line.pct_threshold == 0.005
    assert line.absolute_threshold == 50.0
    assert line.threshold_is_default is False


def test_reconciliation_line_rejects_a_verdict_outside_the_four_states():
    with pytest.raises(ValidationError):
        a_recon_line(verdict="needs_review")


# --------------------------------------------------------------------------
# ReconciliationResult — a real model, carrying both completeness directions
# --------------------------------------------------------------------------


def test_reconciliation_result_carries_both_completeness_directions():
    result = ReconciliationResult(
        lines=[a_recon_line()],
        mappings=[a_mapping()],
        unmatched_reference_items=["GL-002"],
        unmapped_python_outputs=["Provisions!C6"],
    )
    assert result.unmatched_reference_items == ["GL-002"]
    assert result.unmapped_python_outputs == ["Provisions!C6"]


def test_reconciliation_result_verdicts_are_provisional_by_default():
    """Straight out of Agent 3, verdicts are a preview against default
    thresholds. Only Gate 3 may set this True."""
    assert ReconciliationResult(
        lines=[], mappings=[], unmatched_reference_items=[], unmapped_python_outputs=[]
    ).verdicts_are_final is False


# --------------------------------------------------------------------------
# ReferenceFigures — what the old dict could not represent
# --------------------------------------------------------------------------


def test_two_lines_may_share_a_label_and_differ_in_everything_else():
    """The exact case a dict[str, float] could not hold: two GL rows with the
    same label, different accounts, different amounts."""
    extract = ReferenceFigures(
        source_label="Q4 trial balance extract",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        lines=[
            a_reference_line(line_id="GL-001", account_number="4100", amount=1250.0),
            a_reference_line(line_id="GL-002", account_number="4110", amount=880.0),
        ],
        uploaded_at=NOW,
    )
    first, second = extract.lines
    assert first.label == second.label
    assert first.line_id != second.line_id
    assert first.account_number != second.account_number
    assert first.amount != second.amount


def test_control_total_is_never_assumed_confirmed():
    extract = ReferenceFigures(
        source_label="Q4 trial balance extract",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        control_total=2130.0,
        lines=[a_reference_line()],
        uploaded_at=NOW,
    )
    assert extract.control_total_confirmed_by_human is False


def test_amount_cannot_be_negative_sign_lives_in_debit_credit():
    with pytest.raises(ValidationError):
        a_reference_line(amount=-1250.0)


def test_reference_line_rejects_an_orientation_that_is_neither_debit_nor_credit():
    with pytest.raises(ValidationError):
        a_reference_line(debit_credit="unknown")


# --------------------------------------------------------------------------
# AccountMapping — a proposal and an approval can sit on the same report
# --------------------------------------------------------------------------


def test_a_mapping_is_unapproved_until_a_human_approves_it():
    proposal = AccountMapping(
        mapping_id="MAP-002",
        python_output_cell_ref="Provisions!C6",
        reference_line_id="GL-002",
        mapping_type="one_to_one",
        suggested_by="fuzzy_match",
        suggested_confidence=99.0,
    )
    assert proposal.is_approved is False
    assert proposal.approved_by is None


def test_proposed_and_approved_mappings_coexist_on_one_report():
    proposed = a_mapping(
        mapping_id="MAP-002",
        approved_by=None,
        approved_at=None,
        is_approved=False,
        suggested_confidence=97.0,
    )
    report = an_audit_report(mappings=[a_mapping(), proposed])
    assert [m.is_approved for m in report.mappings] == [True, False]
    assert len(report.mappings) == 2


def test_mapping_type_beyond_one_to_one_is_representable():
    mapping = a_mapping(
        mapping_type="many_to_one",
        approval_note="Aggregation handled manually outside this tool.",
    )
    assert mapping.mapping_type == "many_to_one"
    assert mapping.approval_note is not None


def test_mapping_review_edit_requires_a_replacement_reference_line():
    decision = MappingReviewDecision(
        mapping_id="MAP-001",
        action="edit",
        replacement_reference_line_id="GL-002",
    )
    assert decision.replacement_reference_line_id == "GL-002"

    with pytest.raises(ValidationError, match="replacement reference line"):
        MappingReviewDecision(mapping_id="MAP-001", action="edit")


def test_non_edit_mapping_review_rejects_a_hidden_replacement():
    with pytest.raises(ValidationError, match="only an edited mapping"):
        MappingReviewDecision(
            mapping_id="MAP-001",
            action="approve",
            replacement_reference_line_id="GL-002",
        )


# --------------------------------------------------------------------------
# AuditLogRow — a real 3-row chain
# --------------------------------------------------------------------------


def _row_hash(prev_row_hash: str, payload_hash: str, timestamp: datetime) -> str:
    return hashlib.sha256(
        (prev_row_hash + payload_hash + timestamp.isoformat()).encode()
    ).hexdigest()


def test_three_row_chain_each_hash_incorporates_the_previous():
    rows = []
    prev = ZERO_HASH
    for i in range(3):
        payload_hash = hashlib.sha256(f"event-{i}".encode()).hexdigest()
        row_hash = _row_hash(prev, payload_hash, NOW)
        rows.append(
            AuditLogRow(
                row_id=i + 1,
                report_id="RPT-001",
                event_type="gate_decision",
                payload_hash=payload_hash,
                prev_row_hash=prev,
                row_hash=row_hash,
                timestamp=NOW,
            )
        )
        prev = row_hash

    assert rows[0].prev_row_hash == ZERO_HASH
    assert rows[1].prev_row_hash == rows[0].row_hash
    assert rows[2].prev_row_hash == rows[1].row_hash
    # Recomputing from the stored parts reproduces the stored hash.
    for row in rows:
        assert row.row_hash == _row_hash(row.prev_row_hash, row.payload_hash, row.timestamp)
    # Tampering with one payload breaks the link to the next row.
    tampered = _row_hash(rows[1].prev_row_hash, "f" * 64, rows[1].timestamp)
    assert tampered != rows[2].prev_row_hash


def test_event_type_uses_approval_vocabulary_not_signature_vocabulary():
    assert AuditLogRow(
        row_id=1,
        report_id="RPT-001",
        event_type="report_approved",
        payload_hash="b" * 64,
        prev_row_hash=ZERO_HASH,
        row_hash="c" * 64,
        timestamp=NOW,
    ).event_type == "report_approved"
    with pytest.raises(ValidationError):
        AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type="report_signed",
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        )


# --------------------------------------------------------------------------
# TraceabilityEntry — why a trace is missing, not just whether
# --------------------------------------------------------------------------


def test_trace_status_distinguishes_the_reasons_a_trace_is_absent():
    reasons = [
        "traced",
        "partially_traced",
        "unmapped",
        "mapping_pending_approval",
        "mapping_rejected",
        "not_traceable",
    ]
    for reason in reasons:
        entry = TraceabilityEntry(
            report_figure_label="Technical provisions",
            report_value=1250.0,
            derivation=[],
            trace_status=reason,
        )
        assert entry.trace_status == reason


def test_traceability_entry_has_no_value_matching_fields():
    """Lineage comes from the derivation chain. There is no reverse lookup by
    value, so there is nowhere to record one."""
    fields = set(TraceabilityEntry.model_fields)
    assert "derivation" in fields
    assert "is_traceable" not in fields
    assert not fields & {"source_cell", "source_formula", "derivation_note", "matched_value"}


def test_accounting_side_gets_its_own_provenance():
    entry = TraceabilityEntry(
        report_figure_label="Technical provisions",
        report_value=1250.0,
        derivation=[a_derivation_step()],
        accounting_provenance=AccountingProvenance(
            reference_line_id="GL-001",
            account_number="4100",
            ledger_source="SAP FI extract, Q4 close",
            entity="Acme Life SA",
            period="2025-Q4",
            currency="EUR",
            evidence_ref="GL_extract_Q4.csv row 3",
            mapping_id="MAP-001",
            approved_by="Isaac Shukla",
        ),
        trace_status="traced",
    )
    assert entry.accounting_provenance.approved_by == "Isaac Shukla"


# --------------------------------------------------------------------------
# AuditReport — approval is not independence, and the banned words are gone
# --------------------------------------------------------------------------


def test_an_approved_report_can_still_disclose_no_independent_review():
    """These two facts must be able to coexist: someone approved the report,
    and nobody independent reviewed it."""
    report = an_audit_report(
        report_approval_name="Isaac Shukla",
        report_approval_at=NOW,
        report_approval_role="Senior Actuary",
    )
    assert report.report_approval_name == "Isaac Shukla"
    assert "No independent review occurred." in report.independence_disclosure


def test_audit_report_uses_approval_record_vocabulary_only():
    fields = set(AuditReport.model_fields)
    assert not fields & {"signed_by", "signed_at", "signed_role", "attested_by", "attested_at"}
    assert {"report_approval_name", "report_approval_at", "report_approval_role"} <= fields


# --------------------------------------------------------------------------
# Work Package 1, Phase E — explicit per-report AI documentation choice
# --------------------------------------------------------------------------


def test_llm_use_decision_is_an_accepted_event_type():
    assert AuditLogRow(
        row_id=1,
        report_id="RPT-001",
        event_type="llm_use_decision",
        payload_hash="b" * 64,
        prev_row_hash=ZERO_HASH,
        row_hash="c" * 64,
        timestamp=NOW,
    ).event_type == "llm_use_decision"


def test_event_type_list_did_not_become_open_after_the_llm_use_decision_widening():
    for accepted in (
        "gate_decision",
        "state_snapshot",
        "llm_call",
        "llm_use_decision",
        "report_approved",
        "mapping_decision",
        "chain_verification",
        "workbook_identity_mismatch",
    ):
        assert AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type=accepted,
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        ).event_type == accepted

    with pytest.raises(ValidationError):
        AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type="llm_use_confirmed",
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        )


def test_ai_documentation_status_defaults_to_not_recorded_for_backward_compatibility():
    """Item 25: a report/snapshot assembled before this control existed must
    remain readable. Omitting the field must not raise, and must not silently
    read as though AI were used."""
    report = an_audit_report()
    assert report.ai_documentation_status == "not_recorded"


def test_ai_documentation_status_accepts_every_defined_outcome():
    for status in ("generated", "declined", "validation_failed", "unavailable", "not_recorded"):
        assert an_audit_report(ai_documentation_status=status).ai_documentation_status == status

    with pytest.raises(ValidationError):
        an_audit_report(ai_documentation_status="ai_validated")


def test_declined_report_can_carry_empty_documentation_and_manifest():
    """The no-AI completion path assembles a report with no fabricated content."""
    report = an_audit_report(
        ai_documentation_status="declined", documentation=[], llm_data_manifest=[]
    )
    assert report.documentation == []
    assert report.llm_data_manifest == []


def test_the_headline_verdict_is_not_called_validation():
    fields = set(AuditReport.model_fields)
    assert "translation_and_reconciliation_verdict" in fields
    assert not any("validation_verdict" == f or f == "overall_verdict" for f in fields)


def test_reference_figures_is_a_line_list_not_a_dict():
    assert "line_items" not in set(ReferenceFigures.model_fields)
    annotation = ReferenceFigures.model_fields["lines"].annotation
    assert annotation == list[ReferenceFigureLine]


def test_generated_at_and_approval_time_are_separate_moments():
    later = datetime(2026, 8, 10, 17, 0, tzinfo=timezone.utc)
    report = an_audit_report(generated_at=NOW, report_approval_at=later, report_approval_name="Isaac Shukla")
    assert report.generated_at != report.report_approval_at


def test_external_verdict_may_be_not_performed_but_internal_may_not():
    """No reference figures means the accounts pass didn't happen — an explicit
    state. The internal pass always happens, so it has no such state."""
    assert an_audit_report(external_verdict="not_performed").external_verdict == "not_performed"
    with pytest.raises(ValidationError):
        an_audit_report(internal_verdict="not_performed")


def test_parsed_file_keeps_the_two_dependency_graphs_separate():
    fields = set(ParsedFile.model_fields)
    assert {"tab_dependency_graph", "cell_dependency_graph"} <= fields
    assert "dependency_graph" not in fields
    assert "cached_values" not in fields


def test_audit_report_rejects_a_missing_disclaimer():
    with pytest.raises(ValidationError):
        AuditReport(
            **{k: v for k, v in an_audit_report().model_dump().items() if k != "disclaimer"}
        )


def test_chain_verification_is_an_accepted_event_type():
    """Recommendation 1 adds a sixth event. log_event() builds an AuditLogRow
    on every write, so an unlisted value would fail validation and the write
    would raise — the model has to know about it first."""
    assert AuditLogRow(
        row_id=1,
        report_id="RPT-001",
        event_type="chain_verification",
        payload_hash="b" * 64,
        prev_row_hash=ZERO_HASH,
        row_hash="c" * 64,
        timestamp=NOW,
    ).event_type == "chain_verification"


def test_widening_event_type_did_not_widen_it_to_anything():
    """All five original values still hold, and an unknown one still rejects."""
    for accepted in (
        "gate_decision",
        "state_snapshot",
        "llm_call",
        "report_approved",
        "mapping_decision",
        "chain_verification",
    ):
        assert AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type=accepted,
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        ).event_type == accepted

    with pytest.raises(ValidationError):
        AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type="chain_repaired",
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        )


# --------------------------------------------------------------------------
# Recommendation 2 — Gate 1 is bound to a specific workbook, not a filename
# --------------------------------------------------------------------------

VALID_HASH = "a" * 64


def _file_context_kwargs(**overrides):
    values = dict(
        filename="provisions.xlsx",
        description="Q4 provision calculation",
        user_role="actuary",
        confirmed_workbook_hash=VALID_HASH,
        uploaded_at=NOW,
    )
    values.update(overrides)
    return values


def test_file_context_carries_the_confirmed_workbook_hash():
    assert FileContext(**_file_context_kwargs()).confirmed_workbook_hash == VALID_HASH


def test_file_context_without_a_confirmed_hash_is_rejected():
    """A filename is not an identity. Omitting the hash must be impossible,
    not merely discouraged."""
    kwargs = _file_context_kwargs()
    del kwargs["confirmed_workbook_hash"]
    with pytest.raises(ValidationError):
        FileContext(**kwargs)


@pytest.mark.parametrize(
    "bad", ["", "abc", "a" * 63, "a" * 65, "A" * 64, "g" * 64, None, 12345]
)
def test_file_context_rejects_a_malformed_confirmed_hash(bad):
    """Blank and None matter most: a falsy value that validated would make the
    Gate 1 binding optional in practice while still appearing present."""
    with pytest.raises(ValidationError):
        FileContext(**_file_context_kwargs(confirmed_workbook_hash=bad))


def test_workbook_identity_mismatch_is_an_accepted_event_type():
    assert AuditLogRow(
        row_id=1,
        report_id="RPT-001",
        event_type="workbook_identity_mismatch",
        payload_hash="b" * 64,
        prev_row_hash=ZERO_HASH,
        row_hash="c" * 64,
        timestamp=NOW,
    ).event_type == "workbook_identity_mismatch"


def test_event_type_list_did_not_become_open_after_the_second_widening():
    for accepted in (
        "gate_decision",
        "state_snapshot",
        "llm_call",
        "report_approved",
        "mapping_decision",
        "chain_verification",
        "workbook_identity_mismatch",
    ):
        assert AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type=accepted,
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        ).event_type == accepted

    with pytest.raises(ValidationError):
        AuditLogRow(
            row_id=1,
            report_id="RPT-001",
            event_type="workbook_identity_confirmed",
            payload_hash="b" * 64,
            prev_row_hash=ZERO_HASH,
            row_hash="c" * 64,
            timestamp=NOW,
        )


# --------------------------------------------------------------------------
# Recommendation 3 — Recalculation evidence and engine policy
# --------------------------------------------------------------------------


def test_recalculation_engine_profile_candidate_validates():
    profile = RecalculationEngineProfile(
        profile_id="test",
        engine_family="libreoffice",
        exact_version="26.2.5.2",
        operating_system="macos",
        architecture="x86_64",
        supported_extensions=[".xlsx"],
        status="candidate",
    )
    assert profile.status == "candidate"


def test_recalculation_engine_profile_approved_requires_approval_metadata():
    """Approved profiles must have approved_by, approved_at, and qualification_reference."""
    with pytest.raises(ValidationError, match="require approved_by"):
        RecalculationEngineProfile(
            profile_id="test",
            engine_family="libreoffice",
            exact_version="26.2.5.2",
            operating_system="macos",
            architecture="x86_64",
            supported_extensions=[".xlsx"],
            status="approved",
        )


def test_recalculation_policy_validates():
    profile = RecalculationEngineProfile(
        profile_id="test",
        engine_family="libreoffice",
        exact_version="26.2.5.2",
        operating_system="macos",
        architecture="x86_64",
        supported_extensions=[".xlsx"],
        status="candidate",
    )
    policy = RecalculationPolicy(
        policy_id="policy",
        policy_version="1.0.0",
        profiles=[profile],
    )
    assert len(policy.profiles) == 1


def test_artifact_reference_validates():
    ref = ArtifactReference(
        artifact_kind="confirmed_source",
        relative_path="workbooks/test.xlsx",
        sha256="a" * 64,
        byte_size=1024,
    )
    assert ref.artifact_kind == "confirmed_source"


def test_artifact_reference_requires_valid_sha256():
    """Hash validation uses the canonical workbook-identity validator."""
    with pytest.raises(ValidationError):
        ArtifactReference(
            artifact_kind="confirmed_source",
            relative_path="workbooks/test.xlsx",
            sha256="A" * 64,
            byte_size=1024,
        )


def test_recalculation_evidence_validates():
    """A complete evidence record with matching hashes validates."""
    source = ArtifactReference(
        artifact_kind="confirmed_source",
        relative_path="source.xlsx",
        sha256="b" * 64,
        byte_size=1024,
    )
    recalc = ArtifactReference(
        artifact_kind="recalculated_output",
        relative_path="recalculated.xlsx",
        sha256="c" * 64,
        byte_size=1024,
    )
    evidence = RecalculationEvidence(
        source_workbook_hash="b" * 64,
        recalculated_workbook_hash="c" * 64,
        engine_profile_id="test",
        engine_family="libreoffice",
        detected_engine_version="26.2.5.2",
        policy_id="policy",
        policy_hash="d" * 64,
        started_at=datetime(2026, 1, 1, 10, 0),
        completed_at=datetime(2026, 1, 1, 10, 5),
        formula_count_before=100,
        formula_count_after=100,
        formula_manifest_hash_before="e" * 64,
        formula_manifest_hash_after="e" * 64,
        source_artifact=source,
        recalculated_artifact=recalc,
        external_data_refresh_status="not_performed",
        warnings=[],
    )
    assert evidence.external_data_refresh_status == "not_performed"
