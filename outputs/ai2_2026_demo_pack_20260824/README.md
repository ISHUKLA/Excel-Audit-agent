# AI² 2026 demonstration workbook guide

This pack contains three entirely synthetic Excel workbooks designed to exercise the Excel Audit Agent's successful, incomplete, and externally blocked paths. No workbook or reference file contains client, policyholder, insurer, ledger, or production data.

## Common application settings

- Run the application locally with `streamlit run app.py`.
- Reviewer for Gates 1–3: use your own name.
- Gate 4 registered demonstration name: `Isaac Shukla` with role `actuary`.
- Materiality thresholds are human decisions. The exact matches in Cases 1 and 3 pass at the suggested defaults; do not change a threshold merely to force a result.
- Never skip a finding or a gate. Case 2 is complete only after every finding has a recorded disposition.

## Case 1 — Clean reserve calculation

Files:

- `case_1_clean_reserve_calculation.xlsx`
- `case_1_reference_figures.csv`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora Life SA | Aurora Life SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | EUR |
| Basis | IFRS 17 — synthetic demonstration | IFRS 17 — synthetic demonstration |
| Signed net control total | — | 2,797,600 |

Confirm that the reference extract ties to its control total. At Gate 2, designate `Reserve Calculation!B12` as the authoritative output.

Expected outcomes:

- Anomaly findings: none.
- Gross ultimate claims: `12,000,000 × 62% = 7,440,000`.
- Outstanding claims: `7,440,000 − 5,000,000 = 2,440,000`.
- Base technical provisions: `2,440,000 + 250,000 = 2,690,000`.
- Risk margin: `2,690,000 × 4% = 107,600`.
- Total technical provisions: `2,690,000 + 107,600 = 2,797,600`.
- Internal reconstruction: complete, delta `0`, preview verdict `pass`.
- Proposed accounting mapping: one-to-one with 100% suggested confidence and `is_approved=False` until the human approves it.
- External reconciliation after mapping approval: complete, delta `0`, verdict `pass`.
- PDF: available only after the Gate 4 named approval record.

## Case 2 — Spreadsheet control failures

File: `case_2_spreadsheet_control_failures.xlsx`

Gate 1 context:

- Entity: Aurora Life SA
- Period: 2025-Q4
- Currency: EUR
- Basis: IFRS 17 — synthetic demonstration
- Leave CFO reference figures off for this case.

At Gate 2, review every finding and designate `Reserve Calculation!B14` as the authoritative output. Use the actual disposition appropriate for the demonstration and enter a reason where the application requires one.

Expected findings and reconstruction behaviour:

| Location | Intentional condition | Expected signal |
|---|---|---|
| `Circular Control!B7:B8` | Two-cell circular reference | blocker finding |
| `Reserve Calculation!B7` | Formula contains the hardcoded factor `1.075` | warning finding |
| `Reserve Calculation!B12` | `SUM(B8:B9,B11:B11)` skips populated cell `B10` | warning finding |
| `Reserve Calculation!B13` | Uses `VLOOKUP`, outside the supported formula catalogue | output reconstruction is partial |
| `Reserve Calculation!B14` | Depends on the unsupported lookup | verdict `incomplete`, not pass |

The workbook's cached value for `Reserve Calculation!B14` is 2,641,600. The audit agent must still leave the reconstructed target blank and report the output as incomplete; displaying a cached number is not evidence that the Python reconstruction was complete.

## Case 3 — Accounting reconciliation failure

Files:

- `case_3_accounting_reconciliation_failure.xlsx`
- `case_3_reference_figures.csv`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora Life SA | Aurora Life SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | **GBP** — intentional mismatch |
| Basis | IFRS 17 — synthetic demonstration | IFRS 17 — synthetic demonstration |
| Signed net control total | — | 2,490,000 |

Confirm that the reference extract ties to its own signed control total. At Gate 2, designate all three outputs:

- `Reserve Summary!B7`
- `Reserve Summary!B8`
- `Reserve Summary!B9`

Expected outcomes:

- Anomaly findings: none.
- Internal outputs: 1,850,000; 425,000; and 300,000.
- All three internal reconstructions: complete with zero deltas and `pass` preview verdicts.
- Proposed mappings:
  - `Net claim reserves` → `Net claims reserve`, suggested confidence 94.44%, human approval required.
  - `Unearned premium liability` → same-labelled output, suggested confidence 100%, human approval still required.
- Duplicate reference line `REF-0002` remains unmatched.
- Legacy transition adjustment `REF-0004` remains unmatched.
- Python output `Reserve Summary!B9` (`Expense provision`) remains unmapped.
- Context verdict: `mismatch` because workbook currency is EUR and reference currency is GBP.
- Gate 3 result after the human reviews the proposals: internal verdict remains `pass`; external verdict is `block`.

This case proves that a numerically matching line does not override incomplete population or incompatible accounting context, and that internal and external verdicts are never collapsed.

## Expected-result summary

| Case | Findings | Internal result | External result | Intended endpoint |
|---|---|---|---|---|
| 1 — Clean | none | pass | pass after mapping approval | PDF after Gate 4 |
| 2 — Controls | blocker + warnings | incomplete | not performed | explicit incomplete acknowledgement; findings remain recorded |
| 3 — Accounts | none | pass | block | Gate 3 stops the pipeline |

These are demonstration expectations for known synthetic inputs, not evidence that the tool validates an actuarial methodology or is production-certified.
