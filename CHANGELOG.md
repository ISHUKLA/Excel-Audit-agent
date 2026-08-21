# Changelog

One line per meaningful change. This is the project's lightweight change-control
record — it exists so that a reviewer can reconstruct what changed and when
without reading the git log.

## 2026-08-19 — Recommendation 2: bind Gate 1 to the uploaded workbook hash

- Gate 1 previously confirmed a *filename*. Two different workbooks can share a name, so a reviewer could confirm one file and the pipeline could parse another with nothing to detect it. Gate 1 also recorded `not_yet_parsed` as its workbook hash, because nothing had been hashed before the gate ran.
- Added `core/workbook_identity.py` as the single definition of workbook identity: `sha256_bytes()`, `validate_hash_format()`, `verify_bytes_match()`, and `WorkbookIdentityError`. Hashing a path string is refused outright — that is the defect the module exists to prevent.
- `agents/parser.py` now takes bytes rather than a path. It read the workbook five separate times (formula mode, data-only mode, ZIP metadata, VBA detection, hashing); against a path each was an independent window in which the file could change. Each reader now gets its own `BytesIO` over one immutable sequence, so the bytes hashed for Gate 1 are provably the bytes parsed. Verified empirically before the change: all five paths accept `BytesIO`.
- `Orchestrator.run()` takes `workbook_bytes` plus a keyword-only `expected_workbook_hash` with no default — omitting it is a `TypeError`, not an unverified run. There is no flag to skip verification. Identity is checked *before* `context_gate` is called, so no `context_confirmed` decision is ever recorded for a workbook whose identity was never established.
- `FileContext.confirmed_workbook_hash` is required and format-validated: blank, `None`, truncated, uppercase, and non-hex values are all rejected at the model boundary, so a falsy value cannot make the binding silently optional.
- A mismatch is recorded as a new `workbook_identity_mismatch` event carrying the confirmed hash, the observed hash, the filename, the actor, the code version, the timestamp, and a blocked outcome. Its *context* commits to the confirmed hash, never the observed one — recording the observed hash there would assert an identity for a workbook nobody approved.
- No temporary file is written at any point. The residual TOCTOU risk disclosed in the original plan is therefore eliminated rather than mitigated.
- Streamlit shows the short hash prominently with the full 64 characters underneath, and clears the confirmation whenever the uploaded bytes change — including a same-named replacement.
- No database schema change, no migration, no change to the four human gates, reconciliation calculations, or report content. Existing audit databases and snapshots remain readable; historical Gate 1 rows keep `not_yet_parsed`, which is what was true when they were written.
- Known limitation recorded: the workbook is held in memory for the duration of a run and no maximum upload size is enforced. A size limit is a separate governed decision.
- Verification: 377 tests pass (up from 353 after Step 1, 319 before this recommendation), including an acceptance-level test that a one-byte substitution under the same filename is refused with no gate decision and no snapshot.

## 2026-08-19 — Recommendation 1: verify the complete audit chain before every resume

- Closed a gap where `Orchestrator.resume()` restored pipeline state without checking the audit chain behind it. The two existing snapshot checks — content hash, and a matching `state_snapshot` commitment — both pass while an *earlier* log row has been rewritten, because that row still hashes correctly on its own and the snapshot was never touched. Demonstrated against the previous code: with `verify_chain()` reporting `(False, ['1'])`, `resume()` succeeded and restored state at `post_reconciliation`.
- `StateStore._load()` now walks the complete global chain before reading the `snapshots` table, so no state can reach memory from a corrupt history. Both public loaders go through it; the implicit recovery route via `Orchestrator._state_for()` is covered by the same guard.
- Added `ChainIntegrityError`, subclassing `StateIntegrityError`. Distinct because "the history behind this state is broken" and "this one snapshot is broken" are different findings with different remedies; subclassed so existing callers keep working. The message names every failing row ID and states plainly that it does not identify who changed the file, when, or whether deliberately.
- Refusal is unconditional on ownership: the chain is global, so a corrupt row belonging to another report refuses recovery of this one. One bad row makes every report in that `audit.db` unresumable, with no override. This is a real availability cost, accepted deliberately; backups become an operational necessity. New runs on a broken chain remain allowed.
- Added a sixth `AuditLogRow.event_type`, `chain_verification`, recorded once per report per process on successful recovery. Never recorded on failure — appending to a chain already known to be broken would commit a new row's `prev_row_hash` to a corrupt predecessor. Recording failure is fail-closed: the restored state is discarded rather than left in memory unevidenced.
- No schema change, no migration, no change to the four human gates, reconciliation calculations, report content, or LLM handling. Existing audit databases and snapshots remain readable and compatible — though a database already tampered with will now refuse recovery where it previously succeeded.
- Added 18 regressions across the model, snapshot store, and orchestrator: edited early row, deleted mid-chain row, cross-report corruption, no state after refusal, implicit-route refusal, nothing repaired or appended on refusal, once-per-process recording, chain intact after recording, missing-versus-corrupt distinction, and vocabulary. Verification: 319 tests pass (up from 301), end-to-end acceptance unmodified.

## 2026-08-12 — P0 credibility controls

- Established one accounting sign convention in `core/accounting.py`: reference amounts remain non-negative magnitudes, with debit positive and credit negative. Both fuzzy proposals and human-edited mappings now compare against the correctly oriented amount.
- Added an exact decimal control-total tie-out. Gate 1 records the declared total, signed line total, difference, and status; Gate 3 forces the external verdict to `block` on mismatch, and the report displays the mathematical evidence and sign convention.
- Replaced the nominal upload transition with a real Gate 1 screen: the entered workbook, reviewer, accounting, and reference context is summarized before parsing, and the Start action is disabled until an explicit confirmation checkbox is selected.
- Fixed zero-width materiality bands so an exact match passes at zero percentage and absolute thresholds while any non-zero difference still blocks.
- Made every snapshot load fail closed: state content is re-hashed and must also match an intact `state_snapshot` commitment in the audit log. `Orchestrator.resume()` therefore refuses altered or orphaned recovered state.
- Added focused clean, messy, UI, gate, orchestration, report, and recovery regressions for all five controls. Verification: 301 tests pass.

## 2026-08-11 — Step 13: local-first deployment hardening

- Made local execution the documented default and moved the deployment posture ahead of installation instructions. Docker is described as the same trust boundary only when its evidence volume remains local; hosted deployment is explicitly not recommended until real application authentication exists.
- Kept the required `python:3.11-slim` image, WeasyPrint system libraries, dependency installation, port, and Streamlit command. Added `AUDIT_DB_PATH=/data/audit.db` and a `/data` volume so the audit log and state snapshots share mounted storage instead of the container layer.
- Continued excluding `.env`, the real Streamlit secrets file, `audit.db`, all `*.db` files, and the local `.local-data` evidence directory from the Docker build context. The documented run command binds Streamlit to host loopback only.
- Updated `.streamlit/secrets.toml.example` with the required password-equivalent API-key warning.
- Added the four plain limitations from the context brief: no enforced independent reviewer, no application authentication, tamper-evident rather than tamper-proof evidence, and informal rather than certified LLM data minimization.
- Added six deployment regression tests, including proof that the configured database path is shared by the audit log and snapshot store and that blank environment configuration falls back to local `audit.db`. Verification: 289 tests pass. Docker itself is unavailable in the development environment, so no image build was performed here.

## 2026-08-11 — Step 12: end-to-end acceptance test

- Replaced the obsolete one-shot end-to-end tests with four staged acceptance scenarios over a real LibreOffice-recalculated workbook: matching accounting context, looser and stricter thresholds, context mismatch, and no reference figures.
- The main journey exercises all four human gates, explicit authoritative outputs, an unsupported `VLOOKUP` and incomplete control total, human mapping approval, duplicate ledger labels, bidirectional completeness, exact derivation reuse, accounting provenance, mocked Agent 4 minimization, named approval record, PDF bytes, restart recovery, and audit-chain verification plus raw-SQL tamper detection.
- Fixed a Step 7 defect exposed by duplicate-label evidence: once a Python output has been consumed by a proposed mapping, a later reference line cannot reuse it. The later duplicate remains in `unmatched_reference_items` for human review.
- Fixed a Gate 3 aggregation defect: population gaps now outrank a numeric `warn` and produce `incomplete`; a genuine numeric `block` still remains blocking.
- Added focused regressions for both defects. Verification: 283 tests pass across the full suite; the hand-check remains €1,750 versus €1,755 = €5 / 0.285%, which is `warn` at 1%, `pass` at 3%, and `block` at 0.1%.

## 2026-08-11 — Step 11: five-screen Streamlit interface

- Rebuilt `app.py` as five session-state screens: upload/context, findings plus authoritative-output designation, independent reconciliation and mapping review, named approval record, and report/evidence verification. PDF export remains unavailable until Gate 4 has populated the approval name and timestamp.
- Added structured manual and CSV accounting inputs through `core/ui_inputs.py`. Duplicate labels/account numbers are preserved; CSVs missing `account_number`, `label`, `amount`, or `debit_credit` are rejected explicitly; malformed, negative, and incomplete rows fail with row-specific messages.
- Extended Gate 3 to apply percentage and absolute thresholds independently to the internal and external passes, with a separate mandatory deviation reason for each changed pair. The live preview uses the same verdict function without logging or finalizing evidence.
- Added validated mapping dispositions: approve, reject, edit to a different reference line, or acknowledge a non-one-to-one proposal as manual reconciliation. Rejected/manual proposals remain visible and unapproved in the report but are excluded from external verdict lines; edited proposals remain visible beside a named human-direct approved mapping.
- Added orchestrator-only UI queries for defaults, context status, registry checks, independence-disclosure preview, stage, and evidence-chain verification, keeping reconciliation and gate logic out of Streamlit.
- Made post-Gate-3 report preparation resumable. A documentation/provider failure now leaves the recorded Gate 3 snapshot intact, shows a bounded retry message, and retries only downstream documentation/report assembly rather than replaying the human gate.
- Added `test_app.py` and `test_ui_inputs.py`, plus Gate 3/model/orchestrator regressions for independent thresholds, mapping rejection/remapping, premature PDF access, malformed CSV rows, and provider retry without a duplicate Gate 3 event.
- Live walkthrough: exercised upload, context entry, parser/anomaly transition, authoritative-output selection, Gate 3 preview, and the threshold-reason guard in Streamlit. The installed Anthropic account had insufficient credit; that live failure exposed and drove the resumable error-boundary fix. Screens 4–5 were additionally exercised through Streamlit's application test harness with a completed report fixture.

## 2026-08-11 — Step 10: PDF report generator

- Rebuilt `report/generator.py` around the current `AuditReport` contract. The public generator requires the completed named approval record, renders with Jinja2 `StrictUndefined`, and returns PDF bytes without writing evidence to disk.
- Replaced the report template with the fixed "Translation & Reconciliation Report" title, first-page scope and independence disclosures, four-state overall result, separate internal/external verdicts, and bounded named-approval-record and tamper-evident language.
- Added the accounting context comparison, control-total status, approved and unapproved mapping tables, bidirectional completeness gaps, manual aggregate cases, full derivation chains, accounting provenance, minimized-data manifest, audit rows, and evidence identifiers.
- Partial reconstruction remains a distinct `INCOMPLETE` state: its unreconstructed percentage and unsupported elements are printed, while its deltas are labelled "not comparable". Missing reference figures produce an explicit "not performed" accounts section.
- The third-party-data section links to Anthropic's current commercial/API retention documentation and lists only manifest cell references and exclusion reasons; raw prompts and responses are not accepted by the template context.
- Tests: rewrote `test_generator.py` as 7 contract tests covering valid PDF bytes, the approval guard, incomplete wording, prohibited vocabulary, mapping-table separation, context mismatch, header identity/timestamps, findings dispositions, and the no-reference path.
- Visual QA: rendered the representative report with WeasyPrint and Poppler, inspected all four A4 pages, and corrected a split verdict label and two pagination defects.

## 2026-08-11 — Step 9: staged orchestrator

- Rebuilt `agents/orchestrator.py` around the four mandatory pauses. `run()` returns the report ID, parsed workbook, and findings for Gate 2; an `AuditReport` is not returned until the named approval record is completed.
- Added `config/materiality_defaults.json` with the specified 1% percentage and 100-unit absolute defaults. The same per-run values feed Agent 3's preview and Gate 3's final recomputation.
- Snapshots now capture full serializable state at post-parse, post-anomaly, post-Gate-2, post-reconciliation, post-Gate-3, pre-approval-record, and post-approval-record transitions. `resume()` restores Pydantic objects from the latest durable snapshot.
- Mapping approvals are applied to a deep copy of the preview, require a named actor, set `approved_by`/`approved_at`, and write `mapping_decision` evidence. Unapproved proposals remain in the final report; referenced unapproved proposals still block in Gate 3.
- Gate 3's returned `ReconciliationResult` replaces the preview before traceability, documentation, snapshots, or report assembly. The report's internal and external verdicts are taken directly from Gate 3.
- **Interface clarification confirmed by the human:** the build text's `run(...) -> AuditReport` conflicts with its mandatory Gate 2–4 pauses. The staged return described above preserves all four gates rather than pretending a report can exist at pipeline start.
- Tests: rewrote `test_orchestrator.py` as 7 integration tests with mocked agents, gate spies, real audit/snapshot controls, restart recovery, mapping gaps, context mismatch, incomplete reconstruction, and the 0.285% threshold-recompute regression.

## 2026-08-11 — Step 8: documentation agent and LLM data policy

- Completed `core/llm_data_policy.py` as an allowlist: formulas, numbers, blanks, and short structural labels may be sent; long free text, external-link formulas, out-of-scope cells, and unlisted cell types stay local. Every decision appears in an `LLMDataManifestEntry` containing references and reasons, never cell content.
- Rewrote `agents/documentation.py` to call `minimize_for_llm()` for every tab, use `claude-sonnet-4-6`, and add explicit actuary/CRO/CFO/auditor guidance. The prior extended-thinking text-block handling remains intact.
- Added explicit audit dependencies to Agent 4 because the step's three-input sketch omitted the `report_id`, audit context, and log object required by its own evidence rule. Calls log manifest metadata and SHA-256 request/response hashes only; raw LLM output is never persisted.
- Validation failures now use a manual-review fallback and sanitized Pydantic error details with rejected input values removed.
- Tests: `test_documentation.py` 7 → 11; Anthropic is mocked throughout. Coverage includes valid/invalid responses, raw-output non-retention, all tabs, delay behavior, long-text/external-link exclusions, authoritative tab naming, and distinct guidance for all four roles.

## 2026-08-10 — Step 7b: traceability index

- Rewrote `core/traceability.py`. **All value matching removed.** The previous version located a figure's source cell by searching for a matching number (`_trace_source_value`, `_entry_for_primary_input`), which picks the wrong cell whenever a value repeats. Entries are now built from derivation chains that already knew where the figure came from.
- `trace_status` replaces the old `source_tab`/`source_cell`/`derivation_note` fields, with six specific reason codes assigned in precedence order.
- `accounting_provenance` is built from the AccountMapping and ReferenceFigureLine actually used, resolved by `mapping_id` and `reference_line_id` — never a fresh lookup.
- Partial chains are included in full with unsupported nodes visible as `is_supported=False`, rather than truncated at the failure point.
- Tests: `test_traceability.py` 3 → 14, including a structural guard asserting no value-lookup idioms exist in the module source.

## 2026-08-10 — Step 7: reconciliation (Agent 3)

- Rewrote `agents/reconciliation.py`. Returns `ReconciliationResult`, not a tuple. `verdicts_are_final=False` always.
- **Removed the duplicated threshold logic** flagged in Step 6: `_classify_verdict` is gone, replaced by an import of `compute_verdict`. Only `core/verdict_logic.py` now contains a `if delta_pct <` block.
- **Removed `_identify_output_tab`**, which guessed the output tab by keyword-matching the file description. Gate 2 designates outputs now, which is what the step says replaces it.
- Delta is now symmetric — denominator is `max(abs(source), abs(target))`, so swapping the arguments cannot change the percentage. The old version divided by the source alone and returned `inf` when the source was zero.
- Supported catalogue: cell refs (relative and absolute), `+ - * /`, unary minus, parentheses, and `SUM` over ranges or comma lists. Everything else is reported verbatim in `unsupported_elements`, never approximated.
- `is_supported` describes a node's own formula. A supported parent of an unsupported child stays `is_supported=True` with `resolved_value=None`, so coverage counts the node that actually failed.
- Cycles are detected up front on the reachable subgraph, so a circular reference is marked unsupported rather than recursing forever.
- **Judgment call:** blank cells are treated as 0 with a warning in direct arithmetic as well as inside SUM. The step specifies this only for SUM, but Excel applies it everywhere, and treating a blank as unsupported would make ordinary workbooks "partial" for no real reason.
- Imports `_CELL_REF_PATTERN`/`_expand_range`/`_normalize` from `agents/parser.py` rather than re-declaring them, so graph construction and formula substitution cannot drift apart. Cross-module use of underscore names — flagged as a candidate for a shared helper module in a later step.
- Preserved the acronym-blended label similarity from the previous build ("NPR" against "Net premium reserves" scores ~30% on plain character similarity).
- Tests: `test_reconciliation.py` 7 → 36.

## 2026-08-10 — Step 6: human gates

- Built `core/verdict_logic.py` — `compute_verdict`, the single shared definition of pass/warn/block/incomplete. Specified in Step 7 but needed by Gate 3, so implemented here. Both thresholds are evaluated and the worse outcome wins.
- Rewrote all four gates in `core/gates.py`. `sign_off_gate` is gone; `approval_record_gate` replaces it. No `sign` or `attest` appears as a function or variable name anywhere in the module.
- **Bug found and fixed during testing:** Gate 4's own unregistered-name event is a `gate_decision`, so it was landing in the preparer set and producing "prepared by X and approved by X" for a solo run. Preparers are now read from gates 2 and 3 only, per the step's wording.
- **Deviation from the step's stated ordering, deliberate:** the two checks that can raise (threshold deviation, mapping approval) run *before* the recompute mutates the result. The step lists the recompute first, and its stated reason — that no aggregation may read a verdict Agent 3 set — is preserved. Validating first only prevents a blocked gate from leaving behind a result marked `verdicts_are_final=True`.
- **Judgment call:** an empty set of internal lines aggregates to `"incomplete"`, not `"pass"`. The step's rule ("else pass") would report a clean pass over a reconciliation that never happened.
- **Inference, flagged:** `is_ambiguous_match` is derived from `mapping_type != "one_to_one"`, since `AccountMapping` has no explicit ambiguity flag.
- Gate signatures add `audit_log`, `actor`, and `report_id` where the step's sketch elided them but its body requires them.
- Tests: `test_gates.py` 11 → 44, `test_verdict_logic.py` 0 → 22.
- **Known duplication, to be removed by Step 7:** `agents/reconciliation.py:201-203` still contains its own `if delta_pct <` threshold logic. Step 7 replaces it with an import of `compute_verdict`.

## 2026-08-10 — Step 5: anomaly detector (Agent 2)

- **Fixed the false-positive the previous build acknowledged in a comment.** Circular reference detection now reads `cell_dependency_graph`; it previously read the tab-level graph, with a code comment conceding it was "a coarser signal… but it's the only graph ParsedFile exposes." Step 4 gave it the right one.
- Self-referencing cells (`=A1+1` in A1) are now flagged; the old tab-level check skipped cycles of length 1 because a single-tab cycle was meaningless at that granularity.
- Findings list every cell in a cycle, not just that a cycle exists.
- No cap on the number of cycles reported. Truncating would hide exactly what the check exists to surface; deterministic ordering keeps `finding_id`s stable across runs instead.
- **Scope narrowing, carried forward and now documented in the code:** cross-tab inconsistency compares named ranges only. The step also mentions "cell labels", which needs an adjacency convention (label to the left? above? column A?) that has never been specified. Left unimplemented rather than guessed.
- Tests: `test_anomaly.py` 8 → 23, including the negative case for category 4.

## 2026-08-10 — Step 4: parser (Agent 1)

- Rewrote `agents/parser.py` to emit `CellRecord` (formula and cached value on one record), `WorkbookMeta`, and two separate dependency graphs. Kept the existing tab-dedupe, lookalike-name, and VBA helpers.
- Cell-level dependency graph: formula references parsed with lookarounds so function names don't read as cell refs (`LOG10(` would otherwise parse as column LOG, row 10). Ranges expand cell by cell, capped at 5000 cells — beyond that only the endpoints are edged, with a warning, rather than hanging on `C1:C1048576`.
- `calc_mode` read from `calcPr` in the zip's `xl/workbook.xml`, not from openpyxl's synthesized default. Absent or unrecognised reads as `"unknown"`; never silently `"automatic"`.
- A formula cell whose value was never computed gets `data_type="blank"` — the Step 2 enum has no `"unknown"` — with `is_stale=True` and a warning carrying the real meaning. Flagged rather than adding an enum value Step 2 didn't specify.
- Installed LibreOffice (test-only dependency) so fixtures get genuinely calculated cached values. Added `tests/fixture_helpers.py` with `recalculate_workbook`, `set_calc_mode`, and `strip_calc_pr`. `recalculate_workbook` raises when LibreOffice is missing rather than skipping quietly.
- Tests: `test_parser.py` 3 → 21. The manual-calc-mode test recalculates first and *then* switches to manual, so `is_stale=True` can only be caused by the mode and not by a missing value.

## 2026-08-10 — Steps 3 and 3b: audit log and state store

- Rewrote `core/audit_log.py` as a hash-chained, tamper-evident log. New `log_rows` table, `log_event`/`get_rows`/`verify_chain`. The chain is global across the file, not per-report, so deleting one report's rows is detectable.
- **Kept the pre-existing DB-level UPDATE/DELETE triggers** rather than dropping them during the rewrite — Step 3 only required no UPDATE/DELETE in code, but the triggers were already there and are a stronger control. The tampering tests drop the triggers first, which is what an attacker with file access would do, and then prove the hash chain still catches the edit.
- **Left the old `decisions` table and its 8 rows of real audit history from 2026-08-03 untouched.** It is no longer written to. `get_legacy_decisions()` keeps it readable. Removed `log_decision`, so every new write is chained.
- `log_event` requires non-empty `workbook_hash` and `code_version` in context and folds context into the hashed payload. Pre-parse callers (Gate 1 runs before parsing) must pass `workbook_hash="not_yet_parsed"` — an explicit sentinel, not an omission. Step 6 must honour this.
- Added `core/state_store.py`: `save_snapshot`/`load_latest_snapshot`/`load_snapshot`, sharing `audit.db`. Every snapshot's hash is chained into the log.
- `save_snapshot` takes a required `context` argument, which Step 3b's sketch elided — the audit log cannot accept an event without it.
- Unserializable state raises `StateSerializationError` rather than being coerced with `default=str`; a snapshot that restores to something other than what was saved is worse than no snapshot.
- Tests: `test_audit_log.py` 5 → 22, `test_state_store.py` 0 → 17.
- README now discloses that processed workbook contents live durably in `audit.db`, making that file as sensitive as the source workbook.

## 2026-08-10 — Step 2: data models

- Replaced `core/models.py` entirely: 8 models became 18 (the build document numbers them 1–17, plus item 11b). New models: `ReferenceFigureLine`, `AccountMapping`, `CellRecord`, `WorkbookMeta`, `DerivationStep`, `AccountingProvenance`, `ReconciliationResult`, `LLMDataManifestEntry`, `AuditLogRow`, `StateSnapshot`.
- **Deviation from the build document, deliberate:** `AuditLogRow.event_type` uses `"report_approved"`, not the specified `"report_signed"`. Critical rule 15 bans signature vocabulary anywhere in the codebase, and the audit log's own event names are the last place that word should survive. Steps 3 onward must use the same literal.
- Added `Field(ge=0)` to `ReferenceFigureLine.amount` so a negative amount is unrepresentable rather than merely discouraged — sign belongs to `debit_credit`.
- Documented `AccountMapping.suggested_confidence` as 0–100 to match rapidfuzz's scale; the build document did not state a range.
- Rewrote `tests/test_models.py`: 9 tests became 31, covering all nine cases the step names plus malformed-input rejection.
- **The suite is intentionally red from here until the consumers catch up.** 45 of 86 tests fail because the agents, gates, orchestrator, generator, and app still read the old field names. This is migration debt, not breakage, and it is expected to clear as Steps 4–11 rebuild each consumer. Step 12's end-to-end test is the gate back to zero failures.

## 2026-08-10 — Step 1: scaffold

- Added Step 1 scaffold files missing from the original build: `core/state_store.py`, `core/llm_data_policy.py`, `core/verdict_logic.py`, `tests/test_state_store.py`, `tests/test_verdict_logic.py` (docstring stubs, no logic yet).
- Added `config/authorized_approvers.json` — local approver-name registry for Gate 4, seeded with one entry. Not authentication; the file says so itself.
- Added this changelog.
- Noted libreoffice in `README.md` as a test-only system dependency, separate from what is needed to run `app.py`.
- Moved the sqlite3-is-stdlib note from `.env.example` to `requirements.txt`, where it belongs.
- Added `CLAUDE.md` — 4Ds operating rules, the 22 critical rules, and a record of where the code currently trails the build rules.
- Rewrote `README.md`: corrected Gate 4 language away from "signature", stated the audit log is tamper-evident rather than tamper-proof, documented that Docker discards `audit.db` unless it is mounted, and added a known-gaps section.

## Earlier (from git history)

- `21b4dbd` — Agent 4 extracts text blocks by type instead of indexing `content[0]`.
- `78a0bb7` — Initial MVP build: parser, anomaly detector, reconciliation, traceability, documentation, orchestrator, four gates, hash-chained audit log, PDF generator, Streamlit UI, 64 tests.
