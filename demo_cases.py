"""Load demonstration workbooks and reference figures.

Provides synthetic cases for judges to evaluate the pipeline. Loading a case seeds
the UI input fields only; the audit must still pass through all gates normally.
"""

import pathlib

DEMO_DIR = pathlib.Path(__file__).parent / "demo"


def load_case(case_number: int) -> dict:
    """Load a demonstration case.

    Args:
        case_number: 1, 2, or 3

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
    cases = {
        1: {
            "workbook": "case_1_clean_reserve_calculation.xlsx",
            "reference_csv": "case_1_reference_figures.csv",
            "entity": "Aurora Life SA",
            "period": "2025-Q4",
            "currency": "EUR",
            "basis": "IFRS 17 – synthetic demonstration",
            "description": "Case 1: Clean reserve calculation with matching reference figures",
        },
        2: {
            "workbook": "case_2_spreadsheet_control_failures.xlsx",
            "reference_csv": None,
            "entity": "Aurora Life SA",
            "period": "2025-Q4",
            "currency": "EUR",
            "basis": "IFRS 17 – synthetic demonstration",
            "description": "Case 2: Spreadsheet control failures (circular refs, hardcoded values, unsupported formulas)",
        },
        3: {
            "workbook": "case_3_accounting_reconciliation_failure.xlsx",
            "reference_csv": "case_3_reference_figures.csv",
            "entity": "Aurora Life SA",
            "period": "2025-Q4",
            "currency": "EUR",
            "basis": "IFRS 17 – synthetic demonstration",
            "description": "Case 3: Accounting reconciliation failure (currency mismatch with reference figures)",
        },
    }

    if case_number not in cases:
        raise ValueError(f"Case {case_number} not found. Choose 1, 2, or 3.")

    case_spec = cases[case_number]

    # Load workbook bytes
    workbook_path = DEMO_DIR / "workbooks" / case_spec["workbook"]
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    workbook_bytes = workbook_path.read_bytes()

    # Load reference CSV path (or None)
    reference_csv_path = None
    if case_spec["reference_csv"]:
        ref_path = DEMO_DIR / "reference_figures" / case_spec["reference_csv"]
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
    }


def list_cases() -> list[dict]:
    """Return list of available demonstration cases.

    Returns:
        list of dicts with 'number' and 'name' keys
    """
    return [
        {"number": 1, "name": "Clean Reserve Calculation (pass)"},
        {"number": 2, "name": "Spreadsheet Control Failures (incomplete)"},
        {"number": 3, "name": "Accounting Reconciliation Failure (block)"},
    ]
