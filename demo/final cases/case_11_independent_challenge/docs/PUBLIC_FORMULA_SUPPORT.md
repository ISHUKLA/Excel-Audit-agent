# Public reconstruction scope handout

This is the scope the independent author receives before authoring the
challenge. It describes the current deterministic Python reconstruction engine,
not every formula that Excel or LibreOffice can calculate.

## Supported

Plain arithmetic with cell references and these 32 functions:

| Family | Functions |
|---|---|
| Basic numeric | `ABS`, `INT` |
| Rounding | `ROUND`, `ROUNDUP`, `ROUNDDOWN`, `CEILING`, `FLOOR` |
| Conditional and aggregate | `SUM`, `SUMIF`, `SUMIFS`, `COUNTIF`, `COUNTIFS`, `AVERAGEIF`, `AVERAGEIFS`, `IF` |
| Plain and criteria min/max | `MAX`, `MIN`, `MINIFS`, `MAXIFS` |
| Exact lookup | `VLOOKUP`, `MATCH`, `INDEX` |
| Logical | `AND`, `OR` |
| Array product | `SUMPRODUCT` |
| Time value of money | `NPV` |
| Index-based selection | `CHOOSE` |
| Exact lookup extension | `XLOOKUP` |
| Date arithmetic | `DATE`, `EDATE`, `NETWORKDAYS`, `YEARFRAC` |

Cross-tab references and nested supported functions are accepted. `ROUND` uses
Excel-style half-away-from-zero behaviour; `INT` floors toward negative
infinity. Blank handling follows the individual aggregate's documented engine
behaviour.

`VLOOKUP` is supported only with `range_lookup=FALSE`. `MATCH` is supported only
with `match_type=0`. `INDEX` must return one scalar cell. `XLOOKUP` is supported
only for exact match mode and a scalar numeric result. `SUMPRODUCT` requires
matching range dimensions. `NPV` uses Excel's period-one convention for periodic
cash flows. Lookup results used in reconstruction must be numeric.

`EDATE`, `NETWORKDAYS` and `YEARFRAC` accept Excel serial dates, including
results returned by `DATE`. A direct reference to a date-formatted value cell
is outside the current reconstruction scope. `NETWORKDAYS` supports an optional
holiday range. `YEARFRAC` supports basis values 0 through 4.

## Explicitly outside this published scope

- every unlisted function, including `IRR`, `XIRR` and `OFFSET`;
- approximate `VLOOKUP` or `MATCH` modes;
- array formulas, CSE arrays and dynamic-array spill results;
- lookup operations that return whole rows/columns or text into arithmetic;
- external-workbook references; and
- unsupported values or dependency chains whose result cannot be independently
  reconstructed.

Unsupported formulas are retained verbatim. A chain that depends on one is
reported as partial/incomplete, its Python target remains empty, and a cached
spreadsheet value is not relabelled as verified. The author should include both
in-scope and out-of-scope behaviour; do not redesign the challenge to maximise
the supported percentage.

This handout is generated from the same 30-function scope represented in
`core/formula_catalogue.py`; that source file remains authoritative if a frozen
release and this handout ever disagree.
