#!/usr/bin/env python3
"""FIPS multi-node TollGate forwarding policy test.

Orders 3 SHC VMs, builds FIPS, forms a mesh, tests forwarding policy.

Key learnings baked in:
  - SHC VMs kill SSH after ~3min. Builds use nohup+poll, not blocking SSH.
  - fipsctl connect uses POSITIONAL args: connect <PEER> <ADDR> <TRANSPORT>
  - B needs C's identity in identity_cache before transit routing works.
    Identities are only exchanged during direct peer connections, so we
    connect B→C briefly, then disconnect. The cache persists.
  - set_peer_policy is a control-socket command, not a fipsctl subcommand.
    We send it via nc -U /run/fips/control.sock.
  - Both B AND C must be Full for round-trip transit (request + response
    each transit A independently).

Run: python3 testing/fips/run_test.py
  KEEP_VMS=1  — don't cancel VMs after test (for debugging)
"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/src/shc-toolkit"))
from shc_toolkit.client import SHCClient

SSH = [
    "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR",
    "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=20",
]
KEY = os.path.expanduser("~/.ssh/id_rsa.pub")
PASS = FAIL = 0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def ssh_run(ip, cmd, timeout=300):
    r = subprocess.run(SSH + [f"debian@{ip}", cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def ssh_sudo(ip, script, timeout=600):
    r = subprocess.run(
        SSH + [f"debian@{ip}", "sudo bash -s"],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    output = r.stdout.strip()
    if r.returncode != 0 and not output:
        output = f"[rc={r.returncode}] [stderr={r.stderr.strip()[-300:]}]"
    return output, r.stderr.strip(), r.returncode


def fipsctl(ip, *args):
    out, err, rc = ssh_run(ip, f"sudo fipsctl {' '.join(args)} 2>/dev/null", timeout=15)
    try:
        return json.loads(out)
    except Exception:
        return {"raw": out, "error": err}


def result(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} -- {detail}")


# ── Build ─────────────────────────────────────────────────────────────

BUILD_SCRIPT = """set -e
echo 'debian ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/debian
export HOME=/root DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq build-essential git curl pkg-config libssl-dev libclang-dev clang libdbus-1-dev >/dev/null 2>&1
if ! command -v cargo >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y 2>&1 | tail -1
fi
source /root/.cargo/env
cd /opt && rm -rf fips
git clone --depth 50 https://github.com/Amperstrand/fips.git
cd fips && git fetch origin feat/tollgate-peer-policy && git checkout FETCH_HEAD -B feat/tollgate-peer-policy
cargo build --release --bin fips --bin fipsctl 2>&1 | tail -1
cp target/release/fips target/release/fipsctl /usr/local/bin/
mkdir -p /etc/fips /run/fips
echo BUILD_OK
"""


def build_fips_nohup(ip):
    """Launch build via nohup, then poll for completion.

    SHC VMs terminate SSH sessions after ~3 minutes of inactivity.
    cargo build takes ~5 minutes, so we can't use a blocking SSH call.
    """
    log(f"  Launching build on {ip} via nohup...")
    ssh_sudo(ip, f"""
cat > /tmp/build_fips.sh << 'BUILDSCRIPT'
set -e
export HOME=/root DEBIAN_FRONTEND=noninteractive
echo 'debian ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/debian
apt-get update -qq
apt-get install -y -qq build-essential git curl pkg-config libssl-dev libclang-dev clang libdbus-1-dev >/dev/null 2>&1
if ! command -v cargo >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y 2>&1 | tail -1
fi
source /root/.cargo/env
cd /opt && rm -rf fips
git clone --depth 50 https://github.com/Amperstrand/fips.git
cd fips && git fetch origin feat/tollgate-peer-policy && git checkout FETCH_HEAD -B feat/tollgate-peer-policy
cargo build --release --bin fips --bin fipsctl 2>&1 | tail -3
cp target/release/fips target/release/fipsctl /usr/local/bin/
mkdir -p /etc/fips /run/fips
echo BUILD_OK > /tmp/build_status
BUILDSCRIPT
chmod +x /tmp/build_fips.sh
rm -f /tmp/build_status
nohup bash /tmp/build_fips.sh > /tmp/build_fips.log 2>&1 &
echo BUILD_LAUNCHED
""", timeout=30)

    for attempt in range(120):
        time.sleep(15)
        out, _, _ = ssh_run(ip, "cat /tmp/build_status 2>/dev/null", timeout=10)
        if "BUILD_OK" in out:
            log(f"  Build complete on {ip} (attempt {attempt + 1})")
            return True
        # Check for failure
        out, _, _ = ssh_run(ip, "tail -5 /tmp/build_fips.log 2>/dev/null", timeout=10)
        if "error" in out.lower() and "BUILD_OK" not in out:
            # Could be a transient cargo message; only fail if build_status never appears
            pass
        if attempt % 4 == 0:
            log(f"  Still building on {ip}... ({attempt * 15}s)")

    log(f"  Build TIMEOUT on {ip}")
    out, _, _ = ssh_run(ip, "tail -20 /tmp/build_fips.log 2>/dev/null", timeout=10)
    log(f"  Last log: {out[-300:]}")
    return False


# ── FIPS config ───────────────────────────────────────────────────────

FIPS_CONFIG = """node:
  identity:
    persistent: true
tun:
  enabled: true
  name: fips0
  mtu: 1280
dns:
  enabled: true
  bind_addr: "127.0.0.1"
transports:
  udp:
    bind_addr: "0.0.0.0:2121"
    mtu: 1472
peers: []
"""


def start_fips(ip):
    ssh_sudo(ip, f"""
mkdir -p /etc/fips /run/fips
cat > /etc/fips/fips.yaml << 'YAML'
{FIPS_CONFIG}YAML
killall fips 2>/dev/null || true; sleep 1
export HOME=/root; source /root/.cargo/env 2>/dev/null || true
nohup /usr/local/bin/fips --config /etc/fips/fips.yaml > /tmp/fips.log 2>&1 &
sleep 4
pgrep -x fips >/dev/null && echo FIPS_RUNNING || (echo FIPS_FAILED; tail -10 /tmp/fips.log)
""")


def set_peer_policy(ip_a, npub, policy):
    """Set forwarding policy via raw control socket (no fipsctl subcommand)."""
    cmd = (
        f"echo '{{\"command\":\"set_peer_policy\",\"params\":{{\"npub\":\"{npub}\","
        f"\"policy\":\"{policy}\"}}}}' | sudo nc -U /run/fips/control.sock 2>/dev/null"
    )
    out, _, _ = ssh_run(ip_a, cmd, timeout=10)
    try:
        resp = json.loads(out)
        return resp.get("status") == "ok"
    except Exception:
        return False


def ping_test(ip_from, ipv6_dest, count=5):
    """Returns True if all/most pings succeeded (transit works)."""
    out, _, _ = ssh_run(ip_from, f"ping6 -c {count} -W 3 {ipv6_dest}", timeout=30)
    if f"{count} received" in out or f"{count - 1} received" in out:
        return True, out
    return False, out


# ── Main ──────────────────────────────────────────────────────────────

log("=== FIPS Multi-Node TollGate Forwarding Policy Test ===")
c = SHCClient()
pubkey = open(KEY).read().strip()

log("Ordering 3 SHC VMs...")
sids = {}
for name in ["fips-a", "fips-b", "fips-c"]:
    r = c.submit_order(hostname=f"tollgate-{name}", package_id=81, pricing_id=245)
    sid = r["service_ids"][0]
    sids[name] = sid
    log(f"  {name}: #{sid}")

try:
    log("Waiting for provisioning + injecting keys...")
    ips = {}
    for name, sid in sids.items():
        for _ in range(90):
            vm = c.get_vm(sid)
            state = vm.get("provisioning_state", "?")
            vm_ips = vm.get("ips", [])
            ip = vm_ips[0]["ip"] if vm_ips else ""
            if state == "ready" and ip:
                time.sleep(2)
                c.apply_ssh_key_live(sid, pubkey)
                ips[name] = ip
                log(f"  {name} ready: {ip}")
                break
            if state in ("failed", "cancelled"):
                log(f"  {name} FAILED: {state}")
                raise RuntimeError(f"{name} provisioning failed")
            time.sleep(5)
        else:
            raise RuntimeError(f"{name} timeout")

    # ── Build FIPS on all 3 VMs (nohup+poll) ──────────────────────
    log("Building FIPS on all 3 VMs (nohup+poll pattern)...")
    for name, ip in ips.items():
        ok = build_fips_nohup(ip)
        if not ok:
            raise RuntimeError(f"{name} build failed")
        log(f"  {name} build OK")

    # ── Start FIPS daemons ────────────────────────────────────────
    log("Starting FIPS daemons...")
    for name, ip in ips.items():
        out, _, _ = start_fips(ip)
        if "FIPS_RUNNING" in out:
            log(f"  {name} FIPS running")
        else:
            log(f"  {name} FIPS FAILED: {out}")
            raise RuntimeError(f"{name} FIPS start failed")

    # ── Get identities ────────────────────────────────────────────
    log("Getting identities...")
    status_a = fipsctl(ips["a"], "show status")
    status_b = fipsctl(ips["b"], "show status")
    status_c = fipsctl(ips["c"], "show status")

    npub_a = status_a.get("npub", "")
    npub_b = status_b.get("npub", "")
    npub_c = status_c.get("npub", "")
    ipv6_c = status_c.get("ipv6_addr", "")
    log(f"  A: {npub_a[:20]}...")
    log(f"  B: {npub_b[:20]}...")
    log(f"  C: {npub_c[:20]}... ipv6={ipv6_c}")

    if not all([npub_a, npub_b, npub_c, ipv6_c]):
        raise RuntimeError(
            f"Missing identities: A={npub_a} B={npub_b} C={npub_c} ipv6_c={ipv6_c}"
        )

    # ── Connect mesh: B→A, C→A ───────────────────────────────────
    # fipsctl connect uses POSITIONAL args: connect <PEER> <ADDR> <TRANSPORT>
    log("Connecting mesh (B->A, C->A)...")
    ssh_run(ips["b"], f"sudo fipsctl connect {npub_a} {ips['a']}:2121 udp")
    ssh_run(ips["c"], f"sudo fipsctl connect {npub_a} {ips['a']}:2121 udp")
    log("Waiting 20s for mesh convergence...")
    time.sleep(20)

    peers_a = fipsctl(ips["a"], "show peers")
    peer_count = len(peers_a.get("peers", []))
    log(f"Node A has {peer_count} authenticated peers")
    if peer_count < 2:
        raise RuntimeError(f"Expected 2 peers on A, got {peer_count}")

    # ── Identity exchange: B→C connect, verify, disconnect ────────
    # FIPS requires destination identity in identity_cache before transit
    # routing works. Identities are only exchanged during direct peer
    # connections. We connect B→C briefly, verify ping, then disconnect.
    # The identity_cache entry persists after disconnect.
    log("")
    log("=== Identity exchange: B->C direct connect, then disconnect ===")
    ssh_run(ips["b"], f"sudo fipsctl connect {npub_c} {ips['c']}:2121 udp")
    log("Waiting 15s for B-C handshake...")
    time.sleep(15)

    ok, ping_out = ping_test(ips["b"], ipv6_c, count=3)
    if ok:
        log("  B pings C directly: OK (identity exchange successful)")
    else:
        log(f"  B pings C directly: FAILED -- {ping_out[-100:]}")
        raise RuntimeError("Identity exchange failed")

    routing = fipsctl(ips["b"], "show routing")
    log(f"  B identity_cache_entries: {routing.get('identity_cache_entries', '?')}")
    log(f"  B coord_cache_entries: {routing.get('coord_cache_entries', '?')}")

    log("  Disconnecting B-C (identity cache will persist)...")
    ssh_run(ips["b"], f"sudo fipsctl disconnect {npub_c}")
    time.sleep(10)

    routing = fipsctl(ips["b"], "show routing")
    log(f"  After disconnect: identity_cache_entries={routing.get('identity_cache_entries', '?')}")
    peers_b = fipsctl(ips["b"], "show peers")
    log(f"  B has {len(peers_b.get('peers', []))} peer(s) (should be 1: A)")

    # ── Forwarding Policy Test Matrix ─────────────────────────────
    # Both B AND C must be Full for round-trip transit:
    #   B's outbound transits A (checked against B's policy)
    #   C's response transits A (checked against C's policy)

    log("")
    log("=" * 60)
    log("FORWARDING POLICY TEST MATRIX")
    log("=" * 60)

    # Test 1: Both Full (baseline)
    log("")
    log("TEST 1: Both Full (baseline)")
    set_peer_policy(ips["a"], npub_b, "full")
    set_peer_policy(ips["a"], npub_c, "full")
    time.sleep(2)
    ok, out = ping_test(ips["b"], ipv6_c)
    result("Both Full -> transit works", ok, out[-100:])

    # Test 2: B=LocalOnly, C=Full (block source)
    log("")
    log("TEST 2: B=LocalOnly, C=Full (block source)")
    set_peer_policy(ips["a"], npub_b, "local_only")
    set_peer_policy(ips["a"], npub_c, "full")
    time.sleep(2)
    ok, out = ping_test(ips["b"], ipv6_c)
    result("B=LocalOnly -> transit blocked", not ok, out[-100:])

    # Test 3: B=Full, C=LocalOnly (block return path)
    log("")
    log("TEST 3: B=Full, C=LocalOnly (block return path)")
    set_peer_policy(ips["a"], npub_b, "full")
    set_peer_policy(ips["a"], npub_c, "local_only")
    time.sleep(2)
    ok, out = ping_test(ips["b"], ipv6_c)
    result("C=LocalOnly -> transit blocked (return path)", not ok, out[-100:])

    # Test 4: Both LocalOnly (default, block all transit)
    log("")
    log("TEST 4: Both LocalOnly (default)")
    set_peer_policy(ips["a"], npub_b, "local_only")
    set_peer_policy(ips["a"], npub_c, "local_only")
    time.sleep(2)
    ok, out = ping_test(ips["b"], ipv6_c)
    result("Both LocalOnly -> transit blocked", not ok, out[-100:])

    # Test 5: Restore both Full (verify reversibility)
    log("")
    log("TEST 5: Restore both Full")
    set_peer_policy(ips["a"], npub_b, "full")
    set_peer_policy(ips["a"], npub_c, "full")
    time.sleep(2)
    ok, out = ping_test(ips["b"], ipv6_c)
    result("Restore Full -> transit works again", ok, out[-100:])

    # ── Summary ───────────────────────────────────────────────────
    log("")
    log("=" * 60)
    log(f"RESULTS: {PASS} passed, {FAIL} failed")
    log("=" * 60)

    if FAIL == 0:
        log("ALL TESTS PASSED -- TollGate forwarding policy verified!")
    else:
        log("SOME TESTS FAILED -- see output above")

finally:
    cleanup = os.environ.get("KEEP_VMS", "0") != "1"
    if cleanup:
        log("Cleaning up VMs...")
        for sid in sids.values():
            try:
                c.cancel_vm(sid, immediate=True)
            except Exception:
                pass
        log("Done.")
    else:
        log("KEEP_VMS=1 -- VMs left running for debugging")

    sys.exit(1 if FAIL > 0 else 0)
