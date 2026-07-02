#!/usr/bin/env bash
# =============================================================================
# vps-daily-smoke.sh — Headless daily smoke test of the TollGate Go backend
#                       against an OpenWrt QEMU VM running on this VPS.
#
# Runs on: VPS1 (root@23.182.128.51). The OpenWrt VM (10.99.100.1) is reachable
# only from this host (private bridge tg-poc-br).
#
# What it does (idempotent, self-bootstrapping):
#   1. Ensure host deps (sshpass, python modules) + the test repo are present.
#   2. Ensure the OpenWrt VM is running (boot from existing overlay if down).
#   3. Normalize the VM root password to a fixed value via the serial console.
#   4. Deploy the latest Go backend (branch main) to the VM.
#   5. Run the API-tier pytest smoke suite against the VM.
#   6. Emit a one-line result summary + write a full log + JSON status.
#
# Usage:  vps-daily-smoke.sh [--branch <git-branch>] [--no-deploy] [--no-test]
#
# Exit codes: 0 = tests passed; non-zero = a stage failed (see log).
# =============================================================================
set -euo pipefail

# ---- config -----------------------------------------------------------------
WORKDIR="/root/tollgate-virtual-lab"
VM_IP="10.99.100.1"
VM_MAC="52:54:00:12:34:56"
FIXED_PW="tollgate-smoke-2026"          # deterministic; reset via serial each run
REPO_URL="https://github.com/OpenTollGate/physical-router-test-automation.git"
# Prefer the existing checkout on VPS1; fall back to a fresh clone.
if [[ -d "/opt/tollgate-test/.git" ]]; then
  REPO_DIR="/opt/tollgate-test"
else
  REPO_DIR="/root/physical-router-test-automation"
fi
BACKEND_BRANCH="${TOLLGATE_BRANCH:-main}"
DEPLOY=1
TEST=1
LOG_DIR="/var/log/tollgate-smoke"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/smoke-$TS.log"
STATUS_JSON="$LOG_DIR/last-status.json"
SERIAL_SOCK="$WORKDIR/run/serial.sock"
OVERLAY="$WORKDIR/overlays/tollgate-poc.qcow2"
BASE_IMG="$WORKDIR/images/openwrt-base.qcow2"
PIDFILE="$WORKDIR/run/tollgate.pid"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BACKEND_BRANCH="$2"; shift 2;;
    --no-deploy) DEPLOY=0; shift;;
    --no-test) TEST=0; shift;;
    -h|--help) grep '^#' "$0" | head -30; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

mkdir -p "$LOG_DIR" "$WORKDIR/run" "$WORKDIR/overlays"
exec > >(tee -a "$LOG") 2>&1
echo "============================================================"
echo "TollGate daily smoke — $(date -u +%FT%TZ)  branch=$BACKEND_BRANCH"
echo "============================================================"

PASS=0
FAIL_REASON=""

emit_status() { # exit_code pass_bool reason
  printf '{"ts":"%s","branch":"%s","pass":%s,"reason":"%s","log":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$BACKEND_BRANCH" "$2" "${3//\"/\\\"}" "$LOG" > "$STATUS_JSON"
  echo "RESULT: pass=$2 reason=$3 log=$LOG"
}

# ---- stage 0: host deps -----------------------------------------------------
echo "[0] ensuring host dependencies..."
export DEBIAN_FRONTEND=noninteractive
command -v sshpass >/dev/null || apt-get update -qq && apt-get install -y -qq sshpass >/dev/null 2>&1 || true
command -v qemu-system-x86_64 >/dev/null || apt-get install -y -qq qemu-system-x86 >/dev/null 2>&1 || true
python3 -m pip install -q --break-system-packages paramiko pytest pytest-timeout requests >/dev/null 2>&1 || \
  python3 -m pip install -q paramiko pytest pytest-timeout requests >/dev/null 2>&1 || \
  apt-get install -y -qq python3-paramiko python3-pytest python3-requests >/dev/null 2>&1 || true

# ---- stage 0b: test repo ----------------------------------------------------
echo "[0b] ensuring test repo..."
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --depth 1 "$REPO_URL" "$REPO_DIR" || { FAIL_REASON="repo-clone-failed"; emit_status 1 0 "$FAIL_REASON"; exit 1; }
else
  git -C "$REPO_DIR" pull -q --ff-only || true
fi

# ---- stage 1: ensure OpenWrt VM running ------------------------------------
echo "[1] checking OpenWrt VM..."
vm_running() {
  [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}
start_vm() {
  echo "    VM not running — booting from existing overlay..."
  # Bring up the private bridge + tap if absent (mirrors virtual-lab.py start_poc).
  ip link show tg-poc-br >/dev/null 2>&1 || {
    ip link add name tg-poc-br type bridge
    ip link set tg-poc-br up
    ip addr add 10.99.100.2/24 dev tg-poc-br 2>/dev/null || true
  }
  ip link show tg-poc-tap >/dev/null 2>&1 || {
    ip tuntap add dev tg-poc-tap mode tap user root
    ip link set tg-poc-tap master tg-poc-br
    ip link set tg-poc-tap up
  }
  iptables -C FORWARD -i tg-poc-br -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i tg-poc-br -j ACCEPT
  iptables -C FORWARD -o tg-poc-br -j ACCEPT 2>/dev/null || iptables -I FORWARD 2 -o tg-poc-br -j ACCEPT
  iptables -t nat -C POSTROUTING -s 10.99.100.0/24 ! -o tg-poc-br -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -s 10.99.100.0/24 ! -o tg-poc-br -j MASQUERADE
  # Boot OpenWrt from the existing overlay (idempotent base already prepared).
  setsid qemu-system-x86_64 -enable-kvm -m 256 -smp 1 -nographic \
    -serial "unix:$SERIAL_SOCK,server,nowait" \
    -monitor "unix:$WORKDIR/run/monitor.sock,server,nowait" \
    -drive "file=$OVERLAY,if=virtio,format=qcow2" \
    -netdev "tap,id=lan,ifname=tg-poc-tap,script=no,downscript=no" \
    -device "virtio-net-pci,netdev=lan,mac=$VM_MAC" \
    >"$WORKDIR/run/qemu.stdout" 2>"$WORKDIR/run/qemu.stderr" &
  echo $! > "$PIDFILE"
  echo "    VM booting (pid=$(cat "$PIDFILE")); waiting for 10.99.100.1..."
  for i in $(seq 1 60); do
    ping -c1 -W1 "$VM_IP" >/dev/null 2>&1 && { echo "    VM reachable after ${i}s"; break; }
    sleep 1
  done
}

if ! vm_running; then start_vm; fi
# wait for ICMP regardless
for i in $(seq 1 30); do
  ping -c1 -W1 "$VM_IP" >/dev/null 2>&1 && break
  sleep 1
done
ping -c1 -W2 "$VM_IP" >/dev/null 2>&1 || { FAIL_REASON="vm-not-reachable"; emit_status 1 0 "$FAIL_REASON"; exit 1; }

# ---- stage 2: normalize VM root password via serial console -----------------
echo "[2] resetting VM root password via serial console..."
RESET="$WORKDIR/reset-openwrt-password.py"
if [[ -f "$RESET" ]]; then
  python3 "$RESET" "$SERIAL_SOCK" "$FIXED_PW" 2>&1 | tail -2 || true
else
  echo "    (reset script absent — relying on existing auth)"
fi
sleep 2

# ---- stage 3: verify SSH ----------------------------------------------------
echo "[3] verifying SSH to VM..."
ssh_ok=0
for i in $(seq 1 20); do
  if sshpass -p "$FIXED_PW" ssh -o ConnectTimeout=4 -o StrictHostKeyChecking=no \
       -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "root@$VM_IP" 'echo SSH_OK' 2>/dev/null | grep -q SSH_OK; then
    ssh_ok=1; echo "    SSH OK after ${i} tries"; break
  fi
  sleep 2
done
if [[ $ssh_ok -ne 1 ]]; then
  FAIL_REASON="ssh-to-vm-failed"; emit_status 1 0 "$FAIL_REASON"; exit 1
fi

export TOLLGATE_SSH_PASSWORD="$FIXED_PW"
export TOLLGATE_SSH_HOST="$VM_IP"
export TOLLGATE_BACKEND="go"

# ---- stage 4: deploy Go backend --------------------------------------------
if [[ $DEPLOY -eq 1 ]]; then
  echo "[4] deploying Go backend (branch=$BACKEND_BRANCH)..."
  if ( cd "$REPO_DIR" && bash scripts/deploy-ci.sh "$BACKEND_BRANCH" '' "$VM_IP" ); then
    echo "    deploy: OK"
  else
    FAIL_REASON="deploy-failed"; emit_status 1 0 "$FAIL_REASON"; exit 1
  fi
else
  echo "[4] deploy skipped (--no-deploy)"
fi

# ---- stage 5: run API smoke tests ------------------------------------------
if [[ $TEST -eq 1 ]]; then
  echo "[5] running API smoke tests (pytest -m api)..."
  # The router fixture reads TOLLGATE_SSH_HOST (no --router flag exists);
  # TOLLGATE_VIRTUAL_LAB=1 skips the routers.json inventory lookup.
  set +e
  ( cd "$REPO_DIR" && \
      TOLLGATE_SSH_HOST="$VM_IP" TOLLGATE_SSH_PASSWORD="$FIXED_PW" \
      TOLLGATE_VIRTUAL_LAB=1 TOLLGATE_BACKEND=go \
      python3 -m pytest -m api -q --tb=short --no-header 2>&1 ) | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
  set -e
  if [[ $rc -eq 0 ]]; then
    echo "    tests: PASS (exit 0)"
    emit_status 0 1 "tests-passed"
    exit 0
  else
    FAIL_REASON="tests-failed-exit-$rc"; emit_status "$rc" 0 "$FAIL_REASON"
    exit "$rc"
  fi
fi

emit_status 0 1 "deploy-only-ok"
