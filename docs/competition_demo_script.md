# Competition demo script (4–5 minutes)

Practical video script for Case 4 — claims reserve roll-forward and GL
reconciliation. Every claim below is one the prototype can actually show on
screen; nothing here is a benchmark, a savings estimate, or a validation claim.

Core message, said early and repeated at the close:

> Deterministic Python establishes the evidence. Optional AI explains the
> evidence. Qualified humans decide.

## 1. Problem and actuarial use case (30s)

Actuarial reserve figures move from a spreadsheet to a general ledger through
a chain of formulas, sign conventions, and manual reconciliation that is easy
to get subtly wrong and hard to audit after the fact. This tool independently
reconstructs the spreadsheet's own formulas in Python and checks them against
both the workbook's cached numbers and a supplied accounts extract — it does
not certify the result; it surfaces what it found for a named human to decide.

## 2. Deterministic-versus-AI architecture (30s)

Show the architecture diagram or say it plainly: Agents 1–3 (parsing, anomaly
detection, reconciliation) are pure Python — no LLM involved, fully
deterministic, unit tested. Agent 4 (documentation) is the only LLM caller,
is optional, and only explains findings after they already exist — it never
produces a verdict.

## 3. Load Case 4 (15s)

Open the Streamlit app, select "Case 4: Claims Reserve Roll-Forward" from the
demo selector. Show that this seeds the upload and context fields only — the
audit still runs through all four gates normally.

## 4. Gate 1 — workbook identity and context (30s)

Show the confirmed workbook hash and the Gate 1 context screen: entity
"Aurora General Insurance SA", period 2025-Q4, currency EUR. Confirm the
context before anything is parsed — this is a human checkpoint, not a
formality.

## 5. Gate 2 — findings and authoritative output (30s)

Show zero anomaly findings for this workbook. Designate `Controls!B4` as the
authoritative output and explain why: it is the cell where the actuarial
reserve becomes a signed accounting balance.

## 6. Gate 3 — reconstruction, signed credit treatment, mapping, control total, AI (90s)

- **Reconstruction:** show the derivation chain from `Controls!B4` back through
  `Rollforward!B9` to the five `Inputs` cells — 100% formula coverage, delta
  zero against the workbook's own cached value.
- **Signed credit treatment:** point out that the actuarial reserve
  (`Rollforward!B9` = 1,400,000, a positive magnitude) is deliberately
  converted to a negative, credit-orientation balance at `Controls!B4`
  (-1,400,000) — the existing debit/credit sign convention, not a new one
  invented for this case.
- **GL mapping:** show the proposed one-to-one account mapping to the
  reference CSV's "Net claims reserve" line, and that it sits unapproved until
  a named human clicks approve — a confident fuzzy match never self-approves.
- **Control total:** show the external reconciliation line pass at delta
  zero, only after mapping approval.
- **Optional AI decision:** show declining AI documentation for this run —
  the pipeline still completes; AI is optional, not mandatory.

## 7. Gate 4 — named approval (20s)

Enter a name and role from the local authorized-approver registry. This is a
named approval record — explicitly not a "signature" or "attestation" in
this tool's vocabulary.

## 8. PDF / audit evidence (30s)

Generate and open the PDF. Show the disclaimer at the top ("Translation &
Reconciliation Report" — not a validation of the actuarial model), the
derivation chain evidence, the mapping table (approved and any unapproved
proposals both shown), and the append-only, hash-chained audit log. State
plainly: tamper-evident, not tamper-proof.

## 9. Honest limitations (30s)

Say these explicitly, on camera:
- Synthetic demonstration data only.
- Not a claim of IFRS 17 methodology validation, Excel-semantic equivalence,
  or independent assurance.
- No application-level authentication; local-first by design.
- "Not flagged stale" is not the same claim as "verified fresh" — it means no
  known staleness indicator was detected under this prototype's rules.

## 10. Closing business-value statement (15s)

> Deterministic Python establishes the evidence. Optional AI explains the
> evidence. Qualified humans decide — at every one of the four gates, with a
> tamper-evident record of who decided what.
