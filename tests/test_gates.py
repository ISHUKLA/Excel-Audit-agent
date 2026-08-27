"""Tests for core/gates.py — the four human gates.

Several of these are regression tests for specific review findings, and are
labelled as such. They are written so they cannot be satisfied by accident: the
mapping-approval test uses a 99%-confidence match, and the completeness test
uses a reconciliation where everything that IS mapped reconciles perfectly.
"""

from datetime import datetime, timezone

import pytest

from core.audit_log import AuditLog
from core.gates import (
    GateBlockedError,
    approval_record_gate,
    context_gate,
    findings_review_gate,
    preview_reconciliation,
    reconciliation_gate,
)
from core.models import (
    AccountMapping,
    AnomalyFinding,
    AuditReport,
    CellRecord,
    FileContext,
    ParsedFile,
    ReconciliationLine,
    ReconciliationResult,
    ReferenceFigureLine,
    ReferenceFigures,
    WorkbookMeta,
)

NOW = datetime(2026, 8, 10, 9, 30, tzinfo=timezone.utc)
CONTEXT = {"workbook_hash": "a" * 64, "code_version": "0.1.0"}
DEFAULT_PCT = 0.01
DEFAULT_ABS = 100.0
APPROVER = "Isaac Shukla"
REGISTRY = [{"name": "Isaac Shukla", "role": "actuary", "registered_at": "2026-08-10"}]


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(str(tmp_path / "audit.db"))


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def a_file_context(**overrides):
    defaults = dict(
        filename="provisions.xlsx",
        description="Q4 provisions",
        user_role="actuary",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        confirmed_workbook_hash="a" * 64,
        uploaded_at=NOW,
    )
    return FileContext(**{**defaults, **overrides})


def reference_figures(**overrides):
    defaults = dict(
        source_label="Q4 trial balance",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        lines=[
            ReferenceFigureLine(
                line_id="GL-001",
                label="Technical provisions",
                entity="Acme Life SA",
                period="2025-Q4",
                currency="EUR",
                ledger_source="SAP FI",
                debit_credit="credit",
                amount=1250.0,
            )
        ],
        uploaded_at=NOW,
    )
    return ReferenceFigures(**{**defaults, **overrides})


def a_parsed_file():
    return ParsedFile(
        tab_names=["Provisions"],
        cells={
            "Provisions!C5": CellRecord(
                cell_ref="Provisions!C5",
                formula="=SUM(C1:C4)",
                cached_value=1250.0,
                data_type="number",
                number_format="General",
                is_error=False,
                is_stale=False,
                calculation_freshness="fresh",
            )
        },
        named_ranges={},
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(calc_mode="automatic", workbook_hash="a" * 64),
        tab_dependency_graph={},
        cell_dependency_graph={},
        warnings=[],
    )


def a_finding(**overrides):
    defaults = dict(
        finding_id="F0001",
        severity="warning",
        tab="Provisions",
        cell_ref="C5",
        description="Hardcoded literal",
        raw_value="=A1*1.75",
        human_decision="confirmed",
        human_reason="real issue",
        decided_by=APPROVER,
        decided_at=NOW,
    )
    return AnomalyFinding(**{**defaults, **overrides})


def a_line(**overrides):
    defaults = dict(
        check_type="excel_vs_python",
        label="Technical provisions",
        source_value=1250.0,
        target_value=1250.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        pct_threshold=DEFAULT_PCT,
        absolute_threshold=DEFAULT_ABS,
        threshold_is_default=True,
        completeness="complete",
        reconstruction_coverage_pct=100.0,
        unsupported_elements=[],
        derivation=[],
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
        approved_by=APPROVER,
        approved_at=NOW,
        is_approved=True,
    )
    return AccountMapping(**{**defaults, **overrides})


def a_result(**overrides):
    defaults = dict(
        lines=[a_line()],
        mappings=[],
        unmatched_reference_items=[],
        unmapped_python_outputs=[],
        verdicts_are_final=False,
    )
    return ReconciliationResult(**{**defaults, **overrides})


def an_audit_report(**overrides):
    defaults = dict(
        file_context=a_file_context(),
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
        independence_disclosure="",
        generated_at=NOW,
        report_id="RPT-001",
        audit_log_verification_note="Re-verify with AuditLog.verify_chain().",
    )
    return AuditReport(**{**defaults, **overrides})


def run_gate_3(audit_log, result, **overrides):
    shared_pct = overrides.pop("pct_threshold", None)
    shared_absolute = overrides.pop("absolute_threshold", None)
    shared_reason = overrides.pop("threshold_deviation_reason", None)
    kwargs = dict(
        result=result,
        report_id="RPT-001",
        internal_pct_threshold=DEFAULT_PCT if shared_pct is None else shared_pct,
        internal_absolute_threshold=DEFAULT_ABS if shared_absolute is None else shared_absolute,
        external_pct_threshold=DEFAULT_PCT if shared_pct is None else shared_pct,
        external_absolute_threshold=DEFAULT_ABS if shared_absolute is None else shared_absolute,
        default_pct_threshold=DEFAULT_PCT,
        default_absolute_threshold=DEFAULT_ABS,
        internal_threshold_deviation_reason=shared_reason,
        external_threshold_deviation_reason=shared_reason,
        acknowledge_incomplete=False,
        context_match_verdict="not_checked",
        actor=APPROVER,
        audit_log=audit_log,
        context=CONTEXT,
    )
    return reconciliation_gate(**{**kwargs, **overrides})


# ---------------------------------------------------------------------------
# Gate 1
# ---------------------------------------------------------------------------


def test_gate_1_blocks_when_context_is_not_confirmed(audit_log):
    with pytest.raises(GateBlockedError, match="has not been confirmed"):
        context_gate(a_file_context(), None, False, "RPT-001", APPROVER, audit_log, CONTEXT)
    assert audit_log.get_rows("RPT-001") == []


def test_gate_1_passes_and_logs_when_confirmed(audit_log):
    ok, verdict = context_gate(a_file_context(), None, True, "RPT-001", APPROVER, audit_log, CONTEXT)

    assert ok is True
    assert verdict == "not_checked"
    rows = audit_log.get_rows("RPT-001")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "gate_decision"


def test_gate_1_matching_context_produces_match(audit_log):
    _, verdict = context_gate(
        a_file_context(), reference_figures(), True, "RPT-001", APPROVER, audit_log, CONTEXT
    )
    assert verdict == "match"


def test_gate_1_logs_the_mathematical_control_total_check(audit_log):
    import json

    context_gate(
        a_file_context(),
        reference_figures(control_total=-1250.0),
        True,
        "RPT-001",
        APPROVER,
        audit_log,
        CONTEXT,
    )

    payload = json.loads(audit_log.get_rows("RPT-001")[0]["payload_json"])
    assert payload["control_total_check"] == {
        "declared_total": -1250.0,
        "difference": 0.0,
        "signed_line_total": -1250.0,
        "status": "match",
    }


def test_gate_1_period_mismatch_produces_mismatch(audit_log):
    """A Q4 workbook reconciled against a Q3 trial balance."""
    _, verdict = context_gate(
        a_file_context(period="2025-Q4"),
        reference_figures(period="2025-Q3"),
        True,
        "RPT-001",
        APPROVER,
        audit_log,
        CONTEXT,
    )
    assert verdict == "mismatch"


def test_gate_1_entity_and_currency_mismatches_also_produce_mismatch(audit_log):
    for field, value in (("entity", "Other Life SA"), ("currency", "USD")):
        _, verdict = context_gate(
            a_file_context(),
            reference_figures(**{field: value}),
            True,
            "RPT-001",
            APPROVER,
            audit_log,
            CONTEXT,
        )
        assert verdict == "mismatch", field


def test_gate_1_comparison_is_case_and_whitespace_insensitive(audit_log):
    _, verdict = context_gate(
        a_file_context(entity="acme life sa "),
        reference_figures(entity="Acme Life SA"),
        True,
        "RPT-001",
        APPROVER,
        audit_log,
        CONTEXT,
    )
    assert verdict == "match"


def test_gate_1_basis_difference_warns_but_does_not_force_mismatch(audit_log):
    """Bases can legitimately differ by design. Whether this one is acceptable
    is a CFO judgment, so it is surfaced rather than enforced."""
    _, verdict = context_gate(
        a_file_context(basis="IFRS 17"),
        reference_figures(basis="local GAAP"),
        True,
        "RPT-001",
        APPROVER,
        audit_log,
        CONTEXT,
    )
    assert verdict == "match"

    import json

    payload = json.loads(audit_log.get_rows("RPT-001")[0]["payload_json"])
    assert "basis differs" in payload["basis_warning"]


def test_gate_1_mismatch_does_not_block_the_pipeline(audit_log):
    """The Excel-side work is still worth doing when the accounting comparison
    cannot be trusted."""
    ok, verdict = context_gate(
        a_file_context(period="2025-Q4"),
        reference_figures(period="2025-Q3"),
        True,
        "RPT-001",
        APPROVER,
        audit_log,
        CONTEXT,
    )
    assert ok is True and verdict == "mismatch"


# ---------------------------------------------------------------------------
# Gate 2
# ---------------------------------------------------------------------------


def test_gate_2_blocks_on_an_undecided_finding(audit_log):
    findings = [a_finding(), a_finding(finding_id="F0002", human_decision=None)]
    with pytest.raises(GateBlockedError, match="F0002"):
        findings_review_gate(
            findings, a_parsed_file(), ["Provisions!C5"], "RPT-001", APPROVER, audit_log, CONTEXT
        )


def test_gate_2_accepts_all_three_dispositions(audit_log):
    """Confirmed, overridden and dismissed are equally valid. Only None blocks."""
    findings = [
        a_finding(finding_id="F0001", human_decision="confirmed"),
        a_finding(finding_id="F0002", human_decision="overridden"),
        a_finding(finding_id="F0003", human_decision="dismissed"),
    ]
    reviewed, outputs = findings_review_gate(
        findings, a_parsed_file(), ["Provisions!C5"], "RPT-001", APPROVER, audit_log, CONTEXT
    )
    assert len(reviewed) == 3
    assert outputs == ["Provisions!C5"]


def test_gate_2_blocks_when_no_outputs_are_designated(audit_log):
    with pytest.raises(GateBlockedError, match="no authoritative outputs"):
        findings_review_gate([], a_parsed_file(), [], "RPT-001", APPROVER, audit_log, CONTEXT)


def test_gate_2_blocks_and_names_outputs_that_do_not_exist(audit_log):
    with pytest.raises(GateBlockedError, match="Provisions!Z99"):
        findings_review_gate(
            [],
            a_parsed_file(),
            ["Provisions!C5", "Provisions!Z99"],
            "RPT-001",
            APPROVER,
            audit_log,
            CONTEXT,
        )


def test_gate_2_logs_every_disposition_not_just_confirmations(audit_log):
    import json

    findings = [
        a_finding(finding_id="F0001", human_decision="confirmed"),
        a_finding(finding_id="F0002", human_decision="dismissed", human_reason="false positive"),
    ]
    findings_review_gate(
        findings, a_parsed_file(), ["Provisions!C5"], "RPT-001", APPROVER, audit_log, CONTEXT
    )

    payload = json.loads(audit_log.get_rows("RPT-001")[0]["payload_json"])
    assert [d["disposition"] for d in payload["dispositions"]] == ["confirmed", "dismissed"]
    assert payload["authoritative_outputs"] == ["Provisions!C5"]


def test_gate_2_does_not_log_when_it_blocks(audit_log):
    with pytest.raises(GateBlockedError):
        findings_review_gate(
            [a_finding(human_decision=None)],
            a_parsed_file(),
            ["Provisions!C5"],
            "RPT-001",
            APPROVER,
            audit_log,
            CONTEXT,
        )
    assert audit_log.get_rows("RPT-001") == []


# ---------------------------------------------------------------------------
# Gate 3 — the clean path
# ---------------------------------------------------------------------------


def test_gate_3_passes_a_clean_internal_reconciliation(audit_log):
    internal, external, result = run_gate_3(audit_log, a_result())

    assert internal == "pass"
    assert external == "not_performed"
    assert result.verdicts_are_final is True


# ---------------------------------------------------------------------------
# Work Package 2 — the freshness cap survives every Gate 3 recomputation
# ---------------------------------------------------------------------------


def _stale_line(**overrides):
    return a_line(
        calculation_evidence_status="stale",
        stale_cell_refs=["Provisions!C5"],
        **overrides,
    )


def test_gate_3_preview_preserves_the_freshness_cap():
    result = a_result(lines=[_stale_line()])
    internal, external, preview = preview_reconciliation(
        result,
        internal_pct_threshold=1.0,
        internal_absolute_threshold=1_000_000.0,
        external_pct_threshold=1.0,
        external_absolute_threshold=1_000_000.0,
        default_pct_threshold=DEFAULT_PCT,
        default_absolute_threshold=DEFAULT_ABS,
        context_match_verdict="not_checked",
    )
    assert internal == "incomplete"
    assert preview.lines[0].verdict == "incomplete"
    assert preview.verdicts_are_final is False


def test_gate_3_final_recomputation_preserves_the_freshness_cap(audit_log):
    result = a_result(lines=[_stale_line()])
    internal, external, final_result = run_gate_3(
        audit_log,
        result,
        pct_threshold=1.0,
        absolute_threshold=1_000_000.0,
        threshold_deviation_reason="testing that generous thresholds do not override staleness",
        acknowledge_incomplete=True,
    )
    assert internal == "incomplete"
    assert final_result.lines[0].verdict == "incomplete"
    assert final_result.verdicts_are_final is True


def test_gate_3_acknowledgement_permits_continuation_but_does_not_change_the_verdict(audit_log):
    result = a_result(lines=[_stale_line()])
    with pytest.raises(GateBlockedError, match="explicitly acknowledged"):
        run_gate_3(audit_log, result)

    internal, _, final_result = run_gate_3(audit_log, result, acknowledge_incomplete=True)
    assert internal == "incomplete"
    assert final_result.lines[0].verdict == "incomplete"
    assert final_result.lines[0].calculation_evidence_status == "stale"


def test_gate_3_blocks_on_a_blocking_line(audit_log):
    result = a_result(lines=[a_line(delta=500.0, delta_pct=0.4, verdict="pass")])
    with pytest.raises(GateBlockedError, match="internal_verdict=block"):
        run_gate_3(audit_log, result)


def test_gate_3_blockers_cannot_be_bypassed_by_acknowledging(audit_log):
    result = a_result(lines=[a_line(delta=500.0, delta_pct=0.4)])
    with pytest.raises(GateBlockedError):
        run_gate_3(audit_log, result, acknowledge_incomplete=True)


def test_gate_3_incomplete_requires_explicit_acknowledgement(audit_log):
    result = a_result(lines=[a_line(completeness="partial", target_value=None, delta=None, delta_pct=None)])
    with pytest.raises(GateBlockedError, match="explicitly acknowledged"):
        run_gate_3(audit_log, result)

    internal, _, _ = run_gate_3(audit_log, a_result(
        lines=[a_line(completeness="partial", target_value=None, delta=None, delta_pct=None)]
    ), acknowledge_incomplete=True)
    assert internal == "incomplete"


def test_gate_3_an_empty_internal_set_is_incomplete_not_pass(audit_log):
    """Nothing reconstructed is not a clean reconciliation."""
    internal, _, _ = run_gate_3(audit_log, a_result(lines=[]), acknowledge_incomplete=True)
    assert internal == "incomplete"


# ---------------------------------------------------------------------------
# Gate 3 — REGRESSION: verdict recompute
# ---------------------------------------------------------------------------


def test_gate_3_recomputes_a_provisional_block_against_looser_approved_thresholds(audit_log):
    """REGRESSION TEST for the stale-provisional-verdict fix.

    Agent 3 computed "block" against the 1% default. The human approves a 10%
    threshold with a stated reason. If the recompute doesn't happen, the line
    stays "block" and Gate 3 raises — so this test fails loudly if the recompute
    step is ever removed."""
    line = a_line(delta=5.0, delta_pct=0.02, verdict="block")
    result = a_result(lines=[line])

    internal, _, returned = run_gate_3(
        audit_log,
        result,
        pct_threshold=0.10,
        threshold_deviation_reason="Materiality agreed with the CFO for Q4 close",
    )

    assert returned.lines[0].verdict in ("pass", "warn")
    assert returned.lines[0].verdict != "block"
    assert internal in ("pass", "warn")
    assert returned.verdicts_are_final is True


def test_gate_3_recompute_records_the_thresholds_actually_used(audit_log):
    result = a_result(lines=[a_line()])
    _, _, returned = run_gate_3(
        audit_log,
        result,
        pct_threshold=0.05,
        absolute_threshold=250.0,
        threshold_deviation_reason="agreed with CFO",
    )

    assert returned.lines[0].pct_threshold == 0.05
    assert returned.lines[0].absolute_threshold == 250.0
    assert returned.lines[0].threshold_is_default is False


def test_gate_3_recompute_can_make_a_provisional_pass_worse(audit_log):
    """The recompute is not a one-way loosening — a tighter threshold must be
    able to turn a provisional pass into a block."""
    result = a_result(lines=[a_line(delta=5.0, delta_pct=0.002, verdict="pass")])
    with pytest.raises(GateBlockedError):
        run_gate_3(
            audit_log,
            result,
            pct_threshold=0.0001,
            threshold_deviation_reason="tightened for year-end",
        )


# ---------------------------------------------------------------------------
# Gate 3 — REGRESSION: threshold deviation visibility
# ---------------------------------------------------------------------------


def test_gate_3_blocks_a_threshold_change_with_no_stated_reason(audit_log):
    with pytest.raises(GateBlockedError, match="without a stated reason"):
        run_gate_3(audit_log, a_result(), pct_threshold=0.10)


def test_gate_3_blocks_a_whitespace_only_deviation_reason(audit_log):
    with pytest.raises(GateBlockedError):
        run_gate_3(audit_log, a_result(), pct_threshold=0.10, threshold_deviation_reason="   ")


def test_gate_3_logs_a_deviation_as_its_own_flagged_field(audit_log):
    import json

    run_gate_3(
        audit_log,
        a_result(),
        absolute_threshold=500.0,
        threshold_deviation_reason="agreed with the CFO",
    )
    payload = json.loads(audit_log.get_rows("RPT-001")[-1]["payload_json"])

    assert payload["threshold_deviation"]["internal"]["deviated"] is True
    assert payload["threshold_deviation"]["internal"]["reason"] == "agreed with the CFO"
    assert (
        payload["threshold_deviation"]["internal"]["default_absolute_threshold"]
        == DEFAULT_ABS
    )


def test_gate_3_applies_internal_and_external_thresholds_independently():
    result = a_result(
        lines=[
            a_line(delta=5.0, delta_pct=0.005),
            a_line(
                check_type="python_vs_accounts",
                mapping_id="MAP-001",
                delta=5.0,
                delta_pct=0.005,
            ),
        ],
        mappings=[a_mapping()],
    )

    internal, external, preview = preview_reconciliation(
        result,
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABS,
        external_pct_threshold=0.10,
        external_absolute_threshold=DEFAULT_ABS,
        default_pct_threshold=DEFAULT_PCT,
        default_absolute_threshold=DEFAULT_ABS,
        context_match_verdict="match",
    )

    assert (internal, external) == ("warn", "pass")
    assert preview.lines[0].pct_threshold == DEFAULT_PCT
    assert preview.lines[1].pct_threshold == 0.10
    assert result.lines[1].pct_threshold == DEFAULT_PCT


def test_gate_3_requires_a_reason_for_external_threshold_changes(audit_log):
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-001")],
        mappings=[a_mapping()],
    )
    with pytest.raises(GateBlockedError, match="external thresholds"):
        run_gate_3(
            audit_log,
            result,
            external_pct_threshold=0.10,
            external_threshold_deviation_reason=None,
        )


# ---------------------------------------------------------------------------
# Gate 3 — REGRESSION: mapping approval
# ---------------------------------------------------------------------------


def test_gate_3_blocks_an_unapproved_mapping_at_99_percent_confidence(audit_log):
    """A confident string match is a good proposal. It is never an accounting
    decision."""
    mapping = a_mapping(
        suggested_confidence=99.0, is_approved=False, approved_by=None, approved_at=None
    )
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-001")],
        mappings=[mapping],
    )

    with pytest.raises(GateBlockedError, match="unapproved account mappings"):
        run_gate_3(audit_log, result)


def test_gate_3_accepts_the_same_mapping_once_a_human_approves_it(audit_log):
    result = a_result(
        lines=[a_line(), a_line(check_type="python_vs_accounts", mapping_id="MAP-001")],
        mappings=[a_mapping(is_approved=True)],
    )
    _, external, _ = run_gate_3(audit_log, result)
    assert external == "pass"


def test_gate_3_cannot_be_satisfied_by_an_empty_mappings_list(audit_log):
    """The check is against mappings actually referenced by accounts-side lines,
    so "no mappings at all" does not trivially pass it."""
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-001")],
        mappings=[],
    )
    with pytest.raises(GateBlockedError, match="no resolvable mapping"):
        run_gate_3(audit_log, result)


def test_gate_3_blocks_an_accounts_line_with_no_mapping_id_at_all(audit_log):
    result = a_result(lines=[a_line(check_type="python_vs_accounts", mapping_id=None)])
    with pytest.raises(GateBlockedError, match="no mapping_id"):
        run_gate_3(audit_log, result)


# ---------------------------------------------------------------------------
# Gate 3 — REGRESSION: bidirectional completeness
# ---------------------------------------------------------------------------


def test_unmapped_python_outputs_alone_caps_external_at_incomplete(audit_log):
    """Everything mapped reconciles exactly, and unmatched_reference_items is
    empty. One designated output has no counterpart — that alone is enough."""
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-001", delta=0.0, delta_pct=0.0)],
        mappings=[a_mapping()],
        unmatched_reference_items=[],
        unmapped_python_outputs=["Provisions!C6"],
    )
    _, external, _ = run_gate_3(audit_log, result, acknowledge_incomplete=True)

    assert external == "incomplete"
    assert external != "pass"


def test_unmatched_reference_items_alone_caps_external_at_incomplete(audit_log):
    """The other direction, checked independently."""
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-001", delta=0.0, delta_pct=0.0)],
        mappings=[a_mapping()],
        unmatched_reference_items=["GL-002"],
        unmapped_python_outputs=[],
    )
    _, external, _ = run_gate_3(audit_log, result, acknowledge_incomplete=True)
    assert external == "incomplete"


def test_population_gap_outranks_a_numeric_warning(audit_log):
    """A warning describes compared numbers; incomplete describes missing scope."""
    warning_line = a_line(
        check_type="python_vs_accounts",
        mapping_id="MAP-001",
        delta=5.0,
        delta_pct=0.005,
        verdict="warn",
    )
    result = a_result(
        lines=[warning_line],
        mappings=[a_mapping(is_approved=True, approved_by="Isaac Shukla")],
        unmatched_reference_items=["GL-002"],
    )

    _, external, _ = run_gate_3(
        audit_log,
        result,
        acknowledge_incomplete=True,
    )

    assert external == "incomplete"


def test_a_fully_mapped_population_can_reach_pass(audit_log):
    """The control test: without this, "incomplete" might just be what the gate
    always returns."""
    result = a_result(
        lines=[
            a_line(),
            a_line(check_type="python_vs_accounts", mapping_id="MAP-001", delta=0.0, delta_pct=0.0),
        ],
        mappings=[a_mapping()],
    )
    internal, external, _ = run_gate_3(audit_log, result)
    assert internal == "pass"
    assert external == "pass"


# ---------------------------------------------------------------------------
# Gate 3 — REGRESSION: context mismatch
# ---------------------------------------------------------------------------


def test_a_context_mismatch_forces_external_to_block_however_well_figures_agree(audit_log):
    """Every figure agrees to the cent, and it means nothing: these are
    different entities, or different periods, or different currencies."""
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-001", delta=0.0, delta_pct=0.0)],
        mappings=[a_mapping()],
    )
    with pytest.raises(GateBlockedError, match="external_verdict=block"):
        run_gate_3(audit_log, result, context_match_verdict="mismatch")


def test_a_context_mismatch_does_not_corrupt_the_internal_verdict(audit_log):
    """The Excel-side reconstruction is unaffected by an accounting context
    problem, and must not be reported as though it were."""
    import json

    result = a_result(
        lines=[
            a_line(),
            a_line(check_type="python_vs_accounts", mapping_id="MAP-001", delta=0.0, delta_pct=0.0),
        ],
        mappings=[a_mapping()],
    )
    with pytest.raises(GateBlockedError):
        run_gate_3(audit_log, result, context_match_verdict="mismatch")

    payload = json.loads(audit_log.get_rows("RPT-001")[-1]["payload_json"])
    assert payload["internal_verdict"] == "pass"
    assert payload["external_verdict"] == "block"


def test_a_control_total_mismatch_blocks_external_reconciliation(audit_log):
    result = a_result(
        lines=[
            a_line(),
            a_line(check_type="python_vs_accounts", mapping_id="MAP-001"),
        ],
        mappings=[a_mapping()],
    )

    with pytest.raises(GateBlockedError, match="external_verdict=block"):
        run_gate_3(audit_log, result, control_total_verdict="mismatch")


# ---------------------------------------------------------------------------
# Gate 4 — named approval record
# ---------------------------------------------------------------------------


def test_gate_4_blocks_on_an_empty_name(audit_log):
    with pytest.raises(GateBlockedError, match="requires a name"):
        approval_record_gate(an_audit_report(), "", "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT)


def test_gate_4_blocks_on_a_whitespace_name(audit_log):
    with pytest.raises(GateBlockedError):
        approval_record_gate(an_audit_report(), "   ", "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT)


def test_gate_4_records_name_role_and_timestamp(audit_log):
    report = approval_record_gate(
        an_audit_report(), APPROVER, "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT
    )

    assert report.report_approval_name == APPROVER
    assert report.report_approval_role == "Senior Actuary"
    assert report.report_approval_at is not None


def test_gate_4_logs_a_report_approved_event(audit_log):
    approval_record_gate(
        an_audit_report(), APPROVER, "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT
    )
    events = [r["event_type"] for r in audit_log.get_rows("RPT-001")]
    assert "report_approved" in events
    assert "report_signed" not in events


def test_an_unregistered_name_does_not_block_but_is_recorded(audit_log):
    """A registry check is a spell-checker, not authentication. Blocking on it
    would present it as something it isn't — but the discrepancy still belongs
    in the trail."""
    import json

    report = approval_record_gate(
        an_audit_report(), "Someone Unknown", "Controller", REGISTRY, "Someone Unknown", audit_log, CONTEXT
    )

    assert report.report_approval_name == "Someone Unknown"

    payloads = [json.loads(r["payload_json"]) for r in audit_log.get_rows("RPT-001")]
    actions = [p.get("action") for p in payloads]
    assert "approval_record_unregistered_name" in actions
    assert any(p.get("name_in_registry") is False for p in payloads)


def test_a_registered_name_produces_no_unregistered_event(audit_log):
    import json

    approval_record_gate(
        an_audit_report(), "isaac shukla", "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT
    )
    payloads = [json.loads(r["payload_json"]) for r in audit_log.get_rows("RPT-001")]
    assert "approval_record_unregistered_name" not in [p.get("action") for p in payloads]


def test_independence_disclosure_in_the_solo_case(audit_log):
    """The expected case for this build: one person through every gate. The
    disclosure must say so and must not imply otherwise."""
    context_gate(a_file_context(), None, True, "RPT-001", APPROVER, audit_log, CONTEXT)
    findings_review_gate([], a_parsed_file(), ["Provisions!C5"], "RPT-001", APPROVER, audit_log, CONTEXT)

    report = approval_record_gate(
        an_audit_report(), APPROVER, "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT
    )

    assert "same individual" in report.independence_disclosure
    assert "No independent review was performed." in report.independence_disclosure


def test_independence_disclosure_names_both_when_they_differ(audit_log):
    findings_review_gate(
        [], a_parsed_file(), ["Provisions!C5"], "RPT-001", "Preparer Person", audit_log, CONTEXT
    )

    report = approval_record_gate(
        an_audit_report(), "Approver Person", "Controller", REGISTRY, "Approver Person", audit_log, CONTEXT
    )

    assert "prepared by Preparer Person" in report.independence_disclosure
    assert "approved by Approver Person" in report.independence_disclosure


def test_independence_disclosure_is_never_blank(audit_log):
    report = approval_record_gate(
        an_audit_report(), APPROVER, "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT
    )
    assert report.independence_disclosure.strip() != ""
    assert "No independent review was performed." in report.independence_disclosure


def test_approval_and_independence_can_coexist(audit_log):
    """Someone approved it, and nobody independent reviewed it. Both true."""
    context_gate(a_file_context(), None, True, "RPT-001", APPROVER, audit_log, CONTEXT)
    report = approval_record_gate(
        an_audit_report(), APPROVER, "Senior Actuary", REGISTRY, APPROVER, audit_log, CONTEXT
    )
    assert report.report_approval_name is not None
    assert "No independent review" in report.independence_disclosure
