# Demo Workbooks

This directory should contain three synthetic Excel workbooks for demonstration:

1. **01_clean_reserve_calculation.xlsx** — Case 1: All calculations match perfectly
2. **02_spreadsheet_control_failures.xlsx** — Case 2: Contains circular references, hardcoded values, unsupported formulas
3. **03_accounts_reconciliation_failure.xlsx** — Case 3: Currency mismatch with reference figures

These workbooks must be created manually in Excel or programmatically with openpyxl, following the specifications in the parent README.md.

Each workbook should:
- Contain the specific tabs and cell references mentioned
- Have formulas as described (hardcoded literals, VLOOKUP, circular refs, etc.)
- Include cached values matching the expected outcomes
- Be set to automatic or manual calculation mode as appropriate

## Calculation freshness

The three workbooks in this directory were recalculated once, at build time, using LibreOffice 26.2.5.2, so their calc mode is genuinely `automatic` rather than `unknown`. See [`../recalculation_provenance.json`](../recalculation_provenance.json) for exact before/after hashes and [`../README.md`](../README.md#calculation-freshness-provenance) for the full explanation. This is a one-time fixture-generation step — the running application does not invoke LibreOffice or any recalculation engine, and a cell "not flagged stale" is not proof it was freshly recalculated by any particular engine at any particular time.
