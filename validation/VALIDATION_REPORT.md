# Validation and qualification report

Date: 14 September 2026

## Scope and result

This work package qualifies the Excel Audit Agent's declared formula-reconstruction scope against recalculated `.xlsx` files and independently specified expected values. It also exercises a synthetic IFRS 17-related workflow at scale, records a reproducible benchmark and adds automated checks against evidence drift.

All data is synthetic. The result is not Microsoft Excel equivalence, production qualification, IFRS 17 methodology validation or an audit opinion.

The nine completion criteria are met against the committed machine-readable evidence contracts:

1. Every function in `SUPPORTED_FUNCTIONS` has a production-parser workbook case.
2. Supported cases are compared with independently declared expected values and, secondarily, cached workbook values.
3. Approximate lookups, array results and `OFFSET` fail closed as `partial` and `incomplete`.
4. The large fixture contains 10,847 formula cells.
5. The clean large case completes Gates 1, 2, 3 and 4 and generates a PDF only after Gate 4.
6. All six recorded defects are detected in their correct evidence channels.
7. Five raw benchmark runs, an aggregate CSV and this benchmark's Markdown report are committed.
8. README and demonstration contracts are checked against the implementation.
9. The complete suite is required to pass with no skips before commit. The exact count is deliberately reported by the test command and CI rather than frozen in documentation.

## Formula acceptance matrix

The workbook was recalculated with `LibreOfficeDev 26.8.0.0.alpha0`. Its committed SHA-256 is `d6eaaf11c98861ef4218d4a72f453edf86f8cde9c9d7a5c52d049d8d439a7bc0`. Each row below runs through production `parse_workbook()` and `run_reconciliation()`. Numeric comparisons use the test suite's approximate comparison to accommodate workbook serialization precision.

| Function | Test cell | Expected | Reconstructed | Cached | Result |
|---|---|---:|---:|---:|---|
| `ABS` | `Qualification!B5` | 5 | 5 | 5 | Pass |
| `AVERAGEIF` | `Qualification!B17` | 1.666666667 | 1.666666667 | 1.666666667 | Pass |
| `AVERAGEIFS` | `Qualification!B18` | 2.5 | 2.5 | 2.5 | Pass |
| `CEILING` | `Qualification!B11` | 2.5 | 2.5 | 2.5 | Pass |
| `COUNTIF` | `Qualification!B15` | 3 | 3 | 3 | Pass |
| `COUNTIFS` | `Qualification!B16` | 3 | 3 | 3 | Pass |
| `FLOOR` | `Qualification!B12` | 2.5 | 2.5 | 2.5 | Pass |
| `IF` | `Qualification!B21` | 11 | 11 | 11 | Pass |
| `INDEX` | `Qualification!B27` | 1.2 | 1.2 | 1.2 | Pass |
| `INT` | `Qualification!B6` | -2 | -2 | -2 | Pass |
| `MATCH` | `Qualification!B26` | 3 | 3 | 3 | Pass |
| `MAXIFS` | `Qualification!B20` | 10 | 10 | 10 | Pass |
| `MINIFS` | `Qualification!B19` | -5 | -5 | -5 | Pass |
| `ROUND` | `Qualification!B7` | 3 | 3 | 3 | Pass |
| `ROUNDDOWN` | `Qualification!B10` | -1.2 | -1.2 | -1.2 | Pass |
| `ROUNDUP` | `Qualification!B9` | -1.3 | -1.3 | -1.3 | Pass |
| `SUM` | `Qualification!B4` | 25 | 25 | 25 | Pass |
| `SUMIF` | `Qualification!B13` | 5 | 5 | 5 | Pass |
| `SUMIFS` | `Qualification!B14` | 5 | 5 | 5 | Pass |
| `VLOOKUP` | `Qualification!B25` | 1.2 | 1.2 | 1.2 | Pass |

Additional passing cases cover ordinary arithmetic, positive and negative exact-half rounding, `TRUE`, `FALSE`, `TRUE()`, `FALSE()`, a Boolean cell used by `IF`, nested `ROUND(SUMIF(...))`, nested `INDEX(MATCH(...))`, cross-tab ranges, blanks, zeros, text in numeric ranges and negative values. `Errors!B4:B6` preserve recalculated `#N/A`, `#VALUE!` and `#DIV/0!` parser evidence.

### Deliberately unsupported cases

| Construct | Test cell | Expected state | Observed state |
|---|---|---|---|
| Approximate `VLOOKUP` | `Qualification!B32` | partial / incomplete | partial / incomplete |
| Approximate `MATCH` | `Qualification!B33` | partial / incomplete | partial / incomplete |
| Array-returning `INDEX` | `Qualification!B34` | partial / incomplete | partial / incomplete |
| `OFFSET` | `Qualification!B35` | partial / incomplete | partial / incomplete |

## Large synthetic IFRS 17-related workbook

The workbook represents spreadsheet reconstruction and financial reconciliation around a synthetic IFRS 17-related reserve roll-forward. It is not a full IFRS 17 model.

| Measure | Evidence |
|---|---:|
| Seed | 42 |
| Entity | Aurora Mutual Life SE (synthetic) |
| Period | 2025-Q4 |
| Reporting currency | EUR |
| Cohorts | 600 |
| Tabs | 7 |
| Populated cells | 15,806 |
| Formula cells | 10,847 |
| Dependency edges | 69,069 |
| Supported formula cells | 10,847 |
| Unsupported formula cells | 0 |
| Designated-output reconstruction coverage | 100% |
| Clean anomaly findings | 0 |
| Clean internal verdict | pass |
| Clean external verdict after human mapping approval | pass |

The workbook SHA-256 is `d5f5db316247e49b7ab7f191fd3d19b6f824de3c7667f0ccfe3402492dd0413c`. It contains the tabs `Assumptions`, `CohortData`, `ReserveRollforward`, `Aggregation`, `AccountingBridge`, `Controls` and `DemoGuide` in that order.

### Hand-checkable control totals

| Cell | Output | Expected EUR |
|---|---|---:|
| `AccountingBridge!C4` | Opening reserve | -68,698,614.30 |
| `AccountingBridge!C5` | Claims and service movements | -8,615,833.16 |
| `AccountingBridge!C6` | Assumption strengthening | -1,030,479.41 |
| `AccountingBridge!C7` | FX movement | -2,433,696.30 |
| `AccountingBridge!C8` | Closing reserve | -86,856,817.91 |
| `AccountingBridge!C9` | Signed accounting balance | -167,635,441.08 |

The separate `accounting_reference.csv` carries account number, label, debit or credit orientation, entity, period, currency, basis, evidence reference and signed-net control total. The proposed fuzzy mapping remains unapproved until a named human approves it at Gate 3. Internal and external verdicts remain separate throughout.

## Seeded-defect detection matrix

The defective workbook SHA-256 is `5ae34fac91157d94f966decf0ddbc6fda61fea250b2fbe198ded1aaef659f9b5`. The separate defective reference extract SHA-256 is `b8895ef21382ed89e53a57af848bf49d66766f5139fd6290aefae73998e619cc`.

| ID | Type | Expected evidence | Observed evidence | Detected |
|---|---|---|---|---|
| D1 | Hardcoded assumption | Warning at `ReserveRollforward!E4` naming literal `0.035` | Exact warning at the cell | Yes |
| D2 | Omitted row | Warning at `Aggregation!J8` naming skipped `J5` | Exact warning and skipped cell | Yes |
| D3 | Unsupported formula | `AccountingBridge!C9` contains `OFFSET`; partial / incomplete | `OFFSET` named; partial / incomplete | Yes |
| D4 | Unmatched accounting line | `REF-0002` remains unmatched | `REF-0002` in `unmatched_reference_items` | Yes |
| D5 | Unmapped Python output | `AccountingBridge!C10` remains unmapped | Cell in `unmapped_python_outputs` | Yes |
| D6 | Context mismatch | Workbook EUR versus reference GBP | Gate 1 context verdict `mismatch`; external Gate 3 preview blocks | Yes |

No additional anomaly findings occurred in the controlled defective fixture. D3 to D6 deliberately remain in their correct evidence models rather than being forced into `AnomalyFinding`: formula completeness, accounting population completeness and context evidence are distinct controls.

The source specification contains two wording tensions that this implementation resolves explicitly. D2 is represented as a visible row omitted from a portfolio aggregate, because that is the existing generic detector's observable rule; it does not claim to detect a physically deleted cohort record. D6 follows the table's `context mismatch` classification and the project's existing entity, period and currency precondition; it does not reinterpret a currency mismatch as an Excel cached-value mismatch.

## Benchmark

One warm-up was followed by five measured runs. The complete raw evidence is under `benchmark/runs/`, with aggregation in `benchmark/summary.csv`.

| Metric | Median | Slowest or highest |
|---|---:|---:|
| Parsing time | 6.063 s | 7.316 s |
| Anomaly detection time | 2.200 s | 2.648 s |
| Reconstruction time | 4.036 s | 5.627 s |
| Total deterministic pipeline time | 11.817 s | 15.590 s |
| Peak traced memory | 56.200 MB | 63.804 MB |

Environment: Python 3.12.11, macOS 26.6.2, Intel Core i5-1038NG7 at 2.00 GHz, 8 logical cores and 16 GB RAM. No p95 is stated because five samples are too few. No CI timing threshold was added.

## Real implementation defects found and fixed

These were application defects discovered by qualification, not deliberately seeded workbook errors:

1. `TRUE()` and `FALSE()` were interpreted as unsupported functions rather than Boolean tokens. Formula normalization now treats their called and bare forms equivalently.
2. LibreOffice's `_xlfn.` compatibility prefix caused `MINIFS` and `MAXIFS` to be misclassified. Compatibility prefixes are now normalized before catalogue lookup and evaluation.
3. An `IF` condition referencing a Boolean cell failed through the arithmetic-only resolution path. The evaluator now preserves Boolean condition references.
4. Arithmetic expressions used binary floating-point intermediates before Excel-style `ROUND`, producing a EUR 0.20 aggregate drift in the large fixture. Arithmetic evaluation now uses `Decimal` through intermediate operations and converts only the final value to the existing float model contract.
5. `MAX`/`MIN` extracted a cell reference from an argument that was actually an arithmetic expression (e.g. `MAX(A1*2,3)`) and silently discarded the surrounding arithmetic, returning the raw referenced value as if the caller had written a bare reference; `DATE`'s year argument was passed straight to serial-date construction with no adjustment, so `DATE(24,1,1)` resolved as literal year 24 AD instead of Microsoft's documented 0–1899 → +1900 mapping (1924-01-01). Found by external probing of argument expressions in `MAX`/`MIN` and the `DATE` year-mapping rule — a case the original qualification fixtures never exercised, since they only ever passed `MAX`/`MIN` a bare range or a bare literal, never an expression. This is the worst failure mode an audit tool can produce: a reconstruction error, not a workbook error, is capable of registering a false `block` verdict against a cell whose cached value was actually correct — the tool flagging a clean calculation as defective. Both evaluators now route a non-bare-reference argument through the same shared arithmetic-expression evaluator (`_resolve_and_eval_expr`) `ABS`/`ROUND`/etc. already use for their own arguments; `DATE`'s year argument now applies Microsoft's documented offset before serial-date construction. Confirmed end-to-end through the real upload → parse → reconstruct → reconcile path (`parse_workbook()` → `run_reconciliation()`), not just the internal evaluator called directly.
6. `actor`, `event_type`, and `report_id` were stored beside the old row hash rather than inside its protected input. After a direct-file attacker disabled the SQLite append-only trigger, those identifying fields could be changed while `verify_chain()` still returned valid; `timestamp` and decision content were already covered. This was found by external review, not the original qualification work. The fix computes an unambiguous canonical event hash over fixed JSON keys for `report_id`, `event_type`, `actor`, `timestamp`, and `payload_hash`, then chains `SHA256(prev_row_hash + event_hash)`. A separate append-only JSONL checkpoint API records the terminal row ID and hash, allowing `verify_against_checkpoint()` to detect the inherent last-row-deletion limitation when that checkpoint is retained outside the mutable database. The before/after matrix in `tests/test_audit_log_metadata_tampering.py` guards actor, event type, report ID, timestamp, payload content, row reordering, middle-row deletion, fabricated in-chain insertion, tail deletion, the external checkpoint comparison, and an actual Case 2 gate event produced by `Orchestrator.run()`. Payload tampering, reordering, and middle-row deletion were already detected before this fix; this closes a specific metadata-coverage gap and adds an explicit control for a known limitation, rather than claiming the earlier log had no protection.

Each fix has focused regression coverage in `tests/test_reconciliation.py`, except defect 5, which has `tests/test_bugfix_max_min_date.py`, and defect 6, which has `tests/test_audit_log_metadata_tampering.py` plus the existing audit/state/end-to-end suites.

## Files created

- `scripts/generate_qualification_workbook.py`: builds expected values and the unrecalculated formula fixture.
- `scripts/recalculate_with_libreoffice.sh`: reproducibly recalculates and qualifies the formula fixture.
- `scripts/generate_ifrs17_workbook.py`: builds, recalculates and manifests the large synthetic clean workbook.
- `scripts/seed_defects.py`: applies six named defects without modifying the clean workbook.
- `scripts/benchmark.py`: runs warm-up and measured production-path benchmarks.
- `scripts/refresh_case2_demo_contract.py`: refreshes Case 2 after exact-match lookup support changed its original contract.
- `tests/fixtures/qualification_workbook_unrecalculated.xlsx`: retained pre-recalculation generation evidence.
- `tests/fixtures/qualification_workbook.xlsx`: committed pre-recalculated acceptance fixture.
- `tests/fixtures/qualification_expected.json`: independent acceptance values and expected completeness.
- `tests/fixtures/qualification_manifest.json`: engine, hash and cell-level function manifest.
- `tests/fixtures/ifrs17_workbook_clean.xlsx`: clean 10,847-formula synthetic workflow.
- `tests/fixtures/ifrs17_workbook_defective.xlsx`: controlled failure variant.
- `tests/fixtures/accounting_reference.csv`: clean synthetic accounting extract.
- `tests/fixtures/accounting_reference_defective.csv`: unmatched-line and currency-mismatch extract.
- `tests/fixtures/ifrs17_expected.json`: clean outputs and four-gate contract.
- `tests/fixtures/ifrs17_manifest.json`: large-workbook provenance and formula inventory.
- `tests/fixtures/expected_defects.json`: six-defect machine-readable contract.
- `tests/test_qualification_workbook.py`: per-formula production-path acceptance tests.
- `tests/test_ifrs17_clean.py`: scale checks and full four-gate clean journey through PDF generation.
- `tests/test_ifrs17_defective.py`: exact defect and Gate 3 blocking evidence checks.
- `tests/test_evidence_integrity.py`: catalogue, README, demo and benchmark drift checks.
- `benchmark/runs/*.json`: five raw measured runs.
- `benchmark/summary.csv`: aggregate measured values.
- `benchmark/BENCHMARK_REPORT.md`: concise results and limitations.
- `validation/VALIDATION_REPORT.md`: this consolidated evidence report.

## Files changed

- `agents/reconciliation.py`: Boolean, compatibility-prefix, Boolean-condition and Decimal-intermediate fixes.
- `tests/test_reconciliation.py`: focused regressions for the evaluator fixes.
- `README.md`: current function scope, qualification evidence, benchmark and formula semantics.
- `AGENTS.md`: AI-written change log.
- `demo/workbooks/case_2_spreadsheet_control_failures.xlsx`: replaces the now-supported exact lookup with an unsupported approximate lookup and makes the omitted row visible.
- `demo/expected_results/case_2_expected.json`: updated Case 2 evidence contract.
- `demo/README.md` and `demo/recalculation_provenance.json`: updated instructions and recalculation evidence.
- `outputs/ai2_2026_demo_pack_20260824/README.md`, workbook copy and provenance copy: keeps the distributable demonstration pack byte-aligned with the canonical Case 2 assets.

## What the tests cover

- Production parsing and reconstruction of every declared function from a real `.xlsx` fixture.
- Independently known results, cached-value agreement, Boolean representations, nested formulas, cross-tab ranges, blank versus zero, text in ranges, error cells and exact-half rounding.
- Fail-closed behaviour for unsupported lookup modes, array results and `OFFSET`.
- A clean synthetic workflow through all four real gates, human mapping approval and PDF generation.
- Exact evidence for six controlled failure conditions.
- Cases 1, 2 and 3 against machine-readable contracts through production parser and reconciliation code.
- Documentation-to-catalogue and evaluator-to-catalogue integrity.

## Remaining limitations

- The workbooks are synthetic. No customer or production workbook was tested.
- LibreOffice performed fixture recalculation. Microsoft Excel compatibility was not tested.
- The running application does not recalculate uploaded workbooks. It reads supplied formulas, cached values and calculation metadata.
- The reconstruction catalogue remains limited to 20 functions and the documented arithmetic grammar. Approximate lookups, arrays, volatile references and other formula families remain unsupported and incomplete.
- The large case is IFRS 17-related, not a full IFRS 17 model or methodology test.
- The benchmark covers one workbook on one machine. It excludes concurrent use, hosted operation, sustained load, Streamlit interaction, PDF generation, persistent snapshots and Anthropic API calls.
- `tracemalloc` is Python allocation evidence, not whole-process resident memory.
- The defect test measures controlled, known faults. It is not a statistical false-positive or recall study over a representative corpus.
- The same person can still complete every gate; application authentication is not implemented; the audit log is tamper-evident rather than tamper-proof; LLM data minimization remains an informal heuristic.

## Reproduction commands

Normal correctness verification does not require LibreOffice because the qualified workbooks are committed:

```bash
python -m pip install -r requirements.txt
python -m pytest tests/ -v --no-header
python scripts/benchmark.py --runs 5
```

Fixture regeneration does require LibreOffice and will replace the committed evidence hashes:

```bash
python scripts/generate_qualification_workbook.py
bash scripts/recalculate_with_libreoffice.sh
python scripts/generate_ifrs17_workbook.py
python scripts/seed_defects.py
python scripts/refresh_case2_demo_contract.py
python -m pytest tests/ -v --no-header
```

Reviewers can verify exact committed hashes with:

```bash
shasum -a 256 tests/fixtures/qualification_workbook.xlsx
shasum -a 256 tests/fixtures/ifrs17_workbook_clean.xlsx
shasum -a 256 tests/fixtures/ifrs17_workbook_defective.xlsx
shasum -a 256 tests/fixtures/accounting_reference_defective.csv
```
