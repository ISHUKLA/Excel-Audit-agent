# Expected Results

This directory contains one concise, machine-readable JSON file per demonstration
case, documenting the outcome a judge or reviewer should see when running the
pipeline against the committed workbook.

## Structure

- `case_1_expected.json` — Clean reserve calculation: pass / pass.
- `case_2_expected.json` — Spreadsheet control failures: incomplete / not performed.
- `case_3_expected.json` — Accounting reconciliation failure: pass / block.
- `case_4_expected.json` — Claims reserve roll-forward: pass / pass after mapping approval.

Each file records: case number and title; a synthetic-data confirmation; the
workbook and reference-CSV paths; the confirmed Gate 1 context; the
authoritative output cell(s); expected finding categories and counts; expected
internal and external verdicts; the expected overall result or endpoint;
required human actions; and material limitations. `tests/test_demo_cases.py`
asserts that the pipeline's actual, deterministic output matches these files.
