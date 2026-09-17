# Data Governance & Compliance Memo — Excel Audit Agent

**Prepared for:** CFO, Compliance, Internal/External Audit
**Scope:** Data handling, access control, backup, and audit-trail integrity for the Excel Audit Agent tool
**Status:** Reflects the implementation actually in the codebase, not aspirational controls.

---

## 1. Purpose

This memo describes what data the Excel Audit Agent creates and stores, who can access it, how it is protected, and what evidence it can produce for an internal or external review. It is written to be handed to an auditor as-is.

---

## 2. Data Classification

| Data | Location | Access Control | Retention |
|---|---|---|---|
| Workbook content (original `.xlsx` bytes) | In-memory during a session; persisted only if `core/artifact_store.py` retention is enabled for that run | OS file permissions on the host filesystem; no network exposure; content is never sent to any external service in raw form | Until manually deleted, or per the run's retention policy if artifact storage is enabled |
| Audit log (gate decisions, timestamps, actor names, workbook hashes, code version) | `audit.db` (SQLite, hash-chained, append-only — see `core/audit_log.py`) | OS file permissions on the host filesystem (see §4 — not currently enforced in code) | Permanent. No `UPDATE` or `DELETE` path exists in `audit_log.py`; database triggers reject both, and the hash chain detects any change made outside the application |
| Configuration files (authorized approvers, materiality defaults, optional user registry) | `config/authorized_approvers.json`, `config/materiality_defaults.json` | OS file permissions; read-only at runtime — the application never writes to these files | Until manually edited or deleted by an administrator |

**Note on scope:** the audit log records *who* made each gate decision and *what* was decided. It does not store full workbook contents — only cell-level figures and derivation chains needed to reconstruct a finding, subject to the data-minimization rules in `core/llm_data_policy.py` for anything that touches the optional AI documentation step.

---

## 3. Workbook Lifecycle

| Stage | What happens to the data |
|---|---|
| **1. Upload** | The user uploads workbook bytes through the Streamlit UI. The bytes are hashed (SHA-256) before anything else happens. Nothing is written to disk at this point — the bytes confirmed by the human at Gate 1 are the exact bytes parsed. |
| **2. Processing** | Agent 1 (parser) reads the workbook in memory and produces a structured record of cells, formulas, and cached values. Agent 2 (anomaly detector) and Agent 3 (reconciliation) operate on that structured record, not on the original file. |
| **3. Approval** | Each of the four human gates (context confirmation, findings review, reconciliation sign-off, named approval record) writes an entry to the hash-chained audit log, including the actor's identity, a timestamp, and the decision context. No gate can be skipped programmatically. |
| **4. Report generation** | Once Gate 4 completes, a PDF is generated from the reconciliation result and the audit trail. The report explicitly states it does not validate the underlying actuarial model — only that the reconstruction and reconciliation described were performed and reviewed. |
| **5. Retention / Disposal** | The audit log entry for the run is permanent by design. The original workbook bytes are retained only if the operator has explicitly enabled artifact retention (`core/artifact_store.py`); otherwise they exist only for the duration of the session and are discarded when the process ends or the user starts a new run. |

---

## 4. Access Control

- **File system permissions.** `audit.db` and the configuration files inherit standard OS file permissions from the environment they run in. **The application does not currently set or enforce restrictive permissions (e.g., `0600`) in code.** Operators deploying this tool are responsible for restricting filesystem access to `audit.db` and the `config/` directory to authorized users only, consistent with their own environment's security baseline.
- **User identity.** Every gate decision requires an actor identity, selected from a configured user list rather than freely typed, before the gate will proceed. The tool will not record a decision, or advance the pipeline, without an identified actor.
- **No API exposure.** The tool does not expose a network API. It runs as a local Streamlit process. There is no default listener beyond the Streamlit development server, which is not intended to be exposed to the public internet.
- **No cloud sync by default.** Nothing in the default configuration writes workbook data, audit log data, or configuration data to a cloud service. The only outbound network call in the codebase is the optional, per-report AI documentation step, which is gated by an explicit human decision each time and is subject to the minimization rules in `core/llm_data_policy.py` — declining it does not block the rest of the pipeline.

---

## 5. Backup & Recovery

**Backup (single command):**

```
cp audit.db audit.db.$(date +%s).backup
```

**Restore:**

```
cp audit.db.<timestamp>.backup audit.db
```

**Verification after restore.** A restored database should always be verified before it is trusted. The audit log is hash-chained specifically so that this check is possible: each row's hash depends on the row before it, so any row that was altered, reordered, inserted out of sequence, or removed breaks the chain from that point forward. Verification is performed by calling `verify_chain()` on `core/audit_log.py`'s `AuditLog` class against the restored file, which returns whether the chain is intact and, if not, the specific point of divergence.

Backups should be taken before any planned maintenance and on a regular schedule appropriate to the organization's audit-log retention policy. This tool does not automate that schedule (see Limitations, §7).

---

## 6. Compliance & Audit Trail

- **Who accessed what.** Every gate decision — context confirmation, each findings disposition, reconciliation sign-off, and the named approval record — is written to the audit log with the actor's identity and a timestamp. There is no path in the code for a gate to be recorded without an actor.
- **Tamper detection.** The audit log is **tamper-evident, not tamper-proof.** Database triggers reject direct `UPDATE`/`DELETE` at the SQL level, and independently, the hash chain makes it possible to detect after the fact that a row was edited, inserted, or deleted outside the application — including by someone who first disables those triggers. It does not prevent someone with direct file access from modifying the database; it ensures that if they do, `verify_chain()` surfaces the discrepancy rather than silently accepting an altered history.
- **Evidence integrity.** Each audit log entry captures the confirmed workbook hash, the code version that produced the result, and the decision context (thresholds, dispositions, reasons for deviation) in effect at the time. This is intended to let a reviewer reconstruct, months later, exactly what was shown to the human reviewer and what they decided — not just that a decision occurred.

---

## 7. External Auditor Access

To support an external audit, provide:

1. A copy of `audit.db` (the complete hash-chained log for the period in question).
2. This memo, so the auditor understands what the log does and does not guarantee.
3. A verification command the auditor can run themselves, independent of any explanation from the operator.

**Example verification, run by the auditor against the provided copy:**

```python
from core.audit_log import AuditLog

log = AuditLog("audit.db")
is_valid, errors = log.verify_chain()
print("Chain intact:", is_valid)
if errors:
    print(errors)
```

A clean run of this command (`Chain intact: True`, no errors) is the evidence that the log has not been altered outside the application since it was created. It does not, on its own, confirm that the reconciliation figures are correct — only that the record of what was reviewed and decided has not been tampered with.

---

## 8. Limitations (Honest Statement)

This tool is built for single-operator, local-first use. The following limitations are current and should inform any decision to rely on it for a formal control environment:

- **Single-user execution.** The tool assumes one reviewer working through one report at a time on one machine. SQLite's write-ahead logging provides file-level locking for concurrent reads, but the application has not been designed or tested for multiple simultaneous reviewers writing to the same `audit.db`.
- **No application-level role enforcement.** The user registry (the list of names available to select as an actor) is treated as trusted input. The application does not verify that the person operating the keyboard is actually the person selected from the dropdown — it records whichever identity was selected, without a login, password, or independent authentication step. Anyone with access to the running application can select any name in the registry.
- **No encryption at rest.** `audit.db` and the configuration files are stored as plain SQLite and JSON files. Confidentiality of this data depends entirely on the filesystem and host-level protections the operator puts in place — the application provides none of its own.
- **Manual backup.** The backup command in §5 is not scheduled or automated by the tool. If backups are required for a retention or disaster-recovery policy, an operator must run them (or automate them externally) — the application will not remind or enforce this.

These limitations are stated here so that reliance on this tool is a deliberate, informed decision rather than an assumption. None of them are hidden defects; they reflect the current design scope of a local, single-user audit tool, not a multi-tenant compliance platform.

---

*This memo describes the tool's data handling as implemented. It is not itself an attestation, and it does not certify the correctness of any reconciliation the tool produces — that determination remains with the named human reviewer at each gate.*
