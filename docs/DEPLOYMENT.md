# Deployment Runbook — Excel Audit Agent

**Audience:** Systems administrators, IT operations, DevOps engineers deploying for the first time.
**Target deployment time:** 15 minutes (local) or 30 minutes (Docker).

---

## 1. Prerequisites

Before starting, verify your environment meets these requirements:

- **Python 3.11+** — check with `python3 --version`
- **Git** — for cloning the repository
- **~500MB free disk space** — for dependencies, fixtures, and the SQLite audit log
- **Anthropic API key** — starts with `sk-`. Get one from [console.anthropic.com](https://console.anthropic.com/keys) (requires an account with valid billing)
- **Docker & Docker Compose** (optional but recommended) — for isolated deployment

For Docker deployments only: **~2GB RAM**, **port 8501 available** on the host.

---

## 2. Quick Start — Local Deployment (No Docker)

This option is fastest for development or small-team testing. The tool runs directly on your machine.

**Step 1: Clone and install dependencies**

```bash
git clone https://github.com/yourusername/excel-audit-agent.git
cd excel-audit-agent
pip install -r requirements.txt
```

**Step 2: Set the Anthropic API key**

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Or create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**Step 3: Start the app**

```bash
streamlit run app.py
```

The app will print:

```
You can now view your Streamlit app in your browser.
Local URL: http://localhost:8501
```

Open that URL in your browser. You should see the audit agent interface with a sidebar containing a "Who are you?" dropdown (once `config/users.json` is configured — see §4).

---

## 3. Docker Deployment (Recommended for Production)

Docker ensures the tool runs the same way everywhere, isolated from your system Python and dependencies.

**Step 1: Build the image**

```bash
git clone https://github.com/yourusername/excel-audit-agent.git
cd excel-audit-agent
docker build -t excel-audit-agent:latest .
```

This creates a ~800MB image. Expect 2–5 minutes on a typical machine.

**Step 2: Create a persistent data volume**

The audit log and workbook artifacts are stored in `/data` inside the container. You must mount this to a host directory so data survives container restarts.

```bash
mkdir -p ~/.local/share/excel-audit-agent
chmod 700 ~/.local/share/excel-audit-agent
```

**Step 3: Start the container**

```bash
docker run -d \
  --name excel-audit-agent \
  -p 8501:8501 \
  -e ANTHROPIC_API_KEY=sk-ant-your-key-here \
  -v ~/.local/share/excel-audit-agent:/data \
  excel-audit-agent:latest
```

**Flag breakdown:**
- `-d` — run in background (daemon mode)
- `--name excel-audit-agent` — container name (useful for `docker logs`, `docker stop`)
- `-p 8501:8501` — expose port 8501
- `-e ANTHROPIC_API_KEY=...` — set the API key inside the container
- `-v ~/.local/share/excel-audit-agent:/data` — mount host directory to `/data` (where audit.db lives)

**Step 4: Verify the container is running**

```bash
docker ps | grep excel-audit-agent
```

Should show a running container. Then check health:

```bash
curl http://localhost:8501/_stcore/health
```

Should return a 200 response with JSON. The app is ready when you see:

```bash
docker logs excel-audit-agent | tail -3
```

Output should include `Running on http://0.0.0.0:8501`.

Open http://localhost:8501 in your browser.

---

## 4. Configuration

### User Registry

Every audit decision requires an identified actor, selected from a configured list. Create `config/users.json`:

```json
{
  "users": [
    {"username": "alice", "role": "actuary", "display_name": "Alice Actuary"},
    {"username": "bob", "role": "cro", "display_name": "Bob Controls"},
    {"username": "carol", "role": "cfo", "display_name": "Carol CFO"}
  ]
}
```

**Fields:**
- `username` — internal identifier (no spaces; used in audit log)
- `role` — one of `actuary`, `cro`, `cfo`, `auditor` (for context and reporting)
- `display_name` — human-friendly name shown in the Streamlit dropdown

**After editing:** Refresh the browser (F5) to reload the dropdown. No restart needed.

### Authorized Approvers (Gate 4)

Gate 4 requires a named approval record. Edit `config/authorized_approvers.json` to register names:

```json
{
  "approvers": [
    {"name": "Isaac Shukla", "role": "actuary", "registered_at": "2026-08-10"},
    {"name": "Alice Actuary", "role": "actuary", "registered_at": "2026-09-01"}
  ]
}
```

**Note:** This is a spell-checker, not a security control. Anyone with file access can add names. It does not verify who is actually at the keyboard.

### Materiality Thresholds

Default materiality thresholds are in `config/materiality_defaults.json`. Adjust as needed:

```json
{
  "default_pct_threshold": 0.01,
  "default_absolute_threshold": 50000.00
}
```

These can be overridden per report in Gate 3.

---

## 5. Verification Checklist

After deployment, run through this checklist to confirm the tool is working:

1. **Browser access:** Open http://localhost:8501 (or your Docker host IP). The Streamlit sidebar should be visible on the left.

2. **User dropdown:** You should see "Who are you?" in the sidebar. Click it and verify your configured users appear. (If empty, check `config/users.json` exists and is valid JSON.)

3. **Load a demo case:** Under "Excel File", select "Load demonstration case" and pick "Case 1: Reserve roll-forward". This is a safe fixture that doesn't require an external workbook.

4. **Complete Gate 1:** Fill in the description and click "Confirm context". The app should print "Gate 1 called by <your-username>" to the console (check `docker logs` if using Docker). You'll be taken to the findings review page.

5. **Verify audit log:** Check that the report was recorded:

   ```bash
   # Local deployment:
   sqlite3 audit.db "SELECT report_id, event_type, actor FROM log_rows LIMIT 1;"
   
   # Docker deployment:
   docker exec excel-audit-agent sqlite3 /data/audit.db \
     "SELECT report_id, event_type, actor FROM log_rows LIMIT 1;"
   ```

   You should see a row with your username in the `actor` column.

---

## 6. Troubleshooting

### Port 8501 Already in Use

**Error:** `Address already in use` or `OSError: [Errno 48]`

**Fix:**

```bash
lsof -i :8501
kill -9 <PID>
```

Then restart the app. To use a different port:

```bash
streamlit run app.py --server.port=8502
```

### audit.db is Locked

**Error:** `sqlite3.OperationalError: database is locked`

**Cause:** The database file (or its write-ahead log) is held by another process.

**Fix:**

```bash
rm -f audit.db-wal audit.db-shm
# Wait 10 seconds for any locks to clear
sleep 10
streamlit run app.py
```

If using Docker:

```bash
docker exec excel-audit-agent rm -f /data/audit.db-wal /data/audit.db-shm
docker restart excel-audit-agent
```

### Anthropic API Key Not Found

**Error:** `KeyError: ANTHROPIC_API_KEY` or `anthropic.APIError: Invalid API Key`

**Fix:** Ensure the key is set:

**Local deployment:**

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
echo $ANTHROPIC_API_KEY  # Verify it's there
streamlit run app.py
```

**Docker deployment:**

```bash
docker run ... -e ANTHROPIC_API_KEY=sk-ant-your-key-here ...
```

Or create a `.env` file in the project root (for local) or pass it via `--env-file`:

```bash
docker run ... --env-file .env ...
```

---

## 7. Maintenance

### Backup the Audit Log

The audit log is your evidence trail. Back it up regularly:

```bash
# Local:
cp audit.db audit.db.$(date +%s).backup

# Docker:
docker exec excel-audit-agent cp /data/audit.db /data/audit.db.$(date +%s).backup
```

### Restart the Container

```bash
docker restart excel-audit-agent
```

### View Recent Logs

```bash
docker logs --tail=50 excel-audit-agent
```

### Stop the Container

```bash
docker stop excel-audit-agent
```

---

## 8. Support & Documentation

- **README.md** — Overview of the tool's purpose and formula support
- **docs/DATA_GOVERNANCE.md** — Data handling, access control, and audit trail details
- **CLAUDE.md** — Architectural decisions and operational rules
- **GitHub Issues** — Report bugs or request features

For Anthropic API issues, see [Anthropic API Documentation](https://docs.anthropic.com).

---

*Last updated: 2026-09-17*
