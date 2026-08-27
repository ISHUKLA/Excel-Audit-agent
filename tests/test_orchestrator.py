"""Step 9 integration tests for staged orchestration and durable recovery.

Agents are mocked to keep the tests deterministic and prevent live LLM calls.
Human gates, the audit log, and the snapshot store remain real; one test wraps
all four gates with spies to prove orchestration supplies their evidence context.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import (
    RECONCILIATION_PREVIEW_LABEL,
    Orchestrator,
    PipelineStateError,
)
from core.audit_log import AuditLog, NOT_YET_PARSED
from core.gates import GateBlockedError
from core.models import (
    AccountMapping,
    AnomalyFinding,
    CellRecord,
    DerivationStep,
    FileContext,
    LLMDataManifestEntry,
    MappingReviewDecision,
    ParsedFile,
    ReconciliationLine,
    ReconciliationResult,
    ReferenceFigureLine,
    ReferenceFigures,
    TabDocumentation,
    WorkbookMeta,
)
from core.state_store import ChainIntegrityError, StateIntegrityError, StateStore
from core.workbook_identity import WorkbookIdentityError, sha256_bytes

ACTOR = "Isaac Shukla"
# Real bytes and their real hash: the mocked parser reports CONTEXT_HASH, so
# deriving it from WORKBOOK_BYTES keeps the Gate 1 identity check honest
# rather than satisfied by a hand-written constant.
WORKBOOK_BYTES = b"PK\x03\x04 orchestrator fixture workbook"
CONTEXT_HASH = sha256_bytes(WORKBOOK_BYTES)
NOW = datetime.now(timezone.utc)
DEFAULT_PCT = 0.01
DEFAULT_ABS = 100.0


def _file_context(**overrides) -> FileContext:
    values = dict(
        filename="provisions.xlsx",
        description="Q4 provision calculation",
        user_role="actuary",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        confirmed_workbook_hash=CONTEXT_HASH,
        uploaded_at=NOW,
    )
    return FileContext(**{**values, **overrides})


def _reference_figures(**overrides) -> ReferenceFigures:
    line = ReferenceFigureLine(
        line_id="GL-001",
        account_number="3000",
        label="Technical provisions",
        entity="Acme Life SA",
        period=overrides.get("period", "2025-Q4"),
        currency="EUR",
        ledger_source="SAP FI Q4 close",
        debit_credit="debit",
        amount=100.0,
        evidence_ref="trial-balance.csv row 2",
    )
    values = dict(
        source_label="Q4 trial balance",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        lines=[line],
        uploaded_at=NOW,
    )
    values.update(overrides)
    return ReferenceFigures(**values)


def _parsed_file() -> ParsedFile:
    return ParsedFile(
        tab_names=["Provisions"],
        cells={
            "Provisions!B1": CellRecord(
                cell_ref="Provisions!B1",
                cached_value=100.0,
                data_type="number",
                number_format="0.00",
                is_error=False,
                is_stale=False,
            ),
            "Provisions!C5": CellRecord(
                cell_ref="Provisions!C5",
                formula="=B1",
                cached_value=100.0,
                data_type="number",
                number_format="0.00",
                is_error=False,
                is_stale=False,
            ),
        },
        named_ranges={},
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(
            calc_mode="automatic",
            workbook_hash=CONTEXT_HASH,
            app_version="test",
            fully_calculated_on_load=True,
        ),
        tab_dependency_graph={"Provisions": []},
        cell_dependency_graph={
            "Provisions!C5": ["Provisions!B1"],
            "Provisions!B1": [],
        },
        warnings=[],
    )


def _finding(**overrides) -> AnomalyFinding:
    values = dict(
        finding_id="F0001",
        severity="warning",
        tab="Provisions",
        cell_ref="C5",
        description="Hardcoded literal",
        raw_value="=B1*1.25",
        human_decision=None,
    )
    return AnomalyFinding(**{**values, **overrides})


def _line(**overrides) -> ReconciliationLine:
    values = dict(
        check_type="excel_vs_python",
        label="Technical provisions",
        source_value=100.0,
        target_value=100.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        pct_threshold=DEFAULT_PCT,
        absolute_threshold=DEFAULT_ABS,
        threshold_is_default=True,
        completeness="complete",
        reconstruction_coverage_pct=100.0,
        unsupported_elements=[],
        derivation=[
            DerivationStep(
                cell_ref="Provisions!C5",
                formula="=B1",
                depends_on=["Provisions!B1"],
                resolved_value=100.0,
                is_supported=True,
            )
        ],
        mapping_id=None,
    )
    return ReconciliationLine(**{**values, **overrides})


def _mapping(mapping_id="MAP-001", reference_line_id="GL-001", **overrides):
    values = dict(
        mapping_id=mapping_id,
        python_output_cell_ref="Provisions!C5",
        reference_line_id=reference_line_id,
        mapping_type="one_to_one",
        suggested_by="fuzzy_match",
        suggested_confidence=99.0,
        approved_by=None,
        approved_at=None,
        is_approved=False,
    )
    return AccountMapping(**{**values, **overrides})


def _mapping_decision(mapping_id="MAP-001", action="approve", replacement=None):
    return MappingReviewDecision(
        mapping_id=mapping_id,
        action=action,
        replacement_reference_line_id=replacement,
    )


def _external_line(mapping_id="MAP-001", label="Technical provisions", **overrides):
    return _line(
        check_type="python_vs_accounts",
        label=label,
        mapping_id=mapping_id,
        **overrides,
    )


def _result(**overrides) -> ReconciliationResult:
    values = dict(
        lines=[_line()],
        mappings=[],
        unmatched_reference_items=[],
        unmapped_python_outputs=[],
        verdicts_are_final=False,
    )
    return ReconciliationResult(**{**values, **overrides})


def _decided_findings(findings):
    return [
        finding.model_copy(
            update={
                "human_decision": "dismissed",
                "human_reason": "Reviewed false positive",
                "decided_by": ACTOR,
                "decided_at": NOW,
            }
        )
        for finding in findings
    ]


def _documentation_result():
    return (
        [
            TabDocumentation(
                tab_name="Provisions",
                method_summary="Carries the provision output.",
                assumptions=[],
                data_sources=["Provisions!B1"],
                anomalies_noted=[],
                role_notes="Review the output lineage.",
            )
        ],
        [
            LLMDataManifestEntry(
                tab_name="Provisions",
                cell_refs_included=["Provisions!B1", "Provisions!C5"],
                cell_refs_excluded=[],
                exclusion_reasons={},
                sent_at=NOW,
                prompt_char_count=120,
            )
        ],
    )


def _patch_agents(monkeypatch, result: ReconciliationResult):
    calls = {"reconciliation": []}
    monkeypatch.setattr(
        orchestrator_module,
        "parse_workbook",
        lambda _: _parsed_file().model_copy(deep=True),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "detect_anomalies",
        lambda _: [_finding()],
    )

    def fake_reconciliation(**kwargs):
        calls["reconciliation"].append(kwargs)
        return result.model_copy(deep=True)

    monkeypatch.setattr(orchestrator_module, "run_reconciliation", fake_reconciliation)
    monkeypatch.setattr(orchestrator_module, "build_traceability_index", lambda **_: [])
    monkeypatch.setattr(
        orchestrator_module,
        "document_tabs",
        lambda *_, **__: _documentation_result(),
    )
    return calls


def _orchestrator(tmp_path):
    audit_log = AuditLog(str(tmp_path / "audit.db"))
    state_store = StateStore(audit_log.db_path, audit_log=audit_log)
    return (
        Orchestrator(
            audit_log=audit_log,
            state_store=state_store,
            code_version="test-code-version",
        ),
        audit_log,
        state_store,
    )


def _start_preview(orchestrator, reference_figures=None):
    report_id, parsed, findings = orchestrator.run(
        WORKBOOK_BYTES,
        _file_context(),
        reference_figures,
        expected_workbook_hash=CONTEXT_HASH,
        context_confirmed=True,
        actor=ACTOR,
    )
    label, preview = orchestrator.submit_gate2_decisions(
        report_id,
        _decided_findings(findings),
        ["Provisions!C5"],
        actor=ACTOR,
    )
    assert parsed.workbook_meta.workbook_hash == CONTEXT_HASH
    assert label == RECONCILIATION_PREVIEW_LABEL
    return report_id, preview


def test_full_staged_pipeline_snapshots_every_pause_and_supplies_gate_context(
    monkeypatch, tmp_path
):
    calls = _patch_agents(monkeypatch, _result())
    gate_contexts = {}
    for gate_name in (
        "context_gate",
        "findings_review_gate",
        "reconciliation_gate",
        "approval_record_gate",
    ):
        original = getattr(orchestrator_module, gate_name)

        def spy(*args, _original=original, _name=gate_name, **kwargs):
            gate_contexts[_name] = kwargs["context"]
            return _original(*args, **kwargs)

        monkeypatch.setattr(orchestrator_module, gate_name, spy)

    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator)

    internal, external, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=True,
        ai_transmission_acknowledged=True,
    )
    report = orchestrator.submit_approval_record(report_id, ACTOR, "actuary")

    assert (internal, external) == ("pass", "not_performed")
    assert final_result.verdicts_are_final is True
    assert report.translation_and_reconciliation_verdict == "pass"
    assert report.report_approval_name == ACTOR
    assert "No independent review was performed" in report.independence_disclosure
    assert report.generated_at <= report.report_approval_at
    assert len(report.llm_data_manifest) == 1

    expected_snapshots = (
        "post_parse",
        "post_anomaly_detection",
        "post_gate2",
        "post_reconciliation",
        "post_gate3",
        "pre_approval_record",
        "post_approval_record",
    )
    assert all(state_store.load_snapshot(report_id, name) is not None for name in expected_snapshots)
    assert set(gate_contexts) == {
        "context_gate",
        "findings_review_gate",
        "reconciliation_gate",
        "approval_record_gate",
    }
    # Requirement 7: Gate 1 records the REAL workbook hash. This assertion
    # previously expected NOT_YET_PARSED, which was honest when nothing had been
    # hashed before the gate — the bytes are now hashed before Gate 1 runs.
    assert gate_contexts["context_gate"]["workbook_hash"] == CONTEXT_HASH
    assert gate_contexts["context_gate"]["workbook_hash"] != NOT_YET_PARSED
    assert all(context["code_version"] for context in gate_contexts.values())
    # Requirement 12: every gate carries the same identity, start to finish.
    assert {context["workbook_hash"] for context in gate_contexts.values()} == {CONTEXT_HASH}
    assert audit_log.verify_chain() == (True, [])
    assert calls["reconciliation"][0]["pct_threshold"] == DEFAULT_PCT
    assert calls["reconciliation"][0]["absolute_threshold"] == DEFAULT_ABS


def test_post_gate2_snapshot_recovers_findings_and_outputs_after_process_loss(
    monkeypatch, tmp_path
):
    _patch_agents(monkeypatch, _result())

    def crash_after_gate2(**_):
        raise RuntimeError("simulated process loss before reconciliation")

    monkeypatch.setattr(orchestrator_module, "run_reconciliation", crash_after_gate2)
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, _, findings = orchestrator.run(
        WORKBOOK_BYTES,
        _file_context(),
        expected_workbook_hash=CONTEXT_HASH,
        context_confirmed=True,
        actor=ACTOR,
    )

    with pytest.raises(RuntimeError, match="simulated process loss"):
        orchestrator.submit_gate2_decisions(
            report_id,
            _decided_findings(findings),
            ["Provisions!C5"],
            actor=ACTOR,
        )

    assert state_store.load_latest_snapshot(report_id).gate_name == "post_gate2"
    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    recovered = fresh.resume(report_id)

    assert recovered["stage"] == "post_gate2"
    assert recovered["authoritative_outputs"] == ["Provisions!C5"]
    assert recovered["findings"][0].human_decision == "dismissed"
    assert recovered["parsed_file"].cells["Provisions!C5"].formula == "=B1"


def test_resume_refuses_a_snapshot_altered_after_it_was_saved(monkeypatch, tmp_path):
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, _, _ = orchestrator.run(
        WORKBOOK_BYTES,
        _file_context(),
        expected_workbook_hash=CONTEXT_HASH,
        context_confirmed=True,
        actor=ACTOR,
    )
    with sqlite3.connect(state_store.db_path) as connection:
        connection.execute(
            "UPDATE snapshots SET state_json = '{}' WHERE snapshot_id = "
            "(SELECT MAX(snapshot_id) FROM snapshots WHERE report_id = ?)",
            (report_id,),
        )

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    with pytest.raises(StateIntegrityError, match="refusing recovered state"):
        fresh.resume(report_id)


def test_partial_reconstruction_flows_into_report_as_incomplete(monkeypatch, tmp_path):
    partial = _line(
        target_value=None,
        delta=None,
        delta_pct=None,
        verdict="incomplete",
        completeness="partial",
        reconstruction_coverage_pct=50.0,
        unsupported_elements=["Provisions!C5 uses VLOOKUP (unsupported)"],
    )
    _patch_agents(monkeypatch, _result(lines=[partial]))
    orchestrator, _, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator)

    internal, external, _ = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=True,
        ai_transmission_acknowledged=True,
        acknowledge_incomplete=True,
    )

    report = orchestrator.get_report(report_id)
    assert (internal, external) == ("incomplete", "not_performed")
    assert report.translation_and_reconciliation_verdict == "incomplete"
    assert report.reconciliation[0].reconstruction_coverage_pct == 50.0


def test_omitted_mapping_approval_cannot_produce_external_pass(monkeypatch, tmp_path):
    mappings = [_mapping("MAP-001"), _mapping("MAP-002", "GL-002")]
    result = _result(
        lines=[
            _line(),
            _external_line("MAP-001", "Mapped line one"),
            _external_line("MAP-002", "Mapped line two"),
        ],
        mappings=mappings,
    )
    _patch_agents(monkeypatch, result)
    orchestrator, _, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator, _reference_figures())

    with pytest.raises(GateBlockedError, match="MAP-002"):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=[_mapping_decision("MAP-001")],
            internal_pct_threshold=DEFAULT_PCT,
            internal_absolute_threshold=DEFAULT_ABS,
            external_pct_threshold=DEFAULT_PCT,
            external_absolute_threshold=DEFAULT_ABS,
            internal_threshold_deviation_reason=None,
            external_threshold_deviation_reason=None,
            actor=ACTOR,
            use_ai_documentation=True,
            ai_transmission_acknowledged=True,
        )

    assert orchestrator._state[report_id]["reconciliation_result"].verdicts_are_final is False
    assert "report" not in orchestrator._state[report_id]


def test_context_mismatch_forces_external_block_even_when_numbers_match(monkeypatch, tmp_path):
    result = _result(
        lines=[_line(), _external_line()],
        mappings=[_mapping()],
    )
    _patch_agents(monkeypatch, result)
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(
        orchestrator,
        _reference_figures(period="2025-Q3"),
    )

    with pytest.raises(GateBlockedError, match="external_verdict=block"):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=[_mapping_decision()],
            internal_pct_threshold=DEFAULT_PCT,
            internal_absolute_threshold=DEFAULT_ABS,
            external_pct_threshold=DEFAULT_PCT,
            external_absolute_threshold=DEFAULT_ABS,
            internal_threshold_deviation_reason=None,
            external_threshold_deviation_reason=None,
            actor=ACTOR,
            use_ai_documentation=True,
            ai_transmission_acknowledged=True,
        )

    gate3_rows = []
    for row in audit_log.get_rows(report_id):
        payload = json.loads(row["payload_json"])
        if payload.get("gate") == 3:
            gate3_rows.append(payload)
    assert gate3_rows[-1]["context_match_verdict"] == "mismatch"
    assert gate3_rows[-1]["external_verdict"] == "block"


def test_gate3_looser_threshold_replaces_preview_before_report_assembly(monkeypatch, tmp_path):
    preview_line = _line(
        source_value=100.0,
        target_value=99.715,
        delta=0.285,
        delta_pct=0.00285,
        verdict="warn",
    )
    _patch_agents(monkeypatch, _result(lines=[preview_line]))
    orchestrator, _, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator)

    internal, _, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[],
        internal_pct_threshold=0.03,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason="CFO-approved Q4 materiality",
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=True,
        ai_transmission_acknowledged=True,
    )

    assert preview.verdicts_are_final is False
    assert preview.lines[0].verdict == "warn"
    assert internal == "pass"
    assert final_result.verdicts_are_final is True
    assert final_result.lines[0].verdict == "pass"
    report = orchestrator.get_report(report_id)
    assert report.reconciliation[0].verdict == "pass"
    assert report.reconciliation[0].pct_threshold == 0.03


def test_approved_mapping_records_human_and_report_keeps_unapproved_proposals(
    monkeypatch, tmp_path
):
    referenced = _mapping("MAP-001")
    proposal_only = _mapping("MAP-UNUSED", "GL-UNUSED")
    result = _result(
        lines=[_line(), _external_line("MAP-001")],
        mappings=[referenced, proposal_only],
    )
    _patch_agents(monkeypatch, result)
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator, _reference_figures())

    _, external, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[
            _mapping_decision("MAP-001"),
            _mapping_decision("MAP-UNUSED", action="reject"),
        ],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=True,
        ai_transmission_acknowledged=True,
        acknowledge_incomplete=True,
    )

    assert external == "pass"
    approved = next(mapping for mapping in final_result.mappings if mapping.mapping_id == "MAP-001")
    assert approved.is_approved is True
    assert approved.approved_by == ACTOR
    assert approved.approved_at is not None
    report = orchestrator.get_report(report_id)
    assert {mapping.mapping_id for mapping in report.mappings} == {"MAP-001", "MAP-UNUSED"}
    assert next(m for m in report.mappings if m.mapping_id == "MAP-UNUSED").is_approved is False
    assert any(row["event_type"] == "mapping_decision" for row in audit_log.get_rows(report_id))


def test_rejected_mapping_is_kept_as_evidence_but_excluded_from_the_verdict(
    monkeypatch, tmp_path
):
    result = _result(
        lines=[_line(), _external_line()],
        mappings=[_mapping()],
    )
    _patch_agents(monkeypatch, result)
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator, _reference_figures())

    _, external, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[_mapping_decision(action="reject")],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=True,
        ai_transmission_acknowledged=True,
        acknowledge_incomplete=True,
    )

    assert external == "incomplete"
    assert not [line for line in final_result.lines if line.check_type == "python_vs_accounts"]
    assert final_result.mappings[0].is_approved is False
    assert final_result.unmatched_reference_items == ["GL-001"]
    assert final_result.unmapped_python_outputs == ["Provisions!C5"]
    actions = [json.loads(row["payload_json"]).get("action") for row in audit_log.get_rows(report_id)]
    assert "rejected" in actions


def test_edited_mapping_preserves_the_proposal_and_adds_a_human_direct_mapping(
    monkeypatch, tmp_path
):
    replacement = ReferenceFigureLine(
        line_id="GL-002",
        account_number="3001",
        label="Technical provisions corrected",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        ledger_source="SAP FI Q4 close",
        debit_credit="debit",
        amount=100.0,
        evidence_ref="trial-balance.csv row 3",
    )
    refs = _reference_figures(lines=[_reference_figures().lines[0], replacement])
    result = _result(lines=[_line(), _external_line()], mappings=[_mapping()])
    _patch_agents(monkeypatch, result)
    orchestrator, _, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator, refs)

    _, external, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[
            _mapping_decision(action="edit", replacement="GL-002")
        ],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=True,
        ai_transmission_acknowledged=True,
        acknowledge_incomplete=True,
    )

    assert external == "incomplete"
    original = next(mapping for mapping in final_result.mappings if mapping.mapping_id == "MAP-001")
    edited = next(mapping for mapping in final_result.mappings if mapping.mapping_id == "MAP-001-HUMAN")
    assert original.is_approved is False
    assert edited.suggested_by == "human_direct"
    assert edited.is_approved is True and edited.approved_by == ACTOR
    external_line = next(
        line for line in final_result.lines if line.check_type == "python_vs_accounts"
    )
    assert external_line.mapping_id == edited.mapping_id
    assert external_line.target_value == 100.0
    assert final_result.unmatched_reference_items == ["GL-001"]


def test_report_preparation_can_retry_without_replaying_gate_3(monkeypatch, tmp_path):
    _patch_agents(monkeypatch, _result())
    attempts = {"count": 0}

    def flaky_documentation(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary documentation provider failure")
        return _documentation_result()

    monkeypatch.setattr(orchestrator_module, "document_tabs", flaky_documentation)
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator)

    with pytest.raises(RuntimeError, match="temporary documentation provider failure"):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=[],
            internal_pct_threshold=DEFAULT_PCT,
            internal_absolute_threshold=DEFAULT_ABS,
            external_pct_threshold=DEFAULT_PCT,
            external_absolute_threshold=DEFAULT_ABS,
            internal_threshold_deviation_reason=None,
            external_threshold_deviation_reason=None,
            actor=ACTOR,
            use_ai_documentation=True,
            ai_transmission_acknowledged=True,
        )

    assert orchestrator.get_stage(report_id) == "post_gate3"
    gate3_before = [
        row
        for row in audit_log.get_rows(report_id)
        if json.loads(row["payload_json"]).get("gate") == 3
    ]
    assert len(gate3_before) == 1

    report = orchestrator.prepare_report(
        report_id,
        actor=ACTOR,
        use_ai_documentation=True,
        ai_transmission_acknowledged=True,
    )

    assert report.internal_verdict == "pass"
    assert orchestrator.get_stage(report_id) == "pre_approval_record"
    gate3_after = [
        row
        for row in audit_log.get_rows(report_id)
        if json.loads(row["payload_json"]).get("gate") == 3
    ]
    assert len(gate3_after) == 1


# --------------------------------------------------------------------------
# Recommendation 1 — the complete global chain is verified before every resume
# --------------------------------------------------------------------------


def _tamper_with_row(db_path: str, row_id: int) -> None:
    """Rewrite one log row's payload after removing the append-only trigger.

    This is the CRO threat model: someone with file access edits a recorded
    human decision. The snapshots table is deliberately left untouched, so the
    only thing that can catch it is a walk of the whole chain.
    """
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TRIGGER log_rows_no_update")
        connection.execute(
            "UPDATE log_rows SET payload_json = ? WHERE row_id = ?",
            ('{"action":"dismissed","context":{"code_version":"tampered"}}', row_id),
        )


def test_resume_refuses_when_an_earlier_log_row_was_tampered(monkeypatch, tmp_path):
    """The gap this control closes: the snapshot itself is pristine, and its
    commitment in the log still verifies on its own, but an earlier decision in
    the chain was rewritten. Checking only the snapshot cannot see that."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator)

    # Row 1 is the Gate 1 context decision — recorded long before the snapshot
    # that resume() will load, and untouched by Check A or Check B.
    _tamper_with_row(audit_log.db_path, 1)

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    with pytest.raises(ChainIntegrityError, match="1"):
        fresh.resume(report_id)



def test_refused_resume_leaves_no_state_in_memory(monkeypatch, tmp_path):
    """Criterion 5. A refusal that still populated _state would leave the
    pipeline running on history it just declared unreliable."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, _ = _start_preview(orchestrator)
    _tamper_with_row(audit_log.db_path, 1)

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    with pytest.raises(ChainIntegrityError):
        fresh.resume(report_id)
    assert fresh._state == {}

    # And the implicit route refuses too, rather than serving partial state.
    with pytest.raises(ChainIntegrityError):
        fresh.get_report(report_id)


def test_refusal_holds_when_recovery_is_reached_implicitly(monkeypatch, tmp_path):
    """Criterion 13. resume() is not the only door: _state_for() calls it
    whenever a staged method finds no process-local state."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, _ = _start_preview(orchestrator)
    _tamper_with_row(audit_log.db_path, 1)

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    with pytest.raises(ChainIntegrityError, match="does not verify"):
        fresh.get_reconciliation_preview(report_id)


def test_a_refused_resume_appends_nothing_and_repairs_nothing(monkeypatch, tmp_path):
    """Criteria 7 and 8. Corrupt evidence stays exactly as found — its current
    state IS the evidence of what happened to it."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, _ = _start_preview(orchestrator)
    _tamper_with_row(audit_log.db_path, 1)

    with sqlite3.connect(audit_log.db_path) as connection:
        before_log = connection.execute("SELECT * FROM log_rows ORDER BY row_id").fetchall()
        before_snaps = connection.execute("SELECT * FROM snapshots ORDER BY snapshot_id").fetchall()

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    with pytest.raises(ChainIntegrityError):
        fresh.resume(report_id)

    with sqlite3.connect(audit_log.db_path) as connection:
        after_log = connection.execute("SELECT * FROM log_rows ORDER BY row_id").fetchall()
        after_snaps = connection.execute("SELECT * FROM snapshots ORDER BY snapshot_id").fetchall()
    assert after_log == before_log
    assert after_snaps == before_snaps
    # Still broken at the same row: nothing was healed.
    assert audit_log.verify_chain() == (False, ["1"])


def test_successful_resume_records_one_chain_verification_per_process(monkeypatch, tmp_path):
    """Criterion 14, Option B. Recorded once per report per process — not once
    per staged call, or an implicit recovery route would flood the log."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, _ = _start_preview(orchestrator)

    def verification_rows():
        return [
            row
            for row in audit_log.get_rows(report_id)
            if row["event_type"] == "chain_verification"
        ]

    assert verification_rows() == []

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    fresh.resume(report_id)
    assert len(verification_rows()) == 1

    payload = json.loads(verification_rows()[0]["payload_json"])
    assert payload["outcome"] == "verified"
    assert payload["gate_name"] == "post_reconciliation"
    assert payload["verified_rows"] >= 1

    # Repeated recovery in the same process appends nothing further.
    fresh.resume(report_id)
    fresh.get_reconciliation_preview(report_id)
    assert len(verification_rows()) == 1

    # Recording a row must not itself break the chain it attests to.
    assert audit_log.verify_chain() == (True, [])


def test_the_verification_row_does_not_claim_more_than_it_can(monkeypatch, tmp_path):
    """Criterion 15. Rules 13/14/15: tamper-evident, never tamper-proof, and
    never 'validated' — a passing check means no disagreement was detected."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    report_id, _ = _start_preview(orchestrator)
    _tamper_with_row(audit_log.db_path, 1)

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    with pytest.raises(ChainIntegrityError) as caught:
        fresh.resume(report_id)

    message = str(caught.value).lower()
    assert "tamper-evident" in message
    for overstatement in ("tamper-proof", "immutable", "validated", "audit-ready"):
        assert overstatement not in message


def test_a_missing_snapshot_is_not_reported_as_corruption(monkeypatch, tmp_path):
    """Criterion 12. Absence of evidence and corruption of evidence are
    different findings and must not collapse into one signal."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    _start_preview(orchestrator)

    fresh = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        code_version="test-code-version",
    )
    with pytest.raises(PipelineStateError, match="no persisted state exists"):
        fresh.resume("RPT-NEVER-EXISTED")


# --------------------------------------------------------------------------
# Recommendation 2 — Gate 1 is bound to one specific workbook
# --------------------------------------------------------------------------

OTHER_BYTES = b"PK\x03\x04 a different workbook with the same name"


def _audit_rows(audit_log, report_id):
    return audit_log.get_rows(report_id)


def test_matching_workbook_runs_normally(monkeypatch, tmp_path):
    """No false positive: the correct workbook proceeds exactly as before."""
    _patch_agents(monkeypatch, _result())
    orchestrator, _, _ = _orchestrator(tmp_path)
    report_id, parsed, findings = orchestrator.run(
        WORKBOOK_BYTES,
        _file_context(),
        expected_workbook_hash=CONTEXT_HASH,
        context_confirmed=True,
        actor=ACTOR,
    )
    assert parsed.workbook_meta.workbook_hash == CONTEXT_HASH
    assert findings


def test_same_filename_different_contents_is_refused(monkeypatch, tmp_path):
    """The core scenario. The filename is identical; the bytes are not."""
    _patch_agents(monkeypatch, _result())
    orchestrator, _, _ = _orchestrator(tmp_path)
    with pytest.raises(WorkbookIdentityError, match="not the one that was confirmed"):
        orchestrator.run(
            OTHER_BYTES,
            _file_context(),
            expected_workbook_hash=CONTEXT_HASH,
            context_confirmed=True,
            actor=ACTOR,
        )


def test_omitting_the_expected_hash_is_a_type_error(monkeypatch, tmp_path):
    """Requirement 6. A caller cannot simply leave the control out: the
    argument is keyword-only with no default, so omission fails at call time."""
    _patch_agents(monkeypatch, _result())
    orchestrator, _, _ = _orchestrator(tmp_path)
    with pytest.raises(TypeError):
        orchestrator.run(
            WORKBOOK_BYTES,
            _file_context(),
            context_confirmed=True,
            actor=ACTOR,
        )


@pytest.mark.parametrize("bad", ["", "not-a-hash", "a" * 63, "A" * 64, None])
def test_malformed_expected_hash_is_refused(monkeypatch, tmp_path, bad):
    """A falsy or malformed value must not make the binding silently optional."""
    _patch_agents(monkeypatch, _result())
    orchestrator, _, _ = _orchestrator(tmp_path)
    with pytest.raises(WorkbookIdentityError):
        orchestrator.run(
            WORKBOOK_BYTES,
            _file_context(),
            expected_workbook_hash=bad,
            context_confirmed=True,
            actor=ACTOR,
        )


def test_direct_run_call_cannot_bypass_verification(monkeypatch, tmp_path):
    """context_confirmed=True is not enough on its own. A direct Python caller
    asserting the human confirmed still has to supply WHICH workbook."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    with pytest.raises(WorkbookIdentityError):
        orchestrator.run(
            OTHER_BYTES,
            _file_context(),
            expected_workbook_hash=CONTEXT_HASH,
            context_confirmed=True,
            actor=ACTOR,
        )
    assert orchestrator._state == {}


def test_mismatch_prevents_parser_invocation(monkeypatch, tmp_path):
    """Requirement 9: the refusal happens before parsing, not after."""
    _patch_agents(monkeypatch, _result())

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("parse_workbook was called despite a hash mismatch")

    monkeypatch.setattr(orchestrator_module, "parse_workbook", must_not_run)
    orchestrator, _, _ = _orchestrator(tmp_path)
    with pytest.raises(WorkbookIdentityError):
        orchestrator.run(
            OTHER_BYTES,
            _file_context(),
            expected_workbook_hash=CONTEXT_HASH,
            context_confirmed=True,
            actor=ACTOR,
        )


def test_mismatch_creates_no_snapshot_and_no_gate_decision(monkeypatch, tmp_path):
    """Requirements 9 and 10. A mismatch must not leave a context_confirmed
    decision behind — the human confirmed a different workbook."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, state_store = _orchestrator(tmp_path)
    with pytest.raises(WorkbookIdentityError):
        orchestrator.run(
            OTHER_BYTES,
            _file_context(),
            expected_workbook_hash=CONTEXT_HASH,
            context_confirmed=True,
            actor=ACTOR,
        )

    with sqlite3.connect(audit_log.db_path) as connection:
        snapshots = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        gate_rows = connection.execute(
            "SELECT COUNT(*) FROM log_rows WHERE event_type = 'gate_decision'"
        ).fetchone()[0]
    assert snapshots == 0
    assert gate_rows == 0


def test_mismatch_is_recorded_with_the_evidence_required(monkeypatch, tmp_path):
    """The mismatch row carries confirmed hash, observed hash, filename, actor,
    code version, timestamp and a blocked outcome. Its CONTEXT hash is the
    confirmed one: recording the observed hash there would assert an identity
    for a workbook nobody approved."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    with pytest.raises(WorkbookIdentityError):
        orchestrator.run(
            OTHER_BYTES,
            _file_context(),
            expected_workbook_hash=CONTEXT_HASH,
            context_confirmed=True,
            actor=ACTOR,
        )

    with sqlite3.connect(audit_log.db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM log_rows WHERE event_type = 'workbook_identity_mismatch'"
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["confirmed_workbook_hash"] == CONTEXT_HASH
    assert payload["observed_workbook_hash"] == sha256_bytes(OTHER_BYTES)
    assert payload["filename"] == "provisions.xlsx"
    assert payload["outcome"] == "blocked"
    assert row["actor"] == ACTOR
    assert row["timestamp"]
    assert payload["context"]["code_version"] == "test-code-version"
    # The context commits to the approved identity, not the rejected one.
    assert payload["context"]["workbook_hash"] == CONTEXT_HASH
    assert payload["context"]["workbook_hash"] != sha256_bytes(OTHER_BYTES)


def test_the_mismatch_row_keeps_the_chain_verifiable(monkeypatch, tmp_path):
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    with pytest.raises(WorkbookIdentityError):
        orchestrator.run(
            OTHER_BYTES,
            _file_context(),
            expected_workbook_hash=CONTEXT_HASH,
            context_confirmed=True,
            actor=ACTOR,
        )
    assert audit_log.verify_chain() == (True, [])


def test_later_events_reuse_the_confirmed_hash(monkeypatch, tmp_path):
    """Requirement 12: one identity, start to finish."""
    _patch_agents(monkeypatch, _result())
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    report_id, preview = _start_preview(orchestrator)
    hashes = {
        json.loads(row["payload_json"])["context"]["workbook_hash"]
        for row in audit_log.get_rows(report_id)
    }
    assert hashes == {CONTEXT_HASH}


# --- Work Package 1, Phase E: explicit AI documentation choice --------------


def _submit_gate3_with_ai_choice(orchestrator, report_id, preview, *, use_ai, acknowledged=None):
    return orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=use_ai,
        ai_transmission_acknowledged=use_ai if acknowledged is None else acknowledged,
    )


def test_declining_ai_documentation_makes_no_document_tabs_call(monkeypatch, tmp_path):
    """Items 5, 6, 10: decline -> zero calls, report reaches Gate 4 eligibility,
    and the audit trail records the decision but no llm_call event."""
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    _patch_agents(monkeypatch, _result())
    document_calls = []
    monkeypatch.setattr(
        orchestrator_module,
        "document_tabs",
        lambda *args, **kwargs: document_calls.append((args, kwargs)),
    )
    report_id, preview = _start_preview(orchestrator)

    _submit_gate3_with_ai_choice(orchestrator, report_id, preview, use_ai=False)

    assert document_calls == []
    report = orchestrator.get_report(report_id)
    assert report.ai_documentation_status == "declined"
    assert report.documentation == []
    assert report.llm_data_manifest == []

    event_types = [row["event_type"] for row in audit_log.get_rows(report_id)]
    assert "llm_use_decision" in event_types
    assert "llm_call" not in event_types


def test_using_ai_documentation_without_acknowledgment_raises_and_makes_no_call(
    monkeypatch, tmp_path
):
    """Item C/11-adjacent: the confirmation is required only for the AI path,
    and rejecting it happens before any Anthropic call or decision log entry."""
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    _patch_agents(monkeypatch, _result())
    document_calls = []
    monkeypatch.setattr(
        orchestrator_module,
        "document_tabs",
        lambda *args, **kwargs: document_calls.append((args, kwargs)),
    )
    report_id, preview = _start_preview(orchestrator)

    with pytest.raises(ValueError, match="synthetic/authorized"):
        _submit_gate3_with_ai_choice(orchestrator, report_id, preview, use_ai=True, acknowledged=False)

    assert document_calls == []
    event_types = [row["event_type"] for row in audit_log.get_rows(report_id)]
    assert "llm_use_decision" not in event_types


def test_declining_requires_no_transmission_acknowledgment(monkeypatch, tmp_path):
    """The confirmation checkbox is only required for the AI path."""
    orchestrator, _, _ = _orchestrator(tmp_path)
    _patch_agents(monkeypatch, _result())
    report_id, preview = _start_preview(orchestrator)

    internal, external, final_result = _submit_gate3_with_ai_choice(
        orchestrator, report_id, preview, use_ai=False, acknowledged=False
    )
    assert final_result.verdicts_are_final is True


def test_a_failed_decision_log_prevents_any_anthropic_call(monkeypatch, tmp_path):
    """Item 11: if llm_use_decision cannot be recorded, no call is made."""
    orchestrator, audit_log, _ = _orchestrator(tmp_path)
    _patch_agents(monkeypatch, _result())
    document_calls = []
    monkeypatch.setattr(
        orchestrator_module,
        "document_tabs",
        lambda *args, **kwargs: document_calls.append((args, kwargs)),
    )
    report_id, preview = _start_preview(orchestrator)

    original_log_event = audit_log.log_event

    def failing_log_event(*args, **kwargs):
        event_type = kwargs.get("event_type", args[1] if len(args) > 1 else None)
        if event_type == "llm_use_decision":
            raise RuntimeError("simulated audit-log write failure")
        return original_log_event(*args, **kwargs)

    monkeypatch.setattr(audit_log, "log_event", failing_log_event)

    with pytest.raises(RuntimeError, match="simulated audit-log write failure"):
        _submit_gate3_with_ai_choice(orchestrator, report_id, preview, use_ai=True)

    assert document_calls == []


def test_deterministic_verdicts_are_identical_whether_or_not_ai_documentation_is_used(
    monkeypatch, tmp_path
):
    """Item 8: the use/decline choice never changes a deterministic verdict."""

    def _run_once(use_ai: bool, subdir: str):
        report_dir = tmp_path / subdir
        report_dir.mkdir()
        orchestrator, _, _ = _orchestrator(report_dir)
        _patch_agents(monkeypatch, _result())
        report_id, preview = _start_preview(orchestrator)
        return _submit_gate3_with_ai_choice(orchestrator, report_id, preview, use_ai=use_ai)

    internal_with_ai, external_with_ai, result_with_ai = _run_once(True, "with_ai")
    internal_without_ai, external_without_ai, result_without_ai = _run_once(False, "without_ai")

    assert (internal_with_ai, external_with_ai) == (internal_without_ai, external_without_ai)
    assert [line.verdict for line in result_with_ai.lines] == [
        line.verdict for line in result_without_ai.lines
    ]


def test_declined_ai_documentation_still_reaches_gate_4_and_approval(monkeypatch, tmp_path):
    """Items 6, 7: the no-AI path completes through Gate 4 and is PDF-eligible."""
    orchestrator, _, _ = _orchestrator(tmp_path)
    _patch_agents(monkeypatch, _result())
    report_id, preview = _start_preview(orchestrator)

    _submit_gate3_with_ai_choice(orchestrator, report_id, preview, use_ai=False)
    report = orchestrator.submit_approval_record(report_id, ACTOR, "actuary")

    assert report.report_approval_name == ACTOR
    assert report.ai_documentation_status == "declined"


def test_llm_use_decision_precedes_llm_call_when_ai_is_used(monkeypatch, tmp_path):
    """Item 9: the decision event is written before agents/documentation.py's
    own llm_call event, using the REAL document_tabs (not the orchestrator-level
    fake), so the ordering guarantee is exercised end to end."""
    from types import SimpleNamespace

    from agents.documentation import document_tabs as real_document_tabs

    valid_json = json.dumps(
        {
            "method_summary": "Carries the provision output.",
            "assumptions": [],
            "data_sources": [],
            "anomalies_noted": [],
            "role_notes": "",
        }
    )

    class _FakeMessagesAPI:
        def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=valid_json)])

    class _FakeAnthropicClient:
        def __init__(self):
            self.messages = _FakeMessagesAPI()

    audit_log = AuditLog(str(tmp_path / "audit.db"))
    state_store = StateStore(audit_log.db_path, audit_log=audit_log)
    orchestrator = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        documentation_client=_FakeAnthropicClient(),
        code_version="test-code-version",
    )
    _patch_agents(monkeypatch, _result())
    monkeypatch.setattr(orchestrator_module, "document_tabs", real_document_tabs)
    monkeypatch.setattr("agents.documentation.time.sleep", lambda _: None)

    report_id, preview = _start_preview(orchestrator)
    _submit_gate3_with_ai_choice(orchestrator, report_id, preview, use_ai=True)

    event_types = [row["event_type"] for row in audit_log.get_rows(report_id)]
    assert event_types.index("llm_use_decision") < event_types.index("llm_call")

    report = orchestrator.get_report(report_id)
    assert report.ai_documentation_status == "generated"


def test_prepare_report_has_no_default_for_use_ai_documentation(monkeypatch, tmp_path):
    """Every call site must state the reviewer's explicit choice; there is no
    silent default that could let a call happen unconsidered."""
    orchestrator, _, _ = _orchestrator(tmp_path)
    _patch_agents(monkeypatch, _result())
    report_id, _ = _start_preview(orchestrator)

    with pytest.raises(TypeError):
        orchestrator.prepare_report(report_id, actor=ACTOR)
