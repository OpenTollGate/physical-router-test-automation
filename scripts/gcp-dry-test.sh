#!/usr/bin/env bash
set -euo pipefail

# GCP Dry Test Runner — cheapest possible cloud test execution.
#
# Creates an e2-medium VM from snapshot, runs local dry tests, auto-stops.
# A 2-hour auto-shutdown safety net prevents runaway costs.
#
# Usage:
#   ./scripts/gcp-dry-test.sh                  # API tests only (cheapest)
#   ./scripts/gcp-dry-test.sh --playwright      # API + Playwright browser tests
#   ./scripts/gcp-dry-test.sh --nested-kvm      # Use n2-standard-2 for OpenWrt VM tests
#   ./scripts/gcp-dry-test.sh --keep-running    # Don't auto-stop after tests

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

VM_NAME="tollgate-dry-test-$$"
ZONE="${TOLLGATE_GCP_ZONE:-us-east1-b}"
SNAPSHOT="${TOLLGATE_GCP_SNAPSHOT:-tollgate-runner-v17}"
PROJECT="${TOLLGATE_GCP_PROJECT:-tollgate-test-lab}"
PLAYWRIGHT=false
NESTED_KVM=false
KEEP_RUNNING=false

for arg in "$@"; do
  case "$arg" in
    --playwright)   PLAYWRIGHT=true ;;
    --nested-kvm)   NESTED_KVM=true ;;
    --keep-running) KEEP_RUNNING=true ;;
  esac
done

if [[ "$NESTED_KVM" == "true" ]]; then
  MACHINE="n2-standard-2"
  EXTRA_FLAGS="--enable-nested-virtualization"
else
  MACHINE="e2-medium"
  EXTRA_FLAGS=""
fi

echo "=== GCP Dry Test Runner ==="
echo "Machine: $MACHINE  Zone: $ZONE  Snapshot: $SNAPSHOT"
echo "Playwright: $PLAYWRIGHT  Nested KVM: $NESTED_KVM  Keep running: $KEEP_RUNNING"
echo ""

# ─── 0. Check for orphaned VMs ───────────────────────────────────
echo "=== Checking for orphaned VMs ==="
STALE=$(gcloud compute instances list --filter="name~tollgate-dry-test" --format="value(name,zone,status)" 2>/dev/null || true)
if [[ -n "$STALE" ]]; then
  echo "WARNING: Found orphaned dry-test VMs:"
  echo "$STALE"
  echo "Deleting them..."
  echo "$STALE" | while read -r name zone status; do
    [[ -n "$name" ]] && gcloud compute instances delete "$name" --zone="$zone" --quiet 2>/dev/null || true
  done
else
  echo "No orphaned VMs found."
fi
echo ""

# ─── 1. Create VM from snapshot ──────────────────────────────────
echo "=== Creating VM: $VM_NAME ($MACHINE) ==="
gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE" \
  --source-snapshot="$SNAPSHOT" \
  --boot-disk-size=50 \
  --boot-disk-type=pd-standard \
  $EXTRA_FLAGS \
  --tags=tollgate-test \
  2>&1 || { echo "ERROR: Failed to create VM"; exit 1; }

# ─── 2. Install 2-hour auto-shutdown safety net ──────────────────
echo ""
echo "=== Installing 2-hour auto-shutdown safety net ==="
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command='
  echo "shutdown -P +120" | sudo at now + 1 minute 2>/dev/null || {
    sudo apt-get install -y at 2>/dev/null >/dev/null
    echo "shutdown -P +120" | sudo at now + 1 minute
  }
  echo "Auto-shutdown scheduled for 2 hours from now"
  atq 2>/dev/null
' 2>&1 || echo "WARNING: Could not install auto-shutdown"

# ─── 3. Pull latest prta + build backend ─────────────────────────
echo ""
echo "=== Pulling latest code + building backend ==="
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command="
  export PATH=/opt/tollgate-venv/bin:/usr/local/go/bin:\$PATH

  if [[ ! -d ~/src ]]; then
    mkdir -p ~/src && cd ~/src
    git clone https://github.com/OpenTollGate/tollgate-module-basic-go.git 2>&1 | tail -1
    git clone https://github.com/OpenTollGate/physical-router-test-automation.git 2>&1 | tail -1
  fi

  cd ~/src/physical-router-test-automation && git pull origin main 2>&1 | tail -3
  cd ~/src/tollgate-module-basic-go && git pull origin main 2>&1 | tail -3

  cd ~/src/tollgate-module-basic-go/src
  go build -o /tmp/tollgate-wrt . 2>&1 | tail -3
  ls -la /tmp/tollgate-wrt
" 2>&1

# ─── 4. Run API tests ────────────────────────────────────────────
echo ""
echo "=== Running API tests ==="
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command='
  export PATH=/opt/tollgate-venv/bin:/usr/local/go/bin:$PATH
  cd ~/src/physical-router-test-automation
  kill $(lsof -ti:3338 2>/dev/null) $(lsof -ti:2121 2>/dev/null) 2>/dev/null || true
  TOLLGATE_BACKEND_BINARY=/tmp/tollgate-wrt bash scripts/local-test.sh 2>&1
' 2>&1
API_EXIT=$?

# ─── 5. Run Playwright tests (optional) ──────────────────────────
PW_EXIT=0
if [[ "$PLAYWRIGHT" == "true" ]]; then
  echo ""
  echo "=== Running Playwright tests ==="
  gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command='
    export PATH=/opt/tollgate-venv/bin:/usr/local/go/bin:$PATH
    cd ~/src/physical-router-test-automation
    TOLLGATE_BACKEND_BINARY=/tmp/tollgate-wrt bash scripts/local-test.sh --keep-running 2>&1 | tail -3

    cd ~/src/tollgate-captive-portal-site 2>/dev/null || {
      cd ~/src && git clone https://github.com/OpenTollGate/tollgate-captive-portal-site.git 2>&1 | tail -1
    }
    cd ~/src/tollgate-captive-portal-site
    npm install 2>&1 | tail -1
    npm run dev -- --host 127.0.0.1 --port 5173 > /tmp/vite-dev.log 2>&1 &
    VITE_PID=$!
    sleep 5

    cd ~/src/physical-router-test-automation
    npx playwright test tests/captive-portal.local.spec.mjs --reporter=list --timeout=30000 2>&1
    PW_EXIT=$?

    kill $VITE_PID 2>/dev/null
    kill $(lsof -ti:3338 2>/dev/null) $(lsof -ti:2121 2>/dev/null) 2>/dev/null
    exit $PW_EXIT
  ' 2>&1
  PW_EXIT=$?
fi

# ─── 6. Summary ──────────────────────────────────────────────────
echo ""
echo "=== Results ==="
echo "API: $([ $API_EXIT -eq 0 ] && echo 'PASS' || echo 'FAIL')"
if [[ "$PLAYWRIGHT" == "true" ]]; then
  echo "Playwright: $([ $PW_EXIT -eq 0 ] && echo 'PASS' || echo 'FAIL')"
fi

# ─── 7. Shutdown or keep running ─────────────────────────────────
echo ""
if [[ "$KEEP_RUNNING" == "true" ]]; then
  echo "VM kept running: $VM_NAME (zone: $ZONE)"
  echo "Auto-shutdown in 2 hours. Stop manually with:"
  echo "  gcloud compute instances stop $VM_NAME --zone=$ZONE"
else
  echo "=== Stopping VM: $VM_NAME ==="
  gcloud compute instances stop "$VM_NAME" --zone="$ZONE" --quiet 2>&1
  echo "VM stopped. Disk preserved for fast restart."
  echo "To restart: gcloud compute instances start $VM_NAME --zone=$ZONE"
fi

# Exit with worst result
[[ $API_EXIT -ne 0 ]] && exit $API_EXIT
[[ $PW_EXIT -ne 0 ]] && exit $PW_EXIT
exit 0
