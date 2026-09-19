#!/usr/bin/env python3
"""Recalculate Case 14 with the old input, then preserve caches while editing OOXML."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.anomaly_detector import detect_anomalies
from agents.parser import parse_workbook
from agents.reconciliation import run_reconciliation
from core.formula_catalogue import SUPPORTED_FUNCTIONS

CASE_DIR = ROOT / "demo" / "final cases" / "case_14_stale_board_pack"
WORKBOOK = CASE_DIR / "workbooks" / "case_14_stale_board_pack.xlsx"
REFERENCE = CASE_DIR / "reference_figures" / "case_14_stale_gl_extract.csv"
EXPECTED = CASE_DIR / "expected_results" / "case_14_stale_board_pack_expected.json"
INVENTORY = CASE_DIR / "formula_inventories" / "case_14_stale_board_pack_formula_inventory.json"
MANIFEST = CASE_DIR / "manifests" / "case_14_stale_board_pack_manifest.json"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
FUNCTION_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_evidence() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--short"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return commit, dirty


def recalculate(source: Path, destination: Path, soffice: str) -> str:
    version = subprocess.run(
        [soffice, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="case14-recalc-") as temp:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "xlsx", "--outdir", temp, str(source)],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        produced = Path(temp) / source.name
        if not produced.exists():
            raise RuntimeError("LibreOffice did not produce the recalculated workbook")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, destination)
    return version


def patch_current_assumption_and_manual_mode(path: Path) -> None:
    """Change Assumptions!C14 to 1.061 without touching formula caches."""
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}

    workbook_root = ET.fromstring(entries["xl/workbook.xml"])
    relationships_root = ET.fromstring(entries["xl/_rels/workbook.xml.rels"])
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rel_targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships_root.findall(f"{{{rel_ns}}}Relationship")
    }
    relationship_id = None
    rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    for sheet in workbook_root.find("m:sheets", NS):
        if sheet.attrib["name"] == "Assumptions":
            relationship_id = sheet.attrib[rel_attr]
            break
    if relationship_id is None:
        raise RuntimeError("Assumptions sheet not found")
    target = rel_targets[relationship_id].lstrip("/")
    sheet_path = target if target.startswith("xl/") else f"xl/{target}"

    sheet_root = ET.fromstring(entries[sheet_path])
    cell = sheet_root.find(".//m:c[@r='C14']", NS)
    if cell is None:
        raise RuntimeError("Assumptions!C14 not found")
    value = cell.find("m:v", NS)
    if value is None:
        value = ET.SubElement(cell, f"{{{NS['m']}}}v")
    value.text = "1.061"
    entries[sheet_path] = ET.tostring(sheet_root, encoding="utf-8", xml_declaration=True)

    calc_pr = workbook_root.find("m:calcPr", NS)
    if calc_pr is None:
        calc_pr = ET.SubElement(workbook_root, f"{{{NS['m']}}}calcPr")
    calc_pr.attrib.update({"calcId": "191029", "calcMode": "manual", "fullCalcOnLoad": "0"})
    entries["xl/workbook.xml"] = ET.tostring(
        workbook_root, encoding="utf-8", xml_declaration=True
    )

    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def formula_inventory(path: Path) -> list[dict[str, object]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=False, read_only=True)
    inventory = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str) or not cell.value.startswith("="):
                    continue
                functions = sorted(
                    name.upper().removeprefix("_XLFN.")
                    for name in FUNCTION_PATTERN.findall(cell.value)
                )
                unsupported = sorted(set(functions) - SUPPORTED_FUNCTIONS)
                inventory.append(
                    {
                        "cell_ref": f"{sheet.title}!{cell.coordinate}",
                        "formula": cell.value,
                        "functions": functions,
                        "supported_by_catalogue": not unsupported,
                        "unsupported_functions": unsupported,
                    }
                )
    return inventory


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: finalize_case_14.py PRE_CHANGE.xlsx")
    source = Path(sys.argv[1]).resolve()
    soffice = os.environ.get("SOFFICE_BIN") or shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice is required to produce real old-input caches")

    engine_version = recalculate(source, WORKBOOK, soffice)
    patch_current_assumption_and_manual_mode(WORKBOOK)

    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    with REFERENCE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "account_number", "label", "amount", "debit_credit",
                "ledger_source", "evidence_reference",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "account_number": "GL-210100",
                    "label": "Opening reserve",
                    "amount": "65000000",
                    "debit_credit": "debit",
                    "ledger_source": "Synthetic Q3 board-pack booking extract",
                    "evidence_reference": "Q3-2026-OPEN",
                },
                {
                    "account_number": "GL-210900",
                    "label": "Closing IBNR",
                    "amount": "41700000",
                    "debit_credit": "debit",
                    "ledger_source": "Synthetic Q3 board-pack booking extract",
                    "evidence_reference": "Q3-2026-CLOSE",
                },
            ]
        )

    parsed = parse_workbook(WORKBOOK.read_bytes())
    outputs = ["Board Summary!B4", "Board Summary!B9"]
    result = run_reconciliation(parsed, outputs)
    lines = {line.label: line for line in result.lines}
    if parsed.workbook_meta.calc_mode != "manual":
        raise RuntimeError("Case 14 workbook is not in manual calculation mode")
    if parsed.cells["Assumptions!C14"].cached_value != 1.061:
        raise RuntimeError("Case 14 current tail factor was not patched")
    opening = lines["Opening reserve"]
    closing = lines["Closing IBNR"]
    if opening.delta != 0 or opening.verdict != "incomplete":
        raise RuntimeError(f"Opening reserve did not demonstrate the zero-delta cap: {opening}")
    if abs(closing.source_value - 41_700_000) > 0.01:
        raise RuntimeError(f"Closing cached value changed: {closing.source_value}")
    if abs(closing.target_value - 51_000_000) > 0.01 or closing.verdict != "incomplete":
        raise RuntimeError(f"Closing reconstruction did not demonstrate the stale cap: {closing}")

    inventory = formula_inventory(WORKBOOK)
    INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    findings = detect_anomalies(parsed)
    expected = {
        "case_id": "case_14_stale_board_pack",
        "case_version": "1.0.0",
        "synthetic_data": True,
        "gate1_context": {
            "entity": "Aurora General Insurance SA",
            "period": "Q3:26",
            "currency": "EUR",
            "basis": "Synthetic general-insurance reserving demonstration",
        },
        "reference_context": {
            "entity": "Aurora General Insurance SA",
            "period": "Q3:26",
            "currency": "EUR",
            "basis": "Synthetic general-insurance reserving demonstration",
        },
        "authoritative_output_cells": outputs,
        "tail_factor_cell": "Assumptions!C14",
        "old_tail_factor": 1.042,
        "current_tail_factor": 1.061,
        "cached_ultimate_claims": 148_200_000,
        "reconstructed_ultimate_claims": 157_500_000,
        "summary_outputs": {
            "opening_reserve": {
                "cell": "Board Summary!B4", "cached_value": 65_000_000,
                "reconstructed_value": 65_000_000, "delta": 0,
            },
            "closing_ibnr": {
                "cell": "Board Summary!B9", "cached_value": 41_700_000,
                "reconstructed_value": 51_000_000, "delta": 9_300_000,
            },
        },
        "expected_internal_verdict": "incomplete",
        "expected_external_verdict": "incomplete",
        "expected_gate_block": 3,
        "acknowledge_incomplete": False,
        "pdf_available_after_gate4": False,
        "workbook_file": str(WORKBOOK.relative_to(ROOT)),
        "workbook_sha256": sha256(WORKBOOK),
        "reference_file": str(REFERENCE.relative_to(ROOT)),
        "reference_sha256": sha256(REFERENCE),
        "reference_control_total": 106_700_000,
        "formula_inventory_file": str(INVENTORY.relative_to(ROOT)),
        "formula_cell_count": len(inventory),
        "supported_formula_count": sum(item["supported_by_catalogue"] for item in inventory),
        "unsupported_formula_count": sum(not item["supported_by_catalogue"] for item in inventory),
        "observed_findings": [
            {
                "severity": item.severity,
                "cell": f"{item.tab}!{item.cell_ref}",
                "description": item.description,
            }
            for item in findings
        ],
        "boundary": (
            "Demonstrates calculation freshness only. It does not opine on the factor, "
            "reserve adequacy, or reserving methodology."
        ),
    }
    EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    EXPECTED.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")

    tool_git_commit, tool_worktree_dirty = git_evidence()
    manifest = {
        "case_id": expected["case_id"],
        "case_version": expected["case_version"],
        "workbook_file": expected["workbook_file"],
        "workbook_sha256": expected["workbook_sha256"],
        "reference_file": expected["reference_file"],
        "reference_sha256": expected["reference_sha256"],
        "expected_results_file": str(EXPECTED.relative_to(ROOT)),
        "expected_results_sha256": sha256(EXPECTED),
        "formula_inventory_file": expected["formula_inventory_file"],
        "formula_inventory_sha256": sha256(INVENTORY),
        "recalculation_engine": "LibreOffice",
        "recalculation_engine_version": engine_version,
        "recalculated_before_assumption_edit_at_utc": datetime.now(timezone.utc).isoformat(),
        "post_recalculation_ooxml_edit": {
            "cell": "Assumptions!C14", "old_value": 1.042, "new_value": 1.061,
            "calculation_mode": "manual", "formula_caches_preserved": True,
        },
        "python_version": platform.python_version(),
        "tool_git_commit": tool_git_commit,
        "tool_worktree_dirty_at_generation": tool_worktree_dirty,
        "formula_cell_count": len(inventory),
        "supported_formula_count": expected["supported_formula_count"],
        "unsupported_formula_count": expected["unsupported_formula_count"],
        "expected_internal_verdict": "incomplete",
        "expected_external_verdict": "incomplete",
        "expected_gate_block": 3,
        "synthetic_data": True,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"workbook": str(WORKBOOK), "sha256": sha256(WORKBOOK)}, indent=2))


if __name__ == "__main__":
    main()
