#!/usr/bin/env bash
# test-fips.sh — Run fips mesh tests via cloud-lab infrastructure.
#
# Uses the same GCP project, labels, and reaper as TollGate tests.
# Results published via result_publisher.py (Blossom + Nostr kind 30078).
#
# Usage:
#   ./scripts/test-fips.sh chaos --ref master --scenario smoke-10
#   ./scripts/test-fips.sh chaos --ref master --scenario maelstrom --baked
#   ./scripts/test-fips.sh interop --ref-a v0.4.0-rc2 --ref-c v0.3.0
#   ./scripts/test-fips.sh real-mesh --ref master --duration 120
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT="${GCP_PROJECT:-tollgate-test-lab}"
ZONE="${GCP_ZONE:-us-east1-b}"
IMAGE="${FIPS_IMAGE:-fips-cloud-lab-baked}"
MACHINE="${MACHINE_TYPE:-e2-standard-4}"

MODE="${1:-chaos}"
shift || true

case "$MODE" in
  chaos)
    WORKER="lib/cloud_lab/worker/fips_chaos.sh"
    REF="master"; SCENARIO="smoke-10"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --ref) REF="$2"; shift 2 ;;
        --scenario) SCENARIO="$2"; shift 2 ;;
        --machine-type) MACHINE="$2"; shift 2 ;;
        --baked) shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
      esac
    done
    METADATA="fips-ref=${REF},scenario=${SCENARIO}"
    RUN_ID="fips-${SCENARIO}-$(date -u +%Y%m%dT%H%M%SZ)"
    ;;
  interop)
    WORKER="lib/cloud_lab/worker/fips_interop.sh"
    REF_A="v0.4.0-rc2"; REF_B="master"; REF_C="v0.3.0"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --ref-a) REF_A="$2"; shift 2 ;;
        --ref-b) REF_B="$2"; shift 2 ;;
        --ref-c) REF_C="$2"; shift 2 ;;
        --machine-type) MACHINE="$2"; shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
      esac
    done
    METADATA="fips-ref=${REF_A},ref-a=${REF_A},ref-b=${REF_B},ref-c=${REF_C},spec=a%20a%20b%20c"
    RUN_ID="fips-interop-$(date -u +%Y%m%dT%H%M%SZ)"
    MACHINE="e2-standard-8"
    ;;
  real-mesh)
    WORKER="lib/cloud_lab/worker/fips_real_mesh.sh"
    REF="master"; DURATION=120
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --ref) REF="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --machine-type) MACHINE="$2"; shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
      esac
    done
    METADATA="fips-ref=${REF},duration=${DURATION}"
    RUN_ID="fips-real-mesh-$(date -u +%Y%m%dT%H%M%SZ)"
    ;;
  *)
    echo "Usage: $0 {chaos|interop|real-mesh} [options]"
    exit 1
    ;;
esac

VM_NAME="fips-test-${RUN_ID}"
VM_NAME="${VM_NAME:0:63}"
RESULTS_DIR="${REPO_ROOT}/results/${RUN_ID}"

echo "=== fips test (${MODE}) ==="
echo "  run-id:  ${RUN_ID}"
echo "  vm:      ${VM_NAME}"
echo "  mode:    ${MODE}"
echo "  image:   ${IMAGE}"
echo "  machine: ${MACHINE}"

# Check for stale VMs (same labels as cloud-lab.py)
echo ""
echo "Checking for stale fips VMs..."
gcloud compute instances list \
  --project="${PROJECT}" \
  --filter="labels.fips_cloud_run=true" \
  --format="value(name)" 2>/dev/null | while read -r name; do
    if [ -n "$name" ]; then
      echo "  ⚠ Found existing: ${name}"
    fi
  done

# Create VM
echo ""
gcloud compute instances create "${VM_NAME}" \
  --project="${PROJECT}" --zone="${ZONE}" \
  --machine-type="${MACHINE}" \
  --image="${IMAGE}" --image-project="${PROJECT}" \
  --boot-disk-size=50GB --boot-disk-type=pd-ssd \
  --scopes=cloud-platform \
  --tags=fips-cloud-run \
  --labels="fips_cloud_run=true,tollgate_run=true" \
  --metadata "${METADATA},run-id=${RUN_ID}" \
  --metadata-from-file "startup-script=${REPO_ROOT}/${WORKER}" \
  -q 2>&1 | tail -3

echo ""
echo "VM created. Polling for completion..."
echo "  (Reaper will auto-delete after 2h if stuck)"

# Poll for DONE
for i in $(seq 1 120); do
  sleep 30
  STATUS=$(gcloud compute ssh "${VM_NAME}" --zone="${ZONE}" \
    --command="cat /opt/fips-results/DONE 2>/dev/null || cat /opt/fips-results/FAILED 2>/dev/null || echo PENDING" \
    --ssh-flag=-oStrictHostKeyChecking=no --ssh-flag=-oUserKnownHostsFile=/dev/null \
    -q 2>/dev/null || echo "PENDING")

  if echo "$STATUS" | grep -q "completed\|FAILED"; then
    echo "  [${i}x30s] DONE"
    break
  fi
  echo "  [${i}x30s] running..."
done

# Collect results
echo ""
echo "Collecting results..."
mkdir -p "${RESULTS_DIR}"
gcloud compute ssh "${VM_NAME}" --zone="${ZONE}" \
  --command="sudo tar czf /tmp/results.tar.gz -C /opt/fips-results ." \
  --ssh-flag=-oStrictHostKeyChecking=no --ssh-flag=-oUserKnownHostsFile=/dev/null -q 2>/dev/null
gcloud compute scp "${VM_NAME}:/tmp/results.tar.gz" "${RESULTS_DIR}/" \
  --zone="${ZONE}" -q 2>/dev/null
tar xzf "${RESULTS_DIR}/results.tar.gz" -C "${RESULTS_DIR}/" 2>/dev/null
rm -f "${RESULTS_DIR}/results.tar.gz"

# Visualize
echo ""
echo "Visualizing..."
SCEN_DIR=$(find "${RESULTS_DIR}" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)
if [ -n "$SCEN_DIR" ]; then
  python3 "${REPO_ROOT}/lib/fips_visualizer.py" \
    --results-dir "$SCEN_DIR" \
    --output "${RESULTS_DIR}/report.html" 2>/dev/null && echo "  Report generated"
fi

# Publish
echo ""
echo "Publishing to Blossom + Nostr..."
NSEC_FILE="${NSEC_FILE:-${HOME}/.config/prta/nsec}"
if [ -f "$NSEC_FILE" ]; then
  cd "${REPO_ROOT}"
  python3 -m lib.result_publisher "${RESULTS_DIR}" \
    --nsec-file "$NSEC_FILE" \
    --tag "fips-${MODE}" \
    --run-id "${RUN_ID}" \
    --blossom-server https://blossom.psbt.me \
    --relays wss://relay.cashu.email 2>/dev/null && echo "  Published" || echo "  Publish skipped"
else
  echo "  NSEC_FILE not found, skipping publish"
fi

# Cleanup
echo ""
echo "Deleting VM..."
gcloud compute instances delete "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT}" --quiet 2>/dev/null
echo "Done."
