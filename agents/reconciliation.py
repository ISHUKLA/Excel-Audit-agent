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
import operator
import re
from typing import Optional

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from rapidfuzz import fuzz

# Single source of truth for how a cell reference is parsed. Imported rather
# than re-declared so the graph the parser built and the substitutions made here
# can never drift apart.
from agents.parser import _CELL_REF_PATTERN, _expand_range, _normalize
from core.accounting import signed_reference_amount
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

# Deliberately small. Anything outside it is reported as unsupported rather than
# approximated — a partially-interpreted VLOOKUP is a wrong number wearing a
# right number's clothes.
_SUPPORTED_FUNCTIONS = {"SUM"}

_FUNCTION_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
_SUM_PATTERN = re.compile(r"SUM\(([^()]*)\)", re.IGNORECASE)
_STRING_LITERAL_PATTERN = re.compile(r'"[^"]*"')

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
            return step

        step.formula = record.formula
        _note_freshness(ref, record)

        if record.formula is None:
            value = _as_number(record.cached_value)
            if value is None and record.cached_value is not None:
                step.is_supported = False
                unsupported_elements.append(
                    f"{ref} holds a non-numeric value ({record.cached_value!r}) used in arithmetic "
                    f"(unsupported)"
                )
                return step
            if value is None:
                warnings.append(f"{ref}: blank cell — treated as 0, per Excel's convention")
            step.resolved_value = value if value is not None else 0.0
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

        values = {dep: child.resolved_value for dep, child in resolved_dependencies.items()}
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


def _unsupported_reason(formula: str) -> Optional[str]:
    """Why this formula is outside the catalogue, or None if it is inside it."""
    if formula.startswith("{="):
        return "an array formula (unsupported)"
    if "[" in formula and ".xls" in formula:
        return "a reference to another workbook (unsupported)"
    if _STRING_LITERAL_PATTERN.search(formula):
        return "a text literal in arithmetic (unsupported)"

    functions = {name.upper() for name in _FUNCTION_PATTERN.findall(formula)}
    outside = sorted(functions - _SUPPORTED_FUNCTIONS)
    if outside:
        return f"{', '.join(outside)} (unsupported)"
    return None


def _evaluate(formula: str, own_ref: str, values: dict, warnings: list[str]) -> Optional[float]:
    """Compute a supported formula from its already-resolved dependencies."""
    own_tab = own_ref.split("!", 1)[0]
    expr = formula[1:] if formula.startswith("=") else formula

    def _sum_replacement(match: re.Match) -> str:
        total = 0.0
        for argument in match.group(1).split(","):
            for key in _references_in(argument.strip(), own_tab):
                value = values.get(key)
                if value is None:
                    warnings.append(
                        f"{own_ref}: {key} is blank inside a SUM — treated as 0, per Excel's "
                        f"convention"
                    )
                    value = 0.0
                total += value
        return repr(total)

    expr = _SUM_PATTERN.sub(_sum_replacement, expr)

    def _reference_replacement(match: re.Match) -> str:
        quoted, plain, start, end = match.groups()
        if end:
            raise _UnresolvableReference("a bare range outside SUM")
        tab = quoted or plain or own_tab
        value = values.get(f"{tab}!{_normalize(start)}")
        if value is None:
            raise _UnresolvableReference(f"{tab}!{start}")
        return repr(float(value))

    try:
        substituted = _CELL_REF_PATTERN.sub(_reference_replacement, expr)
    except _UnresolvableReference:
        return None

    if re.search(r"[A-Za-z]", substituted):
        return None

    return _safe_eval_arithmetic(substituted)


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
    """Evaluate arithmetic only — no names, no calls, no attribute access."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    try:
        return _eval(tree)
    except (ValueError, ZeroDivisionError, TypeError):
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
