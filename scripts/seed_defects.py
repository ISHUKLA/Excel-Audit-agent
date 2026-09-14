#!/usr/bin/env python3
"""Seed six auditable defects while preserving the clean fixture unchanged.

Formula XML is edited directly so the clean workbook's recalculated caches are
retained as evidence.  Accounting-population and context defects are seeded in
a separate defective reference extract because those facts do not belong in an
Excel formula or in ``AnomalyFinding``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_ifrs17_workbook import (
    BASIS,
    ENTITY,
    PERIOD,
    independently_calculated_totals,
    synthetic_cohorts,
)

FIXTURES = ROOT / "tests" / "fixtures"
CLEAN = FIXTURES / "ifrs17_workbook_clean.xlsx"
DEFECTIVE = FIXTURES / "ifrs17_workbook_defective.xlsx"
CLEAN_REFERENCE = FIXTURES / "accounting_reference.csv"
DEFECTIVE_REFERENCE = FIXTURES / "accounting_reference_defective.csv"
EXPECTED_DEFECTS = FIXTURES / "expected_defects.json"

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": DOC_REL_NS, "pr": PACKAGE_REL_NS}
ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", DOC_REL_NS)


def _read_archive(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _sheet_path(entries: dict[str, bytes], sheet_name: str) -> str:
    workbook = ET.fromstring(entries["xl/workbook.xml"])
    sheet = next(
        item
        for item in workbook.findall("m:sheets/m:sheet", NS)
        if item.get("name") == sheet_name
    )
    relationship_id = sheet.get(f"{{{DOC_REL_NS}}}id")
    relationships = ET.fromstring(entries["xl/_rels/workbook.xml.rels"])
    relationship = next(
        item
        for item in relationships.findall("pr:Relationship", NS)
        if item.get("Id") == relationship_id
    )
    target = relationship.get("Target", "")
    if target.startswith("/xl/"):
        return target.lstrip("/")
    return "xl/" + target.lstrip("/")


def _sheet_root(entries: dict[str, bytes], sheet_name: str) -> tuple[str, ET.Element]:
    path = _sheet_path(entries, sheet_name)
    return path, ET.fromstring(entries[path])


def _find_cell(root: ET.Element, cell_ref: str) -> ET.Element:
    cell = root.find(f".//m:c[@r='{cell_ref}']", NS)
    if cell is None:
        raise RuntimeError(f"cannot seed defect: cell {cell_ref} does not exist")
    return cell


def _replace_formula(
    entries: dict[str, bytes], sheet_name: str, cell_ref: str, formula: str
) -> None:
    path, root = _sheet_root(entries, sheet_name)
    cell = _find_cell(root, cell_ref)
    formula_node = cell.find("m:f", NS)
    if formula_node is None:
        raise RuntimeError(f"cannot seed defect: {sheet_name}!{cell_ref} has no formula")
    formula_node.text = formula.removeprefix("=")
    entries[path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _inline_cell(cell_ref: str, value: str, style: str | None = None) -> ET.Element:
    attributes = {"r": cell_ref, "t": "inlineStr"}
    if style is not None:
        attributes["s"] = style
    cell = ET.Element(f"{{{MAIN_NS}}}c", attributes)
    inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
    text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
    text.text = value
    return cell


def _formula_cell(
    cell_ref: str, formula: str, cached_value: float, style: str | None = None
) -> ET.Element:
    attributes = {"r": cell_ref}
    if style is not None:
        attributes["s"] = style
    cell = ET.Element(f"{{{MAIN_NS}}}c", attributes)
    formula_node = ET.SubElement(cell, f"{{{MAIN_NS}}}f")
    formula_node.text = formula.removeprefix("=")
    value_node = ET.SubElement(cell, f"{{{MAIN_NS}}}v")
    value_node.text = format(cached_value, ".15g")
    return cell


def _append_output_row(
    entries: dict[str, bytes], cached_expense_output: float
) -> None:
    path, root = _sheet_root(entries, "AccountingBridge")
    sheet_data = root.find("m:sheetData", NS)
    if sheet_data is None:
        raise RuntimeError("AccountingBridge sheetData is missing")
    row9 = root.find(".//m:row[@r='9']", NS)
    styles = {
        column: (_find_cell(root, f"{column}9").get("s")) for column in ("A", "B", "C")
    }
    row10 = ET.Element(f"{{{MAIN_NS}}}row", {"r": "10"})
    row10.append(_inline_cell("A10", "SYN-2150", styles["A"]))
    row10.append(_inline_cell("B10", "Expense movement output", styles["B"]))
    row10.append(
        _formula_cell(
            "C10",
            "=-SUM(ReserveRollforward!D4:D603)",
            cached_expense_output,
            styles["C"],
        )
    )
    if row9 is None:
        sheet_data.append(row10)
    else:
        rows = list(sheet_data)
        sheet_data.insert(rows.index(row9) + 1, row10)
    dimension = root.find("m:dimension", NS)
    if dimension is not None:
        dimension.set("ref", "A1:C10")
    entries[path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


# D1: A business assumption is embedded in the formula rather than referenced.
def seed_d1_hardcoded_discount_rate(entries: dict[str, bytes]) -> None:
    _replace_formula(entries, "ReserveRollforward", "E4", "=ROUND(B4*0.035,2)")


# D2: The aggregation total visibly skips the middle product row.
def seed_d2_omitted_aggregation_row(entries: dict[str, bytes]) -> None:
    _replace_formula(entries, "Aggregation", "J8", "=SUM(J4:J4,J6:J6)")


# D3: The designated output contains an unsupported OFFSET call returning zero.
def seed_d3_unsupported_formula(entries: dict[str, bytes]) -> None:
    _replace_formula(
        entries,
        "AccountingBridge",
        "C9",
        "=SUM(C4:C8)+OFFSET(Controls!B4,0,0)",
    )


# D5: A second designated Python output has no accounting-reference counterpart.
def seed_d5_unmapped_python_output(entries: dict[str, bytes]) -> None:
    totals = independently_calculated_totals(synthetic_cohorts())
    _append_output_row(entries, cached_expense_output=float(-totals["expense_movement"]))


# D4 and D6 belong to external evidence: add an unmatched line and change context.
def seed_d4_and_d6_reference_evidence() -> None:
    with CLEAN_REFERENCE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise RuntimeError("clean accounting reference must contain exactly one line")

    primary = dict(rows[0])
    primary["currency"] = "GBP"
    unmatched_amount = 125_000.0
    signed_primary = (
        float(primary["amount"])
        if primary["debit_credit"] == "debit"
        else -float(primary["amount"])
    )
    control_total = signed_primary + unmatched_amount
    primary["control_total"] = str(control_total)
    unmatched = {
        "account_number": "SYN-2998",
        "label": "Policyholder tax payable",
        "amount": str(unmatched_amount),
        "debit_credit": "debit",
        "entity": ENTITY,
        "period": PERIOD,
        "currency": "GBP",
        "ledger_source": "Synthetic Q4 2025 trial balance",
        "basis": BASIS,
        "evidence_reference": "Synthetic defective reference extract row 3",
        "control_total": str(control_total),
    }
    with DEFECTIVE_REFERENCE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(primary), lineterminator="\n")
        writer.writeheader()
        writer.writerow(primary)
        writer.writerow(unmatched)


def main() -> None:
    clean_hash_before = hashlib.sha256(CLEAN.read_bytes()).hexdigest()
    entries = _read_archive(CLEAN)
    seed_d1_hardcoded_discount_rate(entries)
    seed_d2_omitted_aggregation_row(entries)
    seed_d3_unsupported_formula(entries)
    seed_d5_unmapped_python_output(entries)
    with zipfile.ZipFile(DEFECTIVE, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    seed_d4_and_d6_reference_evidence()

    clean_hash_after = hashlib.sha256(CLEAN.read_bytes()).hexdigest()
    if clean_hash_before != clean_hash_after:
        raise RuntimeError("defect seeding modified the clean workbook")

    expected = {
        "schema_version": 1,
        "synthetic_data": True,
        "clean_workbook_sha256": clean_hash_before,
        "defective_workbook_sha256": hashlib.sha256(DEFECTIVE.read_bytes()).hexdigest(),
        "defective_reference_sha256": hashlib.sha256(DEFECTIVE_REFERENCE.read_bytes()).hexdigest(),
        "workbook_context": {"entity": ENTITY, "period": PERIOD, "currency": "EUR"},
        "reference_context": {"entity": ENTITY, "period": PERIOD, "currency": "GBP"},
        "authoritative_outputs": ["AccountingBridge!C9", "AccountingBridge!C10"],
        "defects": {
            "D1": {
                "defect_type": "hardcoded_assumption",
                "evidence_kind": "anomaly_finding",
                "expected_finding_type": "hardcoded_literal",
                "cell_address": "ReserveRollforward!E4",
                "expected_severity": "warning",
                "expected_description_contains": "Hardcoded literal 0.035",
            },
            "D2": {
                "defect_type": "omitted_rows",
                "evidence_kind": "anomaly_finding",
                "expected_finding_type": "omitted_sum_rows",
                "cell_address": "Aggregation!J8",
                "expected_severity": "warning",
                "expected_description_contains": "SUM formula skips rows: J5",
            },
            "D3": {
                "defect_type": "unsupported_formula",
                "evidence_kind": "internal_reconciliation",
                "cell_address": "AccountingBridge!C9",
                "expected_completeness": "partial",
                "expected_verdict": "incomplete",
                "expected_unsupported_contains": "OFFSET",
            },
            "D4": {
                "defect_type": "unmatched_accounting_line",
                "evidence_kind": "external_completeness",
                "reference_line_id": "REF-0002",
                "reference_label": "Policyholder tax payable",
                "expected_status": "unmatched_reference_item",
            },
            "D5": {
                "defect_type": "unmapped_python_output",
                "evidence_kind": "external_completeness",
                "cell_address": "AccountingBridge!C10",
                "expected_status": "unmapped_python_output",
            },
            "D6": {
                "defect_type": "context_mismatch",
                "evidence_kind": "context_match",
                "workbook_currency": "EUR",
                "reference_currency": "GBP",
                "expected_status": "mismatch",
            },
        },
    }
    EXPECTED_DEFECTS.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    print(f"Clean workbook unchanged: {clean_hash_before}")
    print(f"Defective workbook: {expected['defective_workbook_sha256']}")
    print(f"Defective reference: {expected['defective_reference_sha256']}")
    print("Seeded defects: " + ", ".join(expected["defects"]))


if __name__ == "__main__":
    main()
