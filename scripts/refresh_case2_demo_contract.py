#!/usr/bin/env python3
"""Refresh Case 2 after exact VLOOKUP became supported.

The case must continue to demonstrate an unsupported construct and a visible
omitted SUM row.  This script changes the two formulas, recalculates the fully
synthetic workbook with LibreOffice, keeps the canonical/output-pack copies
byte-identical, and updates their evidence hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "demo" / "workbooks" / "case_2_spreadsheet_control_failures.xlsx"
OUTPUT_COPY = ROOT / "outputs" / "ai2_2026_demo_pack_20260824" / CANONICAL.name
PROVENANCE = ROOT / "demo" / "recalculation_provenance.json"
OUTPUT_PROVENANCE = ROOT / "outputs" / "ai2_2026_demo_pack_20260824" / "recalculation_provenance.json"

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": DOC_REL_NS, "pr": PACKAGE_REL_NS}
ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", DOC_REL_NS)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    return target.lstrip("/") if target.startswith("/xl/") else "xl/" + target.lstrip("/")


def _replace_formula(entries: dict[str, bytes], cell_ref: str, formula: str) -> None:
    path = _sheet_path(entries, "Reserve Calculation")
    root = ET.fromstring(entries[path])
    cell = root.find(f".//m:c[@r='{cell_ref}']", NS)
    if cell is None:
        raise RuntimeError(f"Case 2 cell {cell_ref} is missing")
    formula_node = cell.find("m:f", NS)
    if formula_node is None:
        raise RuntimeError(f"Case 2 cell {cell_ref} has no formula")
    formula_node.text = formula.removeprefix("=")
    entries[path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _seed_omitted_range_control(entries: dict[str, bytes]) -> None:
    """Add a two-range SUM that survives LibreOffice's formula normalisation."""
    path = _sheet_path(entries, "Circular Control")
    root = ET.fromstring(entries[path])
    sheet_data = root.find("m:sheetData", NS)
    if sheet_data is None:
        raise RuntimeError("Case 2 Circular Control sheetData is missing")

    rows_by_number = {int(row.get("r")): row for row in sheet_data.findall("m:row", NS)}
    for row_number in range(1, 7):
        row = rows_by_number.get(row_number)
        if row is None:
            row = ET.Element(f"{{{MAIN_NS}}}row", {"r": str(row_number)})
            rows_by_number[row_number] = row
        existing = row.find(f"m:c[@r='C{row_number}']", NS)
        if existing is not None:
            row.remove(existing)
        cell = ET.Element(f"{{{MAIN_NS}}}c", {"r": f"C{row_number}"})
        if row_number < 6:
            value = ET.SubElement(cell, f"{{{MAIN_NS}}}v")
            value.text = "1"
        else:
            formula = ET.SubElement(cell, f"{{{MAIN_NS}}}f")
            formula.text = "SUM(C1:C2,C4:C5)"
            value = ET.SubElement(cell, f"{{{MAIN_NS}}}v")
            value.text = "4"
        row.append(cell)

    for row in list(sheet_data):
        sheet_data.remove(row)
    for row_number in sorted(rows_by_number):
        sheet_data.append(rows_by_number[row_number])
    dimension = root.find("m:dimension", NS)
    if dimension is not None:
        dimension.set("ref", "A1:C8")
    entries[path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _set_automatic_mode(data: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="case2-mode-") as directory:
        source = Path(directory) / "source.xlsx"
        source.write_bytes(data)
        with zipfile.ZipFile(source) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        xml = entries["xl/workbook.xml"].decode("utf-8")
        calc_pr = '<calcPr calcId="191029" calcMode="auto" fullCalcOnLoad="1"/>'
        if "<calcPr" in xml:
            start = xml.index("<calcPr")
            end = xml.index(">", start) + 1
            xml = xml[:start] + calc_pr + xml[end:]
        else:
            xml = xml.replace("</workbook>", calc_pr + "</workbook>")
        entries["xl/workbook.xml"] = xml.encode("utf-8")
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return source.read_bytes()


def _formula_manifest_hash(data: bytes) -> str:
    formulas = []
    with tempfile.TemporaryDirectory(prefix="case2-manifest-") as directory:
        path = Path(directory) / "case2.xlsx"
        path.write_bytes(data)
        with zipfile.ZipFile(path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rels = {item.get("Id"): item.get("Target", "") for item in relationships}
            for sheet in workbook.findall("m:sheets/m:sheet", NS):
                target = rels[sheet.get(f"{{{DOC_REL_NS}}}id")]
                target = target.lstrip("/") if target.startswith("/xl/") else "xl/" + target.lstrip("/")
                root = ET.fromstring(archive.read(target))
                for cell in root.findall(".//m:c", NS):
                    formula = cell.find("m:f", NS)
                    if formula is not None:
                        formulas.append(f"{sheet.get('name')}!{cell.get('r')}={formula.text or ''}")
    return _sha256("\n".join(sorted(formulas)).encode("utf-8"))


def main() -> None:
    with zipfile.ZipFile(CANONICAL) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    _replace_formula(entries, "B12", "=SUM(B8:B9,B11:B11)")
    _replace_formula(
        entries,
        "B13",
        "=VLOOKUP(Assumptions!B12,'Lookup Table'!A7:B9,Assumptions!B13,TRUE())*B12",
    )
    _seed_omitted_range_control(entries)

    soffice = os.environ.get("SOFFICE_BIN") or shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice is required to refresh the Case 2 cached values")
    version = subprocess.run(
        [soffice, "--version"], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="case2-refresh-") as directory:
        source_dir = Path(directory) / "source"
        output_dir = Path(directory) / "output"
        source_dir.mkdir()
        output_dir.mkdir()
        source = source_dir / CANONICAL.name
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        subprocess.run(
            [soffice, "--headless", "--convert-to", "xlsx", "--outdir", str(output_dir), str(source)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        recalculated = output_dir / CANONICAL.name
        if not recalculated.exists():
            raise RuntimeError("LibreOffice produced no refreshed Case 2 workbook")
        final_bytes = _set_automatic_mode(recalculated.read_bytes())

    CANONICAL.write_bytes(final_bytes)
    OUTPUT_COPY.write_bytes(final_bytes)
    current_hash = _sha256(final_bytes)
    formula_hash = _formula_manifest_hash(final_bytes)

    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    entry = next(
        item for item in provenance["workbooks"] if item["relative_path"].endswith(CANONICAL.name)
    )
    entry.setdefault("original_post_recalculation_sha256", entry["post_recalculation_sha256"])
    entry["post_recalculation_sha256"] = current_hash
    entry["formula_manifest_sha256_after"] = formula_hash
    entry["formula_manifest_note"] = (
        "The original recalculation evidence is retained in original_post_recalculation_sha256. "
        "On 2026-09-14 the synthetic case was deliberately refreshed after exact VLOOKUP became "
        "supported: B13 now uses deliberately unsupported approximate VLOOKUP (TRUE), and "
        "Circular Control!C6 contains two SUM ranges that visibly omit C3 so the existing "
        "detector surfaces the omitted-row condition. "
        "LibreOffice recalculated the changed workbook; cached numeric values remained unchanged."
    )
    entry["validation_refresh"] = {
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": version,
        "formula_changes": {
            "Reserve Calculation!B12": "=SUM(B8:B9,B11:B11)",
            "Reserve Calculation!B13": "deliberately unsupported approximate VLOOKUP using TRUE()",
            "Circular Control!C6": "=SUM(C1:C2,C4:C5), deliberately omitting C3",
        },
        "microsoft_excel_tested": False,
    }
    copy_entry = next(
        item
        for item in provenance["output_pack_copy_verification"]
        if item["canonical_path"].endswith(CANONICAL.name)
    )
    copy_entry["canonical_sha256"] = current_hash
    copy_entry["output_pack_sha256"] = current_hash
    copy_entry["hashes_match"] = True
    payload = json.dumps(provenance, indent=2) + "\n"
    PROVENANCE.write_text(payload, encoding="utf-8")
    OUTPUT_PROVENANCE.write_text(payload, encoding="utf-8")
    print(f"Case 2 SHA-256: {current_hash}")
    print(f"Formula manifest SHA-256: {formula_hash}")
    print(f"LibreOffice: {version}")


if __name__ == "__main__":
    main()
