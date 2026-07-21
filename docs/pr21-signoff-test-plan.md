# PR #21 — CobradorWave Hardware Sign-off Test Plan

**PR**: [#21 — fix: URL-param token delivery with raw-token POST, race condition fix, ErrorBoundary, CSP](https://github.com/OpenTollGate/tollgate-captive-portal-site/pull/21)
**Closes**: #5, #18, #22, #26
**Branch**: `shape-a-raw-token`

## Prerequisites

| Requirement | Check |
|---|---|
| Tailscale access to CobradorWave (`100.90.101.9`) | `ping -c 1 100.90.101.9` |
| SSH key for router Alpha (`10.47.41.1`) | `ssh root@10.47.41.1 'echo OK'` |
| `cashu` CLI installed | `cashu --version` |
| `gh` CLI authenticated | `gh auth status` |
| `npx playwright` installed | `npx playwright --version` |
| Tollgate backend running on router | `curl http://10.47.41.1:2121/` returns JSON |

## Option A: Automated sign-off script

```bash
cd /home/c03rad0r/physical-router-test-automation
COBRADOR_ROUTER_IP=10.47.41.1 ./scripts/signoff-pr21-issue5.sh
```

The script:
1. Verifies SSH to the router
2. Builds the SPA from `shape-a-raw-token` branch
3. Deploys `build/` to `/www/` on the router
4. Mints a 210-sat test token from `testnut.cashu.exchange`
5. Runs the Playwright test with `TEST_CASHU_TOKEN=<real-token>`
6. Captures screenshots to `/tmp/pr21-signoff/`
7. Posts results as a PR comment

## Option B: Manual checklist

### Step 1 — Build + Deploy

```bash
cd /home/c03rad0r/tollgate-captive-portal-site
git fetch origin && git checkout shape-a-raw-token && git pull
npm install && npm run build
# Deploy (pick one):
SSH_KEY=~/.ssh/id_ed25519 ./scripts/deploy-to-router.sh 10.47.41.1
# OR manual scp:
scp -r build/* root@10.47.41.1:/www/
ssh root@10.47.41.1 '/etc/init.d/nodogsplash restart'
```

**Pass condition**: `curl -s -o /dev/null -w '%{http_code}' http://10.47.41.1:2050/splash.html` returns `200`.

### Step 2 — Mint test token

```bash
cashu mint --mint-url https://testnut.cashu.exchange --amount 210 --send
# Copy the output token (starts with "cashu")
export TEST_CASHU_TOKEN="cashuB..."
```

**Pass condition**: Token starts with `cashu` and is non-empty.

### Step 3 — Run Playwright test

```bash
cd /home/c03rad0r/physical-router-test-automation
TEST_CASHU_TOKEN="$TEST_CASHU_TOKEN" \
TOLLGATE_NDS_URL="http://10.47.41.1:2050" \
npx playwright test tests/browser/captive_portal_status.spec.mjs \
  --config=playwright.config-browser.js \
  --project=captive-portal-desktop
```

**Pass conditions**:
- [ ] Test 1 `AccessGranted shows live remaining/used/total after payment` — PASS
- [ ] `.tollgate-captive-portal-access-granted-usage` is visible
- [ ] Usage text contains digits (e.g. `"527.3 KiB remaining of 585.9 KiB"`)
- [ ] Screenshot `issue5-usage-stats.png` captured

### Step 4 — Visual verification

Open the screenshot in `/tmp/pr21-signoff/` (or `results/browser/test-output/`). Confirm:

- [ ] "Payment successful!" heading is visible
- [ ] Access duration shows (e.g. "20 minutes")
- [ ] Usage progress bar is visible and partially filled
- [ ] Usage text shows remaining/total in human-readable units
- [ ] "View Balance" link is present
- [ ] No console errors visible in Playwright report

### Step 5 — Session expiry regression (optional)

Wait for the purchased time to expire (or restart backend to force expiry). Confirm:

- [ ] SessionExpired view appears within 60s of expiry
- [ ] SessionExpired shows reconnect guidance
- [ ] No crash or white screen

### Step 6 — Post results

```bash
gh pr comment 21 --repo OpenTollGate/tollgate-captive-portal-site --body "## CobradorWave Sign-off PASS

All checks green. Screenshots attached.

Router: 10.47.41.1 (Alpha)
Token: 210 sats from testnut.cashu.exchange
"
```

## Known issues

1. **Heartbeat fires every 30s** — the first `/usage` poll happens 30s after `authCompleted`. Tests must wait ~35s for the usage panel to populate.
2. **Test 2 (loading state)** is flaky in local verification due to the `/usage` hang behavior. On hardware, the real backend responds quickly so this shouldn't be an issue.
3. **Token must match the configured mint** — use a token from `testnut.cashu.exchange` (the test mint configured on CobradorWave routers). Tokens from other mints will fail validation with CU102 or CU109.

## Local verification (already done)

| Check | Result |
|---|---|
| `npm run build` | ✅ exit 0 (357 KB JS, 118 KB gzipped) |
| Prehydrate `?token=` URL → input populated | ✅ input length 40 |
| validateToken stub → AccessGranted renders | ✅ |
| `/usage` mock → live usage panel | ✅ `"527.3 KiB remaining of 585.9 KiB"`, bar at `90%` |
| Screenshot | ✅ `/tmp/issue5-screenshots/1-live-usage-stats.png` |

Local verification used a mocked `validateToken` (bypassed real Cashu decoding). Hardware sign-off uses a real token from the test mint — the authoritative test.

## What this PR closes

| Issue/PR | How |
|---|---|
| #5 (No Status Page) | `1246a19` + `62de72d` — live usage display in AccessGranted |
| #18 (TIP-03 URL token) | `c94c2aa` — prehydrate + raw-token POST |
| #22 (our #5 fix) | `62de72d` — merged into this branch |
| #26 (V4 Cashu converter) | `3476c12` — V4→V3 conversion |
