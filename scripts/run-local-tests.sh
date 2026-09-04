#!/usr/bin/env bash
set -euo pipefail

# Local test runner for TollGate virtual lab on ai-legion-small.
# Starts CDK mint, configures OpenWrt, runs pytest — no cloud VM needed.
# Results stay local (TOLLGATE_VM_PROVIDER=local, can_publish=False).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
WORKDIR="${HOME}/tollgate-virtual-lab"
PASSWORD="${TOLLGATE_VIRTUAL_LAB_PASSWORD:-$(jq -r .password "${REPO_DIR}/credentials/virtual-lab-credentials.json" 2>/dev/null || echo tollgate)}"

OPENWRT_IP="10.99.99.1"
DEBIAN_IP="10.99.99.100"
HOST_BRIDGE_IP="10.99.99.2"
MINT_PORT=8383
MINT_URL="http://${HOST_BRIDGE_IP}:${MINT_PORT}"

CDK_BIN="/opt/cdk-mintd/cdk-mintd"
CDK_CONFIG="/tmp/cdk-mintd-local/config.toml"
CDK_LOG="/tmp/cdk-mintd-local.log"
CDK_PID_FILE="/tmp/cdk-mintd-local.pid"
# Override with CDK_VER when a specific mint release is needed; the binaries
# themselves are installed by scripts/bake-snapshot.py / shc bootstrap.
CDK_VER="${CDK_VER:-0.18.0}"

log() { echo "[run-local] $*"; }

cdk_minor_version() {
  "${CDK_BIN}" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1 | cut -d. -f2
}

start_mint() {
  if curl -sf "${MINT_URL}/v1/info" >/dev/null 2>&1; then
    log "CDK mint already running at ${MINT_URL}"
    verify_mint_fakewallet || restart_mint
    return
  fi

  if [ ! -x "${CDK_BIN}" ]; then
    log "ERROR: CDK mint not installed at ${CDK_BIN}"
    exit 1
  fi

  log "Starting CDK V2 mint on port ${MINT_PORT}..."
  mkdir -p /tmp/cdk-mintd-local

  local cdk_minor
  cdk_minor=$(cdk_minor_version)
  if [ "${cdk_minor:-0}" -ge 18 ]; then
    # v0.18+: config lives in the mint DB ([ln] renamed to [payment_backend],
    # config.toml ignored at start; `config init --new-mint` required pre-start).
    # Fakewallet value is disposable and the mnemonic is fixed, so start from a
    # fresh work dir every time — no legacy-DB migration edge cases.
    rm -rf /tmp/cdk-mintd-local
    mkdir -p /tmp/cdk-mintd-local
    cat > /tmp/cdk-mintd-local/config.toml << EOF
[info]
url = "${MINT_URL}/"
listen_host = "0.0.0.0"
listen_port = ${MINT_PORT}
mnemonic = "env:CDK_MINTD_MNEMONIC"

[database]
engine = "sqlite"

[payment_backend]
backend = "fakewallet"

[fake_wallet]
fee_percent = 0
reserve_fee_min = 0
min_delay_time = 0
max_delay_time = 0
EOF
    export CDK_MINTD_MNEMONIC="abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    export CDK_MINTD_WORK_DIR=/tmp/cdk-mintd-local
    "${CDK_BIN}" --work-dir /tmp/cdk-mintd-local config validate \
      --file /tmp/cdk-mintd-local/config.toml || { log "ERROR: v0.18 config validation failed"; exit 1; }
    "${CDK_BIN}" --work-dir /tmp/cdk-mintd-local config init --new-mint \
      --file /tmp/cdk-mintd-local/config.toml || { log "ERROR: v0.18 config init failed"; exit 1; }
    setsid bash -c "exec ${CDK_BIN} --work-dir /tmp/cdk-mintd-local" >"${CDK_LOG}" 2>&1 &
  else
    cat > /tmp/cdk-mintd-local/config.toml << EOF
[info]
url = "${MINT_URL}/"
listen_host = "0.0.0.0"
listen_port = ${MINT_PORT}
mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

[database]
engine = "sqlite"

[ln]
ln_backend = "fakewallet"

[fake_wallet]
supported_units = ["sat"]
fee_percent = 0
reserve_fee_min = 0
min_delay_time = 0
max_delay_time = 0
EOF
    setsid bash -c "exec ${CDK_BIN} -c ${CDK_CONFIG}" >"${CDK_LOG}" 2>&1 &
  fi
  echo $! > "${CDK_PID_FILE}"

  for i in $(seq 1 15); do
    if curl -sf "${MINT_URL}/v1/info" >/dev/null 2>&1; then
      log "CDK mint ready (PID $(cat ${CDK_PID_FILE}))"
      verify_mint_fakewallet || restart_mint
      resync_backend_wallet
      return
    fi
    sleep 1
  done
  log "ERROR: CDK mint did not become healthy"
  cat "${CDK_LOG}" | tail -10
  exit 1
}

# /v1/info answering is not proof of health: a wedged cdk-mintd accepts
# quotes but its FakeWallet never settles them, which makes every
# payment test hang until the outer timeout. Probe the actual
# quote→PAID path; a healthy FakeWallet settles in ~1s.
verify_mint_fakewallet() {
  local quote state
  quote=$(curl -sf --max-time 5 -X POST "${MINT_URL}/v1/mint/quote/bolt11" \
    -H 'Content-Type: application/json' -d '{"amount":1,"unit":"sat"}' \
    | jq -r '.quote // empty' 2>/dev/null) || return 1
  [ -n "$quote" ] || return 1
  for _ in $(seq 1 6); do
    state=$(curl -sf --max-time 5 "${MINT_URL}/v1/mint/quote/bolt11/${quote}" \
      | jq -r '.state // empty' 2>/dev/null) || return 1
    [ "$state" = "PAID" ] && return 0
    sleep 2
  done
  log "WARNING: mint FakeWallet did not settle a probe quote (state=${state:-none})"
  return 1
}

resync_backend_wallet() {
  # The Go wallet caches mint state in /etc/tollgate/wallet.db. After the
  # mint work-dir is wiped between suite runs (fresh DB, same keysets), the
  # backend keeps serving kind 21023 "no-reachable-mints" even though its
  # proactive prober reports ok=true and the mint answers 200 — payments
  # still work but discovery/degraded tests cascade-fail. Restarting the
  # backend makes the wallet re-register the mint (kind 10021 within ~10s).
  # Root-caused 2026-09-04: probes ok=true + curl 200 + discovery 21023.
  sshpass -p "${PASSWORD}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    "root@${OPENWRT_IP}" "/etc/init.d/tollgate-wrt restart" >/dev/null 2>&1 || true
  sleep 8
  log "Backend wallet resync: tollgate-wrt restarted"
}

restart_mint() {
  log "Restarting wedged CDK mint..."
  if [ -f "${CDK_PID_FILE}" ]; then
    kill "$(cat ${CDK_PID_FILE})" 2>/dev/null || true
    sleep 1
  fi
  pkill -f "cdk-mintd.*cdk-mintd-local" 2>/dev/null || true
  sleep 1
  start_mint
}

stop_mint() {
  if [ -f "${CDK_PID_FILE}" ]; then
    kill "$(cat ${CDK_PID_FILE})" 2>/dev/null || true
    rm -f "${CDK_PID_FILE}"
    log "CDK mint stopped"
  fi
}

check_vms() {
  if ! sshpass -p "${PASSWORD}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${OPENWRT_IP}" "echo ok" >/dev/null 2>&1; then
    log "ERROR: OpenWrt VM not reachable at ${OPENWRT_IP}"
    log "Run: python3 scripts/virtual-lab.py start-poc --host localhost"
    exit 1
  fi
  if ! ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${DEBIAN_IP}" "echo ok" >/dev/null 2>&1; then
    log "WARNING: Debian VM not reachable at ${DEBIAN_IP} (payment tests will skip)"
  fi
  log "VMs OK"
}

configure_mint() {
  log "Configuring OpenWrt to use local mint..."
  sshpass -p "${PASSWORD}" ssh -o StrictHostKeyChecking=no "root@${OPENWRT_IP}" "
    jq '.accepted_mints = [{\"url\": \"${MINT_URL}\", \"min_balance\": 0, \"balance_tolerance_percent\": 0, \"price_per_step\": 1, \"price_unit\": \"sats\", \"purchase_min_steps\": 0}]' /etc/tollgate/config.json > /tmp/cfg.json
    mv /tmp/cfg.json /etc/tollgate/config.json
    /etc/init.d/tollgate-wrt restart
  " 2>&1 | tail -3

  for i in $(seq 1 20); do
    if sshpass -p "${PASSWORD}" ssh -o StrictHostKeyChecking=no "root@${OPENWRT_IP}" \
      "wget -qO- --timeout=3 http://127.0.0.1:2121/ 2>/dev/null | head -c 20" 2>/dev/null | grep -q "10021\|21023"; then
      log "Backend healthy with local mint"
      return
    fi
    sleep 2
  done
  log "WARNING: Backend health check timed out"
}

run_tests() {
  local test_files="$@"
  if [ -z "$test_files" ]; then
    test_files="tests/api/test_quote_persistence.py tests/api/test_lightning_backoff.py tests/api/test_payment_regression.py tests/api/test_mint_url_fuzzy.py"
  fi

  cd "${REPO_DIR}"
  source "${HOME}/.tollgate-test-venv/bin/activate"

  export TOLLGATE_SSH_HOST="${OPENWRT_IP}"
  export TOLLGATE_LUCI_PASSWORD="${PASSWORD}"
  export TOLLGATE_SSH_PASSWORD="${PASSWORD}"
  export TOLLGATE_TEST_MINT_URL="${MINT_URL}"
  export TOLLGATE_BACKEND=go
  export TOLLGATE_CLIENT_TYPE=container
  export TOLLGATE_VM_PROVIDER=local
  export 
  export TOLLGATE_CASHU_VENV=/opt/cashu-venv
  export TOLLGATE_CLIENT_IP="${DEBIAN_IP}"
  export TOLLGATE_CLIENT_MAC="de:54:4e:91:49:da"
  export CASHU_DIR=/tmp/cashu-local

  mkdir -p /tmp/cashu-local

  # Pre-trigger NDS for Debian client (required for payment tests)
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${DEBIAN_IP}" \
    "curl -s -o /dev/null --max-time 5 http://example.com" 2>/dev/null || true

  log "Running pytest: ${test_files}"
  python3 -m pytest ${test_files} -v --no-deploy --timeout=180 --timeout-method=signal --tb=short -rs "$@"
}

cleanup() {
  stop_mint
}
trap cleanup EXIT

log "=== TollGate Local Test Runner ==="
check_vms
start_mint
configure_mint
run_tests "$@"
