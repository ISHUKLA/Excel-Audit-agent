# CLAUDE.md — Excel Audit Agent

Operating context for every Claude Code session in this repo. The rules below are
not suggestions; they are the operational form of decisions already made. Source
documents: `claude_code_prompting_algorithm_6.md` (the build steps) and
`AI_Collaboration_4Ds_Framework.md` (the reasoning behind the rules). Neither is
in this repo — both live in `~/Downloads/`.

---

## What this project is

Excel Audit Agent parses an actuarial or financial Excel workbook, independently
reconstructs its formulas in Python to check them against the spreadsheet's own
numbers, and optionally reconciles the result against a set of accounts figures
the user supplies. Every finding and every reconciliation line is routed through
a human reviewer at one of four gates before a PDF report is produced.

The tool never certifies a number on its own. It surfaces what it found; a named
human decides what to do with it.

## The four human gates

None of these can be skipped, merged, simplified, or bypassed — not even
temporarily, not even for a demo.

1. **Context confirmation** — the user confirms the file description (and any
   supplied accounts figures) are accurate, before anything is parsed.
2. **Findings review** — every anomaly is shown individually and must be
   confirmed, overridden, or dismissed with a reason. The pipeline does not
   proceed until every finding has a disposition.
3. **Reconciliation sign-off** — the two comparisons (internal consistency;
   accounts reconciliation) are shown side by side, the human sets the
   materiality threshold for each, and a blocking discrepancy stops the process.
4. **Named approval record** — a named person confirms their identity against a
   local registry. Only then is the PDF generated.

## Repo layout

- [app.py](app.py) — Streamlit UI. Calls and renders only; no business logic.
- [agents/parser.py](agents/parser.py) — Agent 1. Reads the workbook.
- [agents/anomaly_detector.py](agents/anomaly_detector.py) — Agent 2. Rule-based, no LLM.
- [agents/reconciliation.py](agents/reconciliation.py) — Agent 3. Two independent passes.
- [agents/documentation.py](agents/documentation.py) — Agent 4. The only LLM caller.
- [agents/orchestrator.py](agents/orchestrator.py) — sequences the pipeline across gates.
- [core/models.py](core/models.py) — Pydantic models. The contract every other module reads from.
- [core/gates.py](core/gates.py) — the four gates. Raise `GateBlockedError`, never silently pass.
- [core/audit_log.py](core/audit_log.py) — hash-chained SQLite log. Append-only.
- [core/traceability.py](core/traceability.py) — figure → source-cell index.
- [report/generator.py](report/generator.py) — PDF via Jinja2 + WeasyPrint.
- [tests/](tests/) — one test module per source module, plus `test_end_to_end.py`.

## Commands

```
pip install -r requirements.txt     # deps
streamlit run app.py                # run the app (needs ANTHROPIC_API_KEY in .env)
pytest tests/                       # full suite
pytest tests/test_parser.py -v      # single module
```

---

## Operating rules — the 4Ds

### Delegation — never decide these alone

- Never choose a materiality threshold value. It is a human-supplied parameter
  with a suggested default. Never hardcode a "reasonable" number and move on.
- Never classify a genuinely ambiguous finding as resolved. If severity or
  classification is unclear from the rules given, surface it as "needs human
  review" rather than picking the more convenient classification.
- Never expand scope mid-step. If a step's prompt doesn't mention something
  (VBA, pivot tables, a new file format), don't add it "while you're in there" —
  flag it as a suggestion for a future step.
- Never simplify, merge, or bypass one of the four gates. If a request would
  require it, say so explicitly and propose an alternative that keeps all four.
- Never treat a prior session's architecture decision as unchangeable without
  flagging the change. If a new step seems to require revisiting an earlier one,
  say so before proceeding.

### Description — how to communicate back

- Before writing code for a step, restate the plan in 3 bullets and wait for
  confirmation, unless explicitly told to proceed without checking in.
- When a prompt is ambiguous, state the assumption in one line before
  proceeding, rather than silently picking an interpretation.
- When proposing anything not explicitly requested (a dependency, a helper
  module, a naming convention), call it out as a proposal, not a fait accompli.

### Discernment — make the output easy to verify

- After each step, include a short "how to verify this" note: what command to
  run, what output to expect, and — for anything numeric — one concrete number
  that can be spot-checked by hand.
- Distinguish, in both code and output, between "the numbers don't match" and
  "I'm not confident this is even the right comparison." Never collapse these
  into the same signal.
- When a test suite is written, state explicitly what it does and does not
  cover, so scope narrowing is visible rather than hidden in a passing run.

### Diligence — the testing and audit bar

- Every module needs a test for the clean case AND a test for messy, malformed,
  or incomplete input before it is done.
- The SQLite audit log must be tested as append-only explicitly, not assumed
  safe because no code path calls UPDATE or DELETE today.
- The end-to-end test must never regress. A change that breaks it does not ship,
  even if every individual module test still passes.
- Keep a running note of which files or functions were AI-written versus
  human-modified (see the bottom of this file). This is built to a standard that
  could survive a compliance review, even though the current audience is one
  person.

---

## Critical rules

These apply to every prompt, every session.

1. Never skip a test. Every module has tests before the next module starts.
2. Never put business logic in `app.py`. It only calls and renders.
3. Never call the Anthropic API without a mock in tests.
4. Never write UPDATE or DELETE in `audit_log.py`.
5. Gate functions must raise `GateBlockedError` — never silently pass.
6. Pydantic validates every agent output before it is consumed.
7. The PDF export button is disabled until Gate 4 completes — enforced in
   Streamlit `session_state`, not just visually.
8. Never merge the two reconciliation passes into a single verdict in code.
   `check_type` stays on every `ReconciliationLine`; `internal_verdict` and
   `external_verdict` stay separate on `AuditReport`. Collapsing them early is
   the single most likely bug in this build — check for it in every review.
9. Never silently omit the CFO or auditor path when their inputs are missing.
   Missing reference figures → an explicit "not performed" state, not a blank
   section. A figure with no traceable source → an explicit "not traceable"
   entry, not a missing row.
10. Treat messy input as the default case. Real Excel files have typos, blank
    tabs, broken formulas, inconsistent labels. A module that only passes on a
    clean fixture is not finished.
11. Never let a `CellRecord` force a choice between formula and cached value —
    both are captured together, always. Reverting to "formula or value" is a
    regression of a defect an actuarial review caught: a blocking bug, not style.
12. Never present a partial reconstruction as complete. `completeness="partial"`
    always maps to `verdict="incomplete"` — a distinct state from pass/warn/
    block, not a variant of "pass," not dropped from the aggregate. The report
    must say what fraction of the calculation was actually reconstructed.
13. The report never claims to validate the actuarial model. Title is
    "Translation & Reconciliation Report," the disclaimer is fixed and rendered
    first, and no section uses "validated" of the underlying methodology or
    assumptions. Only a named human actuary can make that claim, outside this tool.
14. Never call the audit log tamper-proof — call it **tamper-evident**. The hash
    chain makes modification detectable after the fact; it does not make
    `audit.db` unmodifiable by someone with file access. State that distinction
    in code comments, UI copy, and the report.
15. Never call Gate 4's output a "signature," and never an "attestation" either.
    Both were caught in review as overstatement — "attestation" names a specific
    formal service in accounting practice. Call it a **named approval record**:
    a typed identity confirmation checked against a local registry, with a
    timestamp, nothing more. `signed_by`/`signed_at`/`sign_off_gate` and
    `attested_by`/`attested_at`/`attestation_gate` must not exist anywhere;
    `report_approval_name`/`report_approval_at`/`approval_record_gate` are the
    only vocabulary. Treat a fourth euphemism as a pattern to watch permanently.
16. Never log raw LLM responses — successful or failed — outside a clearly
    labeled, non-evidentiary, deleted-at-end-of-run debug location. The audit
    trail records that a call happened, a hash of what was sent and received,
    and the outcome — not the content.
17. Never send more to the Anthropic API than `core/llm_data_policy.py`'s
    minimization rules allow, and never send anything without logging a manifest
    of what was included and excluded. Bypassing `minimize_for_llm()` to send
    raw tab content is a regression of a CRO-flagged gap, not a shortcut.
18. Never let `ReferenceFigures` become a dict again.
    `lines: list[ReferenceFigureLine]` is the only acceptable shape — it lets
    duplicate labels, account numbers, entity/period/currency, debit/credit
    orientation, and row-level evidence coexist. Flattening it back "for
    simplicity" is a regression of the CFO review's foundational finding.
19. Never let a fuzzy match set `AccountMapping.is_approved=True` on its own, no
    matter how high `suggested_confidence` is. Approval requires a human name in
    `approved_by`. A confident string match is a proposal, never an accounting
    decision.
20. Never compute `external_verdict` as "pass" while `unmatched_reference_items`
    or `unmapped_python_outputs` is non-empty, or while
    `context_match_verdict == "mismatch"`. Completeness is checked in both
    directions, and entity/period/currency agreement is a precondition for the
    comparison meaning anything at all — not a detail to check afterwards.
21. Never let a mapping proposal go unreviewed and disappear. Every
    `AccountMapping` a report depends on appears in the report — approved ones in
    the CFO-facing table, unapproved ones in the "proposed but not approved"
    table. Never filtered down to the ones that worked out.
22. Treat the model (Step 2) and everything that reads from it (Steps 6, 8, 9,
    10, 11) as one contract. Before adding a field to a template or prompt,
    check it exists on the model; before adding a role to the model, check every
    role-facing prompt and template handles it. Also: never describe findings
    review as "approved." A finding is confirmed, overridden, or dismissed — all
    three are valid human dispositions, and "approved" implies an endorsement
    the latter two never received. Use "reviewed and dispositioned," and show the
    actual disposition.

---

## Build order

| Step | What | Depends on |
|------|------|-----------|
| 1 | Scaffold (incl. `state_store`, `llm_data_policy`, `verdict_logic`, `authorized_approvers.json`) | — |
| 2 | Pydantic models (`CellRecord`, `DerivationStep`, `WorkbookMeta`, `AuditLogRow`, `StateSnapshot`, `ReferenceFigureLine`, `AccountMapping`, `AccountingProvenance`) | 1 |
| 3 | Audit log — hash-chained, tamper-evident | 2 |
| 3b | Pipeline state snapshot store | 2–3 |
| 4 | Agent 1 — Parser (dual cell+formula capture, calc-state, dual dependency graphs) | 2–3 |
| 5 | Agent 2 — Anomaly detector (cell-level circular reference check) | 4 |
| 6 | Human gates (context match; output designation; mapping approval + verdict recompute; named approval record) | 2–3 |
| 7 | Agent 3 — Reconciliation (formula catalogue, derivation chains, fixed delta, mapping proposals not approvals, bidirectional completeness) | 4–6 |
| 7b | Traceability index (derivation chains + accounting provenance, no value matching) | 4, 7 |
| 8 | Agent 4 — Documentation (data minimization before any LLM call) | 2–4 |
| 9 | Orchestrator (snapshots state at every gate transition; mapping approval flow) | 3b–8, 7b |
| 10 | PDF generator (disclaimers, named-approval-record language, accounting context, mapping tables, evidence integrity) | 2–9 |
| 11 | Streamlit interface (output designation, mapping approval cards, approval-record screen, verify-chain button) | 6–10 |
| 12 | End-to-end test (mapping approval, bidirectional completeness, context mismatch, tamper detection, restart recovery) | all |
| 13 | Docker + deploy (local-first posture, `audit.db` as a mounted volume) | 12 passes |

---

## Current state vs. the standard above

**All gaps identified as of 2026-08-10 have been verified closed.** The gap
list below represents the state as of that date; re-verification on 2026-08-15
confirms all eight items are satisfied in code. The Rules and Build Order
tables above accurately describe the current implementation.

| Rule / Feature | Verified closed | Evidence |
|---|---|---|
| Rule 15 (approval vocabulary) | ✓ | `report_approval_name`/`report_approval_at`/`report_approval_role` consistently used across `core/models.py`, `core/gates.py`, `agents/orchestrator.py`, `report/generator.py`; forbidden vocabulary (`signed_by`, `sign_off_gate`, `attested_by`, `attestation_gate`) not present |
| Rule 18 (ReferenceFigureLine) | ✓ | `core/models.py:33` defines model; `ReferenceFigures.lines: list[ReferenceFigureLine]` at line 60 |
| Rules 19–21 (AccountMapping) | ✓ | `core/models.py:75` defines `AccountMapping`; proposal/approval flow in `agents/reconciliation.py` and `core/gates.py` |
| Rule 11 (CellRecord) | ✓ | `core/models.py:123` defines model with dual formula/cached-value capture |
| Rule 12 (incomplete verdict + completeness) | ✓ | `ReconciliationLine.verdict` includes `"incomplete"` (`core/models.py:254`); `completeness: Literal["complete", "partial"]` field present (line 260) |
| Rule 17 (llm_data_policy.py) | ✓ | File exists at `core/llm_data_policy.py` |
| Step 1/3b scaffold | ✓ | `core/state_store.py`, `core/verdict_logic.py`, `config/authorized_approvers.json` all present |
| Rule 20 (bidirectional completeness) | ✓ | `AuditReport` has both `unmapped_python_outputs` and `context_match_verdict`; enforcement in `core/gates.py` (`_finalize_reconciliation` around line 403) |

## AI-written vs. human-modified

Running note, per the Diligence rules. Update it when you change a file.

| File | Origin | Notes |
|------|--------|-------|
| all source and tests | AI-written | Initial commit `78a0bb7`, MVP build |
| [agents/documentation.py](agents/documentation.py) | AI-written | Commit `21b4dbd`: extract text blocks by type instead of indexing `content[0]` |
| CLAUDE.md | AI-written | This file, 2026-08-10; "Current state vs. the standard" gap list re-verified as closed, 2026-08-15 |
