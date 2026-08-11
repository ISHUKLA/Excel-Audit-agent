"""Decides what may leave this machine for the Anthropic API, and records what did.

Two things happen here. First, minimization: a tab's payload carries the
structural material needed to describe how the tab computes — formulas, numbers,
short labels — and withholds the things most likely to be confidential, chiefly
long free text and anything touching an external file path.

Second, the manifest. Sending less is only half the control; the other half is
being able to say afterwards exactly what went out. The manifest records cell
references and reasons — never cell content — so it can be chained into the
audit log without becoming a second copy of the workbook.
"""

import json
from datetime import datetime, timezone

from core.models import LLMDataManifestEntry, ParsedFile

# Text at or above this length is more likely to be commentary — a note naming a
# person, an account, a counterparty — than a structural label like "Taux
# technique". Withheld by default; a caller who needs it must change this policy
# deliberately rather than by accident.
_FREE_TEXT_THRESHOLD = 40

_REASON_FREE_TEXT = "free text over length threshold, possible PII"
_REASON_EXTERNAL_LINK = "external link path, not sent"
_REASON_OUT_OF_SCOPE = "not reachable from this tab or its designated outputs, not sent"
_REASON_TYPE_NOT_ALLOWED = "cell type not allowed by minimization policy, not sent"


def minimize_for_llm(
    tab_name: str,
    parsed_file: ParsedFile,
    authoritative_outputs: list[str],
) -> tuple[dict, LLMDataManifestEntry]:
    """Build one tab's payload and the manifest of what was withheld.

    Returns (payload, manifest). The manifest is kept whatever happens next — if
    the call fails, the record of what was sent still matters.
    """
    in_scope = _in_scope_cells(tab_name, parsed_file, authoritative_outputs)
    external_link_formulas = set(parsed_file.external_links)

    included: list[str] = []
    excluded: list[str] = []
    exclusion_reasons: dict[str, str] = {}
    cells_payload: dict[str, dict] = {}

    for cell_ref, record in sorted(parsed_file.cells.items()):
        if cell_ref not in in_scope:
            excluded.append(cell_ref)
            exclusion_reasons[cell_ref] = _REASON_OUT_OF_SCOPE
            continue

        if _is_external(record, external_link_formulas):
            excluded.append(cell_ref)
            exclusion_reasons[cell_ref] = _REASON_EXTERNAL_LINK
            continue

        if _is_long_free_text(record):
            excluded.append(cell_ref)
            exclusion_reasons[cell_ref] = _REASON_FREE_TEXT
            continue

        if not _is_allowed(record):
            excluded.append(cell_ref)
            exclusion_reasons[cell_ref] = _REASON_TYPE_NOT_ALLOWED
            continue

        included.append(cell_ref)
        cells_payload[cell_ref] = _cell_payload(record)

    payload = {
        "tab_name": tab_name,
        "cells": cells_payload,
        "named_ranges": _named_ranges_for_tab(tab_name, parsed_file),
        "authoritative_outputs": [
            ref for ref in authoritative_outputs if ref.startswith(f"{tab_name}!")
        ],
        "withheld_cell_count": len(excluded),
    }

    manifest = LLMDataManifestEntry(
        tab_name=tab_name,
        cell_refs_included=included,
        cell_refs_excluded=excluded,
        exclusion_reasons=exclusion_reasons,
        sent_at=datetime.now(timezone.utc),
        # The payload's size, not the whole prompt's — the system prompt is fixed
        # and contains no workbook content.
        prompt_char_count=len(json.dumps(payload, sort_keys=True, separators=(",", ":"))),
    )
    return payload, manifest


def _cell_payload(record) -> dict:
    """What the model sees for one cell. Formula and value, nothing more."""
    return {
        "formula": record.formula,
        "value": record.cached_value,
        "data_type": record.data_type,
        "is_stale": record.is_stale,
    }


def _in_scope_cells(
    tab_name: str, parsed_file: ParsedFile, authoritative_outputs: list[str]
) -> set[str]:
    """Cells this tab's documentation may draw on.

    That means the tab's own cells and what they depend on, plus the derivation
    chains of any designated output that passes through this tab. Everything
    else stays home — documenting one tab is not a reason to send a whole
    workbook.
    """
    graph = parsed_file.cell_dependency_graph
    own_cells = {ref for ref in parsed_file.cells if ref.startswith(f"{tab_name}!")}

    in_scope = _closure(own_cells, graph)

    for output_ref in authoritative_outputs:
        chain = _closure({output_ref}, graph)
        if any(ref.startswith(f"{tab_name}!") for ref in chain):
            in_scope |= chain

    return in_scope


def _closure(roots: set[str], graph: dict) -> set[str]:
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return seen


def _is_external(record, external_link_formulas: set[str]) -> bool:
    """A formula reaching into another workbook.

    The path itself is the risk: it can carry a username, a share name, or a
    directory layout that says more about the organisation than the number does.
    """
    if record.formula is None:
        return False
    if record.formula in external_link_formulas:
        return True
    formula_lower = record.formula.lower()
    return "[" in formula_lower and ".xls" in formula_lower


def _is_long_free_text(record) -> bool:
    """Numbers, blanks, and formula cells go regardless of length.

    Only a non-formula text cell can be withheld on length, and only when it is
    long enough to look like prose rather than a label.
    """
    if record.formula is not None:
        return False
    if record.data_type != "text":
        return False
    return isinstance(record.cached_value, str) and len(record.cached_value) >= _FREE_TEXT_THRESHOLD


def _is_allowed(record) -> bool:
    """Apply the policy as an allowlist; unmentioned cell types stay local."""
    if record.formula is not None:
        return True
    if record.data_type in ("number", "blank"):
        return True
    return (
        record.data_type == "text"
        and isinstance(record.cached_value, str)
        and len(record.cached_value) < _FREE_TEXT_THRESHOLD
    )


def _named_ranges_for_tab(tab_name: str, parsed_file: ParsedFile) -> list[str]:
    names = []
    for scoped_key, definition in parsed_file.named_ranges.items():
        scope, _, name = scoped_key.partition("::")
        stripped = definition.replace("$", "")
        if scope == tab_name or stripped.startswith(f"{tab_name}!") or stripped.startswith(f"'{tab_name}'!"):
            names.append(name)
    return sorted(names)
