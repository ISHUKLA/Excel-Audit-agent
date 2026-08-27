# Demo Workbooks

This directory contains four synthetic Excel workbooks for demonstration:

1. **case_1_clean_reserve_calculation.xlsx** — Case 1: All calculations match perfectly
2. **case_2_spreadsheet_control_failures.xlsx** — Case 2: Contains circular references, hardcoded values, unsupported formulas
3. **case_3_accounting_reconciliation_failure.xlsx** — Case 3: Currency mismatch with reference figures
4. **case_4_claims_reserve_roll_forward.xlsx** — Case 4: Claims reserve roll-forward with a signed GL bridge, built by [`../build_case_4.py`](../build_case_4.py)

These workbooks were created programmatically with openpyxl, following the specifications in the parent README.md.

Each workbook:
- Contains the specific tabs and cell references mentioned
- Has formulas as described (hardcoded literals, VLOOKUP, circular refs, etc.)
- Includes cached values matching the expected outcomes
- Is set to automatic or manual calculation mode as appropriate

## Calculation freshness

The four workbooks in this directory were recalculated once, at build time, using LibreOffice 26.2.5.2, so their calc mode is genuinely `automatic` rather than `unknown`. See [`../recalculation_provenance.json`](../recalculation_provenance.json) for exact before/after hashes and [`../README.md`](../README.md#calculation-freshness-provenance) for the full explanation. This is a one-time fixture-generation step — the running application does not invoke LibreOffice or any recalculation engine, and a cell "not flagged stale" is not proof it was freshly recalculated by any particular engine at any particular time.
