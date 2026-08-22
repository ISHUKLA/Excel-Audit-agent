# Context Brief: Excel-to-Python AI Converter
**Last updated:** July 2026  
**Owner:** Senior Actuary (AI Developer)  
**Status:** Prototype phase (3-month timeline)

---

## What We're Building

**Product name:** Excel-to-Python AI Converter

**The problem:** Actuarial work is stuck in Excel. Files contain calculations, formulas, checks, copy-paste chains, and multiple interdependent tabs. These files are:
- Hard to audit (formulas buried in cells, no documentation)
- Operationally risky (changes are silent, no version control)
- Time-consuming to validate (manual tab-by-tab checking)
- Not traceable to auditors (no explanation of assumptions or methodology)

**The solution:** Take any Excel file (any domain), analyze it, rebuild the core calculation in Python, compare outputs internally AND against the general ledger / accounting figures, and produce a Translation & Reconciliation report, with its approval recorded by name and timestamp, showing:
- What the file does (AI-generated documentation)
- Which numbers match between Excel and Python
- Which numbers reconcile to the accounts (GL, trial balance, statutory filing)
- A traceable path from any reported figure back to its source cell and formula
- Which gaps exist and why
- Who reviewed and approved the validation

**MVP concrete scope:**
- Input: one Excel file with premiums, claims, triangles, assumptions, results tabs, PLUS a reference figure set (GL extract or trial balance line items) to reconcile against
- Process: read → detect tabs → extract formulas → reconstruct in Python → compare Excel vs Python → compare Python vs accounts → report
- Output: timestamped PDF showing discrepancies (both internal and vs. accounts) and AI-written documentation, with a traceability index (report figure → tab → cell → formula)

**Success criteria:**
- Produces a defensible, tamper-evident audit trail (traceable, hash-chained, with a named approval record, timestamped — see Security & Evidence Posture below for what each of those terms actually guarantees, and why "attested" itself got rejected as a term along the way)
- Generates Python code that an actuary can understand and modify
- Creates documentation that a CRO can read without calling the developer
- Reconciles model output to accounting figures within a stated tolerance, for the CFO
- Lets an auditor trace any number in the report back to its exact cell, in a small number of clicks — not a search
- Handles any Excel file structure (not just insurance)

---

## Who Uses It

| User | Role | What they need | How they interact |
|------|------|----------------|-------------------|
| **Senior Actuary** | You (owner) | Code + findings | Runs the tool locally, reviews Python output, uses as proof of validation |
| **CRO** | Risk oversight | Verdict + confidence | Reads the PDF report, calls you only if there are blockers; signs off on high-level safety |
| **CFO** | Financial authority | Numbers reconciled to the accounts (GL / trial balance), within a stated tolerance | Reads the reconciliation summary; needs to see the model output ties to what's booked, not just that Excel matches Python |
| **External Auditor** | Third-party verification | Cell-level traceability — any figure in the report must be traceable back to its source tab, cell, and formula, without asking you | Uses the PDF's traceability index as audit evidence; follows the trail independently |

**Note on reconciliation — two distinct checks, both required:**
1. **Excel vs Python** (internal consistency) — does the reconstructed calculation match the spreadsheet?
2. **Python vs accounts** (external consistency) — does the calculation's output match what's actually booked in the GL / trial balance / statutory return?
The CFO cares primarily about #2. The auditor cares about being able to walk both #1 and #2 backwards to source, unaided.

---

## Your Role

**Title:** Senior Actuary + AI Developer  
**You own:**
- The Python pipeline architecture (agents, reconciliation logic)
- End-to-end delivery of the MVP
- Quality of the audit trail (every decision logged, traceable)
- Integration with Anthropic API for documentation generation

**You do NOT own:**
- Fixing Excel files (the tool documents and flags problems; it does not repair the source file)
- Building a production data warehouse (out of scope)
- Creating a web service with user authentication (solo developer constraint)

---

## Constraints (Non-Negotiable)

| Constraint | Details | Impact |
|-----------|---------|--------|
| **Timeline** | 3 months to first prototype | No gold-plating; prioritize MVP |
| **Team size** | Solo (just you) | Automation and AI assistance are force multipliers; no manual testing at scale |
| **Deployment** | Local execution is the MVP target — see Step 13's "Deployment posture" | Docker is available for the same trust boundary; Streamlit Cloud or any hosted deployment is explicitly deferred until real authentication exists, not a same-tier option |
| **Stack** | Python 3.11+; openpyxl, pandas, Anthropic API | No VBA execution; cached Excel values only |
| **Audit trail** | Hash-chained, tamper-evident SQLite log of every human decision, plus full pipeline state snapshots at each gate | Non-negotiable for CRO review — "tamper-evident" is the accurate claim, not "tamper-proof" |
| **Input quality** | Real Excel files are messy — inconsistent naming, typos, missing cells, broken formulas, blank tabs, mixed data types in a column | Every agent must degrade gracefully: log and flag what it can't parse or match, never crash the pipeline and never silently skip it |
| **Human in the loop** | Four explicit gates (context → findings → reconciliation → sign-off) | AI flags; human approves before report exits |
| **LLM involvement** | Aggressive use for speed (documentation, context interpretation) | But NOT for certifying numbers; Python does the math |

---

## Architecture Snapshot

Two inputs (Excel file + structured GL/trial balance reference lines, each carrying account number, entity, period, currency, and evidence reference — never a flat label-to-value dict) → Gate 1 confirms both AND checks entity/period/currency agreement between them → Orchestrator → Agent 1 (Parser) builds a cell-level record — formula, cached value, type, and format together, never one instead of the other — plus a cell-level dependency graph → Agent 2 (Anomaly detector) runs, including a true cell-level circular-reference check → Gate 2 (findings review, AND the human explicitly designates which output cells are authoritative — never inferred by keyword matching) → Agent 3 (Reconciliation) walks the dependency graph from each designated output, reconstructing only formulas within an explicit supported catalogue (+, -, *, /, SUM) and marking anything else "incomplete," while Pass 2 produces accounting-mapping *proposals* from fuzzy matching — never approvals — and checks completeness in both directions (every reference line needs a Python match; every designated output needs an accounting match) → Gate 3 (a human approves or rejects every mapping proposal one at a time; sets two thresholds — percentage AND absolute — for each of the internal and external checks, with any deviation from the documented default requiring a written reason; an "incomplete" verdict requires explicit acknowledgement; a "block" cannot be bypassed; external verdict cannot be "pass" while any mapping is unapproved, any figure is unmatched in either direction, or entity/period/currency don't agree) → Report assembly (computes the four-state verdict and builds the traceability index — the Excel side from Agent 1/3's derivation chains, the accounting side from the same approved mappings and reference lines, never a fresh lookup on either side) → Gate 4 (a named approval record — a typed name checked against a local approver registry, with a timestamp; explicitly not authentication and not a signature or attestation) → PDF, titled "Translation & Reconciliation Report" with fixed disclaimers on actuarial scope, preparer/approver independence, and accounting-mapping approval status.

Agent 3 (Reconciliation) is the one agent that does two distinct comparisons, not one — this is the load-bearing design decision that makes the CFO and auditor requirements work. Keep these separate in code: two different threshold parameters, two different verdict tables, never collapsed into a single pass/fail. Within Pass 2 specifically, keep "a match was suggested" and "a match was approved" as two distinct states in every layer of the code — the model, the gate, the report — never one standing in for the other.

The full diagram is `excel_audit_agent_architecture_v2` from the design session — reference it when briefing Claude Code on the orchestrator or Agent 3, though note the diagram predates the mapping-approval step added after the CFO review and should be read alongside this section, not in place of it.

---

## Accounting Reconciliation Governance

A CFO review of this project found seven decisions about what "reconciled to the accounts" actually means that were never made — the tool was comparing numbers without a governed process behind the comparison. Here's what's now decided, as honest MVP defaults, not as a claim that a full accounting close process has been replicated.

**What "reconciled to the accounts" means here.** A Python-reconstructed figure is reconciled to the accounts when: it has an `AccountMapping` to a specific `ReferenceFigureLine`, a human has set `is_approved=True` on that mapping, the numeric delta falls within the approved thresholds, and the workbook's entity/period/currency match the reference extract's. Anything short of all four is not a reconciliation — it's a proposal, a gap, or a mismatch, and the report says which.

**Required GL metadata.** Every `ReferenceFigureLine` carries account number, entity, period, currency, ledger source, debit/credit orientation, and an evidence reference. A reference figure with only a label and a number — what the tool used to accept — is no longer a valid input.

**Approved mapping table and mapping owner.** The mapping table is `AccountMapping`, and the owner of each mapping is whoever set `approved_by` — always a named person, never "the algorithm." Fuzzy matching (Step 7) proposes; it never approves. This is enforced at three layers on purpose: the data model (`is_approved` defaults False), the gate (Gate 3 blocks on any unapproved mapping still in use), and the report (proposed and approved mappings are shown in visibly separate tables).

**Aggregation, signs, currencies, rounding.** MVP scope is 1:1 mappings only. One-to-many, many-to-one, and elimination entries are detected and flagged — "requires manual reconciliation, not computed" — never silently summed or split. Amounts are always non-negative with a separate debit/credit field, never sign-encoded, to avoid the ambiguity a bare signed float invites. Currency conversion is out of scope entirely: a currency mismatch between workbook and extract is treated the same as an entity or period mismatch — it caps the external verdict at "block."

**Unmatched and duplicate items.** Both directions of incompleteness are checked and both are visible in the report: `unmatched_reference_items` (GL lines with no Python counterpart) and `unmapped_python_outputs` (designated outputs with no GL counterpart) — populating either one caps the external verdict below "pass." Duplicate labels are explicitly supported by the data model (`ReferenceFigureLine.line_id` is the unique key, not the label), so two GL lines that happen to share a label no longer collide or silently overwrite each other.

**Materiality governance.** Two thresholds — percentage and absolute — evaluated together, per check. Defaults live in `config/materiality_defaults.json`, not hardcoded in the reconciliation logic, so they're inspectable and changeable without a code change. Any threshold used for a specific report that differs from the default requires a written reason, logged in the same hash-chained trail as every other decision — this doesn't prevent someone from raising a threshold to pass a borderline figure, but it makes doing so impossible to do quietly.

**Required evidence for close, management review, and external audit.** This is the one item genuinely outside the tool's authority to define — what counts as sufficient evidence for your organization's close process is a policy decision, not an engineering one. What the tool provides is an input: a report showing exactly what was reconciled, what wasn't, who approved which mappings, and a verifiable trail of all of it. Whether that input satisfies a given close checklist or audit requirement is a judgment call for you or your controllership function, not something this document or the tool can settle.

---

## Security & Evidence Posture

A CRO review of this project found five decisions that were never made
explicit. Here they are, made explicit — as honest defaults for a solo
MVP, not as claims that the gaps are fully solved.

**Data classification.** Treat every uploaded workbook as confidential
business data by default. No automatic classification exists — the tool
does not try to detect whether a file contains personal data, only
applies the same minimization discipline (Step 8) regardless of what a
given file actually contains.

**Authentication and access control.** None exists at the application
level. This is a single-user local tool. `config/authorized_approvers.json`
checked at Gate 4 is a name registry, not authentication — it catches
typos and unregistered names, it does not verify identity. If this tool
is ever run somewhere reachable by more than one person, that gap needs
solving before anything else on this list.

**Retention, deletion, backup.** Everything — the original workbook's
full contents, every finding, every reconciliation line, every gate
decision — becomes durable in `audit.db` on the machine running the
tool, per the state-snapshot store (Step 3b). There is no automatic
deletion, backup, or recovery policy beyond "it's a file on disk you
control." Once a workbook has been processed, `audit.db` is exactly as
sensitive as the source file and needs to be handled that way — this is
a disclosure, not a solved problem.

**Third-party data flow.** Documentation (Agent 4) sends a minimized
subset of each tab's content to the Anthropic API — direct references,
formulas, and short text labels; long free-text cells and external-link
values are withheld by default (Step 8's `llm_data_policy.py`). Every
call is logged with a manifest of exactly what was included and
excluded, never the raw content. Commercial API traffic is not used for
model training and is deleted from Anthropic's backend within 30 days
by default (zero-retention arrangements exist for qualifying accounts) —
see Anthropic's current data retention documentation for specifics. This
is useful context, not a legal determination: whether sending a specific
file's content is compliant with your jurisdiction's data protection
rules is a judgment call the tool cannot make for you.

**Change control.** `code_version` (a git commit hash where available)
is embedded in every report and every audit log row. `CHANGELOG.md`
records what changed and when. There is no formal release-approval
process beyond that — appropriate for a solo build, explicitly
insufficient if this is ever handed to a team.

**Independence.** As a solo project, the same person prepares (Gates
1-3) and attests (Gate 4) every report. The tool does not pretend this
constitutes independent review — `independence_disclosure` states this
plainly on every report, the same way a real audit or actuarial opinion
discloses when independence isn't available rather than staying silent
about it.

**Terminology — say exactly what the mechanism provides, nothing more:**

| Term used | What it actually means here | Term NOT used |
|---|---|---|
| Named approval record | A typed identity confirmation, checked against a local registry, with a timestamp | ~~Signed~~ (implies a cryptographic or legal signature) / ~~Attested~~ (implies a formal accounting or audit attestation engagement) |
| Tamper-evident | Modification after the fact is detectable by re-verifying the hash chain | ~~Tamper-proof~~ / ~~immutable~~ (implies modification is impossible) |
| Translation & Reconciliation | Formula-level translation into Python, reconciled against Excel and, optionally, accounting figures | ~~Validation~~ / ~~audit~~ standing alone (implies methodology or reserve adequacy was reviewed) |

Every one of these distinctions exists because a review — actuarial or
risk — found a place where the tool's language claimed more than its
mechanism delivered. Treat any future feature or report section that
uses the left-hand column's *opposite* word as a regression, not a
wording choice.

---

## Scope: What's In, What's Out

### IN (MVP)
- Read any .xlsx file with formulas and values
- Detect hardcoded literals and assumptions
- Reconstruct calculations in Python using an EXPLICIT supported formula catalogue only: direct cell references, +, -, *, /, and SUM. Anything outside this (VLOOKUP, IF, INDEX/MATCH, array formulas, etc.) is flagged as unsupported and the affected figure is reported as "incomplete," never silently faked or silently dropped
- Compare Excel vs Python outputs using TWO thresholds together (a percentage threshold and an absolute-currency threshold), never one alone — a tiny percentage gap on a near-zero figure and a small absolute gap on a huge figure both need to be catchable
- The human explicitly designates which output cell(s) are authoritative before Agent 3 runs — never inferred by matching keywords in the file description against tab names
- Accept a structured reference figure set (account number, entity, period, currency, ledger source, debit/credit, evidence reference per line — never a flat label-to-value dict) and propose accounting mappings against it using fuzzy matching, with the same two-threshold logic — for the CFO
- Require a human to explicitly approve every accounting mapping before it counts toward a verdict — fuzzy matching proposes, it never approves
- Check completeness in BOTH directions: every reference figure must map to a Python output, and every designated Python output must map to a reference figure — an external "pass" is impossible while either direction has a gap
- Confirm entity, period, and currency agree between the workbook and the reference figures before treating any accounting comparison as meaningful — a mismatch caps the external verdict at "block"
- Build a traceability index: every figure in the report links back to its source — a cell-level derivation chain for Excel-side figures, and account number / ledger source / evidence reference for accounting-side figures — for the auditor
- Generate AI documentation of each tab (method, assumptions, anomalies)
- Produce a PDF report with a named approval record (role-tailored for actuary/CRO/CFO/auditor)
- Hash-chained, tamper-evident SQLite audit log of all human decisions, plus durable state snapshots at every gate transition
- Data-minimization policy and a manifest of exactly what was sent to the Anthropic API for documentation

### OUT (post-MVP)
- VBA macro execution
- Live external data feeds (Bloomberg, APIs)
- Multi-file dependencies (linked workbooks)
- Real-time dashboard or web service
- Custom installation for other companies
- Pivot table re-execution
- Advanced Excel functions (array formulas, complex array indexing)
- Live connection to a GL / ERP system (SAP, Oracle) — MVP takes a manually provided reference figure set, not a system integration
- Automated multi-period reconciliation (this quarter vs last quarter) — MVP is single-period, single-file
- Automated aggregation: one-to-many, many-to-one, and elimination mappings are detected and flagged for manual reconciliation, never computed automatically
- Currency conversion — a currency mismatch between workbook and reference figures is treated as a hard block, not converted
- Full trial-balance debit=credit tie-out — MVP accepts an optional control total with a human confirmation that the extract ties to it, not an automated verification

### PARTIAL (best effort, flag if unsupported)
- Pivot tables — read cached output only, flag if they need re-execution
- External links — detect and warn; user must provide linked files
- Merged cells — detect; document limitation

---

## Where AI is Involved (and How)

### AGGRESSIVE LLM USE (speed is the goal)
1. **Tab documentation** — Send each tab's structure + formulas to Claude; get back plain-English explanation of what it does, what assumptions it uses, what anomalies exist
2. **Context interpretation** — Parse user's file description ("This file calculates earned premium reserves"); LLM helps identify which tabs matter for the core calculation
3. **Finding classification** — LLM helps rank anomalies by risk level (blocker vs warning vs info)
4. **Report generation** — LLM writes the executive summary; user's role (actuary vs CRO vs auditor) shapes the language

### ZERO LLM USE (you control entirely)
1. **Number calculation** — Python/pandas/numpy only; no LLM involved in math
2. **Formula extraction** — Pure Python regex + openpyxl; no LLM parsing
3. **Audit log** — SQLite, hash-chained and tamper-evident; no LLM touches this
4. **Gate decisions** — Human decision is final; LLM never overrides a gate

### HYBRID (LLM assists, you verify)
1. **Hardcoded literal detection** — Regex finds candidates; LLM confirms severity and suggests why the value might be hardcoded
2. **Reconciliation thresholds** — LLM suggests a default materiality threshold; human sets it explicitly before Gate 3

---

## Three-Month Delivery Plan

### Month 1 — Foundation
- Scaffold the project (models, gates, audit log)
- Build Agent 1 (Parser) — read Excel, extract all structure
- Build Agent 2 (Anomaly detector) — find hardcoded values, cross-tab inconsistencies
- **Deliverable:** CLI tool that reads an Excel file and lists all anomalies

### Month 2 — Calculation, reconciliation & documentation
- Build Agent 3 (Reconciliation) — two passes: reconstruct Python calculations and compare to Excel (internal), then compare Python output to GL/trial balance reference figures (external, for CFO)
- Build Agent 4 (Documentation) — LLM-powered tab summaries
- Build the traceability index (report figure → tab → cell → formula) at report-assembly stage
- Build PDF report generator (role-tailored templates: actuary detail, CRO summary, CFO reconciliation, auditor traceability pack)
- **Deliverable:** PDF report from a test Excel file, with its approval named and timestamped, with both reconciliation passes and a working traceability index

### Month 3 — UI & Hardening
- Build Streamlit interface (file upload, four gates, review screens)
- Integration testing end-to-end
- Create realistic demo Excel file (premiums, claims, reserves)
- **Deliverable:** Live Streamlit app; CRO can run it locally and get a report with a named approval record

---

## How to Use This Brief in Future Sessions

1. **For Claude Code sessions:** Paste the [Tech Stack](#tech-stack) and [Step-by-Step Algorithm](#step-by-step-algorithm) sections
2. **For architecture decisions:** Point to the [Constraints](#constraints-non-negotiable) table
3. **For scope clarification:** Reference the [Scope](#scope-whats-in-whats-out) section
4. **For LLM integration questions:** Show the [Where AI is Involved](#where-ai-is-involved-and-how) section

---

## Tech Stack (Reference)

| Layer | Tools | Why |
|-------|-------|-----|
| File parsing | openpyxl | Read formulas + cached values; detect named ranges |
| Data manipulation | pandas + numpy | Actuarial calculations; comparison logic |
| Anomaly detection | networkx + regex | Dependency graphs; hardcoded literal search |
| Documentation | Anthropic API (Claude Sonnet) | Fast LLM for tab explanations |
| Validation | pydantic | Every agent output validated before pipeline continues |
| Interface | Streamlit | File upload, human gates, findings review, report viewer |
| Audit trail | SQLite, hash-chained | Tamper-evident log of all human decisions plus state snapshots; verifiable via `verify_chain()`, embedded in PDF |
| Report generation | jinja2 + weasyprint | PDF creation; role-tailored templates |
| Deployment | Local (Docker optional, same machine) | MVP target is local execution, per Step 13 — Streamlit Cloud is deferred, not a parallel option, until authentication exists |

---

## Questions to Ask Before Starting a Session

If an AI assistant asks these before coding, you'll save time:

1. **Scope clarification:** "Should this handle [specific Excel feature]? If yes, what's the priority?"
2. **Agent design:** "Agent X needs to [task]. Should it use an LLM or pure Python logic?"
3. **Test coverage:** "What's the minimum test case that proves this works?"
4. **Dependency risk:** "Does this module depend on another module that hasn't been built yet?"
5. **Human gate logic:** "If a human overrides this finding, what information must we log?"

---

## Red Flags (Things That Will Derail the 3-Month Timeline)

- ❌ Trying to handle VBA macros or live data feeds → **OUT OF SCOPE**
- ❌ Building a production web service with auth → **USE STREAMLIT LOCALLY**
- ❌ Attempting to reconstruct all Excel functions exactly → **RECONSTRUCT SIMPLE ONES; FLAG COMPLEX ONES**
- ❌ Asking LLM to certify numbers → **LLM DOCUMENTS; PYTHON VERIFIES**
- ❌ Skipping the audit log → **NON-NEGOTIABLE FOR CRO SIGN-OFF**
- ❌ Combining two modules in one prompt to Claude Code → **ONE MODULE = ONE SESSION**
- ❌ Assuming the test Excel file represents real-world input → **TEST WITH MESSY FILES TOO: typos in labels, blank cells, inconsistent tab names, mismatched reference-figure labels**

---

## How to Know You've Succeeded

✅ You have a Streamlit app that runs locally  
✅ You can upload a real Excel file and get a PDF report  
✅ The PDF shows what tabs were found, what anomalies were detected, what the Python reconstruction found  
✅ A CRO can read the summary page and understand the verdict without calling you  
✅ An auditor can use the full report as evidence in their audit file  
✅ Every finding, decision, and override is logged and timestamped in the PDF  
✅ The Python code is documented and could be handed to another actuary for modification  
✅ A CFO can see the model output reconciled against a real GL / trial balance figure, with the gap explained  
✅ An auditor can pick any number in the report and trace it back to its source cell without asking you a single question  
✅ No accounting comparison in the report happened without a named human approving the specific mapping — a fuzzy match alone never produces a passing verdict  
✅ The report shows both directions of the accounting reconciliation — what didn't match on the GL side AND what wasn't mapped on the model side — never just one

---

## Next Steps

1. **Paste this brief into your next AI session** as context
2. **Use the [Step-by-Step Algorithm](#step-by-step-algorithm) when asking Claude Code to build modules**
3. **After each module, run the tests** — this brief assumes 100% test coverage
4. **At the end of Month 1**, demo the anomaly detector to your CRO to confirm direction
5. **At the end of Month 3**, have a working Streamlit app and a realistic demo file

---

## Appendix: Step-by-Step Algorithm for Claude Code

*(See separate document: `claude_code_prompting_algorithm.md`)*

This algorithm breaks the entire build into 13 sequential steps. Each step is one Claude Code session. Do not combine steps. Always test before moving to the next step.
