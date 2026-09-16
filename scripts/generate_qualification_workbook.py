#!/usr/bin/env python3
"""Generate and qualify the real-workbook formula acceptance fixture.

Expected values are declared alongside each synthetic case and written to JSON
before the workbook is saved.  They are never read from formula caches.  The
catalogue import and the coverage assertion make a newly supported function
fail generation until a real workbook case is deliberately added for it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from copy import copy
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.formula_catalogue import SUPPORTED_FUNCTIONS

FIXTURES = ROOT / "tests" / "fixtures"
UNRECALCULATED = FIXTURES / "qualification_workbook_unrecalculated.xlsx"
EXPECTED = FIXTURES / "qualification_expected.json"
MANIFEST = FIXTURES / "qualification_manifest.json"

FUNCTION_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")

NAVY = "17365D"
BLUE = "D9EAF7"
INPUT_YELLOW = "FFF2CC"
LIGHT_GREY = "E7E6E6"
WHITE = "FFFFFF"
RED = "FCE4D6"
GREEN = "E2F0D9"
THIN_GREY = Side(style="thin", color="B7C9D6")


def _case(
    case_id: str,
    formula: str,
    expected_value: float | None,
    expected_status: str = "complete",
    purpose: str = "",
) -> dict:
    called = {match.upper().removeprefix("_XLFN.") for match in FUNCTION_PATTERN.findall(formula)}
    functions = sorted(name for name in called if name in SUPPORTED_FUNCTIONS)
    return {
        "case_id": case_id,
        "formula": formula,
        "expected_value": expected_value,
        "expected_status": expected_status,
        "expected_verdict": "pass" if expected_status == "complete" else "incomplete",
        "functions": functions,
        "purpose": purpose,
    }


def qualification_cases() -> list[dict]:
    """Independently specified expected results for the acceptance fixture."""

    return [
        _case("sum_mixed_range", "=SUM(Inputs!$B$4:$B$8)", 25.0, purpose="Blank/text ignored; zero retained"),
        _case("abs_negative", "=ABS(Inputs!$B$6)", 5.0),
        _case("int_negative", "=INT(-1.2)", -2.0),
        _case("round_positive_half", "=ROUND(2.5,0)", 3.0, purpose="Excel half away from zero"),
        _case("round_negative_half", "=ROUND(-2.5,0)", -3.0, purpose="Excel half away from zero"),
        _case("roundup_negative", "=ROUNDUP(-1.21,1)", -1.3),
        _case("rounddown_negative", "=ROUNDDOWN(-1.29,1)", -1.2),
        _case("ceiling_positive", "=CEILING(2.1,0.5)", 2.5),
        _case("floor_positive", "=FLOOR(2.9,0.5)", 2.5),
        _case("sumif_text", '=SUMIF(Inputs!$A$4:$A$8,"Motor",Inputs!$B$4:$B$8)', 5.0),
        _case(
            "sumifs_text_region",
            '=SUMIFS(Inputs!$B$4:$B$8,Inputs!$A$4:$A$8,"Motor",Inputs!$C$4:$C$8,"North")',
            5.0,
        ),
        _case("countif_text", '=COUNTIF(Inputs!$A$4:$A$8,"Motor")', 3.0),
        _case(
            "countifs_boolean",
            '=COUNTIFS(Inputs!$A$4:$A$8,"Motor",Inputs!$D$4:$D$8,TRUE())',
            3.0,
            purpose="TRUE() boolean call in a criteria function",
        ),
        _case(
            "averageif_blank_zero",
            '=AVERAGEIF(Inputs!$A$4:$A$8,"Motor",Inputs!$B$4:$B$8)',
            5.0 / 3.0,
            purpose="A literal zero contributes to the denominator",
        ),
        _case(
            "averageifs_text_region",
            '=AVERAGEIFS(Inputs!$B$4:$B$8,Inputs!$A$4:$A$8,"Motor",Inputs!$C$4:$C$8,"North")',
            2.5,
        ),
        _case("minifs_text", '=_xlfn.MINIFS(Inputs!$B$4:$B$8,Inputs!$A$4:$A$8,"Motor")', -5.0),
        _case("maxifs_text", '=_xlfn.MAXIFS(Inputs!$B$4:$B$8,Inputs!$A$4:$A$8,"Motor")', 10.0),
        _case("if_true_literal", "=IF(TRUE,11,22)", 11.0),
        _case("if_false_literal", "=IF(FALSE,11,22)", 22.0),
        _case("if_true_call", "=IF(TRUE(),11,22)", 11.0),
        _case("if_false_call", "=IF(FALSE(),11,22)", 22.0),
        _case("vlookup_exact", '=VLOOKUP("Property",Inputs!$E$4:$F$6,2,FALSE())', 1.2),
        _case("match_exact", '=MATCH("Liability",Inputs!$E$4:$E$6,0)', 3.0),
        _case("index_scalar", "=INDEX(Inputs!$F$4:$F$6,2)", 1.2),
        _case(
            "index_match_nested",
            '=INDEX(Inputs!$F$4:$F$6,MATCH("Liability",Inputs!$E$4:$E$6,0))',
            1.3,
        ),
        _case(
            "nested_round_sumif",
            '=ROUND(SUMIF(Inputs!$A$4:$A$8,"Motor",Inputs!$B$4:$B$8),2)',
            5.0,
        ),
        _case("cross_tab_range", "=SUM('Cross Tab'!$B$3:$B$5)", 20.0),
        _case("ordinary_arithmetic", "=(Inputs!$B$4+Inputs!$B$5)*2/3", 20.0),
        _case(
            "vlookup_approximate_unsupported",
            '=VLOOKUP("Property",Inputs!$E$4:$F$6,2,TRUE())',
            None,
            "partial",
            "Approximate lookup must fail closed",
        ),
        _case(
            "match_approximate_unsupported",
            '=MATCH("Property",Inputs!$E$4:$E$6,1)',
            None,
            "partial",
            "Approximate MATCH must fail closed",
        ),
        _case(
            "index_array_unsupported",
            "=INDEX(Inputs!$B$4:$B$8,0)",
            None,
            "partial",
            "Array result must not be silently reduced to a scalar",
        ),
        _case(
            "offset_unsupported",
            "=OFFSET(Inputs!$B$4,1,0)",
            None,
            "partial",
            "Function outside the catalogue",
        ),
        # Group F — AND, OR
        # Note: these return 1 for true / 0 for false in arithmetic context.
        # LibreOffice calculates them correctly, so these do pass through cache.
        _case("and_all_true", "=IF(AND(TRUE,TRUE,TRUE),1,0)", 1.0),
        _case("and_one_false", "=IF(AND(TRUE,FALSE,TRUE),1,0)", 0.0),
        _case("or_one_true", "=IF(OR(FALSE,TRUE,FALSE),1,0)", 1.0),
        _case("or_all_false", "=IF(OR(FALSE,FALSE,FALSE),1,0)", 0.0),
        # Group G — SUMPRODUCT. Uses B4:B6 (10, 20, -5) and F4:F6 (1.1, 1.2, 1.3).
        # Expected: 10*1.1 + 20*1.2 + (-5)*1.3 = 11 + 24 - 6.5 = 28.5.
        # LibreOffice supports SUMPRODUCT, so this will cache correctly.
        _case("sumproduct_2d", "=SUMPRODUCT(Inputs!$B$4:$B$6,Inputs!$F$4:$F$6)", 28.5),
        # Group H — NPV. LibreOffice supports NPV.
        _case(
            "npv_cash_flow",
            "=NPV(0.1,-1000,300,300,300)",
            -230.8585,
            purpose="Excel convention: first flow at period 1, not 0",
        ),
        # Group I — CHOOSE. LibreOffice supports CHOOSE.
        _case("choose_index_1", "=CHOOSE(1,10,20,30)", 10.0),
        _case("choose_index_2", "=CHOOSE(2,10,20,30)", 20.0),
        _case(
            "xlookup_exact",
            '=_xlfn.XLOOKUP("Property",Inputs!$E$4:$E$6,Inputs!$F$4:$F$6,,0,1)',
            1.2,
            purpose="Exact numeric XLOOKUP, recalculated through LibreOffice",
        ),
    ]


def _apply_standard_style(workbook: Workbook) -> None:
    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = "A4"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet["A1"].font = Font(name="Arial", size=14, bold=True, color=NAVY)
        for row in sheet.iter_rows():
            for cell in row:
                font = copy(cell.font)
                font.name = "Arial"
                font.size = 10
                cell.font = font
                cell.alignment = Alignment(vertical="center")


def _style_header(sheet, cell_range: str) -> None:
    for row in sheet[cell_range]:
        for cell in row:
            cell.fill = PatternFill("solid", fgColor=NAVY)
            cell.font = Font(name="Arial", size=10, bold=True, color=WHITE)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(bottom=THIN_GREY)


def generate() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cases = qualification_cases()
    covered = {name for case in cases if case["expected_status"] == "complete" for name in case["functions"]}
    missing = sorted(SUPPORTED_FUNCTIONS - covered)
    if missing:
        raise RuntimeError(f"qualification cases do not cover supported functions: {missing}")

    workbook = Workbook()
    inputs = workbook.active
    inputs.title = "Inputs"
    qualification = workbook.create_sheet("Qualification")
    cross_tab = workbook.create_sheet("Cross Tab")
    errors = workbook.create_sheet("Errors")
    guide = workbook.create_sheet("Guide")

    inputs.append(["Synthetic formula-qualification inputs"])
    inputs.append(["All values are fictional and exist only to exercise the reconstruction engine."])
    inputs.append(["Category", "Amount", "Region", "Included", "Lookup key", "Rate"])
    rows = [
        ["Motor", 10.0, "North", True, "Motor", 1.1],
        ["Property", 20.0, "South", False, "Property", 1.2],
        ["Motor", -5.0, "North", True, "Liability", 1.3],
        ["Liability", None, "North", False, None, None],
        ["Motor", 0.0, "South", True, None, None],
    ]
    for row in rows:
        inputs.append(row)
    inputs["B9"] = "text inside numeric range"
    for row in inputs.iter_rows(min_row=4, max_row=9, min_col=1, max_col=6):
        for cell in row:
            if cell.value is not None:
                cell.fill = PatternFill("solid", fgColor=INPUT_YELLOW)

    qualification.append(["Production-path formula acceptance cases"])
    qualification.append(["Each result is recalculated by LibreOffice and independently reconstructed in Python."])
    qualification.append(["Case", "Formula result", "Expected value", "Expected state", "Purpose"])
    for row_number, case in enumerate(cases, start=4):
        case["cell"] = f"Qualification!B{row_number}"
        qualification.cell(row=row_number, column=1, value=case["case_id"])
        qualification.cell(row=row_number, column=2, value=case["formula"])
        qualification.cell(row=row_number, column=3, value=case["expected_value"])
        qualification.cell(row=row_number, column=4, value=case["expected_status"])
        qualification.cell(row=row_number, column=5, value=case["purpose"])
        fill = GREEN if case["expected_status"] == "complete" else RED
        qualification.cell(row=row_number, column=4).fill = PatternFill("solid", fgColor=fill)
        qualification.cell(row=row_number, column=2).number_format = "0.000000"
        qualification.cell(row=row_number, column=3).number_format = "0.000000"

    cross_tab.append(["Cross-tab range inputs"])
    cross_tab.append(["Item", "Amount"])
    for row in [["A", 4.0], ["B", 6.0], ["C", 10.0]]:
        cross_tab.append(row)

    errors.append(["Deliberate spreadsheet error evidence"])
    errors.append(["These errors are parser evidence, not successful reconstruction cases."])
    errors.append(["Error type", "Formula"])
    error_cases = [
        {"cell": "Errors!B4", "formula": "=NA()", "expected_error": "#N/A"},
        {"cell": "Errors!B5", "formula": '=1+"text"', "expected_error": "#VALUE!"},
        {"cell": "Errors!B6", "formula": "=1/0", "expected_error": "#DIV/0!"},
    ]
    for row_number, item in enumerate(error_cases, start=4):
        errors.cell(row=row_number, column=1, value=item["expected_error"])
        errors.cell(row=row_number, column=2, value=item["formula"])

    guide.append(["Qualification evidence guide"])
    guide.append(["Scope", "Formula reconstruction only; no actuarial methodology validation or audit opinion."])
    guide.append(["Target engine", "LibreOffice headless recalculation; Microsoft Excel compatibility is not tested."])
    guide.append(["Generated by", "scripts/generate_qualification_workbook.py"])
    guide.append(["Recalculated by", "scripts/recalculate_with_libreoffice.sh"])
    guide.append(["Acceptance data", "tests/fixtures/qualification_expected.json"])

    _apply_standard_style(workbook)
    _style_header(inputs, "A3:F3")
    _style_header(qualification, "A3:E3")
    _style_header(cross_tab, "A2:B2")
    _style_header(errors, "A3:B3")
    for sheet in workbook.worksheets:
        for column, width in {"A": 30, "B": 34, "C": 18, "D": 18, "E": 68, "F": 14}.items():
            sheet.column_dimensions[column].width = width
        sheet.row_dimensions[1].height = 24
    qualification.column_dimensions["B"].width = 78
    errors.column_dimensions["B"].width = 35

    workbook.calculation.calcMode = "auto"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.save(UNRECALCULATED)

    expected_payload = {
        "schema_version": 1,
        "synthetic_data": True,
        "supported_functions": sorted(SUPPORTED_FUNCTIONS),
        "cases": cases,
        "error_evidence": error_cases,
    }
    EXPECTED.write_text(json.dumps(expected_payload, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {UNRECALCULATED.relative_to(ROOT)}")
    print(f"Acceptance cases: {len(cases)}")
    print(f"Supported functions covered: {len(covered)}")


def finalize_manifest(workbook_path: Path, engine_version: str) -> None:
    # LibreOffice recalculated the workbook but omits calcMode from calcPr on
    # export.  Record automatic mode only after that successful engine run so
    # the production parser can distinguish this qualified fixture from an
    # arbitrary workbook whose calculation state is unknown.
    with zipfile.ZipFile(workbook_path) as archive:
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
    with zipfile.ZipFile(workbook_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    workbook = load_workbook(workbook_path, data_only=False, read_only=True)
    formula_manifest: dict[str, list[str]] = {}
    all_formula_cells: dict[str, str] = {}
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    ref = f"{sheet.title}!{cell.coordinate}"
                    all_formula_cells[ref] = cell.value
                    for name in FUNCTION_PATTERN.findall(cell.value):
                        normalized_name = name.upper().removeprefix("_XLFN.")
                        formula_manifest.setdefault(normalized_name, []).append(ref)

    digest = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    payload = {
        "schema_version": 1,
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "recalculation_engine": "LibreOffice",
        "engine_version": engine_version.strip(),
        "microsoft_excel_tested": False,
        "workbook": str(workbook_path.relative_to(ROOT)),
        "sha256": digest,
        "generation_command": "python scripts/generate_qualification_workbook.py",
        "recalculation_command": "bash scripts/recalculate_with_libreoffice.sh",
        "supported_functions": expected["supported_functions"],
        "formula_manifest": {name: sorted(cells) for name, cells in sorted(formula_manifest.items())},
        "all_formula_cells": dict(sorted(all_formula_cells.items())),
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"LibreOffice: {engine_version.strip()}")
    print(f"SHA-256: {digest}")
    print(json.dumps(payload["formula_manifest"], indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finalize-manifest", type=Path)
    parser.add_argument("--engine-version")
    args = parser.parse_args()
    if args.finalize_manifest:
        if not args.engine_version:
            parser.error("--engine-version is required with --finalize-manifest")
        finalize_manifest(args.finalize_manifest.resolve(), args.engine_version)
    else:
        generate()


if __name__ == "__main__":
    main()
