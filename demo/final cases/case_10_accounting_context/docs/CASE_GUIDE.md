# Case 10 guide — right numbers, wrong accounting context

## Demonstration

The workbook is synthetic Belgium/EUR evidence for 31 December 2025. The CSV is
synthetic UK/GBP evidence for 30 September 2025. Their three plausible mappings
have zero numeric difference after a named human approves the proposals, but
the comparisons do not describe the same accounting context.

```text
Mapped numeric difference: 0
Internal consistency: PASS
External accounting reconciliation: BLOCK
```

The evidence preserves each reason separately:

- entity mismatch;
- period mismatch;
- currency mismatch;
- unmatched `REF-0004` and `REF-0005`, two distinct rows both labelled
  `Other technical provisions`; and
- unmapped `Accounting Bridge!B7`, the EUR 0.3 million management overlay.

Population gaps cap the accounting comparison at `incomplete` on their own.
The incompatible entity, period and currency independently force the external
`block`; approving fuzzy mappings cannot cure it. Gate 4 and PDF generation
remain unavailable.

## Verify

Run `pytest tests/test_competition_cases_9_10.py -q`. Spot-check the mapped
workbook values of 78.4m, 9.6m and 7.5m against the first three CSV rows; all
three deltas are zero. The five-row CSV control total is 96.25m. Regenerate with
`python scripts/generate_competition_cases.py --cases 10` plus the documented
Node, Artifact Tool and LibreOffice paths.

## Boundaries

This is the refusal-to-provide-comfort demonstration. It does not provide an
audit opinion or imply that a numerical match validates an accounting mapping.
