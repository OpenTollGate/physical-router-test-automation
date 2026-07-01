#!/bin/bash
set -euo pipefail

LOG="/tmp/conwrt-shc.log"
RESULTS_DIR="/tmp/conwrt-results"
CONWRT_REPO="${CONWRT_REPO:-https://github.com/Amperstrand/conwrt.git}"
CONWRT_BRANCH="${CONWRT_BRANCH:-master}"
PRTA_REPO="${PRTA_REPO:-https://github.com/OpenTollGate/physical-router-test-automation.git}"

exec > >(tee -a "$LOG") 2>&1
echo "=== conwrt SHC bootstrap ==="
echo "Started: $(date -u)"
echo "KVM: $(ls -la /dev/kvm 2>/dev/null || echo 'NOT AVAILABLE')"

# ── 1. Install dependencies ──────────────────────────────────────────
echo ">>> Installing QEMU + nak + dependencies..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    qemu-system-x86 qemu-utils sshpass curl wget git python3 python3-pip python3-venv
sudo chmod 666 /dev/kvm 2>/dev/null || true

# Install nak CLI for Nostr/Blossom signing
if ! which nak >/dev/null 2>&1; then
    echo "Installing nak..."
    cd /tmp
    curl -sL "https://api.github.com/repos/fiatjaf/nak/releases/latest" -o nak-releases.json
    NAK_URL=$(python3 -c "
import json
data = json.load(open('nak-releases.json'))
for a in data.get('assets', []):
    name = a['name'].lower()
    if 'linux' in name and 'amd64' in name:
        print(a['browser_download_url'])
        break
" || echo "")
    if [ -n "$NAK_URL" ]; then
        curl -sL "$NAK_URL" -o nak-bin
        chmod +x nak-bin
        if /tmp/nak-bin --version >/dev/null 2>&1; then
            sudo mv nak-bin /usr/local/bin/nak
        else
            mv nak-bin nak.tar.gz && tar xzf nak.tar.gz && sudo mv nak /usr/local/bin/nak && rm -f nak.tar.gz
        fi
    fi
fi
which nak && echo "nak installed: $(nak --version 2>&1 | head -1)" || echo "nak NOT available"

# ── 2. Clone conwrt ──────────────────────────────────────────────────
echo ">>> Cloning conwrt..."
cd /tmp
rm -rf conwrt
git clone --depth 1 -b "$CONWRT_BRANCH" "$CONWRT_REPO"
cd conwrt
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]" -q

# ── 3. Run dry-run use case tests ────────────────────────────────────
echo ">>> Running dry-run use case tests..."
DRY_OUTPUT=$(pytest tests/integration/test_use_cases_dry_run.py -v --tb=short 2>&1 || true)
echo "$DRY_OUTPUT" | tail -5
DRY_PASSED=$(echo "$DRY_OUTPUT" | grep -oP '\d+(?= passed)' | tail -1 || echo 0)
DRY_FAILED=$(echo "$DRY_OUTPUT" | grep -oP '\d+(?= failed)' | tail -1 || echo 0)
echo "Dry-run: $DRY_PASSED passed, $DRY_FAILED failed"

# ── 4. Run QEMU integration tests with KVM ───────────────────────────
echo ">>> Running QEMU integration tests with KVM..."
QEMU_OUTPUT=$(pytest tests/integration/test_sqm.py -v --tb=long -s 2>&1 || true)
echo "$QEMU_OUTPUT" | tail -10
QEMU_PASSED=$(echo "$QEMU_OUTPUT" | grep -oP '\d+(?= passed)' | tail -1 || echo 0)
QEMU_FAILED=$(echo "$QEMU_OUTPUT" | grep -oP '\d+(?= failed)' | tail -1 || echo 0)
echo "QEMU: $QEMU_PASSED passed, $QEMU_FAILED failed"

# ── 5. Collect results ──────────────────────────────────────────────
mkdir -p "$RESULTS_DIR"
ALL_PASSED=$((DRY_PASSED + QEMU_PASSED))
ALL_FAILED=$((DRY_FAILED + QEMU_FAILED))
ALL_TOTAL=$((ALL_PASSED + ALL_FAILED))

cat > "$RESULTS_DIR/comparison.json" << JSONEOF
{
  "run_id": "conwrt-shc-$(date -u +%Y%m%d-%H%M%S)",
  "project": "conwrt",
  "runner": "shc-kvm",
  "date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "passed": $ALL_PASSED,
  "failed": $ALL_FAILED,
  "total": $ALL_TOTAL,
  "tests": {
    "dry_run": {"passed": $DRY_PASSED, "failed": $DRY_FAILED},
    "qemu_kvm": {"passed": $QEMU_PASSED, "failed": $QEMU_FAILED}
  }
}
JSONEOF

cat > "$RESULTS_DIR/summary.md" << MDEOF
# conwrt SHC KVM Test Results

**Runner:** SHC Dev VPS Standard (2C/8GB, KVM)
**Date:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Results: ${ALL_PASSED}/${ALL_TOTAL} passed, ${ALL_FAILED} failed

### Dry-Run Use Case Tests
- ${DRY_PASSED} passed, ${DRY_FAILED} failed
- Tests SQM, DoH, nodns, ssh-hardening, wireguard-client, adguard, mwan3, ssl

### QEMU Integration Tests (KVM-accelerated)
- ${QEMU_PASSED} passed, ${QEMU_FAILED} failed
- Boots real OpenWrt 24.10.2 VM, tests SQM configure + tc qdisc
MDEOF

echo "Results: $ALL_PASSED passed, $ALL_FAILED failed"

# ── 6. Publish results to Nostr/Blossom ─────────────────────────────
if [ -f "${NSEC_FILE:-$HOME/.config/prta/nsec}" ]; then
    echo ">>> Publishing results to Nostr..."
    cd /tmp
    rm -rf prta
    git clone --depth 1 "$PRTA_REPO" prta 2>/dev/null
    if [ -d prta ]; then
        cd prta
        python3 -m venv .venv 2>/dev/null || true
        source .venv/bin/activate 2>/dev/null || true
        pip install -q -e . 2>/dev/null || true

        export PROJECT_TAG=conwrt
        python3 conwrt/publish_results.py \
            --results-dir "$RESULTS_DIR" \
            --run-id "conwrt-shc-$(date -u +%Y%m%d-%H%M%S)" \
            --nsec-file "${NSEC_FILE:-$HOME/.config/prta/nsec}" \
            --summary "conwrt SHC KVM tests — $ALL_PASSED passed, $ALL_FAILED failed (dry-run + QEMU with KVM)" \
            --passed "$ALL_PASSED" --failed "$ALL_FAILED" 2>&1 || echo "Publish failed (non-fatal)"
    fi
else
    echo "No nsec found — skipping publish"
fi

echo "=== Bootstrap complete ==="
echo "Finished: $(date -u)"
echo "BOOTSTRAP_DONE" > /tmp/bootstrap.status
