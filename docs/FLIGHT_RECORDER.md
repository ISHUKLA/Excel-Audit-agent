# Governance Flight Recorder User Guide

## 1. What is the Flight Recorder?

The **Governance Flight Recorder** is a visual execution map of the audit pipeline, displayed after Gate 4 approval is recorded. It shows the complete sequence of decisions, transformations, and evidence flows across all pipeline nodes in real-time.

The flight recorder captures:
- **Authority at each node**: who (human, deterministic agent, or bounded AI) made each decision
- **Evidence in and out**: what data entered and left each stage
- **Status and verdicts**: complete, waiting, blocked, locked, or incomplete
- **Hash chain**: cryptographic fingerprints of every event for tamper detection
- **Governance rules**: what each node is allowed and prohibited to do

The flight recorder is **tamper-evident**: every event is hashed and chained to its predecessor. If any event is modified, the chain detects the change and refuses recovery. This is not tamper-proof (someone with file access can edit the database), but it is tamper-evident (any modification is detectable after the fact).

---

## 2. Pipeline Overview

The audit pipeline is a **7-node sequence**, each controlled by a different authority:

```
Gate 1             Anomaly           Gate 2            Internal &         Gate 3            Optional         Gate 4
(Human)            Detector          (Human)           External            (Human)           AI Doc           (Human)
  │                (Agent)             │                Reconciliation        │              (AI)              │
  │                  │                 │                (Agents)              │                │                │
  ├─→ Context ──────┼─→ Findings ─────┤─→ Reconciliation ──────────────────┤─→ Optional ───┤─→ Approval ───┤
  │   Confirmation   │   Validation    │    (deterministic)                │    Documentation   Record      │
  │                  │                 │                                    │                │                │
  └──────────────────┴─────────────────┴────────────────────────────────────┴────────────────┴────────────────┘
     (Workbook             (Findings              (Reconciliation          (Named           (PDF
      Context)             Disposal)              Verdict)                 Approval)        Export)
```

**Node authorities:**

| Node | Authority Type | Role | Decision Binding |
|------|---|---|---|
| **Gate 1** | Human (Qualified Actuary) | Confirms workbook context (entity, period, currency) before parsing | Gate can block; context mismatch halts pipeline |
| **Anomaly Detector** | Agent (Deterministic Code) | Scans for hardcoded values, circular refs, unsupported formulas | Cannot modify findings; findings flow to Gate 2 |
| **Gate 2** | Human (Qualified Actuary) | Reviews all anomalies; approves, overrides, or dismisses each one | Must disposition every finding; cannot skip findings |
| **Internal & External Reconciliation** | Agents (Deterministic Code) | Reconstructs formulas and compares: (1) internal consistency, (2) reference figures | Cannot override verdicts; propositions flow to Gate 3 |
| **Gate 3** | Human (Qualified Actuary) | Sets materiality thresholds for each reconciliation pass; votes pass/block | **Can block entire pipeline** if reconciliation fails; blocks Gate 4, AI Doc, and PDF |
| **Optional AI Documentation** | AI (Bounded LLM) | Generates explanatory narrative for findings only if Gate 3 passes | Only runs if Gate 3 verdict is pass; does not execute if chain broken |
| **Gate 4** | Human (CRO or Approver) | Named identity check against local registry; approval record recorded | Locked if Gate 3 blocks; otherwise gates the PDF export |

---

## 3. Reading the Flight Recorder

The flight recorder displays each node as an **expandable card**. Clicking a node reveals:

### Status Indicators
- **✓ complete** (green): node executed and produced a verdict or output
- **⏳ waiting** (yellow): predecessor finished; this node not yet started
- **✗ blocked** (red): node detected a control failure and refused to proceed
- **🔒 locked** (gray): cascading lock from Gate 3 prevents this node from executing
- **❌ incomplete** (orange): evidence exists but is abandoned or interrupted

### Node Content
Each expanded node shows:

**Actor and Timestamp**
- Actor type: human, agent, or AI
- Actor name: the role or system performing this node
- Timestamp: ISO 8601 UTC timestamp of the decision

**Evidence In and Out**
- Evidence In: what data entered this node (e.g., workbook hash, findings count, reconciliation pass/fail)
- Evidence Out: what this node produced (e.g., verdict, recommendations, approval name)
- Example: "Evidence In: 3 anomalies detected → [Gate 2] → Evidence Out: 2 confirmed, 1 dismissed"

**Event Hash**
- A 64-character SHA256 hash of this event's payload
- Shortened to 12 characters in the display; full hash available via "📋 Copy" button
- Auditors can verify this hash against the underlying `audit.db` to confirm the event was not modified

**Governance Rules**
- Collapsible section showing:
  - ✓ **Allowed actions** (green): what this node is permitted to do
  - ✗ **Prohibited actions** (red): what this node is forbidden from doing
- Example Gate 2 rules:
  - Allowed: confirm/override/dismiss findings; request additional analysis
  - Prohibited: infer outputs by keyword; approve without reviewing every finding

### Color Coding
- **Green**: complete, all controls passed
- **Yellow**: waiting for predecessor, not yet started
- **Red**: blocked; node detected a violation and halted
- **Gray**: locked by cascading lock; cannot execute

---

## 4. Cascading Locks

**When Gate 3 records a BLOCK verdict**, all downstream nodes automatically lock:

```
┌─────────────────────────────────────────┐
│ Gate 3 Decision: BLOCK (currency mismatch) │
└─────────────────────────────────────────┘
           │
           ├─→ Optional AI Doc  🔒 locked
           │
           ├─→ Gate 4           🔒 locked
           │
           └─→ PDF Export       🔒 locked
```

**Why cascading locks exist:**
- Gate 3 is the final human review of reconciliation
- A BLOCK verdict means the workbook numbers do not reconcile against reference figures
- Allowing Gate 4 or PDF export to proceed despite a reconciliation failure would bypass a critical control
- Locking ensures no approval record or PDF is generated on a broken audit trail

**Locked node behavior:**
- Display status: **🔒 locked** (gray)
- Cannot accept new input
- Cannot produce output
- Cannot be manually unlocked or overridden
- Remains locked until the broken event is fixed and chain re-verified

**Visual warning:**
When cascading locks are active, the flight recorder displays a prominent banner:

```
⚠️ PIPELINE LOCKED

Blocking Node: gate_3
Reason: internal_mismatch (external: pass)
Locked Nodes: optional_ai_doc, gate_4, pdf_export
```

This banner prevents accidental approval attempts on a compromised audit trail.

---

## 5. Authority and Responsibility

Each node is controlled by a specific authority type. This separation enforces the **segregation of duties** principle.

### Human-Controlled Gates

**Gate 1: Context Confirmation** (Qualified Actuary—Preparer Role)
- **Responsibility**: Verify that the workbook description, reference figures (if supplied), and assumptions are accurate before any parsing occurs
- **Allowed**: Read workbook; confirm context; review reference figures; log context agreement
- **Prohibited**: Parse before confirmation; infer context; proceed without confirmation
- **Binding decision**: Can reject the workbook if context is incorrect; halts pipeline

**Gate 2: Findings Review** (Qualified Actuary—Reviewer Role)
- **Responsibility**: Examine every anomaly detected by the agent; make a deliberate decision on each one
- **Allowed**: Confirm, override, or dismiss each finding with a reason; designate authoritative outputs; request additional analysis
- **Prohibited**: Skip findings; infer outputs by keyword; approve without reviewing
- **Binding decision**: Must disposition all findings before proceeding; every finding must have a human decision

**Gate 3: Reconciliation Review** (Qualified Actuary)
- **Responsibility**: Set materiality thresholds for internal and external reconciliation; review verdicts and decide whether to proceed
- **Allowed**: Set materiality; accept or reject reconciliation results; vote pass or block
- **Prohibited**: Modify formulas; override reconciliation logic; approve a reconciliation failure
- **Binding decision**: **Can block entire downstream pipeline** if reconciliation fails; block locks Gate 4, optional AI, and PDF

**Gate 4: Approval Record** (CRO or Authorized Approver)
- **Responsibility**: Confirm their identity against the authorized approver registry; record named approval if all prior gates passed
- **Allowed**: Type their name for identity verification; approve if prior gates passed
- **Prohibited**: Override prior gate decisions; approve if Gate 3 blocked; approve on a broken chain
- **Binding decision**: Records the named approval; gates the PDF export; locked if Gate 3 blocks

### Agent-Controlled Nodes (Deterministic Code)

**Anomaly Detector**
- **Responsibility**: Scan the workbook for rule violations (hardcoded values, circular references, unsupported formulas)
- **Allowed**: Detect anomalies; report findings; provide context for each finding
- **Prohibited**: Modify the workbook; skip checks; resolve or dismiss findings; call LLM
- **Binding decision**: None; findings flow to Gate 2 for human disposition

**Internal & External Reconciliation**
- **Responsibility**: Reconstruct formulas from the workbook and compare against (1) cached values (internal) and (2) reference figures (external)
- **Allowed**: Parse formulas; reconstruct calculations; propose account mappings; report verdicts
- **Prohibited**: Modify formulas; override verdicts; collapse pass and fail into one signal
- **Binding decision**: None; verdicts flow to Gate 3 for human review; proposals require human approval

### AI-Controlled Node (Bounded LLM)

**Optional AI Documentation** (only if Gate 3 passes)
- **Responsibility**: Generate explanatory narrative for findings if Gate 3 approves the reconciliation
- **Allowed**: Summarize findings; provide context; explain anomalies
- **Prohibited**: Run if Gate 3 blocks; modify findings; generate findings on its own
- **Binding decision**: None; narrative is informational; does not gate PDF generation

---

## 6. Tamper-Evidence (Not Tamper-Proof)

The audit trail is **tamper-evident**, not tamper-proof. This distinction is critical for audit readiness.

### How Tamper-Evidence Works

Every event in the audit log is recorded as a row in the `log_rows` table with these hash fields:

- **payload_hash**: SHA256 of the event's payload (what happened)
- **prev_row_hash**: hash of the previous event (commitment to order)
- **row_hash**: SHA256(prev_row_hash + payload_hash + timestamp)

This creates a **hash chain**: each row's hash depends on the previous row's hash.

```
Event 1: payload_hash = H1, prev_row_hash = GENESIS, row_hash = R1
Event 2: payload_hash = H2, prev_row_hash = R1,      row_hash = R2
Event 3: payload_hash = H3, prev_row_hash = R2,      row_hash = R3
         ↑ ↑ ↑ ↑
         If H3 changes, R3 becomes invalid
         If R2 is deleted, Event 3's prev_row_hash no longer matches
         If events are reordered, prev_row_hash values no longer align
```

### Detection and Refusal

When the pipeline loads a report, it runs **verify_chain()** to walk the entire log:

1. Recompute each event's payload_hash from its payload_json
2. Recompute each event's row_hash from prev_row_hash + payload_hash + timestamp
3. Check that each event's prev_row_hash matches the previous event's row_hash
4. If any mismatch is found, mark **chain_valid = False**

If the chain is broken:
- The flight recorder displays **✗ CHAIN BROKEN** in red
- All downstream nodes are marked **🔒 locked**
- The **"Revert and Unlock?"** button (if present) shows: *"Recovery refused: the chain is broken and cannot be repaired."*
- The pipeline **refuses to proceed**: no approval, no PDF export

### Why Not Tamper-Proof?

Someone with write access to `audit.db` **can**:
- Drop the append-only triggers
- Modify an event's payload_json
- Delete a row
- Reorder rows

Someone with write access **cannot**:
- Hide their changes from verify_chain()
- Make a modified event's hash agree with its stored hash
- Make a deleted event's predecessor hash agree with the next event's prev_row_hash
- Continue the chain after tampering without knowing the previous row's hash (which is not stored separately)

**This is the intended design.** The hash chain makes tampering detectable; it does not make `audit.db` unmodifiable by someone with file access. The audit is tamper-evident, meaning changes are visible afterward—not tamper-proof, meaning changes are prevented.

### Demo

See [scripts/demo_tamper_detection.py](../scripts/demo_tamper_detection.py) for a live demonstration of tamper detection.

---

## 7. Using the Flight Recorder

### Enabling the Flight Recorder

The flight recorder is displayed by default below the audit trail on Screen 5 (Report Review).

**Sidebar toggles:**
- **Show Flight Recorder** (default: ON): Display the pipeline execution map
- **Show Evidence Details** (default: OFF): Expand all node cards to show hashes, rules, and full event details

### Interpreting the Display

1. **Scan node statuses left-to-right**: green nodes are complete; yellow nodes are waiting; red nodes are blocked; gray nodes are locked
2. **Expand a node**: click the node card to see actor, timestamp, evidence, hash, and rules
3. **Copy a hash**: click "📋 Copy" next to an event hash to reveal the full 64-character hash for verification
4. **Verify against audit.db**: auditors can run SQL queries to confirm hashes:

   ```bash
   sqlite3 audit.db "SELECT row_id, event_type, row_hash FROM log_rows \
     WHERE report_id = 'your-report-id' ORDER BY row_id;"
   ```

5. **Check for locks**: look for **🔒 locked** indicators and the **⚠️ PIPELINE LOCKED** banner
6. **Review authority**: expand the "Governance rules" section to confirm each node followed its allowed/prohibited actions

### Auditor Checklist

- [ ] All human gates (1, 2, 3, 4) show a named actor and timestamp
- [ ] All agent nodes show evidence of their output flowing to the next gate
- [ ] No node shows a prohibited action in its evidence
- [ ] Chain verification shows **✓ valid** (not broken)
- [ ] If Gate 3 blocked, all downstream nodes show **🔒 locked**
- [ ] Hashes match audit.db when spot-checked
- [ ] PDF export completed only after Gate 4 approval (no exceptions)

---

## 8. Example Scenarios

### Scenario 1: Clean Pass

All nodes complete successfully, PDF exported.

```
✓ gate_1: Context confirmed by Isaac Shukla
✓ anomaly_detector: 3 anomalies detected
✓ gate_2: 2 confirmed, 1 dismissed (manually overridden)
✓ internal_reconciliation: Pass (all formulas match cached values)
✓ external_reconciliation: Pass (reconciled to reference figures within materiality)
✓ gate_3: Pass, materiality set to 0.5%; no block
✓ optional_ai_doc: Documentation generated
✓ gate_4: Approval recorded by Divyank (CRO)
✓ pdf_export: Report generated and saved
```

**Indicators**: All green. No locks. PDF available for download.

---

### Scenario 2: Control Failure at Gate 2

Anomaly detector finds issues; reviewer dismisses one.

```
✓ gate_1: Context confirmed
✓ anomaly_detector: 5 anomalies detected
✓ gate_2: 4 confirmed, 1 dismissed with reason "Obsolete formula, approved for removal"
✓ internal_reconciliation: Pass
✓ external_reconciliation: Pass
✓ gate_3: Pass
✓ optional_ai_doc: Documentation generated
✓ gate_4: Approval recorded
✓ pdf_export: Report generated
```

**Indicators**: All green. The dismissed finding is documented in Gate 2's evidence; auditors can verify the reviewer's reasoning.

---

### Scenario 3: Reconciliation Block at Gate 3

Internal reconciliation passes, but external fails due to currency mismatch.

```
✓ gate_1: Context confirmed
✓ anomaly_detector: No anomalies
✓ gate_2: No findings to disposition
✓ internal_reconciliation: Pass
✗ external_reconciliation: Fail (GBP vs. USD mismatch in reference figures)
✗ gate_3: BLOCK (internal: pass, external: fail; materiality exceeded)
     ├─→ optional_ai_doc: 🔒 locked
     ├─→ gate_4: 🔒 locked
     └─→ pdf_export: 🔒 locked
```

**Indicators**: 
- Gate 3 shows "BLOCK" in red
- Downstream nodes show **🔒 locked** in gray
- **⚠️ PIPELINE LOCKED** banner visible
- PDF cannot be exported
- Auditors must correct the reference figures or override the external verdict before rerunning

---

### Scenario 4: Tamper Detected

Audit trail shows a broken hash chain.

```
✓ gate_1: Context confirmed
✓ anomaly_detector: No anomalies
✓ gate_2: No findings
⚠️ gate_3: ✗ CHAIN BROKEN (hash mismatch at row_id=7)
     ├─→ optional_ai_doc: 🔒 locked
     ├─→ gate_4: 🔒 locked
     └─→ pdf_export: 🔒 locked

Chain Verification: ✗ CHAIN BROKEN
  Broken at row_id: 7 (gate_3 decision)
  Previous hash: expected a1b2c3d4..., got tampering...
  Recovery refused: the chain is broken and cannot be repaired.
```

**Indicators**:
- Chain verification shows **✗ CHAIN BROKEN** in red
- All downstream nodes **🔒 locked**
- PDF cannot be exported
- Pipeline refuses recovery
- **Action**: Investigate why row_id=7 was modified; verify with IT/audit log backup; restart from a known-good snapshot if available

---

## References

- [CLAUDE.md](../CLAUDE.md) — Operating rules and build order
- [scripts/demo_tamper_detection.py](../scripts/demo_tamper_detection.py) — Live tamper detection demonstration
- [core/audit_log.py](../core/audit_log.py) — Hash-chain implementation
- [core/flight_recorder_state.py](../core/flight_recorder_state.py) — Pipeline state machine
- [ui/flight_recorder.py](../ui/flight_recorder.py) — Streamlit display layer
- [ui/flight_recorder_evidence.py](../ui/flight_recorder_evidence.py) — Evidence presentation helpers
