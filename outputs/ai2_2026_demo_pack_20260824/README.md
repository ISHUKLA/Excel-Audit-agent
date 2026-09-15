# AI² 2026 demonstration workbook guide

This pack contains six entirely synthetic Excel workbooks designed to exercise the Excel Audit Agent's successful, incomplete, externally blocked, formula-coverage, and actuarial-to-finance reconciliation paths. No workbook or reference file contains client, policyholder, insurer, ledger, or production data.

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
| `Circular Control!C6` | `SUM(C1:C2,C4:C5)` visibly skips populated cell `C3` | warning finding |
| `Reserve Calculation!B13` | Uses approximate `VLOOKUP` with `TRUE()`; only exact match is supported | output reconstruction is partial |
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

## Case 4 — Claims reserve roll-forward and GL reconciliation

Files:

- `case_4_claims_reserve_roll_forward.xlsx`
- `case_4_reference_figures.csv`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora General Insurance SA | Aurora General Insurance SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | EUR |
| Basis | IFRS 17 — synthetic demonstration | IFRS 17 — synthetic demonstration |

At Gate 2, designate `Controls!B4` as the authoritative output.

Expected outcomes:

- Anomaly findings: none.
- Closing claims reserve (`Rollforward!B9`): `1,250,000 + 480,000 − 390,000 + 85,000 − 25,000 = 1,400,000`.
- `Controls!B4` (`Net claims reserve`) converts the positive actuarial magnitude to a signed credit accounting balance: `-1,400,000`.
- Internal reconstruction: complete, 100% coverage, delta `0`, preview verdict `pass`.
- Reference figures: account `2200`, `Net claims reserve`, amount `1,400,000`, `credit` — signed reference amount `-1,400,000`.
- Proposed accounting mapping: one-to-one, human approval required.
- External reconciliation after mapping approval: delta `0`, verdict `pass`.
- AI documentation is optional for this case.
- PDF: available only after the Gate 4 named approval record.

This case is a synthetic workflow demonstration, not IFRS 17 methodology validation.

## Case 5 — Supported formula demonstration

Files:

- `case_5_supported_formula_demonstration.xlsx`
- `case_5_reference_figures.csv`
- `case_5_expected.json`

Gate 1 context: Aurora Formula Assurance SA, 2025-Q4, EUR, Synthetic formula control demonstration. Enter and confirm the signed reference control total `1,620,523.06`.

At Gate 2, select every cell in `Outputs!C4:C23`. The 20 rows demonstrate the entire live supported catalogue: `SUM`, `ABS`, `INT`, `ROUND`, `ROUNDUP`, `ROUNDDOWN`, `CEILING`, `FLOOR`, `SUMIF`, `SUMIFS`, `COUNTIF`, `COUNTIFS`, `AVERAGEIF`, `AVERAGEIFS`, `MINIFS`, `MAXIFS`, `IF`, exact `VLOOKUP`, exact `MATCH`, and `INDEX` including nested `INDEX/MATCH`.

Expected outcomes:

- No anomaly findings; all 20 designated outputs reconstruct completely with zero deltas.
- Every proposed mapping remains unapproved until a human reviews it.
- Internal verdict `pass`; external verdict `pass` after the 20 mapping approvals.
- `Scope Boundary!B4:B5` contains unsupported approximate lookup and `OFFSET` examples. If selected separately, they are explicitly `partial` and `incomplete`.
- PDF available only after the Gate 4 named approval record.

Presentation script: “Case 5 turns the declared formula scope into something a reviewer can see and challenge. Each supported function has a named financial control, an exact expected value and a selectable output cell. The two formulas outside scope remain visibly incomplete, proving that the agent does not turn partial reconstruction into false assurance.”

## Case 6 — Reserve stress and solvency impact

Files:

- `case_6_reserve_stress_business_impact.xlsx`
- `case_6_reference_figures.csv`
- `case_6_expected.json`
- `CASE_6_BENCHMARK_REPORT.md`
- `case_6_summary.json`

Gate 1 context: Aurora General Insurance SA, 2025-Q4, EUR, Synthetic reserve stress demonstration. Enter and confirm the signed reference control total `52,177,824.82`. At Gate 2, select `Accounting Bridge!C4` and `Accounting Bridge!C5`.

Expected business result:

| Measure | Expected value |
|---|---:|
| Formula cells | 8,453 |
| Baseline technical provisions | EUR 11,411,087.59 |
| Adverse technical provisions | EUR 13,741,241.69 |
| Increase in technical provisions | EUR 2,330,154.10 |
| Reduction in available own funds | EUR 2,330,154.10 |
| Baseline solvency ratio | 181.68% |
| Adverse solvency ratio | 175.03% |
| Deterioration | 6.65 percentage points |

Expected workflow outcomes:

- No anomaly findings; both designated outputs reconstruct completely with zero deltas.
- Both mappings remain proposals until a human approves them.
- Internal verdict `pass`; external verdict `pass` after approval.
- PDF available only after the Gate 4 named approval record.
- The benchmark's median deterministic runtime was 6.990 seconds with 41.634 MB median peak traced Python memory on the documented machine. It does not establish production scalability.

This is a synthetic, illustrative reserve stress workflow. It is not a full actuarial model, does not implement or validate certified Solvency II or IFRS 17 methodology, and is not production-certified.

Presentation script: “Case 6 is a 300-cohort synthetic working model with 8,453 formulas, not a repeated test grid. Under the adverse assumptions, technical provisions rise by EUR 2.33 million, available own funds fall by the same amount, and the illustrative solvency ratio deteriorates by 6.65 percentage points. Glass Box reconstructs the booked baseline outputs, keeps the accounting comparison separate, and still requires a human to approve both mappings and all four gates.”

## Expected-result summary

| Case | Findings | Internal result | External result | Intended endpoint |
|---|---|---|---|---|
| 1 — Clean | none | pass | pass after mapping approval | PDF after Gate 4 |
| 2 — Controls | blocker + warnings | incomplete | not performed | explicit incomplete acknowledgement; findings remain recorded |
| 3 — Accounts | none | pass | block | Gate 3 stops the pipeline |
| 4 — Reserve roll-forward | none | pass | pass after mapping approval | PDF after Gate 4 |
| 5 — Formula catalogue | none | pass | pass after mapping approval | PDF after Gate 4 |
| 6 — Reserve stress | none | pass | pass after mapping approval | PDF after Gate 4 |

These are demonstration expectations for known synthetic inputs, not evidence that the tool validates an actuarial methodology or is production-certified.

## Calculation-freshness provenance

All six workbooks in this pack were recalculated once at build time using the LibreOffice engine recorded per file in [`recalculation_provenance.json`](recalculation_provenance.json). Cases 5 and 6 were recalculated with LibreOfficeDev 26.8.0.0.alpha0; Microsoft Excel compatibility was not tested. The provenance file records before-and-after workbook hashes, formula-manifest hashes, formula counts, verified values and byte-equality checks against the canonical copies under `demo/workbooks/`.

This was a one-time, manual, build-time fixture-generation step, not a capability of the running application: the application never invokes LibreOffice, Microsoft Excel, or any recalculation engine, and an arbitrary workbook a reviewer uploads is never recalculated by it. A cell not flagged stale in this prototype means only that no known staleness indicator was detected under these rules — it is not proof that the workbook was freshly recalculated in Excel, or by any particular engine, at any particular time.
