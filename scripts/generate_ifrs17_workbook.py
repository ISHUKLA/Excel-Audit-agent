#!/usr/bin/env python3
"""Generate the qualified large synthetic IFRS 17-related workbook.

This is a spreadsheet-control and financial-reconciliation demonstration around
an IFRS 17 reserve roll-forward.  It is not a full IFRS 17 model and does not
validate actuarial methodology.  All entities, cohorts, accounts and amounts
are synthetic.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from copy import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.formula_catalogue import SUPPORTED_FUNCTIONS

SEED = 42
COHORT_COUNT = 600
ENTITY = "Aurora Mutual Life SE (synthetic)"
PERIOD = "2025-Q4"
CURRENCY = "EUR"
BASIS = "IFRS 17-related synthetic reserve roll-forward"

FIXTURES = ROOT / "tests" / "fixtures"
CLEAN_WORKBOOK = FIXTURES / "ifrs17_workbook_clean.xlsx"
EXPECTED_PATH = FIXTURES / "ifrs17_expected.json"
REFERENCE_PATH = FIXTURES / "accounting_reference.csv"
MANIFEST_PATH = FIXTURES / "ifrs17_manifest.json"

FUNCTION_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
BOOLEAN_CALLS = {"TRUE", "FALSE"}

NAVY = "17365D"
BLUE = "D9EAF7"
LIGHT_BLUE = "EAF2F8"
INPUT_YELLOW = "FFF2CC"
GREEN = "E2F0D9"
RED = "FCE4D6"
WHITE = "FFFFFF"
BLACK = "000000"
FORMULA_GREEN = "008000"
THIN_GREY = Side(style="thin", color="B7C9D6")


def excel_round(value: Decimal, digits: int = 2) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def synthetic_cohorts() -> list[dict]:
    rng = random.Random(SEED)
    products = ("Term Life", "Annuity", "Savings")
    currencies = ("EUR", "EUR", "EUR", "GBP")
    loss_ratios = {
        "Term Life": Decimal("0.62"),
        "Annuity": Decimal("0.70"),
        "Savings": Decimal("0.55"),
    }
    cohorts = []
    for index in range(1, COHORT_COUNT + 1):
        product = products[(index - 1) % len(products)]
        currency = currencies[(index - 1) % len(currencies)]
        premium = Decimal(rng.randrange(100_000, 500_001, 1_000))
        expected_claims = excel_round(premium * loss_ratios[product])
        opening_reserve = excel_round(expected_claims * Decimal("0.60"))
        cohorts.append(
            {
                "cohort_id": f"SYN-{index:04d}",
                "entity": ENTITY,
                "product": product,
                "currency": currency,
                "premium": premium,
                "expected_claims": expected_claims,
                "opening_reserve": opening_reserve,
                "included": index % 17 != 0,
            }
        )
    return cohorts


def independently_calculated_totals(cohorts: list[dict]) -> dict[str, Decimal]:
    fx = {"EUR": Decimal("1.00"), "GBP": Decimal("1.15")}
    loss_ratios = {
        "Term Life": Decimal("0.62"),
        "Annuity": Decimal("0.70"),
        "Savings": Decimal("0.55"),
    }
    service_rate = Decimal("0.08")
    expense_rate = Decimal("0.02")
    discount_rate = Decimal("0.035")
    strengthening_rate = Decimal("0.015")

    totals = Counter()
    for cohort in cohorts:
        cohort_fx = fx[cohort["currency"]]
        modelled_claims = (
            excel_round(cohort["premium"] * loss_ratios[cohort["product"]])
            if cohort["included"]
            else Decimal("0.00")
        )
        opening = excel_round(cohort["opening_reserve"] * cohort_fx)
        service = excel_round(modelled_claims * service_rate * cohort_fx)
        expense = excel_round(cohort["premium"] * expense_rate * cohort_fx)
        interest = excel_round(opening * discount_rate)
        strengthening = excel_round(opening * strengthening_rate)
        fx_movement = excel_round(
            cohort["opening_reserve"] * (cohort_fx - Decimal("1.00"))
        )
        closing = opening + service + expense + interest + strengthening + fx_movement
        totals.update(
            {
                "opening_reserve": opening,
                "claims_service_movement": service,
                "expense_movement": expense,
                "interest_accretion": interest,
                "assumption_strengthening": strengthening,
                "fx_movement": fx_movement,
                "closing_reserve": closing,
            }
        )
    return dict(totals)


def _header(sheet, row: int, start_col: int, end_col: int) -> None:
    for column in range(start_col, end_col + 1):
        cell = sheet.cell(row=row, column=column)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Arial", size=10, bold=True, color=WHITE)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=THIN_GREY)


def _base_sheet_style(workbook: Workbook) -> None:
    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet["A1"].font = Font(name="Arial", size=14, bold=True, color=NAVY)
        sheet.row_dimensions[1].height = 24
        for row in sheet.iter_rows():
            for cell in row:
                font = copy(cell.font)
                font.name = "Arial"
                font.size = 10
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    font.color = FORMULA_GREEN if "!" in cell.value else BLACK
                cell.font = font
                cell.alignment = Alignment(vertical="center")


def _write_workbook(source_path: Path, cohorts: list[dict], totals: dict[str, Decimal]) -> None:
    workbook = Workbook()
    assumptions = workbook.active
    assumptions.title = "Assumptions"
    cohort_data = workbook.create_sheet("CohortData")
    rollforward = workbook.create_sheet("ReserveRollforward")
    aggregation = workbook.create_sheet("Aggregation")
    bridge = workbook.create_sheet("AccountingBridge")
    controls = workbook.create_sheet("Controls")
    guide = workbook.create_sheet("DemoGuide")

    assumptions.append(["Synthetic IFRS 17-related assumptions"])
    assumptions.append(["Editable inputs", "Value", "Unit / use"])
    assumption_rows = [
        ("Discount rate", 0.035, "Interest accretion"),
        ("Service movement rate", 0.08, "Claims and service movement"),
        ("Expense loading", 0.02, "Expense movement"),
        ("Assumption strengthening rate", 0.015, "Reserve strengthening"),
        ("Reporting FX baseline", 1.0, "FX movement baseline"),
        ("Rounding significance", 100.0, "CEILING/FLOOR controls"),
        ("Upper display precision", 0.0, "ROUNDUP control"),
        ("Lower display precision", 0.0, "ROUNDDOWN control"),
        ("Zero value", 0.0, "Explicit zero branch"),
        ("Exposure unit divisor", 100000.0, "Whole exposure units"),
    ]
    for item in assumption_rows:
        assumptions.append(item)
    assumptions["H2"] = "Currency"
    assumptions["I2"] = "EUR rate"
    assumptions.append([])
    assumptions["H4"], assumptions["I4"] = "EUR", 1.0
    assumptions["H5"], assumptions["I5"] = "GBP", 1.15
    assumptions["K2"], assumptions["L2"] = "Product", "Loss ratio"
    for row, (product, ratio) in enumerate(
        (("Term Life", 0.62), ("Annuity", 0.70), ("Savings", 0.55)), start=4
    ):
        assumptions.cell(row=row, column=11, value=product)
        assumptions.cell(row=row, column=12, value=ratio)
    for row in assumptions.iter_rows(min_row=3, max_row=12, min_col=2, max_col=2):
        row[0].fill = PatternFill("solid", fgColor=INPUT_YELLOW)
        row[0].font = Font(name="Arial", size=10, color="0000FF")
    for ref in ("I4", "I5", "L4", "L5", "L6"):
        assumptions[ref].fill = PatternFill("solid", fgColor=INPUT_YELLOW)
        assumptions[ref].font = Font(name="Arial", size=10, color="0000FF")
    _header(assumptions, 2, 1, 3)
    _header(assumptions, 2, 8, 9)
    _header(assumptions, 2, 11, 12)

    cohort_data.append(["Synthetic policy cohort data and calculated attributes"])
    cohort_data.append([f"{ENTITY}; {PERIOD}; reporting currency {CURRENCY}"])
    cohort_headers = [
        "Cohort ID", "Entity", "Product", "Currency", "Premium", "Expected claims input",
        "Opening reserve input", "Included", "FX to EUR", "Product index", "Loss ratio",
        "Modelled claims", "Absolute input variance", "Exposure units",
    ]
    cohort_data.append(cohort_headers)
    for row_number, cohort in enumerate(cohorts, start=4):
        cohort_data.append(
            [
                cohort["cohort_id"], cohort["entity"], cohort["product"], cohort["currency"],
                float(cohort["premium"]), float(cohort["expected_claims"]),
                float(cohort["opening_reserve"]), cohort["included"],
                f'=VLOOKUP(D{row_number},Assumptions!$H$4:$I$5,2,FALSE())',
                f'=MATCH(C{row_number},Assumptions!$K$4:$K$6,0)',
                f'=INDEX(Assumptions!$L$4:$L$6,J{row_number})',
                f'=IF(H{row_number},ROUND(E{row_number}*K{row_number},2),Assumptions!$B$11)',
                f'=ABS(F{row_number}-L{row_number})',
                f'=INT(E{row_number}/Assumptions!$B$12)',
            ]
        )
    _header(cohort_data, 3, 1, len(cohort_headers))

    rollforward.append(["Reserve roll-forward by synthetic cohort"])
    rollforward.append([f"Amounts in EUR for {PERIOD}"])
    roll_headers = [
        "Cohort ID", "Opening reserve", "Claims / service movement", "Expense movement",
        "Interest accretion", "Assumption strengthening", "FX movement", "Closing reserve",
        "Ceiling control", "Floor control", "Rounded up", "Rounded down",
    ]
    rollforward.append(roll_headers)
    for row_number, cohort in enumerate(cohorts, start=4):
        rollforward.append(
            [
                cohort["cohort_id"],
                f'=ROUND(CohortData!G{row_number}*CohortData!I{row_number},2)',
                f'=ROUND(CohortData!L{row_number}*Assumptions!$B$4*CohortData!I{row_number},2)',
                f'=ROUND(CohortData!E{row_number}*Assumptions!$B$5*CohortData!I{row_number},2)',
                f'=ROUND(B{row_number}*Assumptions!$B$3,2)',
                f'=ROUND(B{row_number}*Assumptions!$B$6,2)',
                f'=ROUND(CohortData!G{row_number}*CohortData!I{row_number}-CohortData!G{row_number}*Assumptions!$B$7,2)',
                f'=SUM(B{row_number}:E{row_number},F{row_number}:G{row_number})',
                f'=CEILING(H{row_number},Assumptions!$B$8)',
                f'=FLOOR(H{row_number},Assumptions!$B$8)',
                f'=ROUNDUP(H{row_number},Assumptions!$B$9)',
                f'=ROUNDDOWN(H{row_number},Assumptions!$B$10)',
            ]
        )
    _header(rollforward, 3, 1, len(roll_headers))

    last_row = COHORT_COUNT + 3
    aggregation.append(["Product aggregation and distribution checks"])
    aggregation.append([f"Synthetic portfolio summary for {PERIOD}"])
    agg_headers = [
        "Product", "Cohort count", "Included count", "Opening reserve", "Service movement",
        "Average closing", "Average included closing", "Minimum closing", "Maximum closing",
        "Closing reserve",
    ]
    aggregation.append(agg_headers)
    for row_number, product in enumerate(("Term Life", "Annuity", "Savings"), start=4):
        aggregation.append(
            [
                product,
                f'=COUNTIF(CohortData!$C$4:$C${last_row},A{row_number})',
                f'=COUNTIFS(CohortData!$C$4:$C${last_row},A{row_number},CohortData!$H$4:$H${last_row},TRUE())',
                f'=SUMIF(CohortData!$C$4:$C${last_row},A{row_number},ReserveRollforward!$B$4:$B${last_row})',
                f'=SUMIFS(ReserveRollforward!$C$4:$C${last_row},CohortData!$C$4:$C${last_row},A{row_number},CohortData!$H$4:$H${last_row},TRUE())',
                f'=AVERAGEIF(CohortData!$C$4:$C${last_row},A{row_number},ReserveRollforward!$H$4:$H${last_row})',
                f'=AVERAGEIFS(ReserveRollforward!$H$4:$H${last_row},CohortData!$C$4:$C${last_row},A{row_number},CohortData!$H$4:$H${last_row},TRUE())',
                f'=_xlfn.MINIFS(ReserveRollforward!$H$4:$H${last_row},CohortData!$C$4:$C${last_row},A{row_number})',
                f'=_xlfn.MAXIFS(ReserveRollforward!$H$4:$H${last_row},CohortData!$C$4:$C${last_row},A{row_number})',
                f'=SUMIF(CohortData!$C$4:$C${last_row},A{row_number},ReserveRollforward!$H$4:$H${last_row})',
            ]
        )
    aggregation["A8"] = "Portfolio total"
    for column in range(2, 11):
        letter = aggregation.cell(row=8, column=column).column_letter
        aggregation.cell(row=8, column=column, value=f"=SUM({letter}4:{letter}6)")
    _header(aggregation, 3, 1, len(agg_headers))
    for cell in aggregation[8]:
        cell.font = Font(name="Arial", size=10, bold=True)
        cell.border = Border(top=THIN_GREY)

    bridge.append(["Accounting bridge outputs"])
    bridge.append(["Signed balances: credits are negative; all figures are synthetic."])
    bridge.append(["Account", "Output label", "Signed balance (EUR)"])
    bridge_rows = [
        ("SYN-2100", "Opening reserve", f"=-SUM(ReserveRollforward!B4:B{last_row})"),
        ("SYN-2110", "Claims and service movements", f"=-SUM(ReserveRollforward!C4:C{last_row})"),
        ("SYN-2120", "Assumption strengthening amount", f"=-SUM(ReserveRollforward!F4:F{last_row})"),
        ("SYN-2130", "FX movement", f"=-SUM(ReserveRollforward!G4:G{last_row})"),
        ("SYN-2140", "Closing reserve", f"=-SUM(ReserveRollforward!H4:H{last_row})"),
        ("SYN-2199", "Signed accounting balance", "=SUM(C4:C8)"),
    ]
    for item in bridge_rows:
        bridge.append(item)
    _header(bridge, 3, 1, 3)
    for row in range(4, 10):
        bridge.cell(row=row, column=3).number_format = '#,##0.00;[Red](#,##0.00);-'

    controls.append(["Independent workbook controls"])
    controls.append(["A zero value is expected for each calculation."])
    controls.append(["Control", "Difference (EUR)"])
    control_rows = [
        ("Closing reserve to roll-forward", f"=AccountingBridge!C8+SUM(ReserveRollforward!H4:H{last_row})"),
        ("Opening reserve to roll-forward", f"=AccountingBridge!C4+SUM(ReserveRollforward!B4:B{last_row})"),
        ("Accounting bridge signed total", "=AccountingBridge!C9-SUM(AccountingBridge!C4:C8)"),
        ("Aggregation closing reserve", f"=ROUND(Aggregation!J8-SUM(ReserveRollforward!H4:H{last_row}),2)"),
        ("Absolute closing difference", "=ABS(B4)"),
    ]
    for item in control_rows:
        controls.append(item)
    _header(controls, 3, 1, 2)

    hand_checks = [
        ("AccountingBridge!C4", "Opening reserve", -totals["opening_reserve"]),
        ("AccountingBridge!C5", "Claims and service movements", -totals["claims_service_movement"]),
        ("AccountingBridge!C6", "Assumption strengthening amount", -totals["assumption_strengthening"]),
        ("AccountingBridge!C7", "FX movement", -totals["fx_movement"]),
        ("AccountingBridge!C8", "Closing reserve", -totals["closing_reserve"]),
    ]
    signed_balance = sum((value for _, _, value in hand_checks), Decimal("0"))
    hand_checks.append(("AccountingBridge!C9", "Signed accounting balance", signed_balance))

    guide.append(["Demonstration guide"])
    guide.append(["Scope", "Spreadsheet reconstruction and financial reconciliation around a synthetic IFRS 17-related process."])
    guide.append(["Boundary", "This workbook does not implement or validate the full IFRS 17 methodology and is not an audit opinion."])
    guide.append(["Synthetic data", "All entities, cohorts, accounts and amounts are fictional."])
    guide.append(["Gate 2 authoritative output", "AccountingBridge!C9"])
    guide.append([])
    guide.append(["Cell", "Hand-checkable output", "Expected signed value (EUR)"])
    for cell_ref, label, value in hand_checks:
        guide.append([cell_ref, label, float(value)])
    guide.append([])
    guide.append(["Demonstration sequence"])
    guide.append(["1", "Confirm entity, period, currency, basis, workbook hash and accounting reference context at Gate 1."])
    guide.append(["2", "Review every finding and designate AccountingBridge!C9 at Gate 2."])
    guide.append(["3", "Approve the proposed accounting mapping and set both materiality thresholds at Gate 3."])
    guide.append(["4", "Create the named approval record at Gate 4; only then generate the PDF."])

    _base_sheet_style(workbook)
    widths = {
        assumptions: {"A": 34, "B": 16, "C": 30, "H": 14, "I": 14, "K": 18, "L": 14},
        cohort_data: {"A": 14, "B": 34, "C": 16, "D": 12, "E": 16, "F": 20, "G": 20, "H": 12, "I": 12, "J": 14, "K": 12, "L": 18, "M": 22, "N": 15},
        rollforward: {"A": 14, "B": 18, "C": 24, "D": 18, "E": 18, "F": 24, "G": 16, "H": 18, "I": 16, "J": 16, "K": 14, "L": 14},
        aggregation: {letter: 20 for letter in "ABCDEFGHIJ"},
        bridge: {"A": 16, "B": 36, "C": 22},
        controls: {"A": 38, "B": 22},
        guide: {"A": 30, "B": 100, "C": 24},
    }
    for sheet, mapping in widths.items():
        for column, width in mapping.items():
            sheet.column_dimensions[column].width = width
    cohort_data.freeze_panes = "A4"
    rollforward.freeze_panes = "A4"
    aggregation.freeze_panes = "A4"
    assumptions.freeze_panes = "A3"
    for sheet in (cohort_data, rollforward, aggregation):
        for row in sheet.iter_rows(min_row=4):
            for cell in row:
                if cell.column >= 5 or sheet is rollforward:
                    cell.number_format = '#,##0.00;[Red](#,##0.00);-'

    workbook.calculation.calcMode = "auto"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.save(source_path)


def _set_qualified_calc_mode(workbook_path: Path) -> None:
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


def _formula_inventory(workbook_path: Path) -> tuple[int, Counter, dict[str, list[str]]]:
    workbook = load_workbook(workbook_path, data_only=False, read_only=True)
    count = 0
    by_function: Counter = Counter()
    cells_by_function: dict[str, list[str]] = {}
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str) or not cell.value.startswith("="):
                    continue
                count += 1
                ref = f"{sheet.title}!{cell.coordinate}"
                names = {
                    name.upper().removeprefix("_XLFN.")
                    for name in FUNCTION_PATTERN.findall(cell.value)
                } - BOOLEAN_CALLS
                for name in names:
                    by_function[name] += 1
                    cells_by_function.setdefault(name, []).append(ref)
    return count, by_function, cells_by_function


def _recalculate(source_path: Path, final_path: Path) -> str:
    soffice = os.environ.get("SOFFICE_BIN") or shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(
            "LibreOffice is unavailable. The large qualification fixture was generated "
            "but not qualified, so no final workbook was produced."
        )
    engine_version = subprocess.run(
        [soffice, "--version"], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="ifrs17-recalc-") as output_dir:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "xlsx", "--outdir", output_dir, str(source_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        produced = Path(output_dir) / source_path.name
        if not produced.exists():
            raise RuntimeError("LibreOffice did not produce the expected recalculated workbook")
        shutil.copyfile(produced, final_path)
    _set_qualified_calc_mode(final_path)
    return engine_version


def _write_reference_and_expected(totals: dict[str, Decimal]) -> list[dict]:
    hand_checks = [
        {"cell": "AccountingBridge!C4", "label": "Opening reserve", "expected_value": float(-totals["opening_reserve"])},
        {"cell": "AccountingBridge!C5", "label": "Claims and service movements", "expected_value": float(-totals["claims_service_movement"])},
        {"cell": "AccountingBridge!C6", "label": "Assumption strengthening amount", "expected_value": float(-totals["assumption_strengthening"])},
        {"cell": "AccountingBridge!C7", "label": "FX movement", "expected_value": float(-totals["fx_movement"])},
        {"cell": "AccountingBridge!C8", "label": "Closing reserve", "expected_value": float(-totals["closing_reserve"])},
    ]
    signed_balance = round(sum(item["expected_value"] for item in hand_checks), 2)
    hand_checks.append(
        {"cell": "AccountingBridge!C9", "label": "Signed accounting balance", "expected_value": signed_balance}
    )

    reference_row = {
        "account_number": "SYN-2199",
        "label": "Signed accounting balance",
        "amount": abs(signed_balance),
        "debit_credit": "credit" if signed_balance < 0 else "debit",
        "entity": ENTITY,
        "period": PERIOD,
        "currency": CURRENCY,
        "ledger_source": "Synthetic Q4 2025 trial balance",
        "basis": BASIS,
        "evidence_reference": "Synthetic reference extract row 2",
        "control_total": signed_balance,
    }
    with REFERENCE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(reference_row), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerow(reference_row)

    expected = {
        "schema_version": 1,
        "synthetic_data": True,
        "seed": SEED,
        "entity": ENTITY,
        "period": PERIOD,
        "currency": CURRENCY,
        "basis": BASIS,
        "cohort_count": COHORT_COUNT,
        "authoritative_outputs": ["AccountingBridge!C9"],
        "hand_checkable_outputs": hand_checks,
        "reference_control_total": signed_balance,
        "expected_findings": [],
        "expected_internal_verdict": "pass",
        "expected_external_verdict": "pass",
        "required_gate_order": [1, 2, 3, 4],
        "pdf_available_after_gate4": True,
    }
    EXPECTED_PATH.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    return hand_checks


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cohorts = synthetic_cohorts()
    totals = independently_calculated_totals(cohorts)
    hand_checks = _write_reference_and_expected(totals)

    with tempfile.TemporaryDirectory(prefix="ifrs17-generate-") as temp_dir:
        source_path = Path(temp_dir) / CLEAN_WORKBOOK.name
        _write_workbook(source_path, cohorts, totals)
        engine_version = _recalculate(source_path, CLEAN_WORKBOOK)

    formula_count, by_function, cells_by_function = _formula_inventory(CLEAN_WORKBOOK)
    missing = sorted(SUPPORTED_FUNCTIONS - set(by_function))
    unsupported = sorted(set(by_function) - SUPPORTED_FUNCTIONS)
    if formula_count < 5_000:
        raise RuntimeError(f"large fixture has only {formula_count} formula cells")
    if missing:
        raise RuntimeError(f"large fixture does not exercise supported functions: {missing}")
    if unsupported:
        raise RuntimeError(f"clean large fixture contains unsupported functions: {unsupported}")

    digest = hashlib.sha256(CLEAN_WORKBOOK.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "synthetic_data": True,
        "workbook": str(CLEAN_WORKBOOK.relative_to(ROOT)),
        "sha256": digest,
        "recalculation_engine": "LibreOffice",
        "engine_version": engine_version,
        "microsoft_excel_tested": False,
        "python_version": platform.python_version(),
        "formula_cell_count": formula_count,
        "supported_functions": sorted(SUPPORTED_FUNCTIONS),
        "formula_count_by_function": dict(sorted(by_function.items())),
        "representative_cells_by_function": {
            name: refs[:5] for name, refs in sorted(cells_by_function.items())
        },
        "authoritative_outputs": hand_checks,
        "generation_command": "python scripts/generate_ifrs17_workbook.py",
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Total formula cell count: {formula_count}")
    print("Count per supported function:")
    for name in sorted(SUPPORTED_FUNCTIONS):
        print(f"  {name}: {by_function[name]}")
    print("Hand-checkable outputs:")
    for item in hand_checks:
        print(f"  {item['cell']} | {item['label']} | {item['expected_value']:.2f}")
    print(f"LibreOffice: {engine_version}")
    print(f"SHA-256: {digest}")
    print("Microsoft Excel compatibility tested: no")


if __name__ == "__main__":
    main()
