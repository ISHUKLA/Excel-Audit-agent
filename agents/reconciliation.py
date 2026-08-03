"""Runs two independent reconciliation passes: Excel-vs-Python (internal) and Python-vs-accounts (external, for the CFO)."""

import ast
import operator
import re

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from rapidfuzz import fuzz

from core.models import FileContext, ParsedFile, ReconciliationLine, ReferenceFigures

_CONFIDENT_THRESHOLD = 85.0
_AMBIGUOUS_THRESHOLD = 60.0
_CLOSE_CANDIDATE_MARGIN = 5.0

_CELL_REF_TOKEN = re.compile(
    r"(?:'([^']+)'!|([A-Za-z_][A-Za-z0-9_. ]*)!)?\$?([A-Za-z]{1,3})\$?(\d{1,7})"
)

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


class _UnresolvableReference(Exception):
    pass


def run_reconciliation(
    parsed_file: ParsedFile,
    file_context: FileContext,
    reference_figures: ReferenceFigures | None = None,
    internal_threshold: float = 0.01,
    external_threshold: float = 0.01,
) -> tuple[list[ReconciliationLine], list[str]]:
    pass1_lines = reconcile_excel_vs_python(parsed_file, file_context, internal_threshold)

    if reference_figures is None:
        return pass1_lines, []

    pass2_lines, unmatched = reconcile_python_vs_accounts(
        pass1_lines, reference_figures, external_threshold
    )
    return pass1_lines + pass2_lines, unmatched


def reconcile_excel_vs_python(
    parsed_file: ParsedFile,
    file_context: FileContext,
    internal_threshold: float = 0.01,
) -> list[ReconciliationLine]:
    output_tab = _identify_output_tab(parsed_file, file_context)
    lines: list[ReconciliationLine] = []

    for key, value in parsed_file.cells.items():
        tab, cell_ref = key.split("!", 1)
        if tab != output_tab or not (isinstance(value, str) and value.startswith("=")):
            continue

        excel_value = parsed_file.cached_values.get(key)
        if not _is_number(excel_value):
            continue  # Excel never cached a value for this formula -- nothing to compare against

        python_value = _reconstruct_formula(value, tab, parsed_file.cached_values)
        if python_value is None:
            continue  # unresolvable reference or unsupported (non +-*/) function -- skip for MVP

        label = _derive_label(tab, cell_ref, parsed_file.cells)
        lines.append(
            _build_line(
                check_type="excel_vs_python",
                label=label,
                source_value=float(excel_value),
                target_value=float(python_value),
                threshold=internal_threshold,
                source_cell=key,
            )
        )

    return lines


def reconcile_python_vs_accounts(
    pass1_lines: list[ReconciliationLine],
    reference_figures: ReferenceFigures,
    external_threshold: float = 0.01,
) -> tuple[list[ReconciliationLine], list[str]]:
    candidates = [line for line in pass1_lines if line.check_type == "excel_vs_python"]

    lines: list[ReconciliationLine] = []
    unmatched: list[str] = []

    for ref_label, accounts_value in reference_figures.line_items.items():
        if not candidates:
            unmatched.append(ref_label)
            continue

        scored = sorted(
            ((_label_similarity(ref_label, c.label), c) for c in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best_candidate = scored[0]

        if best_score < _AMBIGUOUS_THRESHOLD:
            unmatched.append(ref_label)
            continue

        is_ambiguous = best_score < _CONFIDENT_THRESHOLD
        if not is_ambiguous and len(scored) > 1 and best_score - scored[1][0] <= _CLOSE_CANDIDATE_MARGIN:
            is_ambiguous = True

        match_note = None
        if is_ambiguous:
            match_note = (
                f"Ambiguous match ({best_score:.0f}% similarity) between reference label "
                f"'{ref_label}' and computed label '{best_candidate.label}' -- a human should "
                f"confirm this pairing itself, not just the numeric delta."
            )

        lines.append(
            _build_line(
                check_type="python_vs_accounts",
                label=best_candidate.label,
                source_value=best_candidate.target_value,
                target_value=float(accounts_value),
                threshold=external_threshold,
                source_cell=best_candidate.source_cell,
                force_warn=is_ambiguous,
                match_note=match_note,
            )
        )

    return lines, unmatched


def _build_line(
    check_type: str,
    label: str,
    source_value: float,
    target_value: float,
    threshold: float,
    source_cell: str | None,
    force_warn: bool = False,
    match_note: str | None = None,
) -> ReconciliationLine:
    delta_pct = _delta_pct(source_value, target_value)
    delta = abs(source_value - target_value)
    verdict = _classify_verdict(delta_pct, threshold, force_warn)

    return ReconciliationLine(
        check_type=check_type,
        label=label,
        source_value=source_value,
        target_value=target_value,
        delta=delta,
        delta_pct=delta_pct,
        verdict=verdict,
        materiality_threshold=threshold,
        source_cell=source_cell,
        match_note=match_note,
    )


def apply_thresholds(
    lines: list[ReconciliationLine], internal_threshold: float, external_threshold: float
) -> list[ReconciliationLine]:
    """Reclassify each line's verdict against a human-supplied materiality threshold.

    Recomputes only `verdict` and `materiality_threshold` from each line's already-computed
    `delta`/`delta_pct` -- it does not touch label, source_cell, or match_note, and an
    ambiguous match (match_note set) stays forced to "warn" regardless of the new threshold,
    same as when the line was first produced.
    """
    reclassified = []
    for line in lines:
        threshold = internal_threshold if line.check_type == "excel_vs_python" else external_threshold
        verdict = _classify_verdict(line.delta_pct, threshold, force_warn=line.match_note is not None)
        reclassified.append(
            line.model_copy(update={"verdict": verdict, "materiality_threshold": threshold})
        )
    return reclassified


def _delta_pct(source_value: float, target_value: float) -> float:
    delta = abs(source_value - target_value)
    if source_value == 0:
        return 0.0 if delta == 0 else float("inf")
    return delta / abs(source_value)


def _classify_verdict(delta_pct: float, threshold: float, force_warn: bool) -> str:
    if force_warn:
        return "warn"
    if delta_pct < threshold / 10:
        return "pass"
    if delta_pct < threshold:
        return "warn"
    return "block"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _identify_output_tab(parsed_file: ParsedFile, file_context: FileContext) -> str:
    description_lower = file_context.description.lower()

    for tab in parsed_file.tab_names:
        if tab.lower() in description_lower:
            return tab

    description_words = set(re.findall(r"[a-z0-9]+", description_lower))
    best_tab, best_overlap = None, 0
    for tab in parsed_file.tab_names:
        tab_words = set(re.findall(r"[a-z0-9]+", tab.lower()))
        overlap = len(tab_words & description_words)
        if overlap > best_overlap:
            best_tab, best_overlap = tab, overlap

    # No keyword signal at all: fall back to the last tab (common convention for
    # summary/output tabs). A wrong guess here is self-correcting -- a human
    # reviews every produced line at Gate 2/3 regardless.
    return best_tab or parsed_file.tab_names[-1]


def _derive_label(tab: str, cell_ref: str, cells: dict) -> str:
    col_letters, row = coordinate_from_string(cell_ref)
    col_index = column_index_from_string(col_letters)

    if col_index > 1:
        left_key = f"{tab}!{get_column_letter(col_index - 1)}{row}"
        left_value = cells.get(left_key)
        if isinstance(left_value, str) and not left_value.startswith("="):
            label = left_value.strip().rstrip(":").strip()
            if label:
                return label

    return f"{tab}!{cell_ref}"


def _reconstruct_formula(formula: str, own_tab: str, cached_values: dict) -> float | None:
    expr = formula[1:] if formula.startswith("=") else formula

    def _replace(match: re.Match) -> str:
        quoted_tab, unquoted_tab, col, row = match.groups()
        tab = quoted_tab or unquoted_tab or own_tab
        value = cached_values.get(f"{tab}!{col.upper()}{row}")
        if not _is_number(value):
            raise _UnresolvableReference()
        return repr(float(value))

    try:
        substituted = _CELL_REF_TOKEN.sub(_replace, expr)
    except _UnresolvableReference:
        return None

    if re.search(r"[A-Za-z]", substituted):
        return None  # a function name or unresolved reference remains -- unsupported for MVP

    return _safe_eval_arithmetic(substituted)


def _safe_eval_arithmetic(expr: str) -> float | None:
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


def _label_similarity(reference_label: str, candidate_label: str) -> float:
    # Plain character-similarity alone fails on acronyms (e.g. "NPR Total" vs
    # "Net premium reserves" scores ~30%), so it's blended with an
    # initials-vs-word check to catch that common real-world pattern too.
    return max(fuzz.WRatio(reference_label, candidate_label), _acronym_score(reference_label, candidate_label))


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
