# AI² 2026 demonstration workbook guide

This pack contains six entirely synthetic Excel workbooks designed to exercise the Excel Audit Agent's successful, incomplete, externally blocked, formula-coverage, and actuarial-to-finance reconciliation paths. No workbook or reference file contains client, policyholder, insurer, ledger, or production data.

## Common application settings

- Run the application locally with `streamlit run app.py`.
- Reviewer for Gates 1–3: use your own name.
- Gate 4 registered demonstration name: `Isaac Shukla` with role `actuary`.
- Materiality thresholds are human decisions. The exact matches in Cases 1 and 3 pass at the suggested defaults; do not change a threshold merely to force a result.
- Never skip a finding or a gate. Case 2 is complete only after every finding has a recorded disposition.

## Reproducing Cases 5 and 6

The committed workbooks are ready to use and do not require a spreadsheet engine at application runtime. To rebuild them, install or expose `@oai/artifact-tool`, install LibreOffice, then run:

```bash
python scripts/generate_demo_cases_5_6.py \
  --artifact-tool-module /path/to/artifact_tool.mjs \
  --soffice /path/to/soffice
python scripts/benchmark_demo_case_6.py --runs 5
```

If `@oai/artifact-tool` is available to Node.js as an installed package, omit `--artifact-tool-module`. If `soffice` is already on `PATH`, omit `--soffice`. The Python wrapper supplies the supported-function list directly from `core/formula_catalogue.py`; the builder and finaliser both fail if Case 5 drifts from that live catalogue.

## Case 1 – Clean reserve calculation

Files:

- `workbooks/case_1_clean_reserve_calculation.xlsx`
- `reference_figures/case_1_reference_figures.csv`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora Life SA | Aurora Life SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | EUR |
| Basis | IFRS 17 – synthetic demonstration | IFRS 17 – synthetic demonstration |
| Signed net control total | – | 2,797,600 |

Confirm that the reference extract ties to its control total. At Gate 2, designate `Reserve Calculation!B12` as the authoritative output.

Expected outcomes:

- Anomaly findings: none.
- Gross ultimate claims: `12,000,000 × 62% = 7,440,000`.
- Outstanding claims: `7,440,000 – 5,000,000 = 2,440,000`.
- Base technical provisions: `2,440,000 + 250,000 = 2,690,000`.
- Risk margin: `2,690,000 × 4% = 107,600`.
- Total technical provisions: `2,690,000 + 107,600 = 2,797,600`.
- Internal reconstruction: complete, delta `0`, preview verdict `pass`.
- Proposed accounting mapping: one-to-one with 100% suggested confidence and `is_approved=False` until the human approves it.
- External reconciliation after mapping approval: complete, delta `0`, verdict `pass`.
- PDF: available only after the Gate 4 named approval record.

## Case 2 – Spreadsheet control failures

File: `workbooks/case_2_spreadsheet_control_failures.xlsx`

Gate 1 context:

- Entity: Aurora Life SA
- Period: 2025-Q4
- Currency: EUR
- Basis: IFRS 17 – synthetic demonstration
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

## Case 3 – Accounting reconciliation failure

Files:

- `workbooks/case_3_accounting_reconciliation_failure.xlsx`
- `reference_figures/case_3_reference_figures.csv`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora Life SA | Aurora Life SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | **GBP** – intentional mismatch |
| Basis | IFRS 17 – synthetic demonstration | IFRS 17 – synthetic demonstration |
| Signed net control total | – | 2,490,000 |

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

## Case 4 – Claims reserve roll-forward and GL reconciliation

Files:

- `workbooks/case_4_claims_reserve_roll_forward.xlsx`
- `reference_figures/case_4_reference_figures.csv`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora General Insurance SA | Aurora General Insurance SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | EUR |
| Basis | IFRS 17 – synthetic demonstration | IFRS 17 – synthetic demonstration |

At Gate 2, designate `Controls!B4` as the authoritative output.

Expected outcomes:

- Anomaly findings: none.
- Closing claims reserve (`Rollforward!B9`): `1,250,000 + 480,000 − 390,000 + 85,000 − 25,000 = 1,400,000`.
- `Controls!B4` (`Net claims reserve`) converts the positive actuarial magnitude to a signed credit accounting balance: `-1,400,000`.
- Internal reconstruction: complete, 100% coverage, delta `0`, preview verdict `pass`.
- Reference figures: account `2200`, `Net claims reserve`, amount `1,400,000`, `credit` — the signed reference amount is `-1,400,000`.
- Proposed accounting mapping: one-to-one, human approval required — a confident fuzzy match never self-approves.
- External reconciliation after mapping approval: delta `0`, verdict `pass`.
- AI documentation is optional for this case; declining it does not block the pipeline.
- PDF: available only after the Gate 4 named approval record.

This case is a synthetic workflow demonstration, not IFRS 17 methodology validation.

## Case 5 – Supported formula demonstration

Files:

- `workbooks/case_5_supported_formula_demonstration.xlsx`
- `reference_figures/case_5_reference_figures.csv`
- `expected_results/case_5_expected.json`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora Formula Assurance SA | Aurora Formula Assurance SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | EUR |
| Basis | Synthetic formula control demonstration | Synthetic formula control demonstration |
| Signed net control total | – | 1,620,523.06 |

At Gate 2, select every cell in `Outputs!C4:C29`. Each row has a unique label and account number so the external reconciliation remains one-to-one. At Gate 3, review and approve all 26 proposed mappings and select the internal and external materiality thresholds. The output table demonstrates every function in the live formula catalogue: `SUM`, `ABS`, `INT`, `ROUND`, `ROUNDUP`, `ROUNDDOWN`, `CEILING`, `FLOOR`, `SUMIF`, `SUMIFS`, `COUNTIF`, `COUNTIFS`, `AVERAGEIF`, `AVERAGEIFS`, `MINIFS`, `MAXIFS`, `IF`, exact `VLOOKUP`, exact `MATCH`, `INDEX` including nested `INDEX/MATCH`, `AND`, `OR`, `SUMPRODUCT`, `NPV`, `CHOOSE`, and exact `XLOOKUP`.

Expected outcomes:

- Anomaly findings: none.
- All 20 designated formula outputs reconstruct completely with zero deltas.
- Blank cells and numeric zero remain distinct in the input data.
- Positive and negative rounding examples match the independently recorded expected values.
- Every proposed mapping starts with `is_approved=False`; suggested confidence never approves a mapping.
- Internal verdict: `pass`; external verdict after human mapping approval: `pass`.
- `Scope Boundary!B4:B5` deliberately demonstrates approximate lookup and `OFFSET`; if selected separately, both are `partial` and `incomplete`, never complete.
- PDF: unavailable before Gate 4 and available only after the named approval record.

Presentation script: “Case 5 turns the declared formula scope into something a reviewer can see and challenge. Each supported function has a named financial control, an exact expected value and a selectable output cell. The two formulas outside scope remain visibly incomplete, proving that the agent does not turn partial reconstruction into false assurance.”

## Case 6 – Reserve stress and solvency impact

Files:

- `workbooks/case_6_reserve_stress_business_impact.xlsx`
- `reference_figures/case_6_reference_figures.csv`
- `expected_results/case_6_expected.json`
- `../benchmark/CASE_6_BENCHMARK_REPORT.md`

Gate 1 context:

| Field | Workbook | Reference figures |
|---|---|---|
| Entity | Aurora General Insurance SA | Aurora General Insurance SA |
| Period | 2025-Q4 | 2025-Q4 |
| Currency | EUR | EUR |
| Basis | Synthetic reserve stress demonstration | Synthetic reserve stress demonstration |
| Signed net control total | – | 52,177,824.82 |

At Gate 2, select `Accounting Bridge!C4` and `Accounting Bridge!C5`. At Gate 3, review the two one-to-one proposals, approve them only if the labels and accounting orientations are correct, and select both materiality thresholds. The adverse KPIs on `Impact Summary` are reconstructed and tested, but they are not externally mapped because the synthetic trial balance contains only the baseline booked figures.

Expected business result:

| Measure | Expected value |
|---|---:|
| Formula cells | 8,453 |
| Baseline technical provisions | EUR 11,411,087.59 |
| Adverse technical provisions | EUR 13,741,241.69 |
| Increase in technical provisions | EUR 2,330,154.10 |
| Baseline available own funds | EUR 63,588,912.41 |
| Adverse available own funds | EUR 61,258,758.31 |
| Reduction in available own funds | EUR 2,330,154.10 |
| Baseline solvency ratio | 181.68% |
| Adverse solvency ratio | 175.03% |
| Deterioration | 6.65 percentage points |

Expected workflow outcomes:

- Anomaly findings: none.
- Both designated accounting outputs reconstruct completely with zero deltas.
- Both mappings remain proposals until the human approves them.
- Internal verdict: `pass`; external verdict after human mapping approval: `pass`.
- PDF: unavailable before Gate 4 and available only after the named approval record.
- The committed benchmark records a median deterministic runtime of 6.990 seconds and median peak traced memory of 41.634 MB on the documented test machine. These figures describe this synthetic workbook only and do not establish production scalability.

This case demonstrates a transparent scenario workflow in which an adverse reserve result reduces synthetic available own funds and the solvency ratio. It is not a full actuarial model, does not implement or validate certified Solvency II or IFRS 17 methodology, and is not production-certified.

Presentation script: “Case 6 is a 300-cohort synthetic working model with 8,453 formulas, not a repeated test grid. Under the adverse assumptions, technical provisions rise by EUR 2.33 million, available own funds fall by the same amount, and the illustrative solvency ratio deteriorates by 6.65 percentage points. Glass Box reconstructs the booked baseline outputs, keeps the accounting comparison separate, and still requires a human to approve both mappings and all four gates.”

## Expected-result summary

| Case | Findings | Internal result | External result | Intended endpoint |
|---|---|---|---|---|
| 1 – Clean | none | pass | pass after mapping approval | PDF after Gate 4 |
| 2 – Controls | blocker + warnings | incomplete | not performed | explicit incomplete acknowledgement; findings remain recorded |
| 3 – Accounts | none | pass | block | Gate 3 stops the pipeline |
| 4 – Reserve roll-forward | none | pass | pass after mapping approval | PDF after Gate 4 |
| 5 – Formula catalogue | none | pass | pass after mapping approval | PDF after Gate 4 |
| 6 – Reserve stress | none | pass | pass after mapping approval | PDF after Gate 4 |

These are demonstration expectations for known synthetic inputs, not evidence that the tool validates an actuarial methodology or is production-certified.

## Calculation-freshness provenance

All six demonstration workbooks, plus the two static parser fixtures in `tests/fixtures/`, were recalculated once at build time using the LibreOffice engine recorded per file in [`recalculation_provenance.json`](recalculation_provenance.json). Cases 5 and 6 were recalculated with LibreOfficeDev 26.8.0.0.alpha0; Microsoft Excel compatibility was not tested. The provenance file records before-and-after workbook hashes, formula-manifest hashes, formula counts, verified values and byte-equality checks for the output-pack copies.

This was a one-time, manual, build-time fixture-generation step. It is **not** part of the running application: the application never invokes LibreOffice, Microsoft Excel, or any recalculation engine, and an arbitrary workbook a reviewer uploads is never recalculated by it. A cell not flagged stale in this prototype means only that no known staleness indicator was detected under these rules — it is not proof that the workbook was freshly recalculated in Excel, or by any particular engine, at any particular time.
