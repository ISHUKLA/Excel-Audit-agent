# Excel Audit Agent

## Deployment posture

**Default and recommended: run locally.** `audit.db` and any uploaded workbooks stay on your machine; nothing is exposed to a network beyond the Anthropic API call itself.

**Docker:** this has the same trust boundary as local execution only if the `/data` volume stays on the same machine. Do not mount it from network storage or sync it to cloud storage without separately considering that exposure, because the volume contains workbook content and audit evidence.

**Streamlit Cloud or any other hosted deployment is explicitly not recommended without additional access control.** This MVP has no application-level authentication. [`config/authorized_approvers.json`](config/authorized_approvers.json) is a registry check, not authentication, and a hosted instance would be reachable by anyone with the URL. If hosting is genuinely needed, treat it as a distinct follow-on scope item requiring real authentication, not a checkbox on this MVP's deployment options.

## What the tool does

Excel Audit Agent parses an actuarial or financial Excel workbook, independently reconstructs supported formulas in Python, and compares the reconstruction with the spreadsheet's cached values. It can separately reconcile designated outputs against structured accounting figures and routes every finding and reconciliation through four human gates before producing a PDF report. It does not validate the actuarial model or certify a number; it surfaces evidence for a named human to review.

## Requirements

- Python 3.11+
- An Anthropic API key (used by Agent 4 to draft tab documentation — the only LLM call in the pipeline)
- On Linux/Docker, the Pango and GDK-PixBuf system libraries that WeasyPrint needs for PDF output (the `Dockerfile` installs these)

**Test-only system dependency.** Building test fixtures needs `libreoffice`, because openpyxl can read formulas but cannot calculate them — a fixture workbook written by openpyxl alone has formulas with no cached values, which is exactly what the parser and reconciliation tests need to check against. Install it with `brew install --cask libreoffice` on macOS, or `apt install libreoffice-calc` on Debian/Ubuntu. **You do not need it to run `app.py`** — only to regenerate fixtures.

## How to run locally

1. Install the dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.
3. Start the app:
   ```
   streamlit run app.py
   ```
4. Open the URL Streamlit prints (usually `http://localhost:8501`).

Run the test suite with:
```
pytest tests/
```
The current suite status and acceptance-test scope are described under [Testing](#testing).

## How to run with Docker

1. Build the image:
   ```
   docker build -t excel-audit-agent .
   ```
2. Create a private local directory for the evidence database:
   ```
   mkdir -p .local-data
   chmod 700 .local-data
   ```
3. Run the container with the API key from your local `.env` and bind the evidence directory to `/data`:
   ```
   docker run --rm -p 127.0.0.1:8501:8501 --env-file .env \
     --mount type=bind,source="$(pwd)/.local-data",target=/data \
     excel-audit-agent
   ```
4. Open `http://localhost:8501`.

The container writes `/data/audit.db`; the audit log and all state snapshots share that mounted file. The Docker build context excludes `.env`, `.streamlit/secrets.toml`, `audit.db`, every `*.db` file, and `.local-data`, so those sensitive files are not copied into the image.

## The four human gates

The tool is built around four points where a named person, not the AI, has to make a decision before the pipeline continues. None of them can be skipped or merged. Each is enforced in [core/gates.py](core/gates.py), which raises `GateBlockedError` rather than passing silently.

1. **Context confirmation.** Before anything is parsed, the UI displays a summary of the workbook, reviewer, accounting context, and any reference-figure context. Parsing remains disabled until you explicitly check that this displayed context is accurate. If a reference control total is supplied, its mathematical tie-out is also recorded at this gate.

2. **Findings review.** Every anomaly the tool flags is shown to you individually: a hardcoded number buried in a formula, a suspicious skip in a `SUM` range, a circular reference between tabs, an inconsistent cross-tab value. You confirm it's a real issue, override it with a reason, or dismiss it as a false positive with a reason. All three are valid dispositions — a dismissed finding is reviewed, not approved. The pipeline will not proceed until every finding has a disposition attached.

3. **Reconciliation sign-off.** The tool shows you two independent comparisons side by side, and keeps them separate all the way to the report:
   - **Internal consistency** — its own Python reconstruction against the spreadsheet's cached values.
   - **Accounts reconciliation** — the spreadsheet's numbers against the accounts figures you supplied, for the CFO. If you supplied none, this reports as *not performed*, not as a blank section.

   You set the materiality threshold for each (the UI suggests 1%, but the number is yours to choose — the tool never picks it for you). An exact match passes even when both approved thresholds are zero. A blocking discrepancy in either comparison stops the process until it's resolved, and a reference extract that does not mathematically tie to its declared control total cannot proceed through the accounts reconciliation.

4. **Named approval record.** Once everything above is settled, a named person records their name and role, which is stored with a timestamp. Only then is the PDF generated. This is an identity confirmation and nothing more — it is not a professional signature or attestation, and the report does not claim otherwise.

## What the pipeline does

| Stage | Module | What it does |
|-------|--------|--------------|
| Agent 1 — Parser | [agents/parser.py](agents/parser.py) | Reads the workbook with `openpyxl`, capturing each cell's formula *and* its cached value together. Records named ranges, external links, VBA presence, and a cross-tab dependency graph. Flags duplicate and near-duplicate tab names. |
| Agent 2 — Anomaly detector | [agents/anomaly_detector.py](agents/anomaly_detector.py) | Rule-based, no LLM. Detects hardcoded literals in formulas, cross-tab inconsistencies, rows excluded from `SUM` ranges, and circular references. |
| Accounting convention | [core/accounting.py](core/accounting.py) | Applies the single signed-net convention used by external reconciliation and control totals: debit positive, credit negative. |
| Agent 3 — Reconciliation | [agents/reconciliation.py](agents/reconciliation.py) | Two independent passes: Excel vs. Python, and Python vs. accounts. External target values use debit/credit orientation. Where a label match is uncertain, the line is tagged low-confidence rather than being silently treated as a match. |
| Traceability index | [core/traceability.py](core/traceability.py) | Maps each reported figure back to its source tab, cell, and formula. A figure with no traceable source appears as an explicit *not traceable* entry, not a missing row. |
| Agent 4 — Documentation | [agents/documentation.py](agents/documentation.py) | The only module that calls the Anthropic API. Drafts a plain-language summary of each tab's method, assumptions, and data sources. |
| Orchestrator | [agents/orchestrator.py](agents/orchestrator.py) | Sequences the agents across the four gates and keeps the two reconciliation verdicts separate. |
| Report | [report/generator.py](report/generator.py) | Renders the Jinja2 template to PDF via WeasyPrint. Refuses to run before Gate 4 is complete. |

Every agent output is validated by a Pydantic model in [core/models.py](core/models.py) before anything downstream consumes it.

## The audit log

[core/audit_log.py](core/audit_log.py) writes an append-only, hash-chained SQLite log to `audit.db`. Each row carries the hash of the row before it, so any later modification to a row breaks the chain and is detectable on verification.

Two independent controls protect it. SQLite triggers reject `UPDATE` and `DELETE` on the log table, so append-only holds even for writes that bypass the application. And every row's hash commits to the previous row's, so if someone with file access drops those triggers — which they can — any subsequent edit, deletion, or reordering shows up when the chain is verified.

This makes the log **tamper-evident, not tamper-proof**. Anyone with write access to `audit.db` can modify the file; the hash chain means you will be able to tell afterwards that they did. It is not a substitute for file-system permissions or backups.

The chain is global across the whole file rather than per-report, so removing one report's rows entirely is detectable too. Verify it with `AuditLog.verify_chain()`, which returns `(True, [])` on an intact log or `(False, [row_ids])` naming the rows that no longer agree.

**Recovery verifies the whole chain first.** Before any saved snapshot is loaded back into the pipeline — after a restart, or whenever a session resumes a report — the complete chain is walked from the beginning. If any row disagrees, recovery is refused, no state is restored, and the error names the failing rows. Verifying only the snapshot would not be enough: a snapshot stays valid on its own while an earlier decision behind it has been rewritten.

Because the chain is global, this refusal is not limited to the report you are resuming. **One corrupt row makes every report in that `audit.db` unresumable**, and there is no override. The remedy is to restore `audit.db` from a backup and keep the current file as it stands, because it is evidence — nothing is ever repaired automatically. This makes backups an operational necessity rather than merely good practice.

A successful verification is itself recorded in the log, once per report per session. A failed one is not: appending to a chain already known to be broken would write new evidence on top of compromised evidence.

The log contains no verbatim LLM responses. It records that a call happened and what its outcome was.

### `audit.db` is as sensitive as the workbook

Pipeline state is snapshotted to the same database at every gate transition, so that a run survives a restart with its evidence intact rather than just a note that decisions were once made. Before any snapshot is loaded for recovery, the application recalculates its content hash and requires a matching intact snapshot commitment in the audit log; altered or orphaned state is refused. That means **once a workbook has been processed, its contents live durably in `audit.db`, not only in the original .xlsx.**

### Accounting sign and control-total convention

Reference-line amounts are entered as non-negative magnitudes. `debit_credit` supplies the sign: debits contribute positively and credits negatively. A supplied control total must therefore be the signed net total under that same convention. The tie-out uses decimal arithmetic and exact equality; it is a population-integrity control, not a materiality test. A mismatch is preserved as evidence and blocks external reconciliation at Gate 3.

This is deliberate — it is what evidence retention requires — but it has a consequence worth stating plainly: `audit.db` needs the same handling as the source workbook. Same access controls, same disposal policy, same care about where it gets copied. No code in this project can solve that for you.

## Testing

```
pytest tests/           # full suite
pytest tests/test_parser.py -v
```

There is one test module per source module plus an end-to-end test. Each agent has tests for a clean workbook *and* for messy input — blank tabs, broken formulas, inconsistent labels — because real files are messy by default. The audit log is tested for append-only behaviour explicitly, not assumed safe because no code path calls `UPDATE` today. Streamlit's application harness covers the upload shell, premature-export guard, named-approval-record screen, final report screen, and provider-failure copy; browser QA exercises the live flow through Gate 3.

Step 12's acceptance test uses a real LibreOffice-recalculated workbook and the real staged pipeline. It covers duplicate accounting labels, mapping approval, bidirectional completeness, partial reconstruction, context mismatch, threshold recomputation, traceability on both sides, mocked documentation with data minimization, PDF output, restart recovery, and raw-SQL tamper detection. The full suite currently has 301 passing tests.

**What the suite does not cover:** it uses synthetic fixtures rather than a real production workbook, and the Anthropic call in Agent 4 is mocked, so prompt quality and successful live API behaviour remain untested. Step 10's representative PDF has been visually inspected, but large production reports can still exercise pagination combinations absent from the fixture.

## Known limitations

- **No independent reviewer is enforced.** The same person can complete all four gates; the report discloses that no independent review occurred.
- **No application-level authentication exists.** The authorized-approvers file is a local name registry, not authentication or identity verification.
- **The audit log is tamper-evident, not tamper-proof.** Someone with file access can modify or delete `audit.db`; verification makes after-the-fact changes detectable but cannot prevent them.
- **Chain verification does not defend against wholesale forgery.** Someone who rebuilds every row from the beginning produces a chain that verifies. Detecting that needs an anchor held outside the file, which this MVP does not have.
- **Data minimization before LLM calls is informal, not certified.** The local policy withholds long free text and external-link values and records a manifest, but it is not a certified privacy, security, or regulatory control.

## Known gaps

Steps 1–13 implement and test the current MVP model, gates, orchestration, documentation, report, Streamlit interface, acceptance journey, and local-first deployment posture. Hosted operation with real authentication remains post-MVP scope.

Changing a completed Step 1–13 contract is an architecture change. See [CLAUDE.md](CLAUDE.md) for the full rule set and review process.
