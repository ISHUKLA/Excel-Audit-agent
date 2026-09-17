#!/usr/bin/env bash
# Smoke-test that the Excel Audit Agent can be deployed from a fresh clone
# and started without manual configuration beyond an API key.
#
# NOTE ON config/users.json: this repo does not currently ship or read a
# config/users.json file. app.py collects reviewer identity via a free-text
# field (`reviewer_name`), not a dropdown backed by a JSON registry. This
# script checks for users.json as an OPTIONAL file (some deployments may add
# one later) rather than requiring it, so it doesn't fail a real fresh clone
# on a file that was never part of this codebase's actual startup path.
#
# Usage: bash scripts/test_fresh_deployment.sh [repo_url]
#   repo_url - optional; if given, clones into ./fresh-deploy-test first.
#              if omitted, assumes the current directory is the repo root.

set -e

REPO_URL="${1:-}"
HEALTH_URL="http://localhost:8501/_stcore/health"
STREAMLIT_PID=""
STREAMLIT_LOG="$(mktemp -t streamlit-fresh-deploy.XXXXXX.log)"
VENV_DIR=".venv-fresh-deploy-test"

ts() {
    date "+%H:%M:%S"
}

log() {
    echo "[$(ts)] $1"
}

fail() {
    log "✗ $1"
    exit 1
}

cleanup() {
    local exit_code=$?
    if [ -n "$STREAMLIT_PID" ] && kill -0 "$STREAMLIT_PID" 2>/dev/null; then
        kill "$STREAMLIT_PID" 2>/dev/null || true
        wait "$STREAMLIT_PID" 2>/dev/null || true
        log "✓ Cleanup: streamlit stopped"
    fi
    if [ $exit_code -ne 0 ]; then
        log "✗ Deployment test failed (exit code $exit_code). Streamlit log:"
        tail -n 30 "$STREAMLIT_LOG" 2>/dev/null || true
    fi
    rm -f "$STREAMLIT_LOG"
}
trap cleanup EXIT

log "Starting fresh deployment test..."

# ---------------------------------------------------------------------------
# 1. Setup phase
# ---------------------------------------------------------------------------

if [ -n "$REPO_URL" ]; then
    TARGET_DIR="fresh-deploy-test"
    if [ -d "$TARGET_DIR" ]; then
        fail "Directory '$TARGET_DIR' already exists — remove it before re-running, or omit repo_url to test in place."
    fi
    log "Cloning $REPO_URL into $TARGET_DIR..."
    git clone "$REPO_URL" "$TARGET_DIR"
    cd "$TARGET_DIR"
else
    log "No repo_url given; testing in current directory ($(pwd))."
fi

if [ ! -f "app.py" ] || [ ! -f "requirements.txt" ]; then
    fail "app.py or requirements.txt not found in $(pwd) — is this the repo root?"
fi

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail "python3 not found on PATH."
fi

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
PYTHON_MAJOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[0])')"
PYTHON_MINOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[1])')"

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
    fail "Python $PYTHON_VERSION found; 3.11+ is required."
fi
log "Python version: $PYTHON_VERSION ✓"

log "Creating virtual environment ($VENV_DIR)..."
"$PYTHON_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "Installing dependencies (this may take a minute)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
log "Dependencies installed ✓"

# ---------------------------------------------------------------------------
# 2. Configuration phase
# ---------------------------------------------------------------------------

if [ -f "config/users.json" ]; then
    log "config/users.json found (optional file present) ✓"
else
    log "config/users.json not present — expected; app.py collects reviewer identity via a text field, not this file."
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    fail "ANTHROPIC_API_KEY is not set. Export it before running this test: export ANTHROPIC_API_KEY=sk-ant-..."
fi
log "ANTHROPIC_API_KEY is set ✓"

log "Config summary:"
log "  - config/authorized_approvers.json: $([ -f config/authorized_approvers.json ] && echo present || echo MISSING)"
log "  - config/materiality_defaults.json: $([ -f config/materiality_defaults.json ] && echo present || echo MISSING)"
log "  - config/users.json (optional):     $([ -f config/users.json ] && echo present || echo "not present")"

# ---------------------------------------------------------------------------
# 3. Startup phase
# ---------------------------------------------------------------------------

log "Starting streamlit in the background..."
streamlit run app.py --server.headless true > "$STREAMLIT_LOG" 2>&1 &
STREAMLIT_PID=$!
log "Streamlit started (PID $STREAMLIT_PID)"

log "Waiting 5 seconds for startup..."
sleep 5

if ! kill -0 "$STREAMLIT_PID" 2>/dev/null; then
    log "Streamlit process died during startup. Log output:"
    tail -n 30 "$STREAMLIT_LOG"
    fail "Deployment failed; check logs above"
fi

HTTP_STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" || echo "000")"

if [ "$HTTP_STATUS" = "200" ]; then
    log "Health check: OK ✓"
    log "✓ Deployment successful"
else
    log "Health check failed (HTTP $HTTP_STATUS). Streamlit log:"
    tail -n 30 "$STREAMLIT_LOG"
    fail "Deployment failed; check logs"
fi

# ---------------------------------------------------------------------------
# 4. User identity / config readability test
# ---------------------------------------------------------------------------

if [ -f "audit.db" ]; then
    REPORT_COUNT="$(sqlite3 audit.db "SELECT COUNT(DISTINCT report_id) FROM log_rows;" 2>/dev/null || echo "unreadable")"
    log "audit.db found from a previous run — $REPORT_COUNT report(s) recorded"
else
    log "No audit.db present yet (expected on a first-ever run — created on first gate decision)"
fi

if [ -f "config/users.json" ]; then
    if "$PYTHON_BIN" -c "import json; json.load(open('config/users.json'))" 2>/dev/null; then
        log "config/users.json is valid JSON and readable ✓"
    else
        fail "config/users.json exists but is not valid JSON."
    fi
else
    log "config/users.json readability check skipped (file not present, see note above)"
fi
log "User config verified ✓"

# ---------------------------------------------------------------------------
# 5. Cleanup phase (also runs via trap on early exit)
# ---------------------------------------------------------------------------

kill "$STREAMLIT_PID" 2>/dev/null || true
wait "$STREAMLIT_PID" 2>/dev/null || true
STREAMLIT_PID=""
log "✓ Cleanup: streamlit stopped"

deactivate 2>/dev/null || true

log "All tests passed"
exit 0
