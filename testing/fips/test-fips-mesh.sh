#!/usr/bin/env bash
set -euo pipefail

# FIPS Multi-Node TollGate Integration Test
#
# Orders 3 SHC VMs, builds FIPS on each, forms a mesh, tests forwarding policy.
#
# Usage: ./test-fips-mesh.sh
# Env:   SHC_API_KEY must be set
#        FIPS_BRANCH defaults to feat/tollgate-peer-policy

FIPS_BRANCH="${FIPS_BRANCH:-feat/tollgate-peer-policy}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
PASS=0
FAIL=0

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
result() { if [ "$1" = "pass" ]; then PASS=$((PASS+1)); echo "  ✓ PASS: $2"; else FAIL=$((FAIL+1)); echo "  ✗ FAIL: $3"; fi; }

# ── VM lifecycle ─────────────────────────────────────────────────────────

order_vm() {
    local hostname=$1
    python3 -c "
import sys; sys.path.insert(0,'$(realpath ../../shc-toolkit)')
from shc_toolkit.client import SHCClient
c = SHCClient()
r = c.submit_order(hostname='$hostname', package_id=81, pricing_id=245)
sids = r.get('service_ids',[])
if not sids: raise RuntimeError(f'order failed: {r}')
print(sids[0])
"
}

wait_vm() {
    local sid=$1
    python3 -c "
import sys, time; sys.path.insert(0,'$(realpath ../../shc-toolkit)')
from shc_toolkit.client import SHCClient
c = SHCClient()
for _ in range(120):
    vm = c.get_vm($sid)
    state = vm.get('provisioning_state','?')
    ips = vm.get('ips',[])
    ip = ips[0]['ip'] if ips else ''
    if state == 'ready' and ip:
        print(ip)
        sys.exit(0)
    if state in ('failed','error','cancelled'):
        sys.exit(1)
    time.sleep(5)
sys.exit(1)
"
}

inject_key() {
    local sid=$1
    python3 -c "
import sys; sys.path.insert(0,'$(realpath ../../shc-toolkit)')
from shc_toolkit.client import SHCClient
c = SHCClient()
pubkey = open('$HOME/.ssh/id_rsa.pub').read().strip()
c.apply_ssh_key_live($sid, pubkey)
"
}

cancel_vm() {
    local sid=$1
    python3 -c "
import sys; sys.path.insert(0,'$(realpath ../../shc-toolkit)')
from shc_toolkit.client import SHCClient
c = SHCClient()
c.cancel_vm($sid, immediate=True)
" 2>/dev/null || true
}

# ── FIPS setup ───────────────────────────────────────────────────────────

setup_fips() {
    local ip=$1
    log "Setting up FIPS on $ip..."
    ssh $SSH_OPTS debian@$ip 'sudo bash -s' << 'EOF'
set -e
export HOME=/root
echo "debian ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/debian
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq build-essential git curl pkg-config libssl-dev libclang-dev clang libdbus-1-dev >/dev/null 2>&1
if ! command -v cargo >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
source /root/.cargo/env
cd /opt
if [ ! -d fips ]; then
    git clone --depth 50 https://github.com/Amperstrand/fips.git
fi
cd fips
git fetch origin
git checkout origin/feat/tollgate-peer-policy -B feat/tollgate-peer-policy
cargo build --release --bin fips --bin fipsctl 2>&1 | tail -1
cp target/release/fips /usr/local/bin/
cp target/release/fipsctl /usr/local/bin/
mkdir -p /etc/fips /run/fips
chmod 0750 /run/fips
fips --version 2>&1 || fipsctl show status 2>&1 | head -1 || echo "fips installed"
EOF
}

get_npub() {
    local ip=$1
    ssh $SSH_OPTS debian@$ip 'sudo fipsctl show status 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)[\"npub\"])" 2>/dev/null || echo ""'
}

start_fips() {
    local ip=$1 peer_npub=$2 peer_ip=$3
    log "Starting FIPS on $ip (peer: $peer_npub @ $peer_ip)..."
    ssh $SSH_OPTS debian@$ip "sudo bash -c '
mkdir -p /etc/fips
cat > /etc/fips/fips.yaml << YAML
node:
  identity:
    persistent: true
tun:
  enabled: true
  name: fips0
  mtu: 1280
dns:
  enabled: true
  bind_addr: \"127.0.0.1\"
transports:
  udp:
    bind_addr: \"0.0.0.0:2121\"
    mtu: 1472
peers:
  - npub: \"$peer_npub\"
    alias: \"peer\"
    addresses:
      - transport: udp
        addr: \"$peer_ip:2121\"
    connect_policy: auto_connect
YAML

# Kill any existing FIPS
killall fips 2>/dev/null || true
sleep 1
export HOME=/root
source /root/.cargo/env
nohup /usr/local/bin/fips --config /etc/fips/fips.yaml > /tmp/fips.log 2>&1 &
sleep 5
# Check it started
if pgrep -x fips >/dev/null; then
    echo FIPS_STARTED
else
    echo FIPS_FAILED
    cat /tmp/fips.log | tail -20
fi
'" 2>&1
}

fipsctl() {
    local ip=$1
    shift
    ssh $SSH_OPTS debian@$ip "sudo fipsctl $*" 2>&1
}

ping6_mesh() {
    local from_ip=$1 target_ipv6=$2
    ssh $SSH_OPTS debian@$from_ip "ping6 -c 3 -W 2 $target_ipv6" 2>&1
}

# ── Main ─────────────────────────────────────────────────────────────────

log "=== FIPS Multi-Node TollGate Test ==="
log "Ordering 3 SHC VMs..."

SID_A=$(order_vm "tollgate-fips-a")
SID_B=$(order_vm "tollgate-fips-b")
SID_C=$(order_vm "tollgate-fips-c")
log "Ordered: A=#$SID_A B=#$SID_B C=#$SID_C"

log "Waiting for provisioning..."
inject_key $SID_A; inject_key $SID_B; inject_key $SID_C
IP_A=$(wait_vm $SID_A) || { log "FAILED: VM A provisioning"; exit 1; }
IP_B=$(wait_vm $SID_B) || { log "FAILED: VM B provisioning"; exit 1; }
IP_C=$(wait_vm $SID_C) || { log "FAILED: VM C provisioning"; exit 1; }
log "VMs ready: A=$IP_A B=$IP_B C=$IP_C"

trap 'cancel_vm $SID_A; cancel_vm $SID_B; cancel_vm $SID_C' EXIT

log "Building FIPS on all 3 VMs (parallel)..."
setup_fips $IP_A &
setup_fips $IP_B &
setup_fips $IP_C &
wait
log "FIPS built on all VMs."

# ── Generate identities ─────────────────────────────────────────────────
log "Generating FIPS identities..."
for ip in $IP_A $IP_B $IP_C; do
    ssh $SSH_OPTS debian@$ip 'sudo bash -c "
mkdir -p /etc/fips
if [ ! -f /etc/fips/fips.key ]; then
    /usr/local/bin/fipsctl keygen > /dev/null 2>&1 || true
fi
"' 2>&1
done

# Start A first (no peers yet)
start_fips_nostart() {
    local ip=$1
    ssh $SSH_OPTS debian@$ip "sudo bash -c '
mkdir -p /etc/fips
cat > /etc/fips/fips.yaml << YAML
node:
  identity:
    persistent: true
tun:
  enabled: true
  name: fips0
  mtu: 1280
dns:
  enabled: true
  bind_addr: \"127.0.0.1\"
transports:
  udp:
    bind_addr: \"0.0.0.0:2121\"
    mtu: 1472
peers: []
YAML
killall fips 2>/dev/null || true; sleep 1
export HOME=/root
nohup /usr/local/bin/fips --config /etc/fips/fips.yaml > /tmp/fips.log 2>&1 &
sleep 3
'" 2>&1
}

log "Starting FIPS daemons (no peers)..."
start_fips_nostart $IP_A
start_fips_nostart $IP_B
start_fips_nostart $IP_C
sleep 3

# Get npubs
NPUB_A=$(fipsctl $IP_A "show status" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['npub'])" 2>/dev/null || echo "")
NPUB_B=$(fipsctl $IP_B "show status" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['npub'])" 2>/dev/null || echo "")
NPUB_C=$(fipsctl $IP_C "show status" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['npub'])" 2>/dev/null || echo "")

if [ -z "$NPUB_A" ] || [ -z "$NPUB_B" ] || [ -z "$NPUB_C" ]; then
    log "FAILED: Could not get npubs. A=$NPUB_A B=$NPUB_B C=$NPUB_C"
    log "A logs:"; ssh $SSH_OPTS debian@$IP_A "sudo tail -20 /tmp/fips.log" 2>&1
    log "B logs:"; ssh $SSH_OPTS debian@$IP_B "sudo tail -20 /tmp/fips.log" 2>&1
    log "C logs:"; ssh $SSH_OPTS debian@$IP_C "sudo tail -20 /tmp/fips.log" 2>&1
    exit 1
fi
log "Identities: A=${NPUB_A:0:20}... B=${NPUB_B:0:20}... C=${NPUB_C:0:20}..."

# Get IPv6 addresses for mesh pings
IPV6_A=$(fipsctl $IP_A "show status" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['ipv6_addr'])" 2>/dev/null || echo "")
IPV6_B=$(fipsctl $IP_B "show status" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['ipv6_addr'])" 2>/dev/null || echo "")
IPV6_C=$(fipsctl $IP_C "show status" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.load(sys.stdin)['ipv6_addr'])" 2>/dev/null || echo "")
IPV6_C=$(fipsctl $IP_C "show status" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['ipv6_addr'])" 2>/dev/null || echo "")
log "IPv6: A=$IPV6_A B=$IPV6_B C=$IPV6_C"

# ── Connect mesh: B→A and C→A (star topology) ───────────────────────────
log "Connecting mesh..."
fipsctl $IP_B "connect --peer $NPUB_A --address $IP_A:2121 --transport udp" 2>&1 || true
fipsctl $IP_C "connect --peer $NPUB_A --address $IP_A:2121 --transport udp" 2>&1 || true
sleep 10

# Verify peers are authenticated
PEERS_A=$(fipsctl $IP_A "show peers" 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('peers',[])))" 2>/dev/null || echo "0")
log "Node A has $PEERS_A authenticated peers"
if [ "$PEERS_A" -lt 2 ]; then
    log "WARNING: Expected 2 peers on A, got $PEERS_A. Tests may fail."
fi

# ── Scenario 1: Default LocalOnly blocks transit ────────────────────────
log ""
log "=== Scenario 1: Default LocalOnly blocks transit ==="
log "B pings C through A (should FAIL — default policy is LocalOnly)..."
PING1=$(ping6_mesh $IP_B $IPV6_C 2>&1 || echo "PING_FAILED")
if echo "$PING1" | grep -q "0 received"; then
    result pass "Transit blocked by default LocalOnly policy"
else
    result fail "Expected transit to be blocked" "Got: $(echo $PING1 | tail -1)"
fi

# ── Scenario 2: set_peer_policy enables transit ─────────────────────────
log ""
log "=== Scenario 2: set_peer_policy enables transit ==="
log "Setting B's policy to Full on Node A..."
fipsctl $IP_A "set-peer-policy --peer $NPUB_B --policy full" 2>&1 || true
# Also need C to allow B as transit source, or at minimum A to forward
sleep 3
log "B pings C through A (should SUCCEED — policy is now Full)..."
PING2=$(ping6_mesh $IP_B $IPV6_C 2>&1 || echo "PING_FAILED")
if echo "$PING2" | grep -q "3 received"; then
    result pass "Transit allowed after set_peer_policy Full"
else
    result fail "Expected transit to work" "Got: $(echo $PING2 | tail -1)"
fi

# ── Scenario 3: Revoke transit ─────────────────────────────────────────
log ""
log "=== Scenario 3: Revoke transit ==="
log "Setting B's policy back to LocalOnly..."
fipsctl $IP_A "set-peer-policy --peer $NPUB_B --policy local_only" 2>&1 || true
sleep 3
log "B pings C through A (should FAIL — policy revoked)..."
PING3=$(ping6_mesh $IP_B $IPV6_C 2>&1 || echo "PING_FAILED")
if echo "$PING3" | grep -q "0 received"; then
    result pass "Transit blocked after revoke"
else
    result fail "Expected transit to fail" "Got: $(echo $PING3 | tail -1)"
fi

# ── Scenario 4: show_peers reports policy ──────────────────────────────
log ""
log "=== Scenario 4: show_peers reports forwarding_policy ==="
POLICY=$(fipsctl $IP_A "show peers" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in d.get('peers', []):
    if p.get('npub') == '$NPUB_B':
        print(p.get('forwarding_policy', 'MISSING'))
        sys.exit(0)
print('PEER_NOT_FOUND')
" 2>/dev/null || echo "ERROR")
if [ "$POLICY" = "local_only" ]; then
    result pass "show_peers reports forwarding_policy=local_only for B"
else
    result fail "Expected forwarding_policy=local_only" "Got: $POLICY"
fi

# ── Summary ─────────────────────────────────────────────────────────────
log ""
log "=== Results: $PASS passed, $FAIL failed ==="
log "Cleaning up VMs..."
cancel_vm $SID_A; cancel_vm $SID_B; cancel_vm $SID_C
log "Done."
exit $FAIL
