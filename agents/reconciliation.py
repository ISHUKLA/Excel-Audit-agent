"""Agent 3 — two independent reconciliation passes.

Pass 1 compares the workbook's own cached values against a Python
reconstruction of its formulas. Pass 2 compares those Python values against
accounting figures. They are separate functions and produce separately-labelled
lines, because collapsing them into one verdict destroys the distinction between
"this spreadsheet doesn't agree with itself" and "this spreadsheet doesn't agree
with the ledger" — two very different problems.

EVERY verdict this module produces is a PREVIEW, computed against whatever
thresholds were passed in before any human approved them. `verdicts_are_final`
is False on the returned object and Gate 3 is the only place it becomes True.

Pass 2 produces PROPOSALS, never approvals. A fuzzy match — at any confidence —
creates an AccountMapping with is_approved=False. There is no code path here
that sets it True.
"""

import ast
import math
import operator
import re
from decimal import Decimal
from typing import Optional

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from rapidfuzz import fuzz

# Single source of truth for how a cell reference is parsed. Imported rather
# than re-declared so the graph the parser built and the substitutions made here
# can never drift apart.
from agents.comparison import ComparisonError, coerce_to_boolean, evaluate_criteria
from agents.parser import _CELL_REF_PATTERN, _expand_range, _normalize
from core.accounting import signed_reference_amount
from core.formula_catalogue import SUPPORTED_FUNCTIONS as _SUPPORTED_FUNCTIONS
from core.numeric_utils import (
    NumericUtilsError,
    ceiling_to_significance,
    excel_round,
    floor_to_significance,
    rounddown_to_significance,
    roundup_to_significance,
)
from core.models import (
    AccountMapping,
    CellRecord,
    DerivationStep,
    ParsedFile,
    ReconciliationLine,
    ReconciliationResult,
    ReferenceFigureLine,
    ReferenceFigures,
)
from core.verdict_logic import compute_verdict

DEFAULT_PCT_THRESHOLD = 0.01
DEFAULT_ABSOLUTE_THRESHOLD = 100.0

# _SUPPORTED_FUNCTIONS above is imported from core/formula_catalogue.py, the
# single source of truth for the catalogue. Deliberately small there. Anything
# outside it is reported as unsupported rather than approximated — a
# partially-interpreted VLOOKUP is a wrong number wearing a right number's
# clothes.

_FUNCTION_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
_SUM_PATTERN = re.compile(r"SUM\(([^()]*)\)", re.IGNORECASE)
_INNERMOST_CALL_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\(([^()]*)\)")
_STRING_LITERAL_PATTERN = re.compile(r'"[^"]*"')
_BOOLEAN_CALL_PATTERN = re.compile(r"\b(TRUE|FALSE)\s*\(\s*\)", re.IGNORECASE)
_XLFN_PREFIX_PATTERN = re.compile(r"\b_xlfn\.([A-Za-z_][A-Za-z0-9_.]*)", re.IGNORECASE)

# Bounds the innermost-out function-unwrapping loop in _evaluate. Not a
# correctness limit — no formula in scope for this catalogue nests anywhere
# near this deep — just a fail-closed ceiling so a malformed formula can't
# spin the loop forever.
_MAX_FUNCTION_NESTING = 25

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Fuzzy match bands. These decide what gets PROPOSED, never what gets approved.
_CONFIDENT_MATCH = 85.0
_PLAUSIBLE_MATCH = 60.0
_AMBIGUITY_GAP = 5.0

_AGGREGATION_NOTE = (
    "Requires manual reconciliation — aggregation is not computed by this tool."
)


class _UnresolvableReference(Exception):
    """A reference that cannot become a number: text, a date, or a missing chain."""


def _normalize_boolean_tokens(formula: str) -> str:
    """Treat Excel/LibreOffice boolean calls as boolean literals.

    Both spreadsheet engines may serialise the same flag as ``FALSE`` or
    ``FALSE()`` (and likewise for TRUE).  TRUE/FALSE are literals rather than
    formula families in this reconstruction catalogue, so normalise only the
    empty-call spellings before function discovery and evaluation.  Calls with
    arguments remain untouched and therefore fail closed as unsupported.
    """

    return _BOOLEAN_CALL_PATTERN.sub(lambda match: match.group(1).upper(), formula)


def _normalize_compatibility_prefixes(formula: str) -> str:
    """Remove Excel's `_xlfn.` compatibility marker before catalogue lookup.

    The marker says the file writer considers a function newer than the base
    file format; it is not part of the function's name.  The underlying name
    still has to be present in ``SUPPORTED_FUNCTIONS`` or it remains
    unsupported after this normalisation.
    """

    return _XLFN_PREFIX_PATTERN.sub(lambda match: match.group(1), formula)


def _normalize_formula_tokens(formula: str) -> str:
    return _normalize_compatibility_prefixes(_normalize_boolean_tokens(formula))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run_reconciliation(
    parsed_file: ParsedFile,
    authoritative_outputs: list[str],
    reference_figures: Optional[ReferenceFigures] = None,
    pct_threshold: float = DEFAULT_PCT_THRESHOLD,
    absolute_threshold: float = DEFAULT_ABSOLUTE_THRESHOLD,
    default_pct_threshold: float = DEFAULT_PCT_THRESHOLD,
    default_absolute_threshold: float = DEFAULT_ABSOLUTE_THRESHOLD,
    warnings: Optional[list[str]] = None,
) -> ReconciliationResult:
    """Run both passes and return everything in one object.

    `authoritative_outputs` comes from the human at Gate 2. This agent never
    infers which cells matter — an earlier design guessed the output tab by
    keyword-matching the file description, which is not a basis for deciding
    what a validation report is about.
    """
    warnings = warnings if warnings is not None else []
    thresholds_are_default = (
        pct_threshold == default_pct_threshold and absolute_threshold == default_absolute_threshold
    )

    internal_lines = reconcile_excel_vs_python(
        parsed_file=parsed_file,
        authoritative_outputs=authoritative_outputs,
        pct_threshold=pct_threshold,
        absolute_threshold=absolute_threshold,
        thresholds_are_default=thresholds_are_default,
        warnings=warnings,
    )

    mappings: list[AccountMapping] = []
    external_lines: list[ReconciliationLine] = []
    unmatched_reference_items: list[str] = []
    unmapped_python_outputs: list[str] = []

    if reference_figures is not None:
        (
            external_lines,
            mappings,
            unmatched_reference_items,
            unmapped_python_outputs,
        ) = reconcile_python_vs_accounts(
            internal_lines=internal_lines,
            authoritative_outputs=authoritative_outputs,
            reference_figures=reference_figures,
            pct_threshold=pct_threshold,
            absolute_threshold=absolute_threshold,
            thresholds_are_default=thresholds_are_default,
        )

    return ReconciliationResult(
        lines=internal_lines + external_lines,
        mappings=mappings,
        unmatched_reference_items=unmatched_reference_items,
        unmapped_python_outputs=unmapped_python_outputs,
        # Preview only. Gate 3 recomputes every one of these.
        verdicts_are_final=False,
    )


# ---------------------------------------------------------------------------
# Pass 1 — the workbook against a Python reconstruction of itself
# ---------------------------------------------------------------------------


def reconcile_excel_vs_python(
    parsed_file: ParsedFile,
    authoritative_outputs: list[str],
    pct_threshold: float,
    absolute_threshold: float,
    thresholds_are_default: bool,
    warnings: list[str],
) -> list[ReconciliationLine]:
    lines = []
    for output_ref in authoritative_outputs:
        chain, coverage, unsupported, root_value, stale_cell_refs = _build_derivation(
            output_ref, parsed_file, warnings
        )
        completeness = "complete" if coverage >= 100.0 else "partial"
        evidence_status = _evidence_status(stale_cell_refs, parsed_file)

        record = parsed_file.cells.get(output_ref)
        source_value = _as_number(record.cached_value) if record else None
        if record is not None and record.is_stale:
            warnings.append(
                f"{output_ref}: the workbook's own value is stale (never recalculated, or "
                f"the workbook is set to manual calculation) — the comparison is against a "
                f"value Excel itself has not refreshed"
            )
        if evidence_status != "fresh":
            warnings.append(
                f"{output_ref}: calculation evidence is {evidence_status} for "
                f"{', '.join(stale_cell_refs)} — numerical agreement is not evidence of a "
                f"fresh Excel calculation"
            )

        # A partial reconstruction has no target value. Reporting the fraction
        # that did resolve would invite comparing it to the whole.
        target_value = root_value if completeness == "complete" else None
        delta, delta_pct = _delta(source_value, target_value)

        lines.append(
            ReconciliationLine(
                check_type="excel_vs_python",
                label=_derive_label(output_ref, parsed_file.cells),
                source_value=source_value,
                target_value=target_value,
                delta=delta,
                delta_pct=delta_pct,
                verdict=compute_verdict(
                    delta,
                    delta_pct,
                    pct_threshold,
                    absolute_threshold,
                    completeness,
                    evidence_status=evidence_status,
                ),
                pct_threshold=pct_threshold,
                absolute_threshold=absolute_threshold,
                threshold_is_default=thresholds_are_default,
                completeness=completeness,
                reconstruction_coverage_pct=coverage,
                unsupported_elements=unsupported,
                derivation=chain,
                mapping_id=None,
                calculation_evidence_status=evidence_status,
                stale_cell_refs=stale_cell_refs,
            )
        )
    return lines


# ---------------------------------------------------------------------------
# Pass 2 — the Python values against the accounts
# ---------------------------------------------------------------------------


def reconcile_python_vs_accounts(
    internal_lines: list[ReconciliationLine],
    authoritative_outputs: list[str],
    reference_figures: ReferenceFigures,
    pct_threshold: float,
    absolute_threshold: float,
    thresholds_are_default: bool,
) -> tuple[list[ReconciliationLine], list[AccountMapping], list[str], list[str]]:
    """Propose mappings and preliminary comparisons. Approve nothing.

    Returns (lines, mappings, unmatched_reference_items, unmapped_python_outputs).
    The last two are computed from opposite directions and are not the same
    question asked twice: one asks whether every ledger line found a home, the
    other whether every designated output did.
    """
    lines: list[ReconciliationLine] = []
    mappings: list[AccountMapping] = []
    unmatched_reference_items: list[str] = []
    mapped_outputs: set[str] = set()

    by_output = {line.label: line for line in internal_lines}
    output_by_ref = dict(zip(authoritative_outputs, internal_lines))

    for index, reference_line in enumerate(reference_figures.lines, start=1):
        scored = sorted(
            (
                (_label_similarity(reference_line.label, line.label), ref, line)
                for ref, line in output_by_ref.items()
                # A designated output can support only one proposed accounting
                # mapping.  Without this guard, duplicate ledger labels reuse
                # the same output and silently disappear from the unmatched
                # population instead of remaining visible for human review.
                if ref not in mapped_outputs
            ),
            key=lambda item: item[0],
            reverse=True,
        )

        if not scored or scored[0][0] < _PLAUSIBLE_MATCH:
            # No plausible counterpart. No mapping is invented for it.
            unmatched_reference_items.append(reference_line.line_id)
            continue

        best_score, best_ref, best_line = scored[0]
        contenders = [item for item in scored if best_score - item[0] <= _AMBIGUITY_GAP]
        mapping_id = f"MAP-{index:04d}"

        if len(contenders) > 1:
            # Genuinely can't tell which output this ledger line belongs to.
            # Recorded as unsupported aggregation rather than resolved by
            # picking the first one and hoping.
            mapping = AccountMapping(
                mapping_id=mapping_id,
                python_output_cell_ref=best_ref,
                reference_line_id=reference_line.line_id,
                mapping_type="one_to_many",
                suggested_by="fuzzy_match",
                suggested_confidence=round(best_score, 2),
                approval_note=(
                    f"{_AGGREGATION_NOTE} Candidates scored within {_AMBIGUITY_GAP} points: "
                    + ", ".join(f"{ref} ({score:.1f})" for score, ref, _ in contenders)
                ),
                is_approved=False,
            )
            mappings.append(mapping)
            for _, ref, _ in contenders:
                mapped_outputs.add(ref)
            # No comparison line: aggregation is explicitly not computed.
            continue

        is_ambiguous = best_score < _CONFIDENT_MATCH
        mapping = AccountMapping(
            mapping_id=mapping_id,
            python_output_cell_ref=best_ref,
            reference_line_id=reference_line.line_id,
            mapping_type="one_to_one",
            suggested_by="fuzzy_match",
            suggested_confidence=round(best_score, 2),
            approval_note=(
                f"Match itself needs confirmation: '{reference_line.label}' scored "
                f"{best_score:.1f} against '{best_line.label}', below the "
                f"{_CONFIDENT_MATCH:.0f} confidence band."
                if is_ambiguous
                else None
            ),
            # Never True here. Only a human at Gate 3 sets this.
            is_approved=False,
        )
        mappings.append(mapping)
        mapped_outputs.add(best_ref)

        source_value = best_line.target_value
        target_value = signed_reference_amount(reference_line)
        delta, delta_pct = _delta(source_value, target_value)
        # Incompleteness propagates forward from Pass 1 regardless of what the
        # numbers look like or whether the mapping is ever approved. Freshness
        # does too: a Python figure that exactly matches an accounts figure is
        # not evidence the underlying workbook was ever recalculated if the
        # Pass 1 line it came from rests on stale or freshness-unknown evidence.
        completeness = best_line.completeness
        evidence_status = best_line.calculation_evidence_status
        stale_cell_refs = best_line.stale_cell_refs

        lines.append(
            ReconciliationLine(
                check_type="python_vs_accounts",
                label=f"{reference_line.label} ({reference_line.line_id})",
                source_value=source_value,
                target_value=target_value,
                delta=delta,
                delta_pct=delta_pct,
                verdict=compute_verdict(
                    delta,
                    delta_pct,
                    pct_threshold,
                    absolute_threshold,
                    completeness,
                    is_ambiguous_match=is_ambiguous,
                    evidence_status=evidence_status,
                ),
                pct_threshold=pct_threshold,
                absolute_threshold=absolute_threshold,
                threshold_is_default=thresholds_are_default,
                completeness=completeness,
                reconstruction_coverage_pct=best_line.reconstruction_coverage_pct,
                unsupported_elements=best_line.unsupported_elements,
                derivation=best_line.derivation,
                mapping_id=mapping_id,
                calculation_evidence_status=evidence_status,
                stale_cell_refs=stale_cell_refs,
            )
        )

    unmapped_python_outputs = [ref for ref in authoritative_outputs if ref not in mapped_outputs]
    return lines, mappings, unmatched_reference_items, unmapped_python_outputs


# ---------------------------------------------------------------------------
# derivation chains
# ---------------------------------------------------------------------------


def _evidence_status(stale_cell_refs: list[str], parsed_file: ParsedFile) -> str:
    """Reduce a set of stale/unknown cell refs to one line-level status.

    "stale" (cached value confirmed not current) outranks "unknown" (currency
    could not be determined) when a chain has both — it is the stronger claim.
    An empty list means the chain contains no formula cell whose freshness is
    anything but "fresh".
    """
    if not stale_cell_refs:
        return "fresh"
    freshness_values = {
        parsed_file.cells[ref].calculation_freshness
        for ref in stale_cell_refs
        if ref in parsed_file.cells
    }
    if "stale" in freshness_values:
        return "stale"
    if "unknown" in freshness_values:
        return "unknown"
    return "fresh"


def _build_derivation(
    root: str, parsed_file: ParsedFile, warnings: list[str]
) -> tuple[list[DerivationStep], float, list[str], Optional[float], list[str]]:
    """Walk the cell graph out from an output and reconstruct it in Python.

    Returns (chain, coverage_pct, unsupported_elements, root_value, stale_cell_refs).

    `is_supported` describes a node's OWN formula. A node whose formula is
    perfectly supported but whose dependency failed still resolves to None — it
    just isn't itself the reason.

    `stale_cell_refs` collects every formula cell visited (root included) whose
    CellRecord.calculation_freshness is not "fresh" — deterministically, in visit
    order, with duplicates removed. Python may recompute a formula node's value
    independently of its own cached value, so a dependency's staleness does not
    change what this function returns as `root_value`; it is collected anyway as
    a conservative evidence-provenance signal, per the stale-state-fail-closed
    policy: the workbook's own state for that cell was never confirmed, even
    where today's specific recomputation happens to be correct.
    """
    graph = parsed_file.cell_dependency_graph
    reachable = _reachable_from(root, graph)
    in_cycle = _cycle_nodes(reachable, graph)

    steps: dict[str, DerivationStep] = {}
    unsupported_elements: list[str] = []
    stale_cell_refs: list[str] = []
    seen_stale_refs: set[str] = set()
    # Cells that were genuinely blank, not an explicit 0. DerivationStep.
    # resolved_value stores 0.0 for both (arithmetic evaluators like SUM
    # need the "blank in range" case to be a plain, gate-passing number —
    # see the `any(child.resolved_value is None ...)` check below, which
    # would otherwise treat every blank dependency as unresolved). But
    # AVERAGEIF-family evaluators need to tell "matched AND blank" (excluded
    # from the denominator) apart from "matched AND explicitly 0" (counted)
    # — this set is how `values` is built to preserve that distinction for
    # them without changing what arithmetic functions see.
    blank_refs: set[str] = set()

    def _note_freshness(ref: str, record: CellRecord) -> None:
        if record.calculation_freshness == "fresh":
            return
        if ref in seen_stale_refs:
            return
        seen_stale_refs.add(ref)
        stale_cell_refs.append(ref)

    def resolve(ref: str) -> DerivationStep:
        if ref in steps:
            return steps[ref]

        record = parsed_file.cells.get(ref)
        depends_on = list(graph.get(ref, []))
        # Placed before recursion so a cycle cannot re-enter this node.
        step = DerivationStep(
            cell_ref=ref, formula=None, depends_on=depends_on, resolved_value=None, is_supported=True
        )
        steps[ref] = step

        if ref in in_cycle:
            step.is_supported = False
            unsupported_elements.append(f"{ref} is part of a circular reference (unsupported)")
            return step

        if record is None:
            # A referenced cell that holds nothing. Excel reads it as zero.
            warnings.append(f"{ref}: referenced but empty — treated as 0, per Excel's convention")
            step.resolved_value = 0.0
            blank_refs.add(ref)
            return step

        step.formula = record.formula
        _note_freshness(ref, record)

        if record.formula is None:
            raw = record.cached_value
            if isinstance(raw, bool):
                # Checked before the (int, float) branch: bool is an int
                # subclass in Python, and a boolean cell (a flag column
                # feeding a criteria match) is not the same value as 0/1.
                step.resolved_value = raw
            elif isinstance(raw, (int, float)):
                step.resolved_value = float(raw)
            elif isinstance(raw, str):
                # A text leaf — a class-of-business label, say — is not
                # unsupported merely for holding text. It only becomes a
                # problem at the point something tries to use it as a
                # number: arithmetic-only evaluators (SUM, ABS, ROUND, ...)
                # already fail closed on a non-numeric substitution at their
                # own evaluation step; a criteria-matching function
                # (SUMIF, COUNTIF, IF) is exactly what CAN use this value.
                step.resolved_value = raw
            elif raw is None:
                warnings.append(f"{ref}: blank cell — treated as 0, per Excel's convention")
                step.resolved_value = 0.0
                blank_refs.add(ref)
            else:
                step.is_supported = False
                unsupported_elements.append(
                    f"{ref} holds a value of an unsupported type ({raw!r}) (unsupported)"
                )
            return step

        reason = _unsupported_reason(record.formula)
        if reason is not None:
            step.is_supported = False
            unsupported_elements.append(f"{ref} uses {reason}: {record.formula}")
            return step

        resolved_dependencies = {dep: resolve(dep) for dep in depends_on}
        if any(child.resolved_value is None for child in resolved_dependencies.values()):
            # Own formula is fine; something underneath it isn't.
            return step

        # A blank dependency reads as None here, not the 0.0 stored on its
        # own DerivationStep — restoring the distinction arithmetic
        # evaluators (SUM) already know how to treat as 0, and AVERAGEIF-
        # family evaluators need to exclude from a denominator instead.
        values = {
            dep: (None if dep in blank_refs else child.resolved_value)
            for dep, child in resolved_dependencies.items()
        }
        step.resolved_value = _evaluate(record.formula, ref, values, warnings)
        return step

    root_step = resolve(root)
    chain = list(steps.values())
    supported = sum(1 for step in chain if step.is_supported)
    coverage = 100.0 if not chain else round(supported / len(chain) * 100, 6)

    # A chain of entirely supported nodes that still didn't produce a number is
    # not complete either — say so rather than reporting 100% and a None.
    if root_step.resolved_value is None and coverage >= 100.0:
        coverage = 0.0 if len(chain) == 1 else round((len(chain) - 1) / len(chain) * 100, 6)
        unsupported_elements.append(
            f"{root} could not be reconstructed even though every element is supported"
        )

    return chain, coverage, unsupported_elements, root_step.resolved_value, stale_cell_refs


def _reachable_from(root: str, graph: dict) -> set[str]:
    seen, stack = set(), [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return seen


def _cycle_nodes(reachable: set[str], graph: dict) -> set[str]:
    """Every node that can reach itself. Not the same as "has a dependency"."""
    import networkx as nx

    subgraph = nx.DiGraph()
    subgraph.add_nodes_from(reachable)
    for node in reachable:
        for dependency in graph.get(node, []):
            if dependency in reachable:
                subgraph.add_edge(node, dependency)
    return {node for cycle in nx.simple_cycles(subgraph) for node in cycle}


# ---------------------------------------------------------------------------
# the supported formula catalogue
# ---------------------------------------------------------------------------


# Functions whose catalogue entry legitimately takes a string literal
# argument — a criteria like ">100" or a wildcard pattern like "Motor*" is
# not "text used in arithmetic," it's exactly what the function expects.
# IF is included here because its condition argument can contain text
# comparisons like IF(B1="Motor", ...). Every other function still has
# its own string literals rejected by the blanket check below.
_CRITERIA_CONSUMING_FUNCTIONS = {
    "SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS", "AVERAGEIF", "AVERAGEIFS", "MINIFS", "MAXIFS", "IF",
    # VLOOKUP/MATCH's lookup_value is exactly like a criteria: a text
    # literal there (VLOOKUP("Motor", ...)) is what the function expects,
    # not "text used in arithmetic".
    "VLOOKUP", "MATCH", "XLOOKUP",
    # AND/OR's arguments are conditions exactly like IF's, and can contain
    # the same text comparisons, e.g. AND(A1="Motor", B1>0).
    "AND", "OR",
    # CHOOSE(index, value1, value2, ...) commonly mixes text and numeric
    # options in the same call — e.g. CHOOSE(2, "a", 200, "c") — and only
    # the SELECTED value is ever touched at runtime (_choose_evaluator).
    # Without this exemption, a text literal in an UNSELECTED slot would
    # statically disqualify the whole formula regardless of which index is
    # actually chosen; the runtime evaluator, not this textual pre-check,
    # is what decides whether the one value actually picked is numeric.
    "CHOOSE",
}
# Bounds the row*column expansion for a lookup function's table_array/array
# argument, mirroring agents/parser.py's own range-expansion ceiling — a
# range wider than this is rejected rather than expanded cell by cell.
_MAX_TABLE_EXPANSION = 5000
_FUNCTION_CALL_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*$")


def _criteria_call_spans(formula: str) -> list[tuple[int, int]]:
    """(args_start, args_end) for every call in `formula` to a function in
    `_CRITERIA_CONSUMING_FUNCTIONS` — the spans a string literal is allowed
    to fall inside without being flagged as "text used in arithmetic"."""
    spans = []
    stack: list[tuple[int, Optional[str]]] = []
    for i, ch in enumerate(formula):
        if ch == "(":
            name_match = _FUNCTION_CALL_NAME_PATTERN.search(formula[:i])
            name = name_match.group(0).upper() if name_match else None
            stack.append((i + 1, name))
        elif ch == ")" and stack:
            args_start, name = stack.pop()
            if name in _CRITERIA_CONSUMING_FUNCTIONS:
                spans.append((args_start, i))
    return spans


def _unsupported_reason(formula: str) -> Optional[str]:
    """Why this formula is outside the catalogue, or None if it is inside it."""
    formula = _normalize_formula_tokens(formula)
    if formula.startswith("{="):
        return "an array formula (unsupported)"
    if "[" in formula and ".xls" in formula:
        return "a reference to another workbook (unsupported)"

    exempt_spans = _criteria_call_spans(formula)
    for literal_match in _STRING_LITERAL_PATTERN.finditer(formula):
        if not any(start <= literal_match.start() < end for start, end in exempt_spans):
            return "a text literal in arithmetic (unsupported)"

    functions = {name.upper() for name in _FUNCTION_PATTERN.findall(formula)}
    outside = sorted(functions - _SUPPORTED_FUNCTIONS)
    if outside:
        return f"{', '.join(outside)} (unsupported)"
    return None


def _resolve_and_eval_expr(expr_text: str, own_tab: str, values: dict) -> Optional[float]:
    """Substitute bare cell references in `expr_text` and evaluate the arithmetic.

    Shared by the final pass over a formula (after every function call has
    been unwrapped to a literal) and by any function's own argument — ABS's
    or INT's single "value" argument can itself be a cell reference or an
    arithmetic expression, not just a number, and both need the same
    reference-resolution rules a bare SUM argument gets.

    Raises `_UnresolvableReference` for a bare range (only meaningful inside
    a function that expects one, like SUM), a reference truly absent from
    `values` (never resolved), or — since dependency resolution now carries
    text/bool leaves through rather than rejecting them (Step 6) — a
    reference that resolved to something arithmetic can't use. Excel's own
    behavior for `=A1*2` where A1 is text is #VALUE!, not zero: fail closed
    here rather than crash on `float("n/a")` or silently coerce.

    A reference PRESENT in `values` but mapped to None is a genuinely blank
    cell (Step 8's blank/zero distinction, added for AVERAGEIF's
    denominator) — for arithmetic, that is Excel's ordinary "blank reads as
    0" convention, same as a blank cell inside SUM. The None-vs-absent
    distinction matters: `key not in values` means this reference isn't
    even a recognized dependency (a real problem); `values[key] is None`
    means it resolved successfully to nothing.
    """

    def _reference_replacement(match: re.Match) -> str:
        quoted, plain, start, end = match.groups()
        if end:
            raise _UnresolvableReference("a bare range outside SUM")
        tab = quoted or plain or own_tab
        key = f"{tab}!{_normalize(start)}"
        if key not in values:
            raise _UnresolvableReference(f"{tab}!{start}")
        value = values[key]
        if value is None:
            return repr(0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _UnresolvableReference(f"{tab}!{start} is not numeric")
        return repr(float(value))

    substituted = _CELL_REF_PATTERN.sub(_reference_replacement, expr_text)
    if re.search(r"[A-Za-z]", substituted):
        return None
    return _safe_eval_arithmetic(substituted)


def _sum_evaluator(args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str) -> float:
    """SUM(range, ...) — ranges expanded, blank cells treated as 0.

    An argument that is already a plain number is added directly rather than
    passed through `_references_in` (which only ever finds cell references).
    This matters as soon as SUM can appear nested with another function, e.g.
    SUM(ABS(C1), C2): by the time this evaluator sees it, ABS(C1) has already
    been unwrapped to a literal like "10.0", and that argument has no cell
    reference in it at all — treating it as "no references found" would
    silently drop it from the total instead of adding it.

    A text or boolean cell inside the range is SKIPPED, not an error — this
    is Excel's actual documented SUM behavior (SUM ignores non-numeric cells
    in a range) and is deliberately different from bare arithmetic like
    `=A1*2`, where a text A1 is a #VALUE! error. Since Step 6, a dependency
    cell holding text is a normal, fully-supported leaf (core/models.py's
    DerivationStep.resolved_value widening), so this is the first evaluator
    that can actually observe one.
    """
    total = 0.0
    for argument in args_text.split(","):
        argument = argument.strip()
        try:
            total += float(argument)
            continue
        except ValueError:
            pass
        for key in _references_in(argument, own_tab):
            value = values.get(key)
            if value is None:
                warnings.append(
                    f"{own_ref}: {key} is blank inside a SUM — treated as 0, per Excel's "
                    f"convention"
                )
                value = 0.0
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            total += value
    return total


def _abs_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """ABS(value) — the single argument may itself be a reference or expression."""
    inner = _resolve_and_eval_expr(args_text, own_tab, values)
    return None if inner is None else abs(inner)


def _int_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """INT(value) — rounds DOWN toward negative infinity, matching Excel exactly.

    This is not truncation toward zero: INT(-8.9) == -9 in Excel, the same
    way math.floor(-8.9) == -9 in Python. A truncating implementation
    (int(-8.9) == -8, or math.trunc) gives the wrong sign-dependent answer for
    every negative non-integer input — the kind of asymmetry this catalogue's
    correctness notes exist to catch before it ships.
    """
    inner = _resolve_and_eval_expr(args_text, own_tab, values)
    return None if inner is None else float(math.floor(inner))


def _split_two_args(args_text: str) -> Optional[tuple[str, str]]:
    """Split a two-argument function's raw argument text on its single
    top-level comma. None of Group B's arguments can themselves contain a
    comma (no text literals, no nested calls — those were already unwrapped
    before this evaluator runs), so a plain split is safe here."""
    parts = args_text.split(",")
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def _make_value_and_second_arg_evaluator(kernel, second_arg_is_digit_count: bool):
    """Factory for the five Group B rounding evaluators.

    Every one of them has the same shape: resolve two numeric arguments,
    then hand them to the Decimal-based kernel from core/numeric_utils.py.
    Writing that shape once here is what "use the shared arithmetic, don't
    reimplement rounding per function" (Step 4) actually means in code — a
    bug in the kernel is exactly one place to fix, not five.
    """

    def evaluator(
        args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
    ) -> Optional[float]:
        split = _split_two_args(args_text)
        if split is None:
            return None
        value = _resolve_and_eval_expr(split[0], own_tab, values)
        second = _resolve_and_eval_expr(split[1], own_tab, values)
        if value is None or second is None:
            return None
        try:
            return kernel(value, int(second) if second_arg_is_digit_count else second)
        except NumericUtilsError:
            # A domain error in the kernel (none currently raise for Group B's
            # inputs — significance 0 is handled inside the kernel itself —
            # but fail closed rather than propagate a raw exception if that
            # ever changes).
            return None

    return evaluator


_round_evaluator = _make_value_and_second_arg_evaluator(excel_round, second_arg_is_digit_count=True)
_roundup_evaluator = _make_value_and_second_arg_evaluator(
    roundup_to_significance, second_arg_is_digit_count=True
)
_rounddown_evaluator = _make_value_and_second_arg_evaluator(
    rounddown_to_significance, second_arg_is_digit_count=True
)
_ceiling_evaluator = _make_value_and_second_arg_evaluator(
    ceiling_to_significance, second_arg_is_digit_count=False
)
_floor_evaluator = _make_value_and_second_arg_evaluator(
    floor_to_significance, second_arg_is_digit_count=False
)


# ---------------------------------------------------------------------------
# Group C — criteria consumers (SUMIF, and everything that shares its shape)
# ---------------------------------------------------------------------------


def _split_function_args(args_text: str) -> list[str]:
    """Top-level comma split, quote-aware.

    Unlike Group B's `_split_two_args`, a criteria argument can be a quoted
    text literal that itself contains a comma (e.g. a criteria like
    "Motor, Comprehensive" — unusual, but not impossible), so a plain
    `.split(",")` is not safe here. No nested function calls can appear in
    `args_text` either way — `_INNERMOST_CALL_PATTERN` guarantees that by
    construction — so only quotes need tracking, not parentheses.
    """
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in args_text:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [part.strip() for part in parts]


def _split_ampersand(text: str) -> list[str]:
    """Top-level `&` split, quote-aware — Excel's string concatenation
    operator, used to build a criteria from a cell reference, e.g.
    `">"&B1`. A `&` inside a quoted literal is not a split point."""
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == "&" and not in_quotes:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return parts


def _stringify_for_concat(value) -> str:
    """How a resolved value reads as text when concatenated with `&` — used
    only when a criteria has more than one `&`-joined piece, so a numeric
    piece and a text piece combine the way Excel would display them, not the
    way Python's str() would (str(10.0) is "10.0"; Excel's is "10")."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _resolve_criteria_piece(piece: str, own_tab: str, values: dict):
    """One `&`-joined fragment of a criteria argument, resolved to whatever
    it actually is: a quoted text literal, TRUE/FALSE, a bare number, or a
    cell reference (resolved through `values`, exactly like any other
    dependency — a criteria built from `">"&B1` reads B1's ALREADY-RESOLVED
    value here, never re-parses the workbook independently)."""
    piece = piece.strip()
    if len(piece) >= 2 and piece.startswith('"') and piece.endswith('"'):
        return piece[1:-1]
    if piece.upper() == "TRUE":
        return True
    if piece.upper() == "FALSE":
        return False
    try:
        return float(piece)
    except ValueError:
        pass
    match = _CELL_REF_PATTERN.fullmatch(piece)
    if match:
        quoted, plain, start, end = match.groups()
        if end:
            raise _UnresolvableReference("a bare range used as a criteria value")
        tab = quoted or plain or own_tab
        key = f"{tab}!{_normalize(start)}"
        if key not in values:
            raise _UnresolvableReference(f"{tab}!{start}")
        value = values[key]
        # A genuinely blank cell (key present, value None — see Step 8's
        # blank/zero distinction) reads as empty text here, matching
        # Excel's own behavior when a blank cell is used in a criteria or
        # concatenated with &.
        return "" if value is None else value
    raise _UnresolvableReference(f"unrecognized criteria fragment: {piece!r}")


def _resolve_criteria_arg(arg_text: str, own_tab: str, values: dict):
    """A full criteria argument, which may be a single literal/reference or
    several pieces joined by `&` (string concatenation) — the shape
    `SUMIF(A:A, ">"&B1, C:C)` produces. A single piece keeps its own type
    (a number stays a number, so `evaluate_criteria` can still tell a
    numeric criteria from a text one); multiple pieces are always text,
    matching what `&` actually does in Excel."""
    pieces = _split_ampersand(arg_text)
    if len(pieces) == 1:
        return _resolve_criteria_piece(pieces[0], own_tab, values)
    return "".join(_stringify_for_concat(_resolve_criteria_piece(p, own_tab, values)) for p in pieces)


def _is_numeric_cell_value(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sumif_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """SUMIF(range, criteria, [sum_range]).

    `range` and `sum_range` must expand to the same number of cells — Excel
    also supports a sum_range smaller than range with shape expansion from
    its top-left cell; that alignment rule is not implemented here, and a
    mismatched count fails closed (returns None, an unsupported result)
    rather than guess which cells line up.
    """
    parts = _split_function_args(args_text)
    if len(parts) not in (2, 3):
        return None

    range_refs = _references_in(parts[0], own_tab)
    sum_range_refs = _references_in(parts[2], own_tab) if len(parts) == 3 else range_refs
    if len(range_refs) != len(sum_range_refs) or not range_refs:
        return None

    criteria_value = _resolve_criteria_arg(parts[1], own_tab, values)

    total = 0.0
    for range_ref, sum_ref in zip(range_refs, sum_range_refs):
        if evaluate_criteria(values.get(range_ref), criteria_value):
            matched = values.get(sum_ref)
            if _is_numeric_cell_value(matched):
                total += matched
            # A blank or text cell in a MATCHING row contributes 0, the same
            # way SUM silently skips a non-numeric cell — not a warning-
            # worthy event, since SUMIF's sum_range is expected to be numeric
            # and a stray text cell there is Excel's own behavior to ignore it.
    return total


def _collect_criteria_pairs(
    pair_args: list[str], own_tab: str, values: dict, expected_length: Optional[int]
) -> Optional[tuple[list[list[str]], list, int]]:
    """Shared core of SUMIFS/COUNTIF/COUNTIFS: `pair_args` is a flat
    [range1, criteria1, range2, criteria2, ...] list. Every range must
    expand to the same cell count — the first one seen sets the standard if
    `expected_length` isn't already fixed by a caller (SUMIFS' sum_range).

    Returns (range_groups, criteria_values, length), or None if the argument
    count is odd, empty, or any range's length doesn't match — fails closed
    rather than guess an alignment Excel itself wouldn't accept either.
    """
    if not pair_args or len(pair_args) % 2 != 0:
        return None
    range_groups: list[list[str]] = []
    criteria_values = []
    length = expected_length
    for i in range(0, len(pair_args), 2):
        refs = _references_in(pair_args[i], own_tab)
        if length is None:
            length = len(refs)
        if not refs or len(refs) != length:
            return None
        range_groups.append(refs)
        criteria_values.append(_resolve_criteria_arg(pair_args[i + 1], own_tab, values))
    return range_groups, criteria_values, length


def _sumifs_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """SUMIFS(sum_range, criteria_range1, criteria1, [criteria_range2, criteria2, ...]).

    Every criteria pair must match (AND) for a row to contribute to the sum
    — this is the argument-order inverse of SUMIF, where sum_range comes
    LAST and is optional. Excel made these two functions' argument order
    genuinely different; reusing SUMIF's evaluator here would silently
    misalign sum_range with the wrong argument.
    """
    parts = _split_function_args(args_text)
    if len(parts) < 3:
        return None
    sum_refs = _references_in(parts[0], own_tab)
    if not sum_refs:
        return None
    collected = _collect_criteria_pairs(parts[1:], own_tab, values, expected_length=len(sum_refs))
    if collected is None:
        return None
    range_groups, criteria_values, length = collected

    total = 0.0
    for index in range(length):
        if all(
            evaluate_criteria(values.get(refs[index]), criteria)
            for refs, criteria in zip(range_groups, criteria_values)
        ):
            matched = values.get(sum_refs[index])
            if _is_numeric_cell_value(matched):
                total += matched
    return total


def _countifs_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """COUNTIFS(criteria_range1, criteria1, [criteria_range2, criteria2, ...])
    — counts rows where every criteria pair matches. COUNTIF (below) is this
    function called with exactly one pair, not a separate implementation."""
    parts = _split_function_args(args_text)
    collected = _collect_criteria_pairs(parts, own_tab, values, expected_length=None)
    if collected is None:
        return None
    range_groups, criteria_values, length = collected

    count = 0
    for index in range(length):
        if all(
            evaluate_criteria(values.get(refs[index]), criteria)
            for refs, criteria in zip(range_groups, criteria_values)
        ):
            count += 1
    return float(count)


def _countif_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """COUNTIF(range, criteria) — the legacy single-criteria-pair form of
    COUNTIFS, not a duplicated implementation."""
    return _countifs_evaluator(args_text, own_tab, values, warnings, own_ref)


def _averageif_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """AVERAGEIF(range, criteria, [average_range]).

    The denominator is "cells that matched the criteria AND held a number"
    — not "every cell in the range." A matching row whose average_range
    cell is blank or text is excluded from BOTH the numerator and the
    denominator, the same way AVERAGE ignores non-numeric cells; it is NOT
    counted as a zero, which would silently pull the average down.

    No matching numeric cell is Excel's #DIV/0! — returned here as None
    (an honest "could not be reconstructed") rather than 0.0, which would
    read as a false numeric agreement instead of the reconstruction gap it
    actually is. Matching the cached #DIV/0! text exactly is Step 12's
    verdict-granularity work, not this evaluator's job.
    """
    parts = _split_function_args(args_text)
    if len(parts) not in (2, 3):
        return None
    range_refs = _references_in(parts[0], own_tab)
    average_range_refs = _references_in(parts[2], own_tab) if len(parts) == 3 else range_refs
    if len(range_refs) != len(average_range_refs) or not range_refs:
        return None

    criteria_value = _resolve_criteria_arg(parts[1], own_tab, values)

    total = 0.0
    count = 0
    for range_ref, average_ref in zip(range_refs, average_range_refs):
        if evaluate_criteria(values.get(range_ref), criteria_value):
            matched = values.get(average_ref)
            if _is_numeric_cell_value(matched):
                total += matched
                count += 1
    if count == 0:
        return None
    return total / count


def _averageifs_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """AVERAGEIFS(average_range, criteria_range1, criteria1, ...) — same
    argument-order shape as SUMIFS (average_range first, then pairs), and
    the same "matching AND numeric" denominator rule as AVERAGEIF above."""
    parts = _split_function_args(args_text)
    if len(parts) < 3:
        return None
    average_range_refs = _references_in(parts[0], own_tab)
    if not average_range_refs:
        return None
    collected = _collect_criteria_pairs(
        parts[1:], own_tab, values, expected_length=len(average_range_refs)
    )
    if collected is None:
        return None
    range_groups, criteria_values, length = collected

    total = 0.0
    count = 0
    for index in range(length):
        if all(
            evaluate_criteria(values.get(refs[index]), criteria)
            for refs, criteria in zip(range_groups, criteria_values)
        ):
            matched = values.get(average_range_refs[index])
            if _is_numeric_cell_value(matched):
                total += matched
                count += 1
    if count == 0:
        return None
    return total / count


def _minifs_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """MINIFS(min_range, criteria_range1, criteria1, ...) — returns the
    minimum of matching values. If no match, returns #NUM! (None here)."""
    parts = _split_function_args(args_text)
    if len(parts) < 3:
        return None
    min_refs = _references_in(parts[0], own_tab)
    if not min_refs:
        return None
    collected = _collect_criteria_pairs(
        parts[1:], own_tab, values, expected_length=len(min_refs)
    )
    if collected is None:
        return None
    range_groups, criteria_values, length = collected

    min_val = None
    for index in range(length):
        if all(
            evaluate_criteria(values.get(refs[index]), criteria)
            for refs, criteria in zip(range_groups, criteria_values)
        ):
            matched = values.get(min_refs[index])
            if _is_numeric_cell_value(matched):
                if min_val is None or matched < min_val:
                    min_val = matched
    return min_val


def _maxifs_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """MAXIFS(max_range, criteria_range1, criteria1, ...) — returns the
    maximum of matching values. If no match, returns #NUM! (None here)."""
    parts = _split_function_args(args_text)
    if len(parts) < 3:
        return None
    max_refs = _references_in(parts[0], own_tab)
    if not max_refs:
        return None
    collected = _collect_criteria_pairs(
        parts[1:], own_tab, values, expected_length=len(max_refs)
    )
    if collected is None:
        return None
    range_groups, criteria_values, length = collected

    max_val = None
    for index in range(length):
        if all(
            evaluate_criteria(values.get(refs[index]), criteria)
            for refs, criteria in zip(range_groups, criteria_values)
        ):
            matched = values.get(max_refs[index])
            if _is_numeric_cell_value(matched):
                if max_val is None or matched > max_val:
                    max_val = matched
    return max_val


def _evaluate_if_condition(condition_text: str, own_tab: str, values: dict) -> Optional[bool]:
    """Evaluate an IF condition to a boolean, or None if it cannot be resolved.

    A condition can be:
    - A comparison (e.g., "A1>0", "A1>=B1", B1="Motor") — split on operator, evaluate both sides, compare
    - An arithmetic expression (e.g., "A1+B1") — evaluate and coerce to boolean (0=FALSE)
    - A cell reference or literal — coerce to boolean

    For equality/inequality (= and <>), use the comparison engine to handle text matching.
    For numeric comparisons, both sides must be numeric.

    Returns None — never a guessed True/False — when a side of the comparison
    (or the bare condition itself) is a genuinely unresolvable reference: a
    dependency truly absent from `values`, or an unparseable expression. This
    is distinct from a reference that resolves to an actual blank cell
    (present in `values`, mapped to None), which Excel itself treats as
    FALSE. Collapsing "unresolvable" into "False" would silently pick a
    branch and report a computed number for a condition this tool never
    actually evaluated — every caller (IF, and AND/OR which share this
    function) must check for None and propagate it as unsupported rather
    than treat it as a falsy condition.
    """
    condition = condition_text.strip()
    if condition.upper() == "TRUE":
        return True
    if condition.upper() == "FALSE":
        return False

    # Try to find a comparison operator at paren depth 0 (outside of parens and quotes)
    comparison_ops = ("<=", ">=", "<>", "<", ">", "=")
    paren_depth = 0
    in_quotes = False
    operator_pos = -1
    operator_found = None

    i = 0
    while i < len(condition):
        ch = condition[i]
        if ch == '"':
            in_quotes = not in_quotes
            i += 1
        elif ch == '(' and not in_quotes:
            paren_depth += 1
            i += 1
        elif ch == ')' and not in_quotes:
            paren_depth -= 1
            i += 1
        elif paren_depth == 0 and not in_quotes:
            for op in comparison_ops:
                if condition[i:i+len(op)] == op:
                    operator_pos = i
                    operator_found = op
                    i += len(op)
                    break
            else:
                i += 1
        else:
            i += 1

    if operator_found:
        left = condition[:operator_pos].strip()
        right = condition[operator_pos + len(operator_found):].strip()

        # For = and <>, try using the comparison engine which handles text matching
        if operator_found in ("=", "<>"):
            try:
                # Resolve both sides; if they resolve to values, we can compare them
                left_val = None
                right_val = None
                try:
                    left_val = _resolve_and_eval_expr(left, own_tab, values)
                except _UnresolvableReference:
                    # Try as a cell reference alone
                    for key in _references_in(left, own_tab):
                        left_val = values.get(key)
                        break

                try:
                    right_val = _resolve_and_eval_expr(right, own_tab, values)
                except _UnresolvableReference:
                    # Try as a cell reference alone
                    for key in _references_in(right, own_tab):
                        right_val = values.get(key)
                        break

                # Also handle string literals (quoted text)
                if right_val is None and right.startswith('"') and right.endswith('"'):
                    right_val = right[1:-1]
                if left_val is None and left.startswith('"') and left.endswith('"'):
                    left_val = left[1:-1]

                # Use evaluate_criteria for text matching
                if operator_found == "=":
                    return evaluate_criteria(left_val, right_val)
                else:
                    # For <>, negate the result
                    return not evaluate_criteria(left_val, right_val)
            except Exception:
                # An unexpected failure while resolving either side is a
                # condition this tool couldn't evaluate — unsupported, not a
                # false comparison it's actually confident about.
                return None
        else:
            # Numeric comparison operators: both sides must be numeric
            left_val = _resolve_and_eval_expr(left, own_tab, values)
            right_val = _resolve_and_eval_expr(right, own_tab, values)

            if left_val is None or right_val is None:
                # Neither side resolved to a number — e.g. a genuinely
                # missing dependency, or a comparison Excel itself would
                # reject as #VALUE!. Not the same as "compared and False".
                return None

            if operator_found == ">":
                return left_val > right_val
            elif operator_found == "<":
                return left_val < right_val
            elif operator_found == ">=":
                return left_val >= right_val
            elif operator_found == "<=":
                return left_val <= right_val
    else:
        # No operator found, evaluate as expression and coerce to boolean
        try:
            result = _resolve_and_eval_expr(condition, own_tab, values)
        except _UnresolvableReference:
            # A boolean cell is a valid IF condition but deliberately cannot
            # be substituted into arithmetic. Resolve the bare reference as
            # its native bool instead of treating that arithmetic guard as a
            # failed IF condition.
            result = None
        if result is None:
            # Try as a bare cell reference. A reference genuinely absent
            # from `values` (never resolved) is an unsupported condition —
            # only a reference PRESENT but mapped to None is a true blank
            # cell, which Excel does treat as FALSE (handled just below).
            refs = _references_in(condition, own_tab)
            if not refs:
                return None
            key = refs[0]
            if key not in values:
                return None
            result = values.get(key)
        if result is None:
            return False
        # Coerce to boolean
        if isinstance(result, bool):
            return result
        if isinstance(result, (int, float)):
            return result != 0
        return True


def _if_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """IF(condition, value_if_true, value_if_false) — returns one of two values
    based on a boolean condition. Only the chosen branch is ever resolved —
    matching Excel's own lazy evaluation, where IF(B1=0, 0, A1/B1) does not
    raise #DIV/0! when B1 is 0, because the division is never reached."""
    parts = _split_function_args(args_text)
    if len(parts) != 3:
        return None

    condition = parts[0].strip()
    value_if_true = parts[1].strip()
    value_if_false = parts[2].strip()

    condition_result = _evaluate_if_condition(condition, own_tab, values)
    if condition_result is None:
        # Unresolvable is not the same signal as "condition is False" — a
        # branch was never chosen, so nothing here can be reported as complete.
        return None

    chosen_branch = value_if_true if condition_result else value_if_false

    try:
        return _resolve_and_eval_expr(chosen_branch, own_tab, values)
    except _UnresolvableReference:
        return None


# ---------------------------------------------------------------------------
# Group F — logical (AND, OR)
# ---------------------------------------------------------------------------


def _and_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """AND(logical1, [logical2, ...]) — 1.0 if every argument is TRUE, else 0.0.

    Every argument is evaluated regardless of an earlier one already being
    FALSE — Excel does not short-circuit AND, and neither does this: skipping
    a later argument could hide a genuinely unresolvable one behind an
    already-decided result, silently reporting complete when this tool never
    actually checked every condition.

    Reuses `_evaluate_if_condition`, the same condition parser IF uses, so
    AND/IF can never disagree about what a given argument's truth value is.
    A range-shaped argument (`AND(A1:A3>0)`, Excel's array-formula meaning)
    is out of scope like every other array formula: `_evaluate_if_condition`
    has no range handling, so it fails closed to unsupported for free.
    """
    parts = _split_function_args(args_text)
    if not parts:
        return None
    result = True
    for part in parts:
        condition = _evaluate_if_condition(part.strip(), own_tab, values)
        if condition is None:
            return None
        result = result and condition
    return 1.0 if result else 0.0


def _or_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """OR(logical1, [logical2, ...]) — 1.0 if any argument is TRUE, else 0.0.

    Same no-short-circuit rationale as `_and_evaluator`: every argument is
    evaluated, and any single unresolvable argument makes the whole OR
    unsupported, even if an earlier argument already resolved to TRUE.
    """
    parts = _split_function_args(args_text)
    if not parts:
        return None
    result = False
    for part in parts:
        condition = _evaluate_if_condition(part.strip(), own_tab, values)
        if condition is None:
            return None
        result = result or condition
    return 1.0 if result else 0.0


# ---------------------------------------------------------------------------
# Group E — lookup functions (VLOOKUP, MATCH, INDEX)
# ---------------------------------------------------------------------------


_ROW_COL_PATTERN = re.compile(r"([A-Z]{1,3})([0-9]{1,7})")


def _split_row_col(ref: str) -> Optional[tuple[str, int]]:
    match = _ROW_COL_PATTERN.fullmatch(ref)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _expand_table_rows(range_text: str, own_tab: str) -> Optional[list[list[str]]]:
    """A lookup function's array/table_array argument, expanded ROW-MAJOR:
    outer list is rows, inner list is that row's cells left to right.

    `_references_in` (used by SUM and the criteria family) flattens a range
    column-major and throws away which cells shared a row — exactly the
    information VLOOKUP and INDEX need to find a row by its first column and
    then read a different column of THAT SAME row. This is a separate
    helper rather than a reshape of `_references_in`'s output for that
    reason: reshaping a column-major flat list back into rows correctly
    requires the same width/height computation this function does directly.

    A single cell (no ":") is a valid 1x1 table — Excel accepts
    `INDEX(A1,1,1)` — so it is returned as `[[ref]]` rather than rejected.
    """
    range_text = range_text.strip()
    match = _CELL_REF_PATTERN.fullmatch(range_text)
    if not match:
        return None
    quoted, plain, start, end = match.groups()
    tab = quoted or plain or own_tab
    start = _normalize(start)
    if not end:
        return [[f"{tab}!{start}"]]
    end = _normalize(end)

    start_rc = _split_row_col(start)
    end_rc = _split_row_col(end)
    if start_rc is None or end_rc is None:
        return None
    start_col, start_row = start_rc
    end_col, end_row = end_rc

    col_lo, col_hi = sorted((column_index_from_string(start_col), column_index_from_string(end_col)))
    row_lo, row_hi = sorted((start_row, end_row))
    if (col_hi - col_lo + 1) * (row_hi - row_lo + 1) > _MAX_TABLE_EXPANSION:
        return None

    return [
        [f"{tab}!{get_column_letter(col)}{row}" for col in range(col_lo, col_hi + 1)]
        for row in range(row_lo, row_hi + 1)
    ]


def _vlookup_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """VLOOKUP(lookup_value, table_array, col_index_num, [range_lookup]).

    Exact match only. `range_lookup` TRUE — Excel's own default when the
    argument is omitted — assumes the table's first column is sorted
    ascending and binary-searches it; this tool has no way to confirm that
    sort order holds, so an approximate match is reported as unsupported
    (with a warning explaining why) rather than trusted blindly.

    A matched row whose result column holds text is also unsupported: every
    evaluator in this catalogue feeds its result back into `_evaluate`'s
    literal-substitution loop, which is arithmetic-only and cannot carry a
    text value through to further formula unwrapping.
    """
    parts = _split_function_args(args_text)
    if len(parts) not in (3, 4):
        return None

    try:
        lookup_value = _resolve_criteria_arg(parts[0], own_tab, values)
    except _UnresolvableReference:
        return None

    table_rows = _expand_table_rows(parts[1], own_tab)
    if not table_rows or not table_rows[0]:
        return None

    col_index_raw = _resolve_and_eval_expr(parts[2], own_tab, values)
    if col_index_raw is None:
        return None
    col_index = int(col_index_raw)
    if col_index < 1 or col_index > len(table_rows[0]):
        return None

    range_lookup = True
    if len(parts) == 4:
        try:
            range_lookup_value = _resolve_criteria_arg(parts[3], own_tab, values)
        except _UnresolvableReference:
            return None
        try:
            range_lookup = coerce_to_boolean(range_lookup_value)
        except ComparisonError:
            return None

    if range_lookup:
        warnings.append(
            f"{own_ref}: VLOOKUP with an approximate match (range_lookup TRUE, "
            f"or omitted) is not evaluated — this tool does not verify the "
            f"lookup column is sorted, so an approximate match is reported as "
            f"unsupported rather than silently computed"
        )
        return None

    for row in table_rows:
        if evaluate_criteria(values.get(row[0]), lookup_value):
            matched = values.get(row[col_index - 1])
            return float(matched) if _is_numeric_cell_value(matched) else None
    return None


def _match_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """MATCH(lookup_value, lookup_array, [match_type]) — returns the
    1-indexed position of the first match, or None (#N/A) if not found.

    Only match_type 0 (exact) is evaluated. match_type 1 (Excel's own
    default) and -1 both assume the array is sorted and binary-search it —
    the same stance VLOOKUP's approximate mode takes: surfaced as
    unsupported rather than trusted without verification.
    """
    parts = _split_function_args(args_text)
    if len(parts) not in (2, 3):
        return None

    try:
        lookup_value = _resolve_criteria_arg(parts[0], own_tab, values)
    except _UnresolvableReference:
        return None

    array_rows = _expand_table_rows(parts[1], own_tab)
    if not array_rows or not array_rows[0]:
        return None
    if len(array_rows) == 1:
        flat = array_rows[0]
    elif len(array_rows[0]) == 1:
        flat = [row[0] for row in array_rows]
    else:
        # MATCH's lookup_array must be a single row or column, not a 2D grid.
        return None

    match_type = 1
    if len(parts) == 3:
        match_type_raw = _resolve_and_eval_expr(parts[2], own_tab, values)
        if match_type_raw is None:
            return None
        match_type = int(match_type_raw)

    if match_type != 0:
        warnings.append(
            f"{own_ref}: MATCH with an approximate match_type ({match_type}) is "
            f"not evaluated — this tool does not verify the lookup array is "
            f"sorted, so an approximate match is reported as unsupported "
            f"rather than silently computed"
        )
        return None

    for position, key in enumerate(flat, start=1):
        if evaluate_criteria(values.get(key), lookup_value):
            return float(position)
    return None


def _flatten_1d_table(table: list[list[str]]) -> Optional[list[str]]:
    """A row-major `_expand_table_rows` result, flattened to 1D — or None if
    it genuinely has more than one row AND more than one column. Shared by
    XLOOKUP's lookup_array and return_array, both of which must be a single
    row or a single column in real Excel (a 2D return_array is XLOOKUP's
    row/column "spill" behavior — an array formula, out of scope)."""
    if len(table) == 1:
        return table[0]
    if all(len(row) == 1 for row in table):
        return [row[0] for row in table]
    return None


def _xlookup_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found],
    [match_mode], [search_mode]).

    Exact match only (match_mode 0) — the same posture as VLOOKUP/MATCH:
    match_mode +/-1 (next larger/smaller) both assume the array is sorted,
    which this tool cannot verify, so they are unsupported with a warning
    rather than trusted. match_mode 2 (wildcard) is a distinct capability,
    deliberately deferred rather than folded into this first pass.

    search_mode +/-1 (first-to-last / last-to-first) are both supported —
    neither implies a sortedness assumption, only which of several exact
    matches wins when the lookup value repeats. search_mode +/-2 (binary
    search) are unsupported for consistency with the sortedness posture
    above, even though a linear scan here would give a correct answer
    regardless of the array's actual sort order.

    `if_not_found` (the 4th argument) is parsed for arity but not used — a
    "no match" result is unsupported here exactly like VLOOKUP/MATCH's "no
    match", rather than substituting a fallback value that might itself be
    text this tool cannot carry forward.

    A matched `return_array` cell holding text is unsupported for the same
    architectural reason as VLOOKUP/INDEX: no evaluator in this catalogue
    can carry a text result back into further arithmetic. This bites more
    often for XLOOKUP than VLOOKUP in practice, since XLOOKUP is idiomatic
    for text lookups (names, labels) at least as often as numeric ones.
    """
    parts = _split_function_args(args_text)
    if len(parts) < 3 or len(parts) > 6:
        return None

    try:
        lookup_value = _resolve_criteria_arg(parts[0], own_tab, values)
    except _UnresolvableReference:
        return None

    lookup_table = _expand_table_rows(parts[1], own_tab)
    if not lookup_table or not lookup_table[0]:
        return None
    lookup_refs = _flatten_1d_table(lookup_table)
    if lookup_refs is None:
        return None

    return_table = _expand_table_rows(parts[2], own_tab)
    if not return_table or not return_table[0]:
        return None
    return_refs = _flatten_1d_table(return_table)
    if return_refs is None:
        return None

    if len(lookup_refs) != len(return_refs):
        return None

    match_mode = 0
    if len(parts) >= 5:
        match_mode_raw = _resolve_and_eval_expr(parts[4], own_tab, values)
        if match_mode_raw is None:
            return None
        match_mode = int(match_mode_raw)
    if match_mode != 0:
        warnings.append(
            f"{own_ref}: XLOOKUP with an approximate match_mode ({match_mode}) is "
            f"not evaluated — this tool does not verify the lookup array is "
            f"sorted, so an approximate match is reported as unsupported "
            f"rather than silently computed"
        )
        return None

    search_mode = 1
    if len(parts) == 6:
        search_mode_raw = _resolve_and_eval_expr(parts[5], own_tab, values)
        if search_mode_raw is None:
            return None
        search_mode = int(search_mode_raw)
    if search_mode not in (1, -1):
        warnings.append(
            f"{own_ref}: XLOOKUP with search_mode {search_mode} (binary search) is "
            f"not evaluated — this tool does not verify the lookup array is "
            f"sorted, which binary search silently assumes"
        )
        return None

    order = range(len(lookup_refs)) if search_mode == 1 else range(len(lookup_refs) - 1, -1, -1)
    for i in order:
        if evaluate_criteria(values.get(lookup_refs[i]), lookup_value):
            matched = values.get(return_refs[i])
            return float(matched) if _is_numeric_cell_value(matched) else None
    return None


def _index_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """INDEX(array, row_num, [col_num]) — a scalar result only.

    row_num 0 or col_num 0 ("return the whole row/column") produces an
    array in Excel; that is out of scope alongside every other array
    formula, and is reported as unresolved here rather than approximated.
    """
    parts = _split_function_args(args_text)
    if len(parts) not in (2, 3):
        return None

    array_rows = _expand_table_rows(parts[0], own_tab)
    if not array_rows or not array_rows[0]:
        return None

    row_raw = _resolve_and_eval_expr(parts[1], own_tab, values)
    if row_raw is None:
        return None
    row_num = int(row_raw)

    col_num = None
    if len(parts) == 3:
        col_raw = _resolve_and_eval_expr(parts[2], own_tab, values)
        if col_raw is None:
            return None
        col_num = int(col_raw)

    n_rows = len(array_rows)
    n_cols = len(array_rows[0])

    if col_num is None:
        # A 1D array with col_num omitted: row_num addresses a position
        # along whichever dimension actually varies, matching Excel's own
        # behavior for INDEX(single_row_or_column, n).
        if n_rows == 1:
            col_num, row_num = row_num, 1
        elif n_cols == 1:
            col_num = 1
        else:
            return None

    if row_num < 1 or row_num > n_rows or col_num < 1 or col_num > n_cols:
        return None

    matched = values.get(array_rows[row_num - 1][col_num - 1])
    return float(matched) if _is_numeric_cell_value(matched) else None


# ---------------------------------------------------------------------------
# Group I — index-based selection (CHOOSE)
# ---------------------------------------------------------------------------


def _choose_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """CHOOSE(index_num, value1, [value2, ...]) — returns the value at the
    1-based position index_num.

    A fractional index_num is TRUNCATED toward the integer below it — Excel's
    own documented behavior, not round-to-nearest (CHOOSE(1.9, ...) selects
    value1, not value2).

    Only the SELECTED value argument is ever resolved, matching Excel's (and
    this tool's IF's) lazy evaluation — an unresolvable UNCHOSEN argument is
    irrelevant and never touched, so CHOOSE(1, 10, 1/0) is not penalized for
    a division error in a branch that was never taken.

    The selected value resolving to text is unsupported — the same
    architectural wall as VLOOKUP/INDEX/XLOOKUP (no evaluator here can carry
    a text result into further arithmetic), and CHOOSE is at least as often
    used to pick between text labels as numbers in practice.
    """
    parts = _split_function_args(args_text)
    if len(parts) < 2:
        return None

    index_raw = _resolve_and_eval_expr(parts[0], own_tab, values)
    if index_raw is None:
        return None
    index = math.floor(index_raw)
    if index < 1 or index > len(parts) - 1:
        return None

    chosen = parts[index].strip()
    try:
        return _resolve_and_eval_expr(chosen, own_tab, values)
    except _UnresolvableReference:
        return None


# ---------------------------------------------------------------------------
# Group G — array product aggregation (SUMPRODUCT)
# ---------------------------------------------------------------------------


def _sumproduct_element(value) -> float:
    """SUMPRODUCT's own numeric coercion — distinct from every other
    evaluator's "text is unresolvable" rule.

    Text and blank cells inside an array both read as 0 (Excel's actual,
    documented SUMPRODUCT behavior — unlike bare arithmetic, where a text
    operand is #VALUE!). TRUE/FALSE reads as 1/0, which is also a
    SUMPRODUCT-specific rule: Group C's criteria engine treats booleans by
    text-equality, not numeric coercion, and that convention deliberately
    does not leak into this function.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _sumproduct_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """SUMPRODUCT(array1, [array2, ...]) — sums the element-wise products of
    matching-shaped ranges. A single argument is valid Excel and is
    equivalent to SUM.

    Every argument must be a genuine range/cell reference, expanded via the
    row-major, shape-aware `_expand_table_rows` (not the flat
    `_references_in`) so a same-cell-count-but-different-shape mismatch
    (e.g. a 3x2 range against a 2x3 range) is caught as a real dimension
    mismatch rather than silently zipped together by position. A bare
    scalar argument — either a literal (`SUMPRODUCT(A1:A3, 2)`) or a nested
    function's scalar result — fails `_expand_table_rows`'s reference match
    and is unsupported: Excel's scalar-broadcast behavior for SUMPRODUCT is
    explicitly out of scope for this first implementation.
    """
    parts = _split_function_args(args_text)
    if not parts:
        return None

    arrays: list[list[list[str]]] = []
    shape: Optional[tuple[int, int]] = None
    for part in parts:
        table = _expand_table_rows(part.strip(), own_tab)
        if not table or not table[0]:
            return None
        this_shape = (len(table), len(table[0]))
        if shape is None:
            shape = this_shape
        elif this_shape != shape:
            return None
        arrays.append(table)

    total = 0.0
    n_rows, n_cols = shape
    for row in range(n_rows):
        for col in range(n_cols):
            product = 1.0
            for table in arrays:
                product *= _sumproduct_element(values.get(table[row][col]))
            total += product
    return total


# ---------------------------------------------------------------------------
# Group H — time-value-of-money (NPV)
# ---------------------------------------------------------------------------


def _npv_evaluator(
    args_text: str, own_tab: str, values: dict, warnings: list[str], own_ref: str
) -> Optional[float]:
    """NPV(rate, value1, [value2, ...]) — net present value at a fixed rate.

    Matches Excel's own NPV exactly, not a "friendlier" variant: the FIRST
    cash flow is discounted by (1+rate)^1, not (1+rate)^0 — there is no
    period-0 term. A workbook that wants an undiscounted time-0 outflow
    included adds it OUTSIDE the call (Excel's own idiom: `=NPV(rate,
    B2:B10)+B1`), which needs no special support here — it's just ordinary
    addition applied to this function's returned scalar.

    Only equally-spaced periodic cash flows are supported — this is what
    Excel's NPV itself is (irregular/dated cash flows are XNPV, a different
    function, out of scope).

    Range arguments are flattened via the row-major `_expand_table_rows`,
    not the column-major `_references_in`, because a genuine 2D range's
    element ORDER determines each cash flow's period — unlike SUMPRODUCT,
    where only shape-matching (not order) matters. Multiple arguments after
    `rate` concatenate in argument order.

    Per Excel's documented NPV behavior, a text or boolean cell inside a
    range argument is SKIPPED, not zero-filled — it does not consume a
    period position, so every later cash flow's period shifts down by one.
    This is a genuine, easy-to-get-wrong Excel quirk, deliberately not
    treated the same as SUM's or SUMPRODUCT's blank-as-zero convention.
    """
    parts = _split_function_args(args_text)
    if len(parts) < 2:
        return None

    # `_resolve_and_eval_expr` already raises `_UnresolvableReference` for a
    # bare range ("a bare range outside SUM") — a genuine range rate
    # argument fails closed here for free. A single cell reference or a
    # literal both resolve normally, matching Excel's own single-value rate.
    try:
        rate = _resolve_and_eval_expr(parts[0], own_tab, values)
    except _UnresolvableReference:
        return None
    if rate is None:
        return None

    # Collected as raw resolved values (not cell keys) so a literal cash-flow
    # argument needs no synthetic dict entry — this function never mutates
    # the shared `values` dict.
    raw_flows: list[object] = []
    for part in parts[1:]:
        part = part.strip()
        table = _expand_table_rows(part, own_tab)
        if table is not None:
            for row in table:
                raw_flows.extend(values.get(ref) for ref in row)
            continue
        # Not a range — a bare literal or a nested function's scalar result
        # is still a single valid cash flow argument.
        try:
            raw_flows.append(float(part))
        except ValueError:
            return None

    if not raw_flows:
        return None

    total = Decimal(0)
    rate_decimal = Decimal(str(rate))
    period = 0
    for value in raw_flows:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            # Text/blank/error: skipped entirely, not zero-filled — the next
            # cash flow keeps its own period rather than shifting into this
            # one's slot.
            continue
        period += 1
        try:
            discount = (Decimal(1) + rate_decimal) ** period
            total += Decimal(str(value)) / discount
        except (ArithmeticError, ValueError):
            return None
    return float(total)


# Dispatch table. Every key here must also be a key in
# core/formula_catalogue.py's FUNCTION_ARG_SPECS (and vice versa) — enforced
# by tests/test_reconciliation.py's catalogue/evaluator parity test, not by
# convention. Adding a function to one without the other in the same change
# is exactly the drift this table is designed to make impossible to ship
# unnoticed.
_EVALUATORS = {
    "SUM": _sum_evaluator,
    "ABS": _abs_evaluator,
    "INT": _int_evaluator,
    "ROUND": _round_evaluator,
    "ROUNDUP": _roundup_evaluator,
    "ROUNDDOWN": _rounddown_evaluator,
    "CEILING": _ceiling_evaluator,
    "FLOOR": _floor_evaluator,
    "SUMIF": _sumif_evaluator,
    "SUMIFS": _sumifs_evaluator,
    "COUNTIF": _countif_evaluator,
    "COUNTIFS": _countifs_evaluator,
    "AVERAGEIF": _averageif_evaluator,
    "AVERAGEIFS": _averageifs_evaluator,
    "MINIFS": _minifs_evaluator,
    "MAXIFS": _maxifs_evaluator,
    "IF": _if_evaluator,
    "VLOOKUP": _vlookup_evaluator,
    "MATCH": _match_evaluator,
    "INDEX": _index_evaluator,
    "XLOOKUP": _xlookup_evaluator,
    "AND": _and_evaluator,
    "OR": _or_evaluator,
    "SUMPRODUCT": _sumproduct_evaluator,
    "NPV": _npv_evaluator,
    "CHOOSE": _choose_evaluator,
}


def _evaluate(
    formula: str, own_ref: str, values: dict, warnings: list[str]
) -> Optional[float | bool]:
    """Compute a supported formula from its already-resolved dependencies.

    Function calls are unwrapped innermost-out: `_INNERMOST_CALL_PATTERN`
    only matches a call whose argument text contains no further parentheses,
    so `SUM(ABS(C1),C2)` resolves `ABS(C1)` to a literal first, then `SUM(...)`
    sees only literals and cell references — never nested function syntax.
    """
    own_tab = own_ref.split("!", 1)[0]
    normalized_formula = _normalize_formula_tokens(formula)
    expr = normalized_formula[1:] if normalized_formula.startswith("=") else normalized_formula

    for _ in range(_MAX_FUNCTION_NESTING):
        match = _INNERMOST_CALL_PATTERN.search(expr)
        if match is None:
            break
        name = match.group(1).upper()
        evaluator = _EVALUATORS.get(name)
        if evaluator is None:
            # _unsupported_reason() should have screened this out already.
            # Fail closed rather than guess at a function we can't compute.
            return None
        try:
            value = evaluator(match.group(2), own_tab, values, warnings, own_ref)
        except _UnresolvableReference:
            return None
        if value is None:
            return None
        expr = f"{expr[:match.start()]}{value!r}{expr[match.end():]}"
    else:
        # Nesting ceiling reached without exhausting every call — fail closed
        # rather than return a partially-unwrapped result.
        return None

    if expr.strip().upper() == "TRUE":
        return True
    if expr.strip().upper() == "FALSE":
        return False

    try:
        return _resolve_and_eval_expr(expr, own_tab, values)
    except _UnresolvableReference:
        return None


def _references_in(argument: str, own_tab: str) -> list[str]:
    """Fully-qualified cell keys for one SUM argument, ranges expanded."""
    keys: list[str] = []
    for quoted, plain, start, end in _CELL_REF_PATTERN.findall(argument):
        tab = quoted or plain or own_tab
        if not end:
            keys.append(f"{tab}!{_normalize(start)}")
            continue
        expanded = _expand_range(_normalize(start), _normalize(end)) or []
        keys.extend(f"{tab}!{cell}" for cell in expanded)
    return keys


def _safe_eval_arithmetic(expr: str) -> Optional[float]:
    """Evaluate arithmetic only, using decimal operations throughout.

    Excel-style rounding cannot repair binary-float noise introduced before a
    ROUND call.  Converting each resolved scalar to Decimal before applying
    operators preserves the workbook's displayed decimal inputs through the
    whole arithmetic expression, then converts only the final result to float
    for the existing model contract.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def _eval(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            return Decimal(str(node.value))
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    try:
        return float(_eval(tree))
    except (ValueError, ArithmeticError, TypeError):
        return None


# ---------------------------------------------------------------------------
# delta, labels, similarity
# ---------------------------------------------------------------------------


def calculate_delta(
    source_value: Optional[float], target_value: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    """Symmetric and zero-safe.

    Symmetric: the denominator is the larger magnitude, so swapping the two
    arguments cannot change the percentage. Dividing by the source alone would
    make the same pair of numbers disagree by different amounts depending on
    which side of the comparison each happened to land.
    """
    if source_value is None or target_value is None:
        return None, None
    delta = abs(source_value - target_value)
    denominator = max(abs(source_value), abs(target_value))
    delta_pct = 0.0 if denominator < 1e-9 else delta / denominator
    return delta, delta_pct


# Backwards-compatible alias for the Step 7 tests and any saved local imports.
_delta = calculate_delta


def _as_number(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _derive_label(output_ref: str, cells: dict[str, CellRecord]) -> str:
    """A human-readable name for an output, taken from the cell to its left.

    Falls back to the cell reference. A wrong label is visible to the reviewer;
    it never affects a number.
    """
    tab, cell_ref = output_ref.split("!", 1)
    try:
        column_letters, row = coordinate_from_string(cell_ref)
    except (ValueError, TypeError):
        return output_ref

    column_index = column_index_from_string(column_letters)
    if column_index > 1:
        left = cells.get(f"{tab}!{get_column_letter(column_index - 1)}{row}")
        if left is not None and isinstance(left.cached_value, str) and left.formula is None:
            label = left.cached_value.strip().rstrip(":").strip()
            if label:
                return label
    return output_ref


def _label_similarity(reference_label: str, candidate_label: str) -> float:
    """Character similarity, blended with an acronym check.

    Plain similarity fails badly on acronyms — "NPR Total" against "Net premium
    reserves" scores about 30% — and acronyms are ordinary in ledger extracts.
    """
    return max(
        fuzz.WRatio(reference_label, candidate_label),
        _acronym_score(reference_label, candidate_label),
    )


def _acronym_score(label_a: str, label_b: str) -> float:
    initials_a, initials_b = _initials(label_a), _initials(label_b)
    best = 0.0
    for word in re.findall(r"[A-Za-z]+", label_b):
        if len(word) >= 2:
            best = max(best, fuzz.ratio(initials_a, word.upper()))
    for word in re.findall(r"[A-Za-z]+", label_a):
        if len(word) >= 2:
            best = max(best, fuzz.ratio(initials_b, word.upper()))
    return best


def _initials(text: str) -> str:
    return "".join(word[0].upper() for word in re.findall(r"[A-Za-z]+", text) if word)
