#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

# --- Load .env if present (exports all variables) ---
if [[ -f "$REPO_DIR/.env" ]]; then
  set -a
  source "$REPO_DIR/.env"
  set +a
fi

# --- Parse args ---
NO_RENDER=false
RUN_DIR_ARG=""
PYTEST_EXTRA=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --no-render) NO_RENDER=true; shift ;;
    --run-dir)   RUN_DIR_ARG="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: ./scripts/run-api.sh [--no-render] [--run-dir DIR] [extra pytest args...]"
      exit 0
      ;;
    *) PYTEST_EXTRA+=("$1"); shift ;;
  esac
done

# --- Venv ---
VENV="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv}"
source "$VENV/bin/activate"

# --- Run ID and results directory ---
if [[ -n "$RUN_DIR_ARG" ]]; then
  RESULTS_DIR="$(cd "$(dirname "$RUN_DIR_ARG")" && pwd)/$(basename "$RUN_DIR_ARG")"
  RUN_ID="$(basename "$RESULTS_DIR")"
elif [[ -n "${TOLLGATE_RESULTS_DIR:-}" ]]; then
  RESULTS_DIR="$TOLLGATE_RESULTS_DIR"
  RUN_ID="${TOLLGATE_RUN_ID:-$(basename "$RESULTS_DIR")}"
else
  TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')
  GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
  RUN_ID="${TOLLGATE_RUN_ID:-${TIMESTAMP}-${GIT_SHA}}"
  RESULTS_DIR="$REPO_DIR/results/$RUN_ID"
fi

export TOLLGATE_RUN_ID="$RUN_ID"
export TOLLGATE_RESULTS_DIR="$RESULTS_DIR"

STARTED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# --- Backend ---
BACKEND_ARG=""
if [[ -n "${TOLLGATE_BACKEND:-}" ]]; then
  BACKEND_ARG="--backend=$TOLLGATE_BACKEND"
fi

# --- Client mode ---
CLIENT_ARG=""
if [[ -n "${TOLLGATE_CLIENT_TYPE:-}" ]]; then
  CLIENT_ARG="--client=$TOLLGATE_CLIENT_TYPE"
fi

# --- Directory structure ---
mkdir -p "$RESULTS_DIR/raw/api" "$RESULTS_DIR/report" "$RESULTS_DIR/artifacts"

# --- Run pytest ---
echo "==> API tests: run_id=$RUN_ID"
echo "==> Results:   $RESULTS_DIR"

PYTEST_EXIT=0
pytest -m api \
  --html="$RESULTS_DIR/raw/api/report.html" \
  --self-contained-html \
  --junitxml="$RESULTS_DIR/raw/api/junit.xml" \
  --results="$RESULTS_DIR" \
  -v --tb=short --timeout=60 --timeout-method=thread \
  $BACKEND_ARG \
  $CLIENT_ARG \
  "${PYTEST_EXTRA[@]+"${PYTEST_EXTRA[@]}"}" \
  2>&1 | tee "$RESULTS_DIR/raw/api/output.log" || PYTEST_EXIT=$?

FINISHED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# --- Collect and render ---
if [[ "$NO_RENDER" == "false" ]]; then
  echo "==> Collecting results..."

  # --- Query router for SUT version ---
  ROUTER_QUERY_ARG=""
  if [[ -n "${TOLLGATE_SSH_HOST:-}" ]]; then
    ROUTER_QUERY_ARG="--query-router $TOLLGATE_SSH_HOST"
  fi

  python3 "$SCRIPT_DIR/collect-results.py" \
    --run-dir "$RESULTS_DIR" $ROUTER_QUERY_ARG \
    --pytest "api=raw/api/junit.xml" \
    --run-id "$RUN_ID" \
    --sut-backend "${TOLLGATE_BACKEND:-go}" \
    ${TOLLGATE_SUT_COMMIT:+--sut-commit "$TOLLGATE_SUT_COMMIT"} \
    --sut-branch "${TOLLGATE_BRANCH:-}" \
    ${TOLLGATE_PR:+--sut-pr "$TOLLGATE_PR"} \
    --router-id "${TOLLGATE_ROUTER_ID:-}" \
    --router-model "${TOLLGATE_ROUTER_MODEL:-}" \
    --router-arch "${TOLLGATE_ROUTER_ARCH:-}" \
    --client-type "${TOLLGATE_CLIENT_TYPE:-}" \
    ${TOLLGATE_VIRTUAL_LAB:+--virtual-lab} \
    --viewport "${TOLLGATE_VIEWPORT:-desktop}" \
    --test-plan "${TOLLGATE_TEST_PLAN:-api}" \
    --started-at "$STARTED_AT" \
    --finished-at "$FINISHED_AT" \
    --allow-failures

  echo "==> Rendering report..."
  python3 "$SCRIPT_DIR/render-report.py" --run-dir "$RESULTS_DIR"
fi

echo ""
echo "==> Done: run_id=$RUN_ID  results=$RESULTS_DIR  pytest_exit=$PYTEST_EXIT"
exit $PYTEST_EXIT
