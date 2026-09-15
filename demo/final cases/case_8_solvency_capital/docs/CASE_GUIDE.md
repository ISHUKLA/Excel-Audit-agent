# Case 8 guide — Solvency II capital and dividend discussion

## Demonstration

The synthetic workbook displays approved and defective capital bases side by
side. The defective Final SCR formula embeds EUR 47 million directly instead of
referencing the approved EUR 35 million diversification assumption.

| Metric | Approved basis | Workbook calculation |
|---|---:|---:|
| Eligible own funds | EUR 180m | EUR 180m |
| SCR before diversification | EUR 150m | EUR 150m |
| Diversification benefit | (EUR 35m) | (EUR 47m) |
| Operational risk | EUR 8m | EUR 8m |
| Final SCR | EUR 123m | EUR 111m |
| Solvency ratio | 146.3% | 162.2% |

Management's synthetic internal floor is 150%. The defective ratio appears to
support considering a EUR 10 million dividend; the approved-basis ratio is below
that floor. Glass Box neither decides whether a dividend should be paid nor
validates Solvency II methodology.

The hardcoded-literal finding is reviewed at Gate 2. Internal reconstruction
passes at EUR 111 million, while the separate accounting comparison has a
EUR 12 million difference and blocks at Gate 3 when the human-entered threshold
is lower. Only Final SCR is designated; the percentage is not forced through a
currency reconciliation.

## Verify

Run `pytest tests/test_competition_cases_7_8.py -q`. Spot-check
`150m - 47m + 8m = 111m` and `180m / 111m = 162.2%` after display rounding.
Regenerate with `python scripts/generate_competition_cases.py --cases 8` plus
the documented Node, Artifact Tool and LibreOffice paths.
