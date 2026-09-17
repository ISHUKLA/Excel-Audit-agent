#!/usr/bin/env bash
# End-to-end smoke test for the governance flight recorder.
#
# NOTE ON SCOPE: this repo has no Selenium/Playwright (see the same disclaimer
# in scripts/audit_trail_multiuser.sh). Tests 1-3 below therefore drive
# agents.orchestrator.Orchestrator directly against the real demo case
# workbooks (demo/workbooks/case_{1,2,3}_*.xlsx) via
# scripts/flight_recorder_e2e_checks.py, rather than clicking through the
# Streamlit UI. Per CLAUDE.md Rule 2, app.py contains no business logic — it
# only calls this same orchestrator and renders the result — so this exercises
# exactly the pipeline logic the UI exercises. Streamlit is still started and
# health-checked (Setup, below) to confirm the app itself boots.
#
# Steps:
#   Setup   - start streamlit in the background, wait for /healthz
#   Test 1  - Case 1 (clean run): all 9 flight recorder nodes reach "complete"
#   Test 2  - Case 2 (control failures): anomaly detector findings are shown
#   Test 3  - Case 3 (reconciliation block): Gate 3 blocks, downstream locks
#   Test 4  - scripts/demo_tamper_detection.py: hash-chain tamper detection
#   Cleanup - kill streamlit, print pass/fail summary
#
# Usage: bash scripts/test_flight_recorder_e2e.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HEALTH_URL="http://localhost:8501/_stcore/health"
STREAMLIT_PID=""
STREAMLIT_LOG="$(mktemp -t streamlit-flight-recorder-e2e.XXXXXX.log)"

declare -a RESULT_NAMES
declare -a RESULT_STATUS

ts() { date "+%H:%M:%S"; }
log() { echo "[$(ts)] $1"; }

record() {
    RESULT_NAMES+=("$1")
    RESULT_STATUS+=("$2")
}

cleanup() {
    log "Cleanup: stopping streamlit..."
    if [ -n "$STREAMLIT_PID" ] && kill -0 "$STREAMLIT_PID" 2>/dev/null; then
        kill "$STREAMLIT_PID" 2>/dev/null || true
        wait "$STREAMLIT_PID" 2>/dev/null || true
        log "✓ Cleanup: streamlit stopped"
    else
        log "(streamlit was not running)"
    fi
    rm -f "$STREAMLIT_LOG"
    rm -f "$CASE1_DB" "$CASE2_DB" "$CASE3_DB"

    echo ""
    echo "======================================================================"
    echo "RESULTS"
    echo "======================================================================"
    local overall=0
    for i in "${!RESULT_NAMES[@]}"; do
        if [ "${RESULT_STATUS[$i]}" = "PASS" ]; then
            echo "  ✓ PASS  ${RESULT_NAMES[$i]}"
        else
            echo "  ✗ FAIL  ${RESULT_NAMES[$i]}"
            overall=1
        fi
    done
    echo "======================================================================"
    if [ "$overall" -eq 0 ]; then
        echo "All flight recorder e2e checks passed."
    else
        echo "One or more flight recorder e2e checks failed. See output above."
    fi
    exit "$overall"
}
trap cleanup EXIT

cd "$REPO_ROOT"

CASE1_DB="$(mktemp -t fr_e2e_case1.XXXXXX.db)"
CASE2_DB="$(mktemp -t fr_e2e_case2.XXXXXX.db)"
CASE3_DB="$(mktemp -t fr_e2e_case3.XXXXXX.db)"
rm -f "$CASE1_DB" "$CASE2_DB" "$CASE3_DB"  # mktemp creates them; the app must create fresh ones

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
echo "======================================================================"
echo "SETUP"
echo "======================================================================"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    log "Note: ANTHROPIC_API_KEY is not set. Streamlit will still boot; AI"
    log "  documentation is declined (use_ai_documentation=False) in every"
    log "  check below, so no Anthropic call is made or required."
fi

log "Starting streamlit in the background..."
streamlit run app.py --server.headless true > "$STREAMLIT_LOG" 2>&1 &
STREAMLIT_PID=$!
log "Streamlit started (PID $STREAMLIT_PID)"

HEALTHY=0
for _ in $(seq 1 10); do
    sleep 1
    if ! kill -0 "$STREAMLIT_PID" 2>/dev/null; then
        break
    fi
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$HEALTH_URL" 2>/dev/null || echo 000)"
    if [ "$STATUS" = "200" ]; then
        HEALTHY=1
        break
    fi
done

if [ "$HEALTHY" -eq 1 ]; then
    log "✓ Streamlit health check OK"
    record "Setup: streamlit boots and passes health check" "PASS"
else
    log "✗ Streamlit did not become healthy. Log tail:"
    tail -n 30 "$STREAMLIT_LOG" 2>/dev/null || true
    record "Setup: streamlit boots and passes health check" "FAIL"
fi

# ---------------------------------------------------------------------------
# Test 1: Case 1 (clean run) — all flight recorder nodes complete
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "TEST 1: Case 1 — clean run, all pipeline nodes complete"
echo "======================================================================"
if python3 "$SCRIPT_DIR/flight_recorder_e2e_checks.py" case_1 "$CASE1_DB"; then
    record "Test 1: Case 1 clean run — all nodes complete" "PASS"
else
    record "Test 1: Case 1 clean run — all nodes complete" "FAIL"
fi

# ---------------------------------------------------------------------------
# Test 2: Case 2 (control failures) — anomaly findings shown
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "TEST 2: Case 2 — control failures, anomaly findings shown"
echo "======================================================================"
if python3 "$SCRIPT_DIR/flight_recorder_e2e_checks.py" case_2 "$CASE2_DB"; then
    record "Test 2: Case 2 control failures — findings shown" "PASS"
else
    record "Test 2: Case 2 control failures — findings shown" "FAIL"
fi

# ---------------------------------------------------------------------------
# Test 3: Case 3 (reconciliation block) — Gate 3 blocks, downstream locks
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "TEST 3: Case 3 — reconciliation block, downstream nodes lock"
echo "======================================================================"
if python3 "$SCRIPT_DIR/flight_recorder_e2e_checks.py" case_3 "$CASE3_DB"; then
    record "Test 3: Case 3 reconciliation block — Gate 3 blocks and locks downstream" "PASS"
else
    record "Test 3: Case 3 reconciliation block — Gate 3 blocks and locks downstream" "FAIL"
fi

# ---------------------------------------------------------------------------
# Test 4: tamper detection demo
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "TEST 4: Tamper detection (scripts/demo_tamper_detection.py)"
echo "======================================================================"
# demo_tamper_detection.py reads the real ./audit.db read-only: it copies it to
# audit.db.tamper_test and tampers with ONLY that copy, then deletes the copy
# in its own cleanup step. It is never pointed at a substitute database and
# the real ./audit.db (whatever a human has been using this session) is never
# moved, overwritten, or deleted by this test.
if [ ! -f "$REPO_ROOT/audit.db" ]; then
    log "No ./audit.db found — running Case 1 once to create one with a report in it."
    python3 "$SCRIPT_DIR/flight_recorder_e2e_checks.py" case_1 "$REPO_ROOT/audit.db" > /dev/null
fi

pushd "$REPO_ROOT" > /dev/null
TAMPER_OUTPUT="$(python3 scripts/demo_tamper_detection.py 2>&1)"
TAMPER_EXIT=$?
echo "$TAMPER_OUTPUT"
popd > /dev/null

if [ "$TAMPER_EXIT" -eq 0 ] && echo "$TAMPER_OUTPUT" | grep -qi "hash mismatch detected\|chain broken"; then
    record "Test 4: tamper detection — hash chain catches tampering" "PASS"
else
    record "Test 4: tamper detection — hash chain catches tampering" "FAIL"
fi
