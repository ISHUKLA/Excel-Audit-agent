"""Load demonstration workbooks and reference figures.

Provides synthetic cases for judges to evaluate the pipeline. Loading a case seeds
the UI input fields only; the audit must still pass through all gates normally.
Case 11 is listed for discoverability but remains protocol-only: it has no workbook
and cannot be loaded into the audit pipeline.
"""

import pathlib

DEMO_DIR = pathlib.Path(__file__).parent / "demo"


_CASES: dict[int | str, dict[str, object]] = {
    1: {
        "name": "Clean Reserve Calculation (pass)",
        "workbook": "workbooks/case_1_clean_reserve_calculation.xlsx",
        "reference_csv": "reference_figures/case_1_reference_figures.csv",
        "entity": "Aurora Life SA",
        "period": "2025-Q4",
        "currency": "EUR",
        "basis": "IFRS 17 – synthetic demonstration",
        "description": "Case 1: Clean reserve calculation with matching reference figures",
    },
    2: {
        "name": "Spreadsheet Control Failures (incomplete)",
        "workbook": "workbooks/case_2_spreadsheet_control_failures.xlsx",
        "reference_csv": None,
        "entity": "Aurora Life SA",
        "period": "2025-Q4",
        "currency": "EUR",
        "basis": "IFRS 17 – synthetic demonstration",
        "description": "Case 2: Spreadsheet control failures (circular refs, hardcoded values, unsupported formulas)",
    },
    3: {
        "name": "Accounting Reconciliation Failure (block)",
        "workbook": "workbooks/case_3_accounting_reconciliation_failure.xlsx",
        "reference_csv": "reference_figures/case_3_reference_figures.csv",
        "entity": "Aurora Life SA",
        "period": "2025-Q4",
        "currency": "EUR",
        "basis": "IFRS 17 – synthetic demonstration",
        "description": "Case 3: Accounting reconciliation failure (currency mismatch with reference figures)",
    },
    4: {
        "name": "Claims Reserve Roll-Forward (pass after mapping approval)",
        "workbook": "workbooks/case_4_claims_reserve_roll_forward.xlsx",
        "reference_csv": "reference_figures/case_4_reference_figures.csv",
        "entity": "Aurora General Insurance SA",
        "period": "2025-Q4",
        "currency": "EUR",
        "basis": "IFRS 17 – synthetic demonstration",
        "description": "Case 4: Claims reserve roll-forward with signed GL reconciliation",
    },
    5: {
        "name": "Supported Formula Demonstration (pass)",
        "workbook": "workbooks/case_5_supported_formula_demonstration.xlsx",
        "reference_csv": "reference_figures/case_5_reference_figures.csv",
        "entity": "Aurora Formula Assurance SA",
        "period": "2025-Q4",
        "currency": "EUR",
        "basis": "Synthetic formula control demonstration",
        "description": "Case 5: All supported formulas, plus explicit unsupported boundaries",
    },
    6: {
        "name": "Reserve Stress Business Impact (pass after mapping approval)",
        "workbook": "workbooks/case_6_reserve_stress_business_impact.xlsx",
        "reference_csv": "reference_figures/case_6_reference_figures.csv",
        "entity": "Aurora General Insurance SA",
        "period": "2025-Q4",
        "currency": "EUR",
        "basis": "Synthetic reserve stress demonstration",
        "description": "Case 6: Large synthetic reserve stress and solvency impact",
    },
    "7a": {
        "name": "Clean IFRS 17 Cohort (pass)",
        "workbook": "final cases/case_7_ifrs17/workbooks/case_7a_ifrs17_clean.xlsx",
        "reference_csv": "final cases/case_7_ifrs17/reference_figures/case_7a_ifrs17_clean_reference_figures.csv",
        "entity": "Glass Box Life Belgium SA",
        "period": "31 December 2025",
        "currency": "EUR",
        "basis": "Synthetic IFRS 17 reporting demonstration",
        "description": "Case 7a: Clean IFRS 17-style cohort aggregation",
    },
    "7b": {
        "name": "Missing IFRS 17 Cohort (block)",
        "workbook": "final cases/case_7_ifrs17/workbooks/case_7b_ifrs17_missing_cohort.xlsx",
        "reference_csv": "final cases/case_7_ifrs17/reference_figures/case_7b_ifrs17_missing_cohort_reference_figures.csv",
        "entity": "Glass Box Life Belgium SA",
        "period": "31 December 2025",
        "currency": "EUR",
        "basis": "Synthetic IFRS 17 reporting demonstration",
        "description": "Case 7b: Final populated IFRS 17 cohort omitted",
    },
    8: {
        "name": "Solvency II Capital Decision (block)",
        "workbook": "final cases/case_8_solvency_capital/workbooks/case_8_solvency_capital_decision.xlsx",
        "reference_csv": "final cases/case_8_solvency_capital/reference_figures/case_8_solvency_capital_reference_figures.csv",
        "entity": "Glass Box Life Belgium SA",
        "period": "31 December 2025",
        "currency": "EUR",
        "basis": "Synthetic Solvency II capital demonstration",
        "description": "Case 8: Solvency II capital and dividend decision",
    },
    9: {
        "name": "Life Pricing Incomplete Reconstruction (incomplete)",
        "workbook": "final cases/case_9_life_pricing/workbooks/case_9_life_pricing_incomplete.xlsx",
        "reference_csv": None,
        "entity": "Glass Box Life Belgium SA",
        "period": "31 December 2025",
        "currency": "EUR",
        "basis": "Synthetic life-pricing demonstration",
        "description": "Case 9: Life pricing with an incomplete reconstruction",
    },
    10: {
        "name": "Wrong Accounting Context (block)",
        "workbook": "final cases/case_10_accounting_context/workbooks/case_10_accounting_context_failure.xlsx",
        "reference_csv": "final cases/case_10_accounting_context/reference_figures/case_10_wrong_ledger.csv",
        "entity": "Glass Box Life Belgium SA",
        "period": "31 December 2025",
        "currency": "EUR",
        "basis": "Synthetic reserve-accounting demonstration",
        "reference_entity": "Glass Box Life UK Ltd",
        "reference_period": "30 September 2025",
        "reference_currency": "GBP",
        "reference_basis": "Synthetic UK reserve ledger extract",
        "description": "Case 10: Right numbers, wrong accounting context",
    },
    11: {
        "name": "Independent Challenge (protocol only)",
        "protocol_only": True,
        "protocol_path": "final cases/case_11_independent_challenge/docs/README.md",
    },
}


def load_case(case_number: int | str) -> dict:
    """Load a demonstration case.

    Args:
        case_number: 1–6, ``"7a"``, ``"7b"``, 8, 9, or 10. Case 11 is
            protocol-only and raises ``ValueError``.

    Returns:
        dict with keys:
            - 'workbook_bytes': the .xlsx file as bytes
            - 'reference_csv_path': path to reference figures CSV (or None)
            - 'entity': suggested entity name
            - 'period': suggested period
            - 'currency': suggested currency
            - 'basis': suggested basis
            - 'description': suggested file description
    """
    if case_number not in _CASES:
        raise ValueError(
            f"Case {case_number} not found. Choose 1–6, 7a, 7b, 8, 9, 10, or 11."
        )

    case_spec = _CASES[case_number]
    if case_spec.get("protocol_only"):
        raise ValueError(
            "Case 11 is protocol-only and has no workbook to load. Follow "
            f"{DEMO_DIR / str(case_spec['protocol_path'])}."
        )

    # Load workbook bytes
    workbook_path = DEMO_DIR / str(case_spec["workbook"])
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    workbook_bytes = workbook_path.read_bytes()

    # Load reference CSV path (or None)
    reference_csv_path = None
    if case_spec["reference_csv"]:
        ref_path = DEMO_DIR / str(case_spec["reference_csv"])
        if ref_path.exists():
            reference_csv_path = str(ref_path)

    return {
        "workbook_bytes": workbook_bytes,
        "reference_csv_path": reference_csv_path,
        "entity": case_spec["entity"],
        "period": case_spec["period"],
        "currency": case_spec["currency"],
        "basis": case_spec["basis"],
        "description": case_spec["description"],
        "reference_entity": case_spec.get("reference_entity", case_spec["entity"]),
        "reference_period": case_spec.get("reference_period", case_spec["period"]),
        "reference_currency": case_spec.get("reference_currency", case_spec["currency"]),
        "reference_basis": case_spec.get("reference_basis", case_spec["basis"]),
        "protocol_only": False,
    }


def list_cases() -> list[dict]:
    """Return list of available demonstration cases.

    Returns:
        list of dicts with 'number' and 'name' keys
    """
    return [
        {
            "number": case_number,
            "name": case_spec["name"],
            "protocol_only": bool(case_spec.get("protocol_only")),
            "protocol_path": case_spec.get("protocol_path"),
        }
        for case_number, case_spec in _CASES.items()
    ]
