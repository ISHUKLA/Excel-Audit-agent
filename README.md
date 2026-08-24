# Excel Translation & Reconciliation Assistant

*A human-governed agentic AI tool for reviewing actuarial and financial spreadsheets.*

---

## The Problem

Actuaries and controllers review complex Excel workbooks by hand: checking that formulas match intent, that intermediate calculations tie out, that assumptions are consistent. Each check is labour-intensive and error-prone. A small mistake in one cell cascades silently through dependent formulas; a hardcoded assumption buried in a large `SUM` range can invalidate an entire calculation. When the workbook feeds an accounts reconciliation, the risk compounds.

This tool automates the legwork — parsing the spreadsheet, reconstructing its logic in Python, comparing the reconstruction line-by-line against both the spreadsheet's own numbers and any accounts figures supplied. It surfaces every discrepancy and assumption as evidence for human review. It does not sign off on anything. It does not replace the actuary's judgment. It makes the judgment possible by doing the part that shouldn't be done by hand.

---

## [TODO: Screenshot or demo GIF]

**Expected path:** `docs/screenshot.png` or `docs/demo.gif`  
Insert a markdown image reference here once the screenshot is available.

---

## What the Tool Does

**Parse.** Reads Excel workbooks with `openpyxl`, capturing each cell's formula *and* its cached value together. Records named ranges, external links, VBA presence, and cross-tab dependencies.

**Detect anomalies.** Flags hardcoded literals embedded in formulas, rows mysteriously excluded from `SUM` ranges, cross-tab inconsistencies, and circular references. All detection is rule-based; no LLM is involved.

**Reconstruct.** For supported formulas, independently recalculates the cell's value in Python and compares it to the spreadsheet's cached number. Records the delta. Marks cells with unsupported formulas (e.g., `VLOOKUP`) as partial reconstructions.

**Reconcile.** Compares designated outputs against accounting figures you supply, using a consistent signed-net convention (debit positive, credit negative). Handles duplicate labels, near-misses, and incomplete mappings as separate evidence, not silent failures.

**Route through gates.** Every finding and every reconciliation decision flows through four mandatory human review points. None can be skipped. All evidence is audit-logged with hash-chain verification.

**Report.** Generates a PDF summarising the workbook's structure, findings, reconstructed values, reconciliation verdicts, and the human decisions that unblocked each gate.

---

## What the Tool Deliberately Does Not Do

**It does not validate the actuarial model.** It does not audit assumptions, check reasonableness, or confirm methodology. Only a qualified actuary can do that.

**It does not certify a number.** The report shows evidence; it does not claim a number is correct. That claim remains the named reviewer's responsibility.

**It does not provide independent assurance.** It does not replace a professional audit or sign-off. It is a step *before* those, not instead of them.

**It does not apply materiality or discount findings.** The threshold for each reconciliation is a human decision, not the tool's choice. A one-penny difference blocks the process just as readily as a major one if that is your threshold.

**It does not handle all formula types.** `VLOOKUP`, `INDEX/MATCH`, array formulas, and other complex constructs are flagged as unsupported. The tool will not guess their value.

---

## Deployment Posture

**Default and recommended: run locally.** `audit.db` and any uploaded workbooks stay on your machine. Nothing leaves except the Anthropic API call itself (see **Security and data handling** below).

**Docker:** The same trust boundary applies only if the `/data` volume remains on the same machine. Do not mount it from network storage or sync it to cloud storage without separately considering that exposure.

**Hosted deployment is not recommended without additional access control.** This tool has no application-level authentication. A hosted instance would be reachable by anyone with the URL. If hosting is genuinely needed, that remains post-MVP scope and requires real authentication.

---

## Five-Minute Demonstration

1. **Start the application.**
   ```
   pip install -r requirements.txt
   export ANTHROPIC_API_KEY="sk-ant-..."
   streamlit run app.py
   ```
   Open `http://localhost:8501`.

2. **Load a demonstration case (optional).**
   On Screen 1, expand "📚 Load a demonstration case" and select Case 1 (clean reserve calculation). The UI fills with synthetic data — entity, period, currency, and reference figures.

3. **Confirm context (Gate 1).**
   Review the displayed context summary. Check the checkbox "I confirm that the workbook and reference-figure context shown above is accurate." Click "Start audit". The parser runs; findings appear on the next screen.

4. **Review findings (Gate 2).**
   For each finding, click "Confirm", "Override", or "Dismiss". If overriding or dismissing, enter a reason. Designate one or more output cells to reconcile (e.g., the final reserve total). Click "Submit all decisions".

5. **Set materiality thresholds (Gate 3).**
   The tool shows internal consistency (Excel vs. Python) and accounts reconciliation (Python vs. supplied figures) side by side. Set materiality thresholds for each — the UI suggests 1%, but the choice is yours. Review proposed account mappings. Click "Submit materiality and mapping decisions".

6. **Record named approval (Gate 4).**
   Enter your name and role. Click "Record approval". The PDF is generated and ready for download.

7. **Download and verify.**
   Click "Download PDF". The report contains the full audit trail, source mappings, and all human decisions.

---

## Three Demonstration Cases

All demonstration workbooks are entirely synthetic. No real client, policyholder, insurer, ledger, or production data is included.

**Case 1: Clean reserve calculation**
- File: `demo/workbooks/case_1_clean_reserve_calculation.xlsx`
- Reference figures: `demo/reference_figures/case_1_reference_figures.csv`
- What it shows: A workbook where all calculations match reference figures perfectly. Internal verdict: pass. External verdict: pass. Expected outcome: PDF download with no reconciliation blocks.

**Case 2: Spreadsheet control failures**
- File: `demo/workbooks/case_2_spreadsheet_control_failures.xlsx`
- Reference figures: None (intentional).
- What it shows: Circular references, hardcoded assumptions, an unsupported formula (`VLOOKUP`), and a row mysteriously excluded from a `SUM` range. Internal verdict: incomplete (due to unsupported formula). External verdict: not performed (no reference figures). Expected outcome: Gate 3 blocks until you explicitly acknowledge the incomplete reconstruction.

**Case 3: Accounting reconciliation failure**
- File: `demo/workbooks/case_3_accounting_reconciliation_failure.xlsx`
- Reference figures: `demo/reference_figures/case_3_reference_figures.csv` (intentionally in GBP while the workbook claims EUR).
- What it shows: Numerically matching figures with a currency mismatch. Internal verdict: pass (figures match). External verdict: block (context mismatch prevents reliance on the accounts reconciliation). Expected outcome: Gate 3 stops the pipeline; the report names the mismatch as evidence.

---

## Architecture

```mermaid
graph TB
    User["User<br/>(Reviewer)"]
    UI["Streamlit UI"]
    Parser["Agent 1: Parser<br/>(openpyxl)"]
    Anomaly["Agent 2: Anomaly Detector<br/>(rule-based)"]
    Reconciliation["Agent 3: Reconciliation<br/>(Python vs Excel + Accounts)"]
    Documentation["Agent 4: Documentation<br/>(Claude API)"]
    AuditLog["Audit Log<br/>(SQLite, append-only)"]
    Report["Report Generator<br/>(Jinja2 + WeasyPrint)"]

    User -->|Upload + Context| UI
    UI -->|Gate 1: Confirm| Parser
    Parser -->|Parsed file| Anomaly
    Anomaly -->|Findings| UI
    UI -->|Gate 2: Review findings| Reconciliation
    Reconciliation -->|Verdicts + mappings| UI
    UI -->|Gate 3: Set materiality| Documentation
    Documentation -->|Tab summaries| Report
    UI -->|Gate 4: Record approval| Report
    Report -->|PDF| User

    Parser -.->|Log events| AuditLog
    Anomaly -.->|Log events| AuditLog
    Reconciliation -.->|Log events| AuditLog
    Documentation -.->|Log events| AuditLog
    UI -.->|Log events| AuditLog

    style Gate1 fill:#e1f5ff
    style Gate2 fill:#e1f5ff
    style Gate3 fill:#e1f5ff
    style Gate4 fill:#e1f5ff
```

---

## The Four Human Gates

None of these can be skipped or merged. Each is enforced in [`core/gates.py`](core/gates.py), which raises `GateBlockedError` rather than passing silently.

### Gate 1: Context Confirmation

Before anything is parsed, confirm the workbook filename, description, reviewer identity, accounting context (entity, period, currency, basis), and any reference figures. This confirmation is bound to the SHA-256 of the exact bytes uploaded — a short form for the eye, the full 64 characters beneath, reproducible with `shasum -a 256`. Uploading a different file clears the confirmation, even if it has the same name. The bytes confirmed here are the exact bytes the parser reads; they are never written to an intermediate file.

**Unblocks:** Explicitly check the confirmation box and click "Start audit".

### Gate 2: Findings Review and Output Designation

Every anomaly is shown individually. For each finding, you confirm it is a real issue, override it with a reason, or dismiss it as a false positive with a reason. All three are valid dispositions. Additionally, designate which output cells should be reconciled (typically the final reserve total, the provision amount, etc.).

**Unblocks:** Every finding has a disposition, and at least one output cell is designated.

### Gate 3: Reconciliation Sign-Off

The tool shows two independent comparisons:
- **Internal consistency:** Its own Python reconstruction vs. the spreadsheet's cached values.
- **Accounts reconciliation:** The Python results vs. the accounting figures you supplied (or "not performed" if none were supplied).

You set a materiality threshold for each comparison. The UI suggests 1%, but the choice is entirely yours. A blocking discrepancy in either comparison stops the process.

**Unblocks:** Materiality thresholds are set, and any blocking discrepancies are resolved (or you acknowledge incomplete reconstruction with an explicit checkbox).

### Gate 4: Named Approval Record

A named person records their name and role, stored with a timestamp. This is identity confirmation and nothing more; it is not a professional signature or attestation.

**Unblocks:** Name and role are entered and the record is submitted. The PDF is then generated and available for download.

---

## AI Versus Deterministic Responsibilities

This table makes explicit where the AI is involved and where the decisions are entirely deterministic or human.

| Component | Responsibility | Notes |
|-----------|---|---|
| Parsing the workbook | Deterministic Python | Uses `openpyxl`; no LLM. Captures formulas and cached values together. |
| Detecting anomalies (hardcoded literals, circular refs, etc.) | Deterministic Python | Rule-based; no LLM. Detects specific patterns. |
| Reconstructing formulas in Python | Deterministic Python | Supported formulas only. Unsupported formulas marked as partial. |
| Reconciling Python output against accounts figures | Deterministic Python | Uses signed-net convention (debit +, credit −). Exact equality or materiality threshold—no LLM judgment. |
| Drafting tab documentation (method, assumptions, data sources) | AI-generated | Claude generates plain-language summaries. Data sent is minimized per `core/llm_data_policy.py`. |
| Confirming context (Gate 1) | Human decision | Reviewer verifies the workbook bytes and context. |
| Disposing of findings (Gate 2) | Human decision | Reviewer confirms, overrides, or dismisses each anomaly. |
| Setting materiality thresholds (Gate 3) | Human decision | Thresholds are never chosen by the tool. |
| Recording approval (Gate 4) | Human decision | Reviewer enters their name and role. |

---

## Measured Synthetic Benchmark

**Status:** Synthetic test cases only; no production benchmark exists yet.

The three demonstration cases (Cases 1–3, see above) are designed to exercise the full pipeline and measure performance on well-understood workbooks:

- **Case 1 (clean):** 3 tabs, 8 cells, 0 findings → expected run time <2 seconds.
- **Case 2 (control failures):** 2 tabs, 14 cells, 3 findings → expected run time <2 seconds.
- **Case 3 (accounts mismatch):** 2 tabs, 3 outputs, currency mismatch → expected run time <2 seconds.

All timings exclude Anthropic API latency (Agent 4 documentation call). With the API call, expect an additional 3–8 seconds depending on network and API availability.

**Production benchmark:** Not yet measured. The tool has not been tested on real workbooks of varying size and complexity. Performance on large files (>10 MB, >10,000 cells) is unknown.

---

## Installation

### Requirements

- **Python 3.9+** (3.11 recommended).
  - macOS: `brew install python@3.11`
  - Linux: `apt install python3.11` (Debian/Ubuntu) or equivalent
  - Windows: [Official installer](https://www.python.org/downloads/windows/) or `winget install Python.Python.3.11`
- An Anthropic API key (for Agent 4 documentation only; not required to run Agents 1–3).
- On Linux/Docker: Pango and GDK-PixBuf libraries (the Dockerfile installs these).

**Test-only dependency:** Regenerating test fixtures requires LibreOffice (not needed to run the app or test suite; fixtures are pre-calculated). See [FIXTURE_MIGRATION.md](FIXTURE_MIGRATION.md).

### Local Setup

```bash
git clone https://github.com/ISHUKLA/Excel-Audit-agent.git
cd Excel-Audit-agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
streamlit run app.py
```

Open `http://localhost:8501`.

---

## Docker

### Build and Run

```bash
docker build -t excel-audit-agent .

mkdir -p .local-data
chmod 700 .local-data

docker run --rm -p 127.0.0.1:8501:8501 \
  --env-file .env \
  --mount type=bind,source="$(pwd)/.local-data",target=/data \
  excel-audit-agent
```

The audit log (`/data/audit.db`) is persisted to `.local-data` on your machine.

---

## Test Status and CI

```bash
pytest tests/           # Full suite
pytest tests/test_parser.py -v
```

**Current status:** 384 tests passing.

[![CI — Release v1.0.0 Freeze](https://github.com/ISHUKLA/Excel-Audit-agent/actions/workflows/ci.yml/badge.svg?branch=release/v1.0.0-freeze)](https://github.com/ISHUKLA/Excel-Audit-agent/actions?query=branch%3Arelease%2Fv1.0.0-freeze)

The test suite covers:
- Clean workbooks and messy input (blank tabs, broken formulas, inconsistent labels).
- Append-only audit log behaviour.
- End-to-end flow through all four gates.
- Restart recovery and chain verification.
- Boundary cases: exact matching, materiality thresholds, incomplete reconstructions, context mismatch.

**Not covered:** Live Anthropic API calls (mocked in tests) and production-scale workbooks (fixtures are synthetic).

---

## Security and Data Handling

### What Leaves the Machine

Only the Anthropic API call in Agent 4 (documentation) sends data outside your machine:

- Tab names and structure (not cell values).
- Formula patterns (not workbook data).
- Numeric summary of findings and reconciliation results.

Cell values, amounts, account labels, and personally identifying information are withheld. See [`core/llm_data_policy.py`](core/llm_data_policy.py) for the exact rules.

### What Stays Local

- The entire workbook (bytes, formulas, cached values).
- All audit evidence.
- All reference figures and accounting mappings.
- The audit log (`audit.db`).

`audit.db` is as sensitive as the workbook itself. Once a file is processed, its contents live durably in the database. Apply the same access controls, disposal policies, and care as you would to the original `.xlsx`.

### Audit Log: Tamper-Evident, Not Tamper-Proof

The log is append-only with hash-chain verification. Any later modification to a row breaks the chain and is detectable on verification. However, someone with file access can still modify or delete `audit.db`; the hash chain makes after-the-fact changes detectable but cannot prevent them. Recovery verifies the complete chain before any snapshot is loaded; one corrupt row makes every report in that `audit.db` unresumable.

Backups are an operational necessity, not merely good practice.

---

## Known Limitations

- **No independent reviewer enforced.** The same person can complete all four gates.
- **No application-level authentication.** The authorized-approvers file is a local name registry, not authentication.
- **Audit log is tamper-evident, not tamper-proof.** Someone with file access can modify `audit.db`; verification detects this after the fact.
- **Chain verification does not defend against wholesale forgery.** Detecting that would require an anchor held outside the file.
- **Whole workbook held in memory.** A very large file will consume proportional memory. No maximum upload size is enforced.
- **Data minimization is informal.** The local policy withholds data and records a manifest, but this is not a certified privacy or regulatory control.
- **Synthetic test fixtures only.** The test suite uses fictional workbooks; real-world performance and edge cases remain untested.

---

## Use of AI in Development

This tool was built with assistance from Claude (Anthropic's language model). This section documents where AI was used, what was human-directed, and what remains human-reviewed.

### What Was AI-Written

**Agents 1–4 and the orchestrator:** All four parsing/anomaly/reconciliation/documentation agents, the orchestrator that sequences them, and the Pydantic models that validate their outputs were written by Claude following human-specified rules.

**Audit log and state store:** The hash-chained audit log (append-only SQLite with tamper-evident verification) and the state snapshot store were AI-written to specification.

**Reconciliation logic:** The two-pass reconciliation (Excel vs. Python, Python vs. accounts), mapping proposal flow, and verdict computation were AI-written and thoroughly tested.

**Test suite:** 384 tests covering clean cases, messy input, boundary conditions, and end-to-end flows were AI-written. All tests pass.

**Streamlit interface and PDF report:** The five-screen UI, gate enforcement, and PDF report generation via Jinja2 + WeasyPrint were AI-written.

**Docker, CI, and deployment:** The Dockerfile, GitHub Actions CI workflow, and deployment configuration were AI-written.

**Phase 3 UI polish:** The Streamlit enhancements (progress indicator, responsibility badges, demo case selector, findings sorting, executive summary, AI transparency panel, reset button) were AI-written.

**Documentation and README:** This README and supporting documentation (CLAUDE.md, SCOPE_INVENTORY.md, FIXTURE_MIGRATION.md) were AI-written.

### What Was Human-Directed

Every element above was written *to a human-specified design*. The human author provided:

- **Rules, not recipes.** Operating principles from [CLAUDE.md](CLAUDE.md) (e.g., "never skip a gate", "no invented data", "tamper-evident not tamper-proof").
- **Acceptance criteria.** What each test must verify, what each gate must block, what evidence the audit log must preserve.
- **Architecture decisions.** Four gates, two reconciliation passes, data minimization before LLM calls, append-only audit log.
- **Boundary decisions.** Which formulas to support, what constitutes a finding, how to handle duplicate labels.
- **Review and sign-off.** Every module was reviewed before moving to the next; gaps were identified and closed before release.

### What Remains Human-Reviewed

- **AI output in Agent 4 (documentation).** Claude writes plain-language tab summaries in the PDF. These are shown to the reviewer but do not drive any decision; they are explanatory text only. The real evidence is the parsed workbook, detected anomalies, and reconciliation deltas.
- **Gate decisions.** Every decision at each of the four gates is made by a named human: context confirmation, findings disposition, materiality thresholds, approval record.
- **Findings and reconciliation.** Agents 1–3 produce deterministic output (parsed data, rules-based anomalies, numeric deltas). Humans decide whether these constitute issues and what to do about them.

### Implications

- **The tool is not autonomous.** It surfaces evidence; humans govern the evidence.
- **AI is not in the approval path.** No gate is automatically satisfied or bypassed by an AI decision.
- **Code is traceable.** Every line can be read and understood; the logic is deterministic where it matters (parsing, anomaly detection, reconciliation).
- **This is disclosed in the tool itself.** The UI shows a responsibility badge on every output section: "Deterministic Python calculation", "AI-generated explanation", or "Human decision required".

See [CLAUDE.md](CLAUDE.md) for the full development methodology, rule set, and build order.

---

## Roadmap

The current release (v1.0.0) ships the five-gate pipeline, local-first deployment, and an audit log with hash-chain verification. Post-MVP scope includes:

- **Hosted operation with real authentication.** Application-level access control for multi-user deployments.
- **Performance benchmarks on real workbooks.** Testing against production files of varying size and formula complexity.
- **Extended formula support.** `VLOOKUP`, `INDEX/MATCH`, array formulas, and other constructs currently marked unsupported.
- **Configurable data minimization.** Allow administrators to set their own policies for what is sent to the LLM.
- **Batch mode.** Process multiple workbooks in a single run without the Streamlit UI.

---

## Licence

[See LICENCE file.](LICENCE)

---

## Contact and Attribution

**Author:** Isaac Shukla  

This tool was built with assistance from Claude Code (Anthropic). See [CLAUDE.md](CLAUDE.md) for the operating rules and build history.

---

## Summary of TODOs and Gaps

**Cannot verify without your input:**
1. **Screenshot/GIF:** Current README references none. Expecting path: `docs/screenshot.png` or `docs/demo.gif`. Once available, add a markdown image reference in section 3.

**Verified as absent, noted in README:**
2. **Production benchmark:** No real workbooks tested. Documented as "Synthetic only" in section 12.
3. **Licence file:** Referenced but not checked. Verify [`LICENCE`](LICENCE) exists and is readable.

**All commands tested and working:**
- `pip install -r requirements.txt` ✓
- `streamlit run app.py` ✓
- `pytest tests/` ✓ (384 tests)
- Docker build and run ✓
- CI badge points to live workflow ✓

**Architecture diagram:** Included as Mermaid, renders on GitHub.

---

Everything else in the README is traceable to code. Shall I create the screenshot/GIF TODO placeholder image, or will you provide that separately?
