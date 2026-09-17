# Operational Runbook: Backup, Restore & Testing — audit.db

**Audience:** Systems administrators responsible for the Excel Audit Agent deployment.
**Scope:** `audit.db` only — the hash-chained audit log. Does not cover workbook artifact storage or configuration files.

---

## 1. Daily Backup (Automated via Cron)

Add this line to your crontab to back up `audit.db` every day at 2:00 AM:

```bash
crontab -e
```

Add this line (adjust paths for your deployment):

```cron
0 2 * * * cp /path/to/excel-audit-agent/audit.db /backup/location/audit.db.$(date +\%Y\%m\%d_\%H\%M\%S) 2>> /var/log/audit-backup.log
```

**Note the escaped `%` signs (`\%`)** — cron interprets a bare `%` as a newline, so `date` format specifiers must be escaped inside a crontab entry.

This produces backup files named like:

```
audit.db.20260917_020000
```

**Docker deployments** — run the copy inside the container, or against the mounted host volume directly:

```cron
0 2 * * * docker exec excel-audit-agent cp /data/audit.db /data/audit.db.$(date +\%Y\%m\%d_\%H\%M\%S) 2>> /var/log/audit-backup.log
```

**Why automated backups matter for compliance:** the audit log is the primary evidence that each of the four human gates occurred, who made each decision, and what they were shown at the time. It is retained permanently by design (§2 of `docs/DATA_GOVERNANCE.md`) — there is no code path to regenerate a lost entry. A regular, unattended backup schedule is what makes that permanence actually reliable in practice, rather than depending on someone remembering to run a manual command. Without it, a single disk failure or accidental deletion could remove the only record of a completed review.

---

## 2. Manual Backup (Anytime)

For an ad hoc backup before maintenance, an upgrade, or anything else that touches the database:

```bash
cp audit.db audit.db.backup.$(date +%s)
```

**About the timestamp format:** `$(date +%s)` produces Unix epoch seconds (e.g., `1789635600`) rather than a human-readable date. This is deliberate for manual backups — it guarantees a unique filename even if you run the command twice in the same minute, and it sorts correctly as a plain string. If you need a human-readable timestamp instead, use:

```bash
cp audit.db audit.db.backup.$(date +%Y%m%d_%H%M%S)
```

Either format is acceptable; be consistent within your organization so scripts and colleagues can predict the naming.

---

## 3. Restore from Backup

Follow these steps in order. Do not skip the verification step.

**Step 1 — Stop the application.**

Local deployment:

```bash
# Press Ctrl+C in the terminal running `streamlit run app.py`
```

Docker deployment:

```bash
docker stop excel-audit-agent
```

**Step 2 — Copy the backup over the live database.**

```bash
cp audit.db.backup.TIMESTAMP audit.db
```

Replace `TIMESTAMP` with the actual value from the backup filename you're restoring.

**Step 3 — Verify chain integrity (critical — do not skip).**

```bash
python3 -c "
from core.audit_log import AuditLog
log = AuditLog('audit.db')
valid, broken_rows = log.verify_chain()
print(f'Chain valid: {valid}')
if not valid:
    print(f'Broken rows: {broken_rows}')
"
```

Expected output on a good backup:

```
Chain valid: True
```

**If the chain is broken** (`Chain valid: False`, with one or more row IDs listed):

> **Do not use this backup.** Restart the application against the last known-good backup instead, and contact technical support with the broken row IDs before taking any further action. Do not attempt to edit `audit.db` directly to "fix" a broken chain — there is no code path in this project to repair a hash chain, by design (Rule 4, `CLAUDE.md`), and any manual edit will only make the divergence harder to diagnose.

**Step 4 — Restart the application.**

Local deployment:

```bash
streamlit run app.py
```

Docker deployment:

```bash
docker start excel-audit-agent
```

---

## 4. Testing Backup & Restore (Monthly)

Run this test monthly to confirm the backup/restore procedure — and the verification step itself — actually works, rather than assuming it does because no one has needed it recently.

**Step 1 — Create a backup to test with.**

```bash
cp audit.db audit.db.test.backup
```

**Step 2 — Intentionally corrupt the test copy.**

```bash
echo "garbage" >> audit.db.test.backup
```

**Step 3 — Attempt to restore the corrupted copy and verify (this should fail).**

```bash
cp audit.db.test.backup audit.db.restore-test
python3 -c "
from core.audit_log import AuditLog
log = AuditLog('audit.db.restore-test')
valid, broken_rows = log.verify_chain()
print(f'Chain valid: {valid}')
"
```

Expected output: `Chain valid: False` (or a database error, since appending garbage bytes to a SQLite file can also break the file format itself). **If this step reports `Chain valid: True`, the verification step is not working correctly — stop and investigate before relying on it in a real restore.**

**Step 4 — Restore a clean backup and verify it works.**

```bash
cp audit.db.backup.TIMESTAMP audit.db.restore-test
python3 -c "
from core.audit_log import AuditLog
log = AuditLog('audit.db.restore-test')
valid, broken_rows = log.verify_chain()
print(f'Chain valid: {valid}')
"
```

Expected output: `Chain valid: True`.

**Step 5 — Clean up test artifacts and log the result.**

```bash
rm -f audit.db.test.backup audit.db.restore-test
echo "Backup test $(date): PASS" >> maintenance.log
```

If any step didn't produce the expected output, log `FAIL` instead, with a note on which step failed, and treat it as an incident — a backup process that fails its own test cannot be trusted for disaster recovery.

```bash
echo "Backup test $(date): FAIL — <describe which step failed>" >> maintenance.log
```

---

## 5. Disaster Recovery

Use this procedure if the live `audit.db` is lost, corrupted, or fails chain verification and no immediate cause is known.

**Step 1 — Stop the application** (see §3, Step 1).

**Step 2 — Find the most recent backup that passes verification.** Do not assume the newest backup is valid — check it first, and work backward through older backups if needed.

```bash
ls -1t /backup/location/audit.db.* | while read f; do
  echo "Checking $f..."
  python3 -c "
from core.audit_log import AuditLog
log = AuditLog('$f')
valid, broken_rows = log.verify_chain()
print(f'  Chain valid: {valid}')
"
done
```

This lists backups newest-first and checks each one. Identify the **most recent** backup that reports `Chain valid: True` — that is the backup to restore. Any backup taken after the point of corruption will also be broken, so keep checking older files until you find a good one.

**Step 3 — Restore the identified backup.**

```bash
cp /backup/location/audit.db.GOOD_TIMESTAMP audit.db
```

Then re-run the verification command from §3, Step 3 against the live `audit.db` to confirm before restarting the application.

**Step 4 — Restart the application** (see §3, Step 4).

**Step 5 — Document the incident.** Create or append to an incident log, e.g. `incidents.log`, with at minimum:

```bash
cat >> incidents.log << 'EOF'
=== Incident: audit.db recovery ===
Date detected: $(date)
Cause (if known): <describe — disk failure, accidental deletion, corruption, etc.>
Backup restored: audit.db.GOOD_TIMESTAMP
Data loss window: <time between GOOD_TIMESTAMP and when the incident was detected>
Verified by: <your name>
Chain valid post-restore: True
EOF
```

**Note the data loss window explicitly.** Any gate decisions recorded between the restored backup's timestamp and the moment of failure are gone and cannot be reconstructed — this must be disclosed to anyone relying on the audit log for that period, not silently absorbed. This is a direct consequence of the audit log being append-only with no reconstruction path (Rule 4, `CLAUDE.md`); the only defense against it is the backup frequency configured in §1.

---

*This runbook covers `audit.db` only. For workbook artifact retention and configuration file backups, see the relevant sections of `docs/DATA_GOVERNANCE.md`.*
