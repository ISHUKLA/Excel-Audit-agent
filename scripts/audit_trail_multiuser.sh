#!/bin/bash
set -e

# Test: multi-user audit trail recording
# Verifies that audit.db correctly records different users completing different gates
# This is a semi-manual test (requires human UI interaction; no Selenium/Playwright)
#
# Expected flow:
#   User 1 (alice) opens app, types "alice" as reviewer name, loads Case 1, completes Gate 1
#   User 2 (bob) does same report, types "bob", completes Gate 2
#   Query audit.db to verify both names appear in the audit trail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STREAMLIT_PID=""
TEMP_VENV="${REPO_ROOT}/.venv-audit-trail-test"

# Trap to clean up streamlit on exit
trap cleanup EXIT

cleanup() {
    if [[ -n "$STREAMLIT_PID" ]] && kill -0 "$STREAMLIT_PID" 2>/dev/null; then
        echo ""
        echo "[$(date '+%H:%M:%S')] Stopping streamlit (PID $STREAMLIT_PID)..."
        kill "$STREAMLIT_PID" || true
        sleep 2
    fi
    # Don't remove venv; let user keep it for next run
}

# === SETUP ===
echo "[$(date '+%H:%M:%S')] Multi-user audit trail test"
echo ""

cd "$REPO_ROOT"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo "[$(date '+%H:%M:%S')] Python version: $PYTHON_VERSION ✓"

# Check ANTHROPIC_API_KEY
if [[ -z "$ANTHROPIC_API_KEY" ]]; then
    echo "ERROR: ANTHROPIC_API_KEY not set"
    echo "Set it with: export ANTHROPIC_API_KEY=sk-..."
    exit 1
fi
echo "[$(date '+%H:%M:%S')] ANTHROPIC_API_KEY is set ✓"

# Create/use venv
if [[ ! -d "$TEMP_VENV" ]]; then
    echo "[$(date '+%H:%M:%S')] Creating virtual environment..."
    python3 -m venv "$TEMP_VENV"
fi
source "$TEMP_VENV/bin/activate"

# Install deps
if ! python3 -c "import streamlit" 2>/dev/null; then
    echo "[$(date '+%H:%M:%S')] Installing dependencies..."
    pip install -q -r "$REPO_ROOT/requirements.txt"
fi
echo "[$(date '+%H:%M:%S')] Dependencies ready ✓"

# === START STREAMLIT ===
echo "[$(date '+%H:%M:%S')] Starting streamlit in background..."
cd "$REPO_ROOT"
streamlit run app.py \
    --server.headless true \
    --logger.level=error \
    > /tmp/streamlit_audit_trail.log 2>&1 &
STREAMLIT_PID=$!

# Wait for startup
sleep 3
if ! kill -0 "$STREAMLIT_PID" 2>/dev/null; then
    echo "ERROR: streamlit failed to start"
    cat /tmp/streamlit_audit_trail.log
    exit 1
fi
echo "[$(date '+%H:%M:%S')] Streamlit started (PID $STREAMLIT_PID) ✓"

# Check health
HEALTH_CHECK=$(curl -s http://localhost:8501/_stcore/health 2>/dev/null || echo "")
if [[ "$HEALTH_CHECK" != *"ok"* ]]; then
    echo "WARNING: Health check returned: $HEALTH_CHECK"
    echo "Streamlit may still be starting. Waiting 3 more seconds..."
    sleep 3
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║             USER 1 TEST: alice completes Gate 1               ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "1. Open your browser: http://localhost:8501"
echo "2. You should see the Excel Audit Agent app"
echo "3. In the 'Reviewer full name' field, type: alice"
echo "4. Click 'Load Case Study' and select 'Case 1 (Moderate divergence)'"
echo "5. Review the context (file description, any accounts figures)"
echo "6. Click 'Confirm context' (Gate 1 decision)"
echo "7. Return here and press ENTER to continue..."
echo ""
read -p "Press ENTER once alice has completed Gate 1: " _

echo "[$(date '+%H:%M:%S')] Querying audit.db for alice's gate_decision events..."
ALICE_COUNT=$(sqlite3 "$REPO_ROOT/audit.db" \
    "SELECT COUNT(*) FROM log_rows WHERE actor = 'alice' AND event_type = 'gate_decision';" 2>/dev/null || echo "0")

if [[ "$ALICE_COUNT" -gt 0 ]]; then
    echo "[$(date '+%H:%M:%S')] ✓ Found $ALICE_COUNT gate_decision event(s) for alice"
else
    echo "[$(date '+%H:%M:%S')] ✗ No gate_decision events found for alice"
    echo "   (Make sure you typed 'alice' exactly in the Reviewer field)"
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║             USER 2 TEST: bob completes Gate 2                 ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "1. Still at http://localhost:8501"
echo "2. Clear the 'Reviewer full name' field"
echo "3. Type: bob"
echo "4. Load the SAME case: 'Case 1 (Moderate divergence)'"
echo "5. Confirm context (just like alice did)"
echo "6. You'll now see 'Findings review' (Gate 2)"
echo "   - Scroll through the findings (there are several)"
echo "   - For each one, click Confirm, Override, or Dismiss"
echo "   - Gate 2 won't advance until ALL findings have a disposition"
echo "7. Return here and press ENTER once bob completes Gate 2..."
echo ""
read -p "Press ENTER once bob has completed Gate 2: " _

echo "[$(date '+%H:%M:%S')] Querying audit.db for both users..."
echo ""

# Count events per actor
ALICE_EVENTS=$(sqlite3 "$REPO_ROOT/audit.db" \
    "SELECT COUNT(*) FROM log_rows WHERE actor = 'alice';" 2>/dev/null || echo "0")
BOB_EVENTS=$(sqlite3 "$REPO_ROOT/audit.db" \
    "SELECT COUNT(*) FROM log_rows WHERE actor = 'bob';" 2>/dev/null || echo "0")

echo "Audit trail summary:"
echo "  alice: $ALICE_EVENTS events"
echo "  bob:   $BOB_EVENTS events"
echo ""

# List all distinct actors
echo "All actors in audit.db:"
sqlite3 "$REPO_ROOT/audit.db" \
    "SELECT DISTINCT actor FROM log_rows ORDER BY actor;" 2>/dev/null | \
    while read actor; do
        echo "  - $actor"
    done

echo ""

# Detailed audit trail for both users
echo "Gate decision timeline:"
sqlite3 "$REPO_ROOT/audit.db" \
    "SELECT created_at, actor, json_extract(payload_json, '$.gate') as gate_num
     FROM log_rows
     WHERE event_type = 'gate_decision' AND actor IN ('alice', 'bob')
     ORDER BY created_at ASC;" 2>/dev/null | \
    while read -r timestamp actor gate; do
        echo "  $timestamp | $actor | Gate $gate"
    done

echo ""

# === RESULTS ===
if [[ "$ALICE_COUNT" -gt 0 && "$BOB_EVENTS" -gt 0 ]]; then
    echo "✓ TEST PASSED: Both users recorded in audit trail"
    exit 0
else
    echo "✗ TEST FAILED: Not all users recorded"
    if [[ "$ALICE_COUNT" -eq 0 ]]; then
        echo "  - alice: no events found"
    fi
    if [[ "$BOB_EVENTS" -eq 0 ]]; then
        echo "  - bob: no events found"
    fi
    exit 1
fi
