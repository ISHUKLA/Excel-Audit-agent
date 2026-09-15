# Case 7 guide — missing IFRS 17 cohort

## Demonstration

Both workbooks are entirely synthetic and use the same seven tabs. The clean
workbook aggregates all six cohorts. The defective workbook's claims `SUM`
stops one row early, omitting `Cash Flow Calculation!B9`, whose amount is
exactly EUR 6.2 million. Python reproduces each workbook as written; it does not
silently repair the defect.

| Result | Clean | Defective |
|---|---:|---:|
| Claims present value | EUR 104.2m | EUR 98.0m |
| Fulfilment cash flows | EUR 98.4m | EUR 92.2m |
| Closing liability | EUR 128.4m | EUR 122.2m |
| Internal verdict | `pass` | `pass` |
| External verdict | `pass` after mapping review | `block` below the EUR 6.2m difference |

The clean report becomes available only after all four human gates. The
defective path stops at Gate 3, so Gate 4 and PDF generation remain unavailable.

## Verify

Run `pytest tests/test_competition_cases_7_8.py -q`. For a manual check,
`128.4m - 122.2m = 6.2m`. Regenerate with
`python scripts/generate_competition_cases.py --cases 7` plus the documented
Node, Artifact Tool and LibreOffice paths.

## Boundaries

This demonstrates spreadsheet translation, an omitted-range review finding and
accounting reconciliation. It does not validate IFRS 17 methodology,
assumptions, classification, measurement choices, or disclosures.
