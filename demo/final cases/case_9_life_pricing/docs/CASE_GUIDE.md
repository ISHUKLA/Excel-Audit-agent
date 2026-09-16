# Case 9 guide — life pricing incomplete reconstruction

## Demonstration

This entirely synthetic protection-product projection shows annual premium
volume of EUR 120 million, an approved lapse assumption of 6%, and a 3% lapse
embedded directly in `Policy Projection!C4`. Supported intermediate arithmetic,
rounding, conditional aggregation, exact lookup and cross-tab references
reconstruct normally.

`=SUMPRODUCT(NetCashFlows,DiscountFactors)` now reconstructs independently, so
the EUR 4.6 million present-value margin is traceable. The designated
`Profitability Summary!B8` still depends on `=IRR(NetCashFlows)`, which remains
outside the published catalogue. Python therefore supplies no substitute target
for the designated profitability result and never describes that cached output
as reconstructed or verified. The internal verdict is `incomplete`, never
`pass`.

No accounting figures are supplied, so the external verdict is explicitly
`not_performed`. The live demonstration leaves the existing incomplete-result
acknowledgement unchecked: Gate 3 blocks and Gate 4/PDF stay unavailable. The
software contract does contain an explicit human acknowledgement path for an
incomplete result; this case does not use it.

The negative EUR 4.8 million separately controlled result and EUR 9.4 million
illustrative swing are contextual evidence only. They are not results certified
by Glass Box.

## Verify

Run `pytest tests/test_competition_cases_9_10.py -q`. Inspect the formula
inventory for the one unsupported formula cell and spot-check
`4.6m - (-4.8m) = 9.4m`. Regenerate with
`python scripts/generate_competition_cases.py --cases 9` plus the documented
Node, Artifact Tool and LibreOffice paths.

## Boundaries

This demonstrates honest incompleteness and a hardcoded-assumption finding. It
does not validate product pricing, lapse behaviour, profitability, or launch
suitability.
