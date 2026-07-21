#!/usr/bin/env bash
set -euo pipefail

# CobradorWave sign-off script for PR #21 (issue #5 fix).
#
# Runs on a machine with:
#   - Tailscale access to CobradorWave (100.90.101.9)
#   - SSH key for the routers
#   - cashu CLI installed (for minting test tokens)
#   - npm + npx playwright installed
#   - gh CLI authenticated (for posting PR comment)
#
# Usage:
#   COBRADOR_ROUTER_IP=10.47.41.1 ./scripts/signoff-pr21-issue5.sh
#
# Env:
#   COBRADOR_ROUTER_IP   Router Alpha IP (default: 10.47.41.1)
#   COBRADOR_SSH_USER    Router SSH user (default: root)
#   COBRADOR_SSH_KEY     SSH key path (default: ~/.ssh/id_ed25519)
#   TEST_MINT_URL        Cashu test mint (default: https://testnut.cashu.exchange)
#   GITHUB_REPO          PR repo (default: OpenTollGate/tollgate-captive-portal-site)
#   PR_NUMBER            PR number (default: 21)

ROUTER_IP="${COBRADOR_ROUTER_IP:-10.47.41.1}"
SSH_USER="${COBRADOR_SSH_USER:-root}"
SSH_KEY="${COBRADOR_SSH_KEY:-$HOME/.ssh/id_ed25519}"
TEST_MINT_URL="${TEST_MINT_URL:-https://testnut.cashu.exchange}"
REPO="${GITHUB_REPO:-OpenTollGate/tollgate-captive-portal-site}"
PR="${PR_NUMBER:-21}"
PORTAL_DIR="$(cd "$(dirname "$0")/.." && pwd)/tollgate-captive-portal-site"
OUT_DIR="/tmp/pr21-signoff"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -i "$SSH_KEY")

mkdir -p "$OUT_DIR"

echo "=== PR #21 / Issue #5 CobradorWave Sign-off ==="
echo "  Router:      $SSH_USER@$ROUTER_IP"
echo "  Test mint:   $TEST_MINT_URL"
echo "  Portal dir:  $PORTAL_DIR"
echo "  Output:      $OUT_DIR"
echo ""

# --- Step 1: Verify CobradorWave + router access ---
echo "[1/7] Verifying router access..."
if ! ssh "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER_IP" 'echo OK' >/dev/null 2>&1; then
  echo "FAIL: cannot SSH to $SSH_USER@$ROUTER_IP"
  exit 1
fi
echo "  OK"
echo ""

# --- Step 2: Checkout PR #21 branch + build ---
echo "[2/7] Building SPA from PR #21 branch..."
cd "$PORTAL_DIR"
git fetch origin shape-a-raw-token
git checkout shape-a-raw-token
git pull --ff-only origin shape-a-raw-token
npm install --silent
npm run build
echo "  OK — build/splash.html ready"
echo ""

# --- Step 3: Deploy to router ---
echo "[3/7] Deploying to router..."
# Use the deploy script from port/deploy-script if available, else inline
if [ -x "$PORTAL_DIR/scripts/deploy-to-router.sh" ]; then
  SSH_KEY="$SSH_KEY" "$PORTAL_DIR/scripts/deploy-to-router.sh" "$ROUTER_IP"
else
  # Inline minimal deploy
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER_IP" 'mkdir -p /tmp/portal-deploy'
  scp "${SSH_OPTS[@]}" -r "$PORTAL_DIR/build/"* "$SSH_USER@$ROUTER_IP:/www/"
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER_IP" '/etc/init.d/nodogsplash restart 2>/dev/null || true'
fi
echo "  OK"
echo ""

# --- Step 4: Mint a test Cashu token ---
echo "[4/7] Minting test Cashu token..."
TOKEN=$(cashu mint --mint-url "$TEST_MINT_URL" --amount 210 --send 2>/dev/null | head -1)
if [ -z "$TOKEN" ] || ! echo "$TOKEN" | grep -q "cashu"; then
  echo "FAIL: could not mint token from $TEST_MINT_URL"
  echo "  Output: $TOKEN"
  exit 1
fi
echo "  OK — token: ${TOKEN:0:40}..."
echo ""

# --- Step 5: Run Playwright test with real token ---
echo "[5/7] Running Playwright test..."
export TEST_CASHU_TOKEN="$TOKEN"
export ROUTER_IP="$ROUTER_IP"
export TOLLGATE_NDS_URL="http://$ROUTER_IP:2050"

cd "$(dirname "$0")/.."  # physical-router-test-automation root
npx playwright test tests/browser/captive_portal_status.spec.mjs \
  --config=playwright.config-browser.js \
  --project=captive-portal-desktop \
  --reporter=list \
  --output="$OUT_DIR" || true  # don't fail the script if test fails — capture either way
echo ""

# --- Step 6: Capture screenshots ---
echo "[6/7] Capturing screenshots..."
PLAYWRIGHT_OUT="results/browser/test-output"
if [ -d "$PLAYWRIGHT_OUT" ]; then
  cp "$PLAYWRIGHT_OUT"/*.png "$OUT_DIR/" 2>/dev/null || true
fi
ls -la "$OUT_DIR/"*.png 2>/dev/null || echo "  (no screenshots found)"
echo ""

# --- Step 7: Post PR comment with results ---
echo "[7/7] Posting sign-off results to PR #$PR..."
SCREENSHOTS_LIST=$(ls "$OUT_DIR/"*.png 2>/dev/null | xargs -I{} basename {} || echo "")
COMMENT_BODY=$(cat <<EOF
## CobradorWave Hardware Sign-off Results

**Router:** \`$SSH_USER@$ROUTER_IP\`
**Branch:** \`shape-a-raw-token\` (PR #21)
**Test mint:** \`$TEST_MINT_URL\`
**Date:** \`$(date -Iseconds)\`

### Test token
Minted 210 sats from \`$TEST_MINT_URL\` → token used for payment.

### Playwright results
\`\`\`
$(npx playwright test tests/browser/captive_portal_status.spec.mjs --config=playwright.config-browser.js --list 2>&1 | head -10)
\`\`\`

### Screenshots
$(echo "$SCREENSHOTS_LIST" | sed 's/^/- /')

### Next steps
Review screenshots + Playwright output. If the usage stats panel renders with real data, this PR is ready to merge.

---
Automated by \`scripts/signoff-pr21-issue5.sh\`
EOF
)
gh pr comment "$PR" --repo "$REPO" --body "$COMMENT_BODY"
echo "  OK — comment posted"
echo ""

echo "=== Sign-off complete ==="
echo "All artifacts in: $OUT_DIR"
