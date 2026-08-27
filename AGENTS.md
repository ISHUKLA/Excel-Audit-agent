# AGENTS.md — Excel Audit Agent

Operating context for every Codex session in this repo. The rules below are
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
- [core/accounting.py](core/accounting.py) — signed debit/credit and control-total convention.
- [core/models.py](core/models.py) — Pydantic models. The contract every other module reads from.
- [core/gates.py](core/gates.py) — the four gates. Raise `GateBlockedError`, never silently pass.
- [core/audit_log.py](core/audit_log.py) — hash-chained SQLite log. Append-only.
- [core/state_store.py](core/state_store.py) — durable snapshots with fail-closed recovery verification.
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
23. No external AI call may occur without an explicit per-report use decision
    recorded before the call. Declining AI documentation must not block
    deterministic completion. This is a decision inside the existing Gate 3
    workflow, not a fifth gate — see `agents/orchestrator.py`'s
    `prepare_report`/`submit_gate3_decisions` and the `llm_use_decision` audit
    event.

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

**Read this before starting any step.** Steps 1–13 now implement and exercise
Revision 6's data, gate, orchestration, documentation, report, acceptance, and
local-first deployment contracts. No build-order step remains as of 2026-08-11.
Hosted operation with real authentication is explicitly post-MVP scope.

Any future request that changes a completed Step 1–13 contract is still a prior
architecture change. Per the Delegation rules, flag it and get confirmation
before proceeding — do not fold it into an unrelated step.

## AI-written vs. human-modified

Running note, per the Diligence rules. Update it when you change a file.

| File | Origin | Notes |
|------|--------|-------|
| all source and tests | AI-written | Initial commit `78a0bb7`, MVP build |
| Recommendation 2 — Gate 1 bound to the uploaded workbook hash (`core/workbook_identity.py`, `agents/parser.py`, `agents/orchestrator.py`, `core/models.py`, `app.py`, focused tests) | AI-written | Bytes-based parsing with per-reader `BytesIO`, required confirmed hash, identity verified before Gate 1, `workbook_identity_mismatch` evidence, no temporary file, 2026-08-19 |
| Recommendation 1 — complete audit-chain verification before resume (`core/state_store.py`, `core/audit_log.py`, `core/models.py`, `agents/orchestrator.py`, `app.py`, focused tests) | AI-written | Global chain verified before any snapshot load; `ChainIntegrityError`; `chain_verification` event recorded once per report per process; fail-closed recording, 2026-08-19 |
| Recommendation 3, Phase E2 — protected workbook artifact retention (`core/artifact_store.py`, `tests/test_artifact_store.py`, `.gitignore`, `.dockerignore`) | AI-written | Hash-verifiable append-only storage with fsync/rename atomicity, symlink/traversal rejection, no overwrite/delete APIs, concurrency-safe with ArtifactExistsError, 73 tests (clean/negative/concurrency), caller-supplied size cap, 2026-08-21 |
| Recommendation 3, Phase E1 — recalculation evidence and engine-policy foundation (`core/recalculation_policy.py`, `core/models.py` four new models, `config/recalculation_engines.json`, `tests/test_recalculation_policy.py`, focused tests in `test_models.py`) | AI-written | Candidate LibreOffice profile with approvals-only runtime gating, policy loader with exact-bytes hash and fail-closed fallback, models for profiles/policy/evidence with comprehensive validation, 2026-08-21 |
| Recommendation 3, Phase E3 — recalculation engine adapter and integration (`core/recalculation.py`, `tests/test_recalculation.py`, `tests/test_recalculation_qualification.py`) | AI-written | Engine-neutral adapter interface + LibreOffice subprocess implementation + preflight validation + formula inventory/manifest + output verification + RecalculationService 12-step orchestrator requiring approved profiles; unit tests with fake adapters only; synthetic workbook qualification setup with stale-cache preconditions, 2026-08-21 |
| P0 credibility control set (`core/accounting.py`, accounting/gate/state/report/UI paths and focused tests) | AI-written | User-directed debit/credit orientation, control-total block, explicit Gate 1, zero thresholds, and verified recovery, 2026-08-12 |
| [agents/documentation.py](agents/documentation.py) | AI-written | Commit `21b4dbd`; Step 8 rewrite on 2026-08-11 adds minimized payloads, role guidance, safe audit hashes, and validated fallback |
| [core/llm_data_policy.py](core/llm_data_policy.py) | AI-written | Step 8 minimization allowlist and non-sensitive transmission manifest, 2026-08-11 |
| [tests/test_documentation.py](tests/test_documentation.py) | AI-written | Step 8 mocked LLM, privacy, manifest, and four-role coverage, 2026-08-11 |
| [agents/orchestrator.py](agents/orchestrator.py) | AI-written | Step 9 staged pipeline; Step 11 UI support; Step 13 mounted audit/state database path from `AUDIT_DB_PATH`, 2026-08-11 |
| [tests/test_orchestrator.py](tests/test_orchestrator.py) | AI-written | Step 9 integration; Step 11 mapping edit/reject and downstream retry coverage, 2026-08-11 |
| [config/materiality_defaults.json](config/materiality_defaults.json) | AI-written | Step 9 organization-default starter thresholds, 2026-08-11 |
| [report/generator.py](report/generator.py) | AI-written | Step 10 guarded PDF assembly and current-model presentation context, 2026-08-11 |
| [report/templates/report.html](report/templates/report.html) | AI-written | Step 10 seven-section A4 report, bounded claims, mapping/evidence tables, and print layout, 2026-08-11 |
| [tests/test_generator.py](tests/test_generator.py) | AI-written | Step 10 PDF contract, vocabulary, incomplete-result, context, mapping, and no-reference coverage, 2026-08-11 |
| [app.py](app.py) | AI-written | Step 11 five-screen Streamlit workflow, guarded transitions, mapping review, approval record, and evidence verification, 2026-08-11 |
| [core/ui_inputs.py](core/ui_inputs.py) | AI-written | Step 11 structured manual/CSV reference-figure validation, 2026-08-11 |
| [core/gates.py](core/gates.py) | AI-written | Step 11 independent internal/external threshold evaluation; Step 12 makes population incompleteness outrank numeric warnings, 2026-08-11 |
| [core/models.py](core/models.py) | AI-written | Step 11 validated `MappingReviewDecision` gate input, 2026-08-11 |
| [agents/reconciliation.py](agents/reconciliation.py) | AI-written | Step 11 public symmetric delta helper; Step 12 prevents duplicate ledger labels from reusing one output, 2026-08-11 |
| [tests/test_end_to_end.py](tests/test_end_to_end.py) | AI-written | Step 12 real-workbook acceptance journey across all gates, mapping, thresholds, traceability, reporting, tamper detection, and recovery, 2026-08-11 |
| [tests/test_reconciliation.py](tests/test_reconciliation.py) | AI-written | Step 12 duplicate-label/output-reuse regression, 2026-08-11 |
| [tests/test_gates.py](tests/test_gates.py) | AI-written | Step 12 incomplete-population versus warning aggregation regression, 2026-08-11 |
| [Dockerfile](Dockerfile) | AI-written | Step 13 Python 3.11 image, WeasyPrint libraries, and `/data` evidence volume, 2026-08-11 |
| [.dockerignore](.dockerignore) | AI-written | Step 13 excludes secrets, databases, and local evidence volumes from image context, 2026-08-11 |
| [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) | AI-written | Step 13 API-key placement and password-equivalent warning, 2026-08-11 |
| [README.md](README.md) | AI-written | Step 13 local-first deployment posture, Docker volume instructions, gates, and plain limitations, 2026-08-11 |
| [tests/test_deployment.py](tests/test_deployment.py) | AI-written | Step 13 image, secret, mounted-path, README-order, and limitation regressions, 2026-08-11 |
| [tests/test_app.py](tests/test_app.py) | AI-written | Step 11 per-screen shell, export guard, vocabulary, and retry-message coverage, 2026-08-11 |
| [tests/test_ui_inputs.py](tests/test_ui_inputs.py) | AI-written | Step 11 clean, duplicate, missing-column, and malformed reference-input coverage, 2026-08-11 |
| `outputs/ai2_2026_demo_pack_20260824/` | AI-written | Three synthetic demonstration workbooks, two matching reference-figure CSVs, and a case guide covering the clean, spreadsheet-control-failure, and accounting-reconciliation-failure journeys, 2026-08-24 |
| AGENTS.md | AI-written | This file, 2026-08-10 |
