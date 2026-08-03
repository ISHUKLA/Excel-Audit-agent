"""Parses the uploaded Excel file into structured cell, formula, and sheet data."""

import re
import zipfile

import networkx as nx
import openpyxl
from openpyxl.worksheet.formula import ArrayFormula

from core.models import ParsedFile

_TAB_REF_PATTERN = re.compile(r"'([^']+)'!|([A-Za-z_][A-Za-z0-9_.]*)!")
_NUMBER_LIKE_PATTERN = re.compile(r"^-?[\d,]+\.?\d*%?$")
_ERROR_VALUES = {"#REF!", "#DIV/0!", "#NAME?", "#VALUE!", "#N/A", "#NULL!", "#NUM!"}


def parse_workbook(path: str) -> ParsedFile:
    warnings: list[str] = []

    wb_formulas = openpyxl.load_workbook(path, data_only=False)
    wb_values = openpyxl.load_workbook(path, data_only=True)

    raw_tab_names = list(wb_formulas.sheetnames)
    tab_names, dedupe_warnings = _dedupe_tab_keys(raw_tab_names)
    name_to_key = dict(zip(raw_tab_names, tab_names))
    warnings.extend(dedupe_warnings)
    warnings.extend(_check_sounds_like_duplicates(raw_tab_names))

    cells: dict[str, object] = {}
    cached_values: dict[str, object] = {}
    external_links: list[str] = []
    named_ranges: dict[str, str] = {
        f"workbook::{name}": str(defn.value) for name, defn in wb_formulas.defined_names.items()
    }
    graph = nx.DiGraph()
    graph.add_nodes_from(tab_names)

    for original_name, tab_key in zip(raw_tab_names, tab_names):
        ws_formulas = wb_formulas[original_name]
        ws_values = wb_values[original_name]

        for name, defn in ws_formulas.defined_names.items():
            named_ranges[f"{tab_key}::{name}"] = str(defn.value)

        for merged_range in ws_formulas.merged_cells.ranges:
            warnings.append(f"{tab_key}: merged cell range {merged_range}")

        max_row = ws_formulas.max_row or 0
        max_col = ws_formulas.max_column or 0
        if max_row == 0 or max_col == 0:
            continue  # blank tab: recorded in tab_names with zero cells

        for row in ws_formulas.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col):
            for cell in row:
                raw_formula_value = cell.value
                cached_value = ws_values[cell.coordinate].value

                if raw_formula_value is None and cached_value is None:
                    continue

                key = f"{tab_key}!{cell.coordinate}"
                cached_values[key] = cached_value

                if isinstance(raw_formula_value, ArrayFormula):
                    formula_text = raw_formula_value.text
                    warnings.append(f"{key}: array formula")
                elif isinstance(raw_formula_value, str) and raw_formula_value.startswith("{="):
                    formula_text = raw_formula_value
                    warnings.append(f"{key}: array formula")
                elif isinstance(raw_formula_value, str) and raw_formula_value.startswith("="):
                    formula_text = raw_formula_value
                else:
                    formula_text = None

                if formula_text is not None:
                    cells[key] = formula_text

                    if isinstance(cached_value, str) and cached_value.startswith("#"):
                        warnings.append(f"{key}: formula error {cached_value}")

                    if "[" in formula_text and ".xls" in formula_text:
                        external_links.append(formula_text)

                    for ref_name in _extract_tab_references(formula_text):
                        target_key = name_to_key.get(ref_name)
                        if target_key and target_key != tab_key:
                            graph.add_edge(tab_key, target_key)
                else:
                    stored_value = raw_formula_value if raw_formula_value is not None else cached_value
                    if isinstance(stored_value, str) and stored_value in _ERROR_VALUES:
                        warnings.append(f"{key}: formula error {stored_value}")
                    elif isinstance(stored_value, str) and _looks_like_number(stored_value):
                        warnings.append(f"{key}: number stored as text ({stored_value!r})")
                    cells[key] = stored_value

    return ParsedFile(
        tab_names=tab_names,
        cells=cells,
        cached_values=cached_values,
        named_ranges=named_ranges,
        external_links=external_links,
        has_vba=_detect_vba(path),
        dependency_graph=nx.to_dict_of_lists(graph),
        warnings=warnings,
    )


def _dedupe_tab_keys(raw_names: list[str]) -> tuple[list[str], list[str]]:
    keys: list[str] = []
    seen: dict[str, int] = {}
    warnings: list[str] = []
    for name in raw_names:
        if name not in seen:
            seen[name] = 0
            keys.append(name)
        else:
            seen[name] += 1
            new_key = f"{name}_{seen[name]}"
            warnings.append(f"duplicate tab name '{name}' renamed to '{new_key}' for cell-key purposes")
            keys.append(new_key)
    return keys, warnings


def _check_sounds_like_duplicates(raw_names: list[str]) -> list[str]:
    warnings: list[str] = []
    normalized_seen: dict[str, str] = {}
    for name in raw_names:
        normalized = name.strip().lower()
        first_seen = normalized_seen.get(normalized)
        if first_seen is not None and first_seen != name:
            warnings.append(f"tab names '{first_seen}' and '{name}' look like duplicates")
        else:
            normalized_seen.setdefault(normalized, name)
    return warnings


def _extract_tab_references(formula: str) -> set[str]:
    refs = set()
    for quoted, unquoted in _TAB_REF_PATTERN.findall(formula):
        name = quoted or unquoted
        if name:
            refs.add(name)
    return refs


def _looks_like_number(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    return bool(_NUMBER_LIKE_PATTERN.match(stripped))


def _detect_vba(path: str) -> bool:
    with zipfile.ZipFile(path) as archive:
        return "xl/vbaProject.bin" in archive.namelist()
