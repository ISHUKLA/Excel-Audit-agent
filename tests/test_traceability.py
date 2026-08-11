"""Tests for core/traceability.py.

Two of these are regression tests for specific review findings and are labelled
as such. The identical-values test is the one that would start failing if
value-based lookup were ever reintroduced.
"""

from datetime import datetime, timezone

from core.models import (
    AccountMapping,
    AnomalyFinding,
    CellRecord,
    DerivationStep,
    ParsedFile,
    ReconciliationLine,
    ReconciliationResult,
    ReferenceFigureLine,
    ReferenceFigures,
    WorkbookMeta,
)
from core.traceability import build_traceability_index

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def a_parsed_file(cells=None, graph=None):
    return ParsedFile(
        tab_names=["Provisions"],
        cells=cells or {},
        named_ranges={},
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(calc_mode="automatic", workbook_hash="a" * 64),
        tab_dependency_graph={},
        cell_dependency_graph=graph or {},
        warnings=[],
    )


def a_step(ref, formula=None, depends_on=None, value=None, supported=True):
    return DerivationStep(
        cell_ref=ref,
        formula=formula,
        depends_on=depends_on or [],
        resolved_value=value,
        is_supported=supported,
    )


def a_line(**overrides):
    defaults = dict(
        check_type="excel_vs_python",
        label="Technical provisions",
        source_value=1000.0,
        target_value=1000.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        pct_threshold=0.01,
        absolute_threshold=100.0,
        threshold_is_default=True,
        completeness="complete",
        reconstruction_coverage_pct=100.0,
        unsupported_elements=[],
        derivation=[],
        mapping_id=None,
    )
    return ReconciliationLine(**{**defaults, **overrides})


def a_mapping(**overrides):
    defaults = dict(
        mapping_id="MAP-0001",
        python_output_cell_ref="Provisions!C5",
        reference_line_id="GL-001",
        mapping_type="one_to_one",
        suggested_by="fuzzy_match",
        suggested_confidence=97.0,
        approved_by="Isaac Shukla",
        approved_at=NOW,
        is_approved=True,
    )
    return AccountMapping(**{**defaults, **overrides})


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
        amount=1000.0,
        evidence_ref="GL_extract_Q4.csv row 3",
    )
    return ReferenceFigureLine(**{**defaults, **overrides})


def reference_figures(lines=None):
    return ReferenceFigures(
        source_label="Q4 trial balance",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        lines=lines if lines is not None else [a_reference_line()],
        uploaded_at=NOW,
    )


def a_result(**overrides):
    defaults = dict(
        lines=[],
        mappings=[],
        unmatched_reference_items=[],
        unmapped_python_outputs=[],
        verdicts_are_final=True,
    )
    return ReconciliationResult(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# TEST 1 — a real multi-step chain survives intact
# ---------------------------------------------------------------------------


def test_a_three_step_derivation_chain_is_carried_through_exactly():
    """Output -> intermediate -> raw input. Same cells, same order, nothing
    summarised, nothing dropped."""
    chain = [
        a_step("Provisions!C5", formula="=C4*2", depends_on=["Provisions!C4"], value=1000.0),
        a_step("Provisions!C4", formula="=C1+C2", depends_on=["Provisions!C1"], value=500.0),
        a_step("Provisions!C1", value=500.0),
    ]
    result = a_result(lines=[a_line(derivation=chain)])

    entries = build_traceability_index(a_parsed_file(), result, [])

    assert len(entries) == 1
    assert [step.cell_ref for step in entries[0].derivation] == [
        "Provisions!C5",
        "Provisions!C4",
        "Provisions!C1",
    ]
    assert entries[0].derivation == chain
    assert entries[0].trace_status == "traced"


def test_the_formulas_in_the_chain_are_preserved():
    chain = [
        a_step("Provisions!C5", formula="=C4*2", depends_on=["Provisions!C4"], value=1000.0),
        a_step("Provisions!C4", formula="=SUM(C1:C3)", value=500.0),
    ]
    entry = build_traceability_index(a_parsed_file(), a_result(lines=[a_line(derivation=chain)]), [])[0]
    assert [step.formula for step in entry.derivation] == ["=C4*2", "=SUM(C1:C3)"]


# ---------------------------------------------------------------------------
# TEST 2 — REGRESSION: identical values, different chains
# ---------------------------------------------------------------------------


def test_two_figures_with_identical_values_keep_their_own_chains():
    """REGRESSION TEST for the value-matching bug.

    Two entirely unrelated outputs, both exactly 1000.0. A lookup that finds a
    source cell by searching for a matching number cannot tell these apart and
    will attach one figure's chain to the other. This test fails immediately if
    value-based lookup is ever reintroduced."""
    first_chain = [
        a_step("Provisions!C5", formula="=C1*2", depends_on=["Provisions!C1"], value=1000.0),
        a_step("Provisions!C1", value=500.0),
    ]
    second_chain = [
        a_step("Reserves!Z9", formula="=Z1+Z2", depends_on=["Reserves!Z1"], value=1000.0),
        a_step("Reserves!Z1", value=400.0),
    ]

    result = a_result(
        lines=[
            a_line(label="Technical provisions", source_value=1000.0, target_value=1000.0, derivation=first_chain),
            a_line(label="Claims reserves", source_value=1000.0, target_value=1000.0, derivation=second_chain),
        ]
    )
    entries = build_traceability_index(a_parsed_file(), result, [])

    by_label = {entry.report_figure_label: entry for entry in entries}
    assert by_label["Technical provisions"].derivation[0].cell_ref == "Provisions!C5"
    assert by_label["Claims reserves"].derivation[0].cell_ref == "Reserves!Z9"
    # Same number on both, and no crossover.
    assert by_label["Technical provisions"].report_value == by_label["Claims reserves"].report_value
    assert by_label["Technical provisions"].derivation != by_label["Claims reserves"].derivation


def test_three_identical_values_stay_distinct():
    chains = [
        [a_step(f"Tab{i}!A1", formula="=B1*1", value=1000.0)] for i in range(1, 4)
    ]
    result = a_result(
        lines=[a_line(label=f"Figure {i}", target_value=1000.0, derivation=chain) for i, chain in enumerate(chains, 1)]
    )
    entries = build_traceability_index(a_parsed_file(), result, [])
    assert [e.derivation[0].cell_ref for e in entries] == ["Tab1!A1", "Tab2!A1", "Tab3!A1"]


# ---------------------------------------------------------------------------
# TEST 3 — REGRESSION: the accounting side gets its own provenance
# ---------------------------------------------------------------------------


def test_an_approved_mapping_produces_full_accounting_provenance():
    """REGRESSION TEST for the CFO-flagged gap: the Excel side had a derivation
    chain and the accounting figure beside it had nowhere to point."""
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-0001")],
        mappings=[a_mapping()],
    )
    entry = build_traceability_index(a_parsed_file(), result, [], reference_figures())[0]

    assert entry.trace_status == "traced"
    provenance = entry.accounting_provenance
    assert provenance is not None
    assert provenance.account_number == "4100"
    assert provenance.ledger_source == "SAP FI extract, Q4 close"
    assert provenance.approved_by == "Isaac Shukla"
    assert provenance.reference_line_id == "GL-001"
    assert provenance.mapping_id == "MAP-0001"
    assert provenance.entity == "Acme Life SA"
    assert provenance.period == "2025-Q4"
    assert provenance.currency == "EUR"
    assert provenance.evidence_ref == "GL_extract_Q4.csv row 3"


def test_provenance_comes_from_the_mapping_actually_used_not_the_first_available():
    """Two mappings in the result; the line names the second. The provenance
    must follow the line's mapping_id, not whatever came first."""
    other_reference = a_reference_line(line_id="GL-002", account_number="4110", ledger_source="Oracle GL")
    result = a_result(
        lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-0002")],
        mappings=[
            a_mapping(mapping_id="MAP-0001", reference_line_id="GL-001"),
            a_mapping(mapping_id="MAP-0002", reference_line_id="GL-002", approved_by="Someone Else"),
        ],
    )
    entry = build_traceability_index(
        a_parsed_file(), result, [], reference_figures([a_reference_line(), other_reference])
    )[0]

    assert entry.accounting_provenance.account_number == "4110"
    assert entry.accounting_provenance.ledger_source == "Oracle GL"
    assert entry.accounting_provenance.approved_by == "Someone Else"


# ---------------------------------------------------------------------------
# TEST 4 — all five trace_status values in one run
# ---------------------------------------------------------------------------


def test_all_five_trace_statuses_are_reachable_and_distinct():
    """No generic catch-all. Each gap reports the specific reason it is a gap,
    which is the whole reason for having reason codes instead of a boolean."""
    result = a_result(
        lines=[
            # traced — a complete Excel reconstruction
            a_line(label="Complete output", derivation=[a_step("Provisions!C5", value=1000.0)]),
            # partially_traced — an unsupported formula in the chain
            a_line(
                label="Partial output",
                completeness="partial",
                target_value=None,
                verdict="incomplete",
                derivation=[
                    a_step("Provisions!C9", formula="=VLOOKUP(A1,X,2,0)", supported=False),
                    a_step("Provisions!C8", formula="=C9*2"),
                ],
            ),
            # unmapped — an accounts line with no mapping proposal at all
            a_line(label="Unmapped output", check_type="python_vs_accounts", mapping_id=None),
            # mapping_pending_approval — proposed, not approved
            a_line(label="Pending output", check_type="python_vs_accounts", mapping_id="MAP-PEND"),
            # mapping_rejected — approved, but an aggregation this tool won't compute
            a_line(label="Aggregate output", check_type="python_vs_accounts", mapping_id="MAP-AGG"),
        ],
        mappings=[
            a_mapping(mapping_id="MAP-PEND", is_approved=False, approved_by=None, approved_at=None),
            a_mapping(mapping_id="MAP-AGG", mapping_type="many_to_one", approval_note="manual"),
        ],
    )
    entries = build_traceability_index(a_parsed_file(), result, [], reference_figures())
    statuses = {entry.report_figure_label: entry.trace_status for entry in entries}

    assert statuses["Complete output"] == "traced"
    assert statuses["Partial output"] == "partially_traced"
    assert statuses["Unmapped output"] == "unmapped"
    assert statuses["Pending output"] == "mapping_pending_approval"
    assert statuses["Aggregate output"] == "mapping_rejected"
    assert len(set(statuses.values())) == 5


def test_not_traceable_is_reachable_but_is_not_the_fallback_for_the_others():
    """The sixth status exists as a genuine last resort — a line naming a
    mapping that isn't in the result at all."""
    result = a_result(lines=[a_line(check_type="python_vs_accounts", mapping_id="MAP-GHOST")])
    entry = build_traceability_index(a_parsed_file(), result, [], reference_figures())[0]
    assert entry.trace_status == "not_traceable"


# ---------------------------------------------------------------------------
# partial traces are shown, not hidden
# ---------------------------------------------------------------------------


def test_a_partial_chain_keeps_its_unsupported_nodes_visible():
    """Showing how far the reconstruction got, and exactly where it stopped, is
    more useful than showing nothing."""
    chain = [
        a_step("Provisions!C5", formula="=C9*2"),
        a_step("Provisions!C9", formula="=VLOOKUP(A1,X,2,0)", supported=False),
    ]
    result = a_result(lines=[a_line(completeness="partial", target_value=None, derivation=chain)])
    entry = build_traceability_index(a_parsed_file(), result, [])[0]

    assert entry.trace_status == "partially_traced"
    assert len(entry.derivation) == 2
    assert [step.is_supported for step in entry.derivation] == [True, False]


# ---------------------------------------------------------------------------
# unmatched ledger lines and findings
# ---------------------------------------------------------------------------


def test_an_unmatched_reference_line_appears_in_the_index():
    """The ledger side of the gap, visible here and not only in a separate list."""
    result = a_result(unmatched_reference_items=["GL-001"])
    entries = build_traceability_index(a_parsed_file(), result, [], reference_figures())

    assert len(entries) == 1
    assert entries[0].trace_status == "unmapped"
    assert "GL-001" in entries[0].report_figure_label
    assert entries[0].report_value == 1000.0
    assert entries[0].derivation == []


def test_a_finding_traces_to_its_own_exact_cell():
    """Agent 2 already knows the cell. No matching required."""
    parsed = a_parsed_file(
        cells={
            "Provisions!C7": CellRecord(
                cell_ref="Provisions!C7",
                formula="=A1*1.75",
                cached_value=1.75,
                data_type="number",
                number_format="General",
                is_error=False,
                is_stale=False,
            )
        },
        graph={"Provisions!C7": ["Provisions!A1"]},
    )
    finding = AnomalyFinding(
        finding_id="F0001",
        severity="warning",
        tab="Provisions",
        cell_ref="C7",
        description="Hardcoded literal 1.75",
        raw_value="=A1*1.75",
    )
    entry = build_traceability_index(parsed, a_result(), [finding])[0]

    assert entry.trace_status == "traced"
    assert entry.derivation[0].cell_ref == "Provisions!C7"
    assert entry.derivation[0].formula == "=A1*1.75"
    assert entry.derivation[0].depends_on == ["Provisions!A1"]


def test_nothing_is_omitted_from_the_index():
    """Every figure the report depends on gets an entry, whatever its status."""
    result = a_result(
        lines=[
            a_line(label="Traced"),
            a_line(label="Unmapped", check_type="python_vs_accounts", mapping_id=None),
        ],
        unmatched_reference_items=["GL-001"],
    )
    finding = AnomalyFinding(
        finding_id="F0001",
        severity="info",
        tab="Provisions",
        cell_ref="C7",
        description="note",
        raw_value="x",
    )
    entries = build_traceability_index(a_parsed_file(), result, [finding], reference_figures())

    assert len(entries) == 4
    assert all(entry.trace_status for entry in entries)


def test_an_empty_run_produces_an_empty_index_not_an_error():
    assert build_traceability_index(a_parsed_file(), a_result(), []) == []


# ---------------------------------------------------------------------------
# no value matching anywhere
# ---------------------------------------------------------------------------


def test_the_module_contains_no_value_based_lookup():
    """A structural guard: the reintroduction of value matching would almost
    certainly involve searching cells for a number."""
    import inspect

    import core.traceability as module

    source = inspect.getsource(module)
    assert "cached_value ==" not in source
    assert "report_value ==" not in source
    for smell in ("find_cell_by_value", "search_for_value", "matching_value"):
        assert smell not in source
