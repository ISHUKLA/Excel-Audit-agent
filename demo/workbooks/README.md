# Demo Workbooks

This directory contains six synthetic Excel workbooks for demonstration:

1. **case_1_clean_reserve_calculation.xlsx** — Case 1: All calculations match perfectly
2. **case_2_spreadsheet_control_failures.xlsx** — Case 2: Contains circular references, hardcoded values, unsupported formulas
3. **case_3_accounting_reconciliation_failure.xlsx** — Case 3: Currency mismatch with reference figures
4. **case_4_claims_reserve_roll_forward.xlsx** — Case 4: Claims reserve roll-forward with a signed GL bridge, built by [`../build_case_4.py`](../build_case_4.py)
5. **case_5_supported_formula_demonstration.xlsx** — Case 5: User-facing coverage of every function in the live supported formula catalogue, with unsupported boundary examples kept separate
6. **case_6_reserve_stress_business_impact.xlsx** — Case 6: 300-cohort, 8,453-formula baseline-versus-adverse reserve stress with own-funds and solvency-ratio impact

Cases 1–4 were created with the established Python fixture builders. Cases 5 and 6 were authored with `@oai/artifact-tool` by `scripts/build_demo_cases_5_6.mjs`, then recalculated and qualified by `scripts/finalize_demo_cases_5_6.py`.

Each workbook:
- Contains the specific tabs and cell references mentioned
- Has formulas as described (hardcoded literals, VLOOKUP, circular refs, etc.)
- Includes cached values matching the expected outcomes
- Is set to automatic or manual calculation mode as appropriate

## Calculation freshness

The six workbooks in this directory were recalculated once at build time using the LibreOffice versions recorded in [`../recalculation_provenance.json`](../recalculation_provenance.json), so their calc mode is explicitly `automatic` rather than `unknown`. Microsoft Excel compatibility was not tested for Cases 5 and 6. See [`../README.md`](../README.md#calculation-freshness-provenance) for the full explanation. This is a one-time fixture-generation step; the running application does not invoke LibreOffice or any recalculation engine, and a cell "not flagged stale" is not proof it was freshly recalculated by any particular engine at any particular time.
