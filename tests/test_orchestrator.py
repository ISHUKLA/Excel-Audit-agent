"""Step 9 integration tests for staged orchestration and durable recovery.

Agents are mocked to keep the tests deterministic and prevent live LLM calls.
Human gates, the audit log, and the snapshot store remain real; one test wraps
all four gates with spies to prove orchestration supplies their evidence context.
"""

import json
from datetime import datetime, timezone

import pytest

import agents.orchestrator as orchestrator_module
from agents.orchestrator import (
    RECONCILIATION_PREVIEW_LABEL,
    Orchestrator,
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
from core.state_store import StateStore

ACTOR = "Isaac Shukla"
CONTEXT_HASH = "b" * 64
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
        debit_credit="credit",
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
        "unused.xlsx",
        _file_context(),
        reference_figures,
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
    assert gate_contexts["context_gate"]["workbook_hash"] == NOT_YET_PARSED
    assert all(context["code_version"] for context in gate_contexts.values())
    assert all(
        context["workbook_hash"] for name, context in gate_contexts.items() if name != "context_gate"
    )
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
        "unused.xlsx",
        _file_context(),
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
        debit_credit="credit",
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
        )

    assert orchestrator.get_stage(report_id) == "post_gate3"
    gate3_before = [
        row
        for row in audit_log.get_rows(report_id)
        if json.loads(row["payload_json"]).get("gate") == 3
    ]
    assert len(gate3_before) == 1

    report = orchestrator.prepare_report(report_id, actor=ACTOR)

    assert report.internal_verdict == "pass"
    assert orchestrator.get_stage(report_id) == "pre_approval_record"
    gate3_after = [
        row
        for row in audit_log.get_rows(report_id)
        if json.loads(row["payload_json"]).get("gate") == 3
    ]
    assert len(gate3_after) == 1
