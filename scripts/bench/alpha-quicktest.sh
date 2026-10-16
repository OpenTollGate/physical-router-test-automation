#!/usr/bin/env bash
# alpha-quicktest.sh — one-command quick-test cycle for the COVR-X1860
# TollGate rig (tollgate-AFA5, the wired ALPHA seat on VLAN 1005).
#
#   acquire the rig lock
#     -> ACQUIRE  verify host prep on ai-legion-small (stable manual
#                 addressing on the bench NIC), ARP-pin .1 against the
#                 switch-SVI collision, verify board identity, ensure the
#                 DUT's dedicated management IP .61 exists
#     -> INSTALL  fetch + verify the feed release artifact (provenance via
#                 scripts/download-feed-release.sh), transfer ai-legion ->
#                 small -> DUT (scp -O, hash re-verified on the DUT), full
#                 reinstall (apk del + apk add — the "flash" step for this
#                 package-flow rig), with wait-for-SSH loops around both
#                 network bounces
#     -> SETUP    re-assert default route + DNS (works around
#                 OpenTollGate/tollgate-rs#29), reset wallet.db, pin
#                 accepted_mints to the fakewallet mint, restart, settle
#     -> TEST     standard e2e suite: kind:10021 health gate, mint 11 sat +
#                 send 4 sat via cdk-cli, independent netns client (fresh
#                 MAC/IP every cycle), portal prime, token POST ->
#                 kind:1022, ndsctl Authenticated, /usage allotment,
#                 internet through DUT
#     -> TEARDOWN remove the netns client rig, restore addressing, re-apply
#                 the ARP pin, verify the bench NIC is whole
#
# The teardown also runs from an EXIT/INT/TERM trap, so a failed, killed
# or Ctrl-C'd cycle still restores ai-legion-small's bench NIC.
#
# "Flash" on this rig means a full package reinstall (apk del removes the
# package, its deps, and bounces the network; apk add reinstalls from the
# verified artifact + feeds), not a sysupgrade: the DUT has no serial/
# console access on this seat and its bench network config is
# operator-managed, so an image flash cannot be recovered remotely.
# Image-based cycles need console access first — see docs/E2E-TIMINGS.md.
#
# All DUT contact after ACQUIRE goes through the DUT's dedicated secondary
# management address (default 192.168.105.61) — the OpenWrt bench switch
# still announces the DUT's primary .1 on its own VLAN 1005 SVI (collision
# the bench owner must remove); the dedicated IP sidesteps the ARP race.
#
# Usage:
#   alpha-quicktest.sh --release v0.6.0-rc6 [--no-reinstall] \
#        [--result DIR] [--lock FILE] [--keep-client]
#
# Env:  ALPHA_SMALL          jump host             (default ubuntu@192.168.13.221)
#       ALPHA_SMALL_KEY      ssh key for the jump  (default ~/.ssh/id_ed25519)
#       ALPHA_DUT_KEY        key on small for DUT  (default /home/ubuntu/.ssh/id_ed25519)
#       ALPHA_ROUTER         DUT primary address   (default 192.168.105.1)
#       ALPHA_MGMT           DUT mgmt secondary    (default 192.168.105.61)
#       ALPHA_DUT_MAC        DUT br-private MAC    (default a8:63:7d:8f:61:a8)
#       ALPHA_MINT           fakewallet mint URL   (default http://192.168.13.221:8383)
#       CDK_CLI              cdk-cli binary        (default /opt/cdk-mintd/cdk-cli)
#       ALPHA_CLIENT_IP      netns client IP       (default 192.168.105.50)
#       ALPHA_GW_IP          small's NAT secondary (default 192.168.105.2)
#
# Exit: 0 ok; 2 usage; 3 lock busy; 10 acquire; 20 install; 30 setup;
#       40 test; 50 teardown.
#
# ONE-TIME HOST PREP (ai-legion-small) — stable bench-NIC addressing so
# NetworkManager never strands the rig (DHCP profile + manual adds get
# wiped on renewal; there is no DHCP server on VLAN 1005):
#   sudo nmcli con mod bench-trunk ipv4.method manual \
#        ipv4.addresses "192.168.105.10/24,192.168.105.2/24"
#   sudo nmcli con up bench-trunk
# plus `iptables -t nat -A POSTROUTING -s 192.168.105.0/24 -j MASQUERADE`
# and net.ipv4.ip_forward=1 for the rig upstream.
#
# DEPENDENCIES (bench-owner): remove the OpenWrt switch's VLAN 1005 SVI
# that collides with the DUT's primary address (192.168.105.1). Until then
# this script's ARP pin + dedicated mgmt IP are load-bearing.
#
# BENCH-NIC EXCLUSIVITY: the TEST phase flushes enp5s0 (the "bench-trunk"
# NIC) and re-bridges it for the netns client. If that NIC ever grows
# addresses outside 192.168.105.0/24 (another lane), the cycle ABORTS
# before touching it — multi-lane fabric rule (AGENTS.md).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

RELEASE=""; NO_REINSTALL=0; KEEP_CLIENT=0
RESULT="results/alpha-quicktest"
LOCK="$HOME/.hermes/state/alpha-quicktest.lock"
SMALL="${ALPHA_SMALL:-ubuntu@192.168.13.221}"
SMALL_KEY="${ALPHA_SMALL_KEY:-$HOME/.ssh/id_ed25519}"
DUT_KEY="${ALPHA_DUT_KEY:-/home/ubuntu/.ssh/id_ed25519}"
ROUTER="${ALPHA_ROUTER:-192.168.105.1}"
MGMT="${ALPHA_MGMT:-192.168.105.61}"
DUT_MAC="${ALPHA_DUT_MAC:-a8:63:7d:8f:61:a8}"
MINT="${ALPHA_MINT:-http://192.168.13.221:8383}"
CDK_CLI="${CDK_CLI:-/opt/cdk-mintd/cdk-cli}"
CLIENT_IP="${ALPHA_CLIENT_IP:-192.168.105.50}"
GW_IP="${ALPHA_GW_IP:-192.168.105.2}"
CLIENT_NS="alpha-qt"
CLIENT_BR="alpha-qt-br"
ARCH="mipsel_24kc-ramips-mt7621"
STAGE="alpha-qt.$$"                 # per-run staging names on small/DUT
APK_NAME="tollgate-quicktest.apk"

usage() { awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1 && /^set -/ {exit}' "$0"; exit 2; }
need()  { [ -n "${!1:-}" ] || usage; }
while [ $# -gt 0 ]; do
  case "$1" in
    --release) RELEASE="${2:-}"; need RELEASE; shift 2 ;;
    --result)  RESULT="${2:-}";  need RESULT;  shift 2 ;;
    --lock)    LOCK="${2:-}";    need LOCK;    shift 2 ;;
    --no-reinstall) NO_REINSTALL=1; shift ;;
    --keep-client) KEEP_CLIENT=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done
need RELEASE

SSH_SMALL=(ssh -i "$SMALL_KEY" -o IdentitiesOnly=yes -o ProxyJump=none
           -o ConnectTimeout=20 -o ServerAliveInterval=15 "$SMALL")
SCP_SMALL=(scp -q -i "$SMALL_KEY" -o IdentitiesOnly=yes -o ProxyJump=none)
# DUT hops: accept any host key (the .1 address flaps DUT<->switch until the
# bench owner removes the SVI; board identity is verified via ubus instead).
SSH_DUT_OPTS=(-o ConnectTimeout=12 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
              -o ServerAliveInterval=10 -o ServerAliveCountMax=3)

LOCK_DIR="$(dirname "$LOCK")"
mkdir -p "$LOCK_DIR" 2>/dev/null
exec 9>"$LOCK" 2>/dev/null || { echo "cannot open lock: $LOCK" >&2; exit 3; }
RUN_DIR="$RESULT/run-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR" 2>/dev/null || { echo "cannot create $RUN_DIR" >&2; exit 3; }
TIMES="$RUN_DIR/phases.tsv"
: > "$TIMES" 2>/dev/null || { echo "cannot write $TIMES" >&2; exit 3; }

log()  { echo "[alpha-quicktest $(date +%H:%M:%S)] $*" | tee -a "$RUN_DIR/cycle.log"; }
fail() { log "FAIL($1): $2"; exit "$1"; }
phase() { printf '%s\t%s\t%s\n' "$1" "$2" "$(date +%s.%N)" >> "$TIMES"; }

# ---- DUT helpers (all through the jump host) ---------------------------------
dut_pin() { "${SSH_SMALL[@]}" "sudo -n ip neigh replace $ROUTER lladdr $DUT_MAC dev enp5s0 nud permanent" 2>/dev/null; }

dut_run() { # dut_run <addr> <local-script> [args]
  local addr="$1" script="$2" args="${3:-}"
  "${SCP_SMALL[@]}" "$script" "$SMALL:/tmp/$STAGE.sh" >/dev/null 2>&1 \
    && "${SSH_SMALL[@]}" "scp -q -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null /tmp/$STAGE.sh root@$addr:/tmp/$STAGE.sh && ssh ${SSH_DUT_OPTS[*]} root@$addr 'sh /tmp/$STAGE.sh $args'"
}

dut_alive() { # dut_alive <addr> -> 0 iff SSH answers AND it is the COVR
  local addr="$1"
  "${SSH_SMALL[@]}" "ssh ${SSH_DUT_OPTS[*]} root@$addr 'ubus call system board 2>/dev/null | grep -q COVR-X1860'" 2>/dev/null
}

wait_dut() { # wait_dut <seconds> — try .61 then pinned .1
  local deadline=$(( $(date +%s) + $1 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    dut_alive "$MGMT" && return 0
    dut_pin; dut_alive "$ROUTER" && return 0
    sleep 5
  done
  return 1
}

ensure_mgmt_ip() { # add the dedicated secondary if missing (idempotent)
  dut_pin
  "${SSH_SMALL[@]}" "ssh ${SSH_DUT_OPTS[*]} root@$ROUTER 'ip -4 addr show br-private | grep -q $MGMT || ip addr add $MGMT/24 dev br-private; ip -4 addr show br-private | grep inet'" 2>/dev/null
}

restore_bench_nic() { # idempotent teardown of the netns rig + NIC restore
  "${SSH_SMALL[@]}" "sudo -n ip netns del $CLIENT_NS 2>/dev/null; sudo -n ip link del v0 2>/dev/null; \
    sudo -n ip link set enp5s0 nomaster 2>/dev/null; sudo -n ip link del $CLIENT_BR 2>/dev/null; \
    sudo -n nmcli con up bench-trunk >/dev/null 2>&1; \
    sudo -n ip neigh del $ROUTER dev enp5s0 2>/dev/null; \
    sudo -n ip neigh replace $ROUTER lladdr $DUT_MAC dev enp5s0 nud permanent; \
    sudo -n rm -rf /etc/netns/$CLIENT_NS /tmp/$STAGE.sh /tmp/$STAGE-token.txt 2>/dev/null; \
    ip -4 addr show enp5s0 | grep -c inet"
}

TEARDOWN_OK=0
LOCK_OK=0
on_exit() {
  local rc=$?
  # Only a run that ACQUIRED the lock may restore the bench NIC: a
  # lock-busy loser (LOCK_OK=0) must never tear down the holder's rig
  # (the netns/bridge names are fixed).
  if [ "$LOCK_OK" = 1 ] && [ "$rc" -ne 0 ] && [ "$TEARDOWN_OK" != 1 ] && [ "$KEEP_CLIENT" != 1 ]; then
    log "exit trap: restoring bench NIC (cycle failed with rc=$rc)"
    restore_bench_nic >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ---- lock (after trap setup so failures release cleanly) --------------------
flock -n 9 || { echo "bench lock busy: $LOCK" >&2; exit 3; }
LOCK_OK=1
log "lock acquired: $LOCK (run dir $RUN_DIR, release $RELEASE)"

# ==== 1. ACQUIRE =============================================================
phase acquire start
log "ACQUIRE: host prep + reach DUT via $SMALL"
"${SSH_SMALL[@]}" "ip -4 addr show enp5s0 | grep -q '$GW_IP/24'" \
  || fail 10 "small is missing $GW_IP on enp5s0 — run the ONE-TIME HOST PREP in the header"
dut_pin || fail 10 "cannot ARP-pin on small (SVI-collision workaround)"
dut_alive "$ROUTER" || fail 10 "DUT not reachable via $SMALL (is tollgate-wrt installed and br-private up?)"
ensure_mgmt_ip >/dev/null || fail 10 "cannot ensure mgmt secondary $MGMT on DUT"
dut_alive "$MGMT"   || fail 10 "DUT not reachable on dedicated mgmt IP $MGMT"
"${SSH_SMALL[@]}" "ssh ${SSH_DUT_OPTS[*]} root@$MGMT 'ubus call system board | grep -E \"\\\"hostname\\\"|\\\"model\\\"\"; df -h / | tail -1'" | tee "$RUN_DIR/acquire.out"
log "ACQUIRE: ok — COVR-X1860 on dedicated mgmt $MGMT"
phase acquire end

# ==== 2. INSTALL =============================================================
phase install start
log "INSTALL: fetching feed artifact $RELEASE ($ARCH apk)"
APK="$("$HERE/../download-feed-release.sh" "$RELEASE" "$ARCH" apk "$RUN_DIR")" \
  || fail 20 "feed artifact download/verify failed"
APK_SHA="$(sha256sum "$APK" | cut -d' ' -f1)"
log "INSTALL: artifact $(basename "$APK") (${APK_SHA:0:16}...)"
"${SCP_SMALL[@]}" "$APK" "$SMALL:/tmp/$APK_NAME" || fail 20 "transfer to small failed"

log "INSTALL: staging artifact on DUT (transfer if hash mismatch)"
echo "sha256sum /tmp/$APK_NAME" > "$RUN_DIR/push.sh"
PUSHED=""
for i in 1 2 3 4 5; do
  if dut_run "$MGMT" "$RUN_DIR/push.sh" | grep -q "$APK_SHA"; then PUSHED=1; break; fi
  log "INSTALL: stage attempt $i (hash mismatch/absent — transferring)"
  "${SSH_SMALL[@]}" "scp -q -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null /tmp/$APK_NAME root@$MGMT:/tmp/$APK_NAME" \
    || { sleep 10; ensure_mgmt_ip >/dev/null 2>&1; dut_pin; }
done
[ -n "$PUSHED" ] || fail 20 "artifact staging on DUT failed after retries"

cat > "$RUN_DIR/install.sh" <<'EOF'
set -e
[ "$1" = "reinstall" ] && { apk del tollgate-wrt 2>&1 | tail -2 || true; }
echo INSTALL-SCRIPT-OK
EOF
if [ "$NO_REINSTALL" = 0 ]; then
  log "INSTALL: apk del (flash-equivalent purge; network may bounce)"
  dut_run "$MGMT" "$RUN_DIR/install.sh" reinstall | tee "$RUN_DIR/del.out" || true
  log "INSTALL: waiting for DUT SSH after purge…"
  wait_dut 180 || fail 20 "DUT did not come back after apk del"
fi
ensure_mgmt_ip >/dev/null 2>&1 || true
cat > "$RUN_DIR/add.sh" <<EOF
set -e
echo "=== route first (del dropped it) ==="
ip route replace default via $GW_IP dev br-private
printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /tmp/resolv.conf
echo "=== sha256 on DUT ==="; sha256sum /tmp/$APK_NAME
n=0; until apk update >/tmp/apkupd.log 2>&1; do n=\$((n+1)); [ \$n -ge 4 ] && { cat /tmp/apkupd.log; exit 1; }; sleep 5; done
echo "apk update ok"
n=0; until apk add --allow-untrusted /tmp/$APK_NAME 2>&1 | grep -vE "uci: Entry not found|init\.d/(rpcd|uhttpd)" | tail -4; do
  n=\$((n+1)); [ \$n -ge 4 ] && exit 1; ip route replace default via $GW_IP dev br-private; sleep 5
done
echo "=== verify deps ==="
apk info tollgate-wrt | head -1
command -v jq || exit 1
command -v ndsctl || exit 1
echo ADD-VERIFY-OK
EOF
log "INSTALL: apk add (network restarts again; tolerating bounce)"
dut_run "$MGMT" "$RUN_DIR/add.sh" | tee "$RUN_DIR/install.out" || true
if ! grep -q "ADD-VERIFY-OK" "$RUN_DIR/install.out" 2>/dev/null; then
  log "INSTALL: session bounced mid-add — waiting for DUT…"
  wait_dut 180 || fail 20 "DUT did not come back after apk add"
  ensure_mgmt_ip >/dev/null || fail 20 "cannot re-add mgmt IP after install"
  dut_run "$MGMT" "$RUN_DIR/add.sh" | tee "$RUN_DIR/install.out" \
    || fail 20 "apk add failed (see $RUN_DIR/install.out)"
fi
grep -q "ADD-VERIFY-OK" "$RUN_DIR/install.out" || fail 20 "install incomplete (deps missing — see $RUN_DIR/install.out)"
wait_dut 120 || fail 20 "DUT unreachable after install settle"
ensure_mgmt_ip >/dev/null 2>&1 || true
log "INSTALL: ok — $(grep -m1 'tollgate-wrt-' "$RUN_DIR/install.out")"
phase install end

# ==== 3. SETUP ===============================================================
phase setup start
log "SETUP: route+DNS (#29 workaround), wallet reset, mint config, restart"
cat > "$RUN_DIR/setup.sh" <<EOF
set -e
ip route replace default via $GW_IP dev br-private
printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /tmp/resolv.conf
echo "=== egress ==="; ping -c 2 -W 3 8.8.8.8 2>&1 | tail -1
echo "=== mint reachable (from the mint itself, not our echo) ==="
if wget -q -O- -T 8 $MINT/v1/info 2>/dev/null | grep -q '"pubkey"'; then echo MINT-REACHABLE; else echo MINT-UNREACHABLE; fi
cp /etc/tollgate/config.json /etc/tollgate/config.json.quicktest-bak 2>/dev/null || true
rm -f /etc/tollgate/wallet.db
jq '.accepted_mints = [{
  "url": "$MINT",
  "min_balance": 64,
  "balance_tolerance_percent": 10,
  "payout_interval_seconds": 60,
  "min_payout_amount": 128,
  "price_per_step": 1,
  "price_unit": "sat",
  "purchase_min_steps": 1
}]' /etc/tollgate/config.json > /tmp/cfg.new && mv /tmp/cfg.new /etc/tollgate/config.json
echo "=== configured mint ==="; jq -c '.accepted_mints[0].url' /etc/tollgate/config.json
/etc/init.d/tollgate-wrt restart
sleep 15
pgrep -l tollgate
EOF
OUT=$(dut_run "$MGMT" "$RUN_DIR/setup.sh") || { echo "$OUT" >&2; fail 30 "setup failed"; }
echo "$OUT" | tee "$RUN_DIR/setup.out"
echo "$OUT" | grep -q 'MINT-REACHABLE'   || fail 30 "mint not reachable from DUT (wget /v1/info failed)"
echo "$OUT" | grep -q 'tollgate-wrt' || fail 30 "daemon not running after restart"
log "SETUP: ok"
phase setup end

# ==== 4. TEST ================================================================
phase test start
log "TEST: health gate"
cat > "$RUN_DIR/health.sh" <<'EOF'
ndsctl status 2>&1 | sed -n '1,6p'
wget -q -O- -T 10 http://127.0.0.1:2121/ 2>&1 | head -c 300; echo
EOF
OUT=$(dut_run "$MGMT" "$RUN_DIR/health.sh") || fail 40 "health gate ssh failed"
echo "$OUT" | tee "$RUN_DIR/health.out"
echo "$OUT" | grep -q '"kind":10021' || fail 40 "health gate: no kind:10021 ad on :2121"
log "TEST: health gate ok (kind:10021)"

log "TEST: minting 11 sat + sending 4 sat via fakewallet"
"$CDK_CLI" mint "$MINT" 11 > "$RUN_DIR/mint11.raw" 2>&1 || { tail -3 "$RUN_DIR/mint11.raw" >&2; fail 40 "cdk-cli mint failed"; }
"$CDK_CLI" send --mint-url "$MINT" --amount 4 --include-fee 2>/dev/null \
  | grep -o 'cashuB[A-Za-z0-9_-]*' > "$RUN_DIR/token4.txt"
[ -s "$RUN_DIR/token4.txt" ] || fail 40 "cdk-cli send produced no token"
log "TEST: token ready ($(wc -c < "$RUN_DIR/token4.txt")B)"

log "TEST: netns client + portal prime + payment + internet"
"${SCP_SMALL[@]}" "$RUN_DIR/token4.txt" "$SMALL:/tmp/$STAGE-token.txt" || fail 40 "token transfer failed"
cat > "$RUN_DIR/client.sh" <<EOF
set -e
# --- exclusivity guard: this NIC must belong to the 105 lane only ---
FOREIGN=\$(ip -4 addr show enp5s0 | grep -o 'inet [0-9.]*' | awk '{print \$2}' | grep -v '^192\.168\.105\.' || true)
[ -z "\$FOREIGN" ] || { echo "REFUSING: enp5s0 carries non-105 addresses: \$FOREIGN"; exit 1; }
# --- fresh client rig every cycle: a reused (already-Authenticated) MAC
#     would consume the token and fail the gate-open race ---
ip netns del $CLIENT_NS 2>/dev/null || true
ip link del v0 2>/dev/null || true
ip link del $CLIENT_BR 2>/dev/null || true
ip link set enp5s0 nomaster 2>/dev/null || true
ip link add $CLIENT_BR type bridge; ip link set $CLIENT_BR up
ip addr flush dev enp5s0
ip link set enp5s0 master $CLIENT_BR
ip addr add 192.168.105.10/24 dev $CLIENT_BR
ip addr add $GW_IP/24 dev $CLIENT_BR
ip netns add $CLIENT_NS
ip link add v0 type veth peer name v1
ip link set v0 master $CLIENT_BR; ip link set v0 up
ip link set v1 netns $CLIENT_NS
ip netns exec $CLIENT_NS ip link set lo up
ip netns exec $CLIENT_NS ip link set v1 up
ip netns exec $CLIENT_NS ip addr add $CLIENT_IP/24 dev v1
ip netns exec $CLIENT_NS ip route add default via $ROUTER
mkdir -p /etc/netns/$CLIENT_NS
echo "nameserver 1.1.1.1" > /etc/netns/$CLIENT_NS/resolv.conf
CMAC=\$(ip netns exec $CLIENT_NS ip link show v1 | grep ether | awk '{print \$2}')
[ -n "\$CMAC" ] || { echo "cannot determine client MAC"; exit 1; }
echo "CLIENT_MAC=\$CMAC"
ip netns exec $CLIENT_NS ping -c 2 -W 2 $ROUTER 2>&1 | tail -1
echo "=== deauth any stale session for this MAC (must be unauthenticated) ==="
ssh -i $DUT_KEY ${SSH_DUT_OPTS[*]} root@$MGMT "ndsctl deauth \$CMAC" 2>/dev/null || true
echo "=== portal prime ==="
ip netns exec $CLIENT_NS curl -sS -m 10 -o /dev/null -w "portal %{http_code}\n" http://$ROUTER:2050/
echo "=== token POST ==="
ip netns exec $CLIENT_NS curl -sS -m 30 -X POST -H "Content-Type: text/plain" \
  -d @/tmp/$STAGE-token.txt -w "\nPOST %{http_code}\n" http://$ROUTER:2121/
echo "=== internet through DUT ==="
ip netns exec $CLIENT_NS curl -sS -m 10 -o /dev/null -w "net %{http_code} %{time_total}s\n" http://1.1.1.1/
echo "=== usage ==="
ip netns exec $CLIENT_NS curl -sS -m 10 http://$ROUTER:2121/usage; echo
echo "=== ndsctl auth ==="
ssh -i $DUT_KEY ${SSH_DUT_OPTS[*]} root@$MGMT "ndsctl status" 2>/dev/null | grep -A8 "\$CMAC" | head -9
EOF
OUT=$("${SCP_SMALL[@]}" "$RUN_DIR/client.sh" "$SMALL:/tmp/$STAGE.sh" && "${SSH_SMALL[@]}" "sudo -n sh /tmp/$STAGE.sh") \
  || { echo "$OUT" >&2; fail 40 "client/payment phase failed"; }
echo "$OUT" | tee "$RUN_DIR/payment.out"
echo "$OUT" | grep -q '"kind":1022'          || fail 40 "payment did not return kind:1022"
echo "$OUT" | grep -q 'POST 200'             || fail 40 "payment POST not HTTP 200"
echo "$OUT" | grep -q 'State: Authenticated' || fail 40 "client MAC not Authenticated in ndsctl"
echo "$OUT" | grep -qE '^net 30[01]'         || fail 40 "internet through DUT failed"
echo "$OUT" | grep -qE '^[0-9]+/[0-9]+'      || fail 40 "/usage did not return numbers"
log "TEST: PASS (kind:1022, authenticated, usage, internet-through-DUT)"
phase test end

# ==== 5. TEARDOWN ============================================================
phase teardown start
if [ "$KEEP_CLIENT" = 1 ]; then
  log "TEARDOWN: --keep-client, leaving netns rig on small"
else
  log "TEARDOWN: removing netns rig, restoring addressing + ARP pin"
  ADDR_COUNT="$(restore_bench_nic | tail -1)"
  if [ "$ADDR_COUNT" != "2" ]; then
    log "TEARDOWN: bench NIC has $ADDR_COUNT/2 addresses — re-running NM activation"
    "${SSH_SMALL[@]}" "sudo -n nmcli con up bench-trunk" >/dev/null 2>&1 || true
    ADDR_COUNT="$("${SSH_SMALL[@]}" "ip -4 addr show enp5s0 | grep -c inet" 2>/dev/null | tail -1)"
    [ "$ADDR_COUNT" = "2" ] || fail 50 "bench NIC not restored (expected 2 addresses, got $ADDR_COUNT)"
  fi
  log "TEARDOWN: bench NIC whole (2 addresses)"
fi
TEARDOWN_OK=1
phase teardown end

# ---- summary ----------------------------------------------------------------
python3 - "$TIMES" "$RUN_DIR/result.json" "$(basename "$RUN_DIR")" "$RELEASE" <<'PYEOF'
import sys, json
ts, order = {}, []
for line in open(sys.argv[1]):
    p, w, t = line.strip().split('\t')
    if w == 'start': ts[p] = float(t); order.append(p)
    else: ts[p] = float(t) - ts[p]
js = {
    'run': sys.argv[3], 'release': sys.argv[4],
    'phases': {p: round(ts[p], 1) for p in order},
    'total_seconds': round(sum(ts.values()), 1),
}
open(sys.argv[2], 'w').write(json.dumps(js, indent=1))
for p in order: print(f"{p}\t{js['phases'][p]:.1f}s")
print(f"TOTAL\t{js['total_seconds']:.1f}s (sum of phases)")
PYEOF
log "CYCLE COMPLETE — results in $RUN_DIR"
exit 0
