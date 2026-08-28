# One-slide summary

**Problem.** Actuarial and financial Excel workbooks carry formula errors,
stale calculations, and sign-convention mistakes through to the general
ledger with no independent check — and no audit trail of who accepted what.

**Solution.** Excel Audit Agent independently reconstructs a workbook's own
formulas in deterministic Python, checks them against the workbook's cached
values and (optionally) a supplied accounts extract, and routes every finding
through one of four mandatory human gates before producing a report. It never
certifies a number on its own.

**Innovation.** Deterministic Python does the calculation checking; an LLM is
used only — and optionally — to explain findings that already exist in plain
language. The two are never merged: a finding's verdict never depends on what
the LLM said, and the internal (Excel-vs-Python) and external
(Python-vs-accounts) comparisons stay on two separate, never-collapsed
verdicts.

**Actuarial relevance.** Case 4 demonstrates a claims-reserve roll-forward
(opening reserve, incurred claims, paid claims, assumption strengthening, FX)
bridged to a signed general-ledger credit balance — the kind of
actuarial-to-finance handoff that is usually reconciled by hand.

**Technical evidence.**
- 494 automated tests passed on the submitted commit, locally and in GitHub CI on Python 3.11 and 3.13.
- An append-only, hash-chained audit log (tamper-evident, not tamper-proof).
- A stale/unknown calculation status that can never silently produce a pass,
  under any threshold — including a zero delta.

**Control boundary.** No claim of IFRS 17 methodology validation, Excel
correctness, independent assurance, or production readiness. Local-first,
no application-level authentication. Synthetic demonstration data only.

**Three proof points.**
1. Every finding is disposed of individually — confirmed, overridden, or
   dismissed with a reason — never silently resolved.
2. A confident fuzzy account-name match is a proposal, never an approval; a
   named human always approves the mapping.
3. Stale or freshness-unknown calculation evidence caps every affected
   verdict at "incomplete" — a zero-width threshold cannot override it.

**Closing message.** Deterministic Python establishes the evidence. Optional
AI explains the evidence. Qualified humans decide.
