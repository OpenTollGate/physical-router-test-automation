#!/usr/bin/env bash
# router-vm-smoke.sh — run a chosen TollGate suite against the ephemeral local
# QEMU lab (OpenWrt + Debian client + fakewallet mint).
#
# Invoked by CI on the lab host. Selectable at run time:
#   BACKEND  go | go-cdk | rust-basic | rust      (default: go)
#   TIER     smoke | critical | extended | all     (default: smoke)
#   PUBLISH  true | false                          (default: false)
#   TOLLGATE_BINARY  path to a tollgate-wrt (x86_64) to deploy into the VM
#                    (default: ~/tollgate-virtual-lab/tollgate-wrt if present)
set -euo pipefail

BACKEND="${BACKEND:-go}"
TIER="${TIER:-smoke}"
PUBLISH="${PUBLISH:-false}"
REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO"

case "$TIER" in
  smoke)    MARK=smoke ;;
  critical) MARK=critical ;;
  extended) MARK=extended ;;
  all)      MARK="" ;;
  *) echo "router-vm-smoke: bad TIER '$TIER'"; exit 2 ;;
esac

# shellcheck disable=SC1090
source "$HOME/.tollgate-test-venv/bin/activate" 2>/dev/null || true
export PATH="$HOME/.local/bin:$PATH"
mkdir -p results

VM_IP=10.99.99.1
MINT=http://10.99.99.2:8383
BIN="${TOLLGATE_BINARY:-$HOME/tollgate-virtual-lab/tollgate-wrt}"
PASSWORD="${TOLLGATE_VIRTUAL_LAB_PASSWORD:-$(jq -r .password credentials/virtual-lab-credentials.json 2>/dev/null || echo tollgate)}"
SSHOPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
vm_ssh() { sshpass -p "$PASSWORD" ssh "${SSHOPTS[@]}" "root@$VM_IP" "$@"; }

RPC_PID=""
cleanup() {
  [ -n "$RPC_PID" ] && kill "$RPC_PID" 2>/dev/null || true
  python3 scripts/virtual-lab.py stop-poc --host localhost || true
}
trap cleanup EXIT

python3 scripts/virtual-lab.py start-poc --host localhost

# npub-gated issuance RPC (deliberate auth bypass).
if [ -f scripts/mint_allowlist_rpc.py ]; then
  MINT_ALLOWLIST_CONFIG="${MINT_ALLOWLIST_CONFIG:-configs/mint_allowlist.example.json}" \
  MINT_ALLOWLIST_PORT="${MINT_ALLOWLIST_PORT:-8391}" \
  MINT_ADMIN_URL="${MINT_ADMIN_URL:-}" \
    python3 scripts/mint_allowlist_rpc.py >/tmp/mint-allowlist-rpc.log 2>&1 &
  RPC_PID=$!
fi

# ── start the CDK fakewallet mint on :8383 (v0.18 flow) ─────────────────────
CDK=/opt/cdk-mintd/cdk-mintd
if [ -x "$CDK" ]; then
  if curl -sf "$MINT/v1/info" >/dev/null 2>&1; then
    echo "[router-vm] mint already up"
  else
    rm -rf /tmp/cdk-mintd-local; mkdir -p /tmp/cdk-mintd-local
    cat > /tmp/cdk-mintd-local/config.toml <<EOF
[info]
url = "$MINT/"
listen_host = "0.0.0.0"
listen_port = 8383
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
    "$CDK" --work-dir /tmp/cdk-mintd-local config init --new-mint --file /tmp/cdk-mintd-local/config.toml >/dev/null 2>&1 || true
    setsid "$CDK" --work-dir /tmp/cdk-mintd-local >/tmp/cdk-mintd-local.log 2>&1 &
    for _ in $(seq 1 20); do
      curl -sf "$MINT/v1/info" >/dev/null 2>&1 && break; sleep 1
    done
    echo "[router-vm] mint up: $(curl -sf "$MINT/v1/info" >/dev/null 2>&1 && echo yes || echo NO)"
  fi
else
  echo "[router-vm] WARN: cdk-mintd missing at $CDK -> no mint"
fi

# ── provision the TollGate daemon in the OpenWrt VM (:2121) ─────────────────
# start-poc provisions OpenWrt (network/SSH) but not the daemon; the tests need
# a live backend. Deploy the binary + procd init + config, then restart.
if [ -f "$BIN" ]; then
  echo "[router-vm] deploying backend $BIN"
  # The tests call the API with curl *on the router*; OpenWrt ships busybox
  # wget but not curl, so install it.
  vm_ssh "opkg update >/dev/null 2>&1; opkg install curl >/dev/null 2>&1" || true
  vm_ssh "/etc/init.d/tollgate-wrt stop 2>/dev/null; killall tollgate-wrt 2>/dev/null; sleep 2" || true
  cat "$BIN" | vm_ssh "cat > /usr/bin/tollgate-wrt && chmod +x /usr/bin/tollgate-wrt"
  vm_ssh 'cat > /etc/init.d/tollgate-wrt << "INIT"
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
  procd_open_instance
  procd_set_param command /usr/bin/tollgate-wrt
  procd_set_param respawn
  procd_set_param stdout 1
  procd_set_param stderr 1
  procd_close_instance
}
INIT
chmod +x /etc/init.d/tollgate-wrt'
  vm_ssh "mkdir -p /etc/tollgate && cat > /etc/tollgate/config.json << CFG
{
  \"config_version\": \"v0.0.8\",
  \"log_level\": \"info\",
  \"accepted_mints\": [{\"url\": \"$MINT\", \"min_balance\": 0, \"balance_tolerance_percent\": 0, \"price_per_step\": 1, \"price_unit\": \"sats\", \"purchase_min_steps\": 0}],
  \"profit_share\": [{\"factor\": 1, \"identity\": \"owner\"}],
  \"step_size\": 22020096,
  \"metric\": \"bytes\",
  \"reseller_mode\": false
}
CFG"
  # Lab convenience: OpenWrt's firewall would otherwise hide the backend from
  # the host/bridge. Accept input on the lab LAN (test environment only).
  vm_ssh "uci set firewall.@defaults[0].input='ACCEPT' 2>/dev/null || true; \
    uci set firewall.@defaults[0].forward='ACCEPT' 2>/dev/null || true; \
    uci add firewall rule >/dev/null 2>&1 || true; \
    uci set firewall.@rule[-1].name='Allow-tollgate-lab' 2>/dev/null || true; \
    uci set firewall.@rule[-1].src='lan'; \
    uci set firewall.@rule[-1].proto='tcp'; \
    uci set firewall.@rule[-1].dest_port='2121 8080 2050 80 443'; \
    uci set firewall.@rule[-1].target='ACCEPT'; \
    uci commit firewall 2>/dev/null || true; fw4 restart" || true
  vm_ssh "/etc/init.d/tollgate-wrt restart; sleep 6"
  # Wait until the backend is reachable FROM THE HOST (loopback readiness
  # races ahead of the LAN/firewall path that pytest actually uses).
  for _ in $(seq 1 20); do
    if curl -sf --max-time 3 "http://$VM_IP:2121/" >/dev/null 2>&1; then
      echo "[router-vm] backend reachable from host on :2121"; break
    fi
    sleep 2
  done
  curl -sf --max-time 3 "http://$VM_IP:2121/" >/dev/null 2>&1 \
    || echo "[router-vm] WARN: backend still not reachable from host"
else
  echo "[router-vm] WARN: no backend binary at $BIN — tests will fail (no :2121)"
fi

# ── router/backend env for pytest (mirrors run-local-tests.sh) ──────────────
export TOLLGATE_SSH_HOST="$VM_IP"
export TOLLGATE_SSH_PASSWORD="$PASSWORD"
export TOLLGATE_LUCI_PASSWORD="$PASSWORD"
export TOLLGATE_TEST_MINT_URL="$MINT"
export TOLLGATE_CLIENT_IP="${TOLLGATE_CLIENT_IP:-10.99.99.100}"
export TOLLGATE_CLIENT_MAC="${TOLLGATE_CLIENT_MAC:-de:54:4e:91:49:da}"
export TOLLGATE_VIRTUAL_LAB=1
export TOLLGATE_BACKEND="$BACKEND"
export TOLLGATE_ROUTER_ARCH="${TOLLGATE_ROUTER_ARCH:-x86_64}"

if [ -n "$MARK" ]; then
  python3 -m pytest -m "$MARK" tests/api -q --junitxml results/junit.xml
else
  python3 -m pytest tests/api -q --junitxml results/junit.xml
fi

if [ "$PUBLISH" = "true" ]; then
  ./scripts/publish-report.sh || true
fi
