# Case 14 — The board pack shows the number from before the assumption change

This synthetic general-insurance reserving case demonstrates calculation
freshness. It does not opine on the tail factor, reserve adequacy, or reserving
methodology.

## Story

The workbook was recalculated with a tail development factor of `1.042`. After
that recalculation, `Assumptions!C14` was changed to `1.061` and the workbook's
calculation mode was set to manual without refreshing formula caches. The Board
Summary therefore still displays ultimate claims of EUR 148.2m and closing IBNR
of EUR 41.7m. Reconstructing the formulas from the current inputs produces EUR
157.5m and EUR 51.0m respectively, an understatement of EUR 9.3m.

`Board Summary!B4` is deliberately unchanged: its cache and reconstruction are
both EUR 65.0m. Because its derivation still consists of formula cells in a
manual-calculation workbook, its exact zero delta is also `incomplete`, never
`pass`.

The supplied synthetic GL extract contains the two stale board-pack amounts.
After a human approves the proposed mappings, the accounts-side lines inherit
the stale evidence and remain `incomplete`, including the EUR 9.3m difference.

## Expected four-gate journey

1. Gate 1 confirms the workbook hash and the synthetic entity, period, currency,
   and basis.
2. Gate 2 designates `Board Summary!B4` and `Board Summary!B9` as authoritative
   outputs. Any detector findings must still be dispositioned normally.
3. Gate 3 shows internal and external verdicts of `incomplete` and names every
   stale formula cell in each derivation. Leave **Acknowledge incomplete**
   unchecked. Gate 3 stops the run.
4. Gate 4 and PDF export remain unavailable.

## Evidence construction

`scripts/build_case_14.mjs` authors the old-input workbook with Artifact Tool.
`scripts/finalize_case_14.py` recalculates it once with LibreOffice, then edits
the worksheet and workbook OOXML directly to set `Assumptions!C14` to `1.061`
and `calcMode="manual"`. It never opens and resaves the workbook after that
edit, so the old formula `<v>` values remain intact.

Rebuild with the repository's configured Node, Artifact Tool module,
LibreOffice executable, and Python environment. Then verify with:

```bash
pytest tests/test_case_14_stale_board_pack.py tests/test_verdict_logic.py -q
```
