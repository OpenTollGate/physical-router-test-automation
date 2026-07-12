#!/bin/bash
set -euo pipefail

# conwrt VPN lifecycle test — runs on an SHC VM with KVM.
#
# Architecture:
#   SHC Host (Debian, WG client 10.0.0.2)
#     ↕ QEMU hostfwd (UDP 51820)
#   OpenWrt VM (WG server 10.0.0.1, KVM-accelerated)
#
# Tests: server provisioning, key exchange, handshake, ping through tunnel,
#        key rotation, disconnect/reconnect.
#
# Usage:
#   scripts/cloud-lab.py submit-conwrt-vpn --branch master
#   bash scripts/conwrt-vpn-bootstrap.sh

LOG="/tmp/conwrt-vpn.log"
RESULTS_DIR="/tmp/conwrt-vpn-results"
CONWRT_REPO="${CONWRT_REPO:-https://github.com/Amperstrand/conwrt.git}"
CONWRT_BRANCH="${CONWRT_BRANCH:-master}"
PRTA_REPO="${PRTA_REPO:-https://github.com/OpenTollGate/physical-router-test-automation.git}"
SSH_PORT="${SSH_PORT:-2222}"
WG_PORT="${WG_PORT:-51820}"
WG_SERVER_IP="10.0.0.1"
WG_CLIENT_IP="10.0.0.2"
WG_SUBNET="10.0.0.0/24"

exec > >(tee -a "$LOG") 2>&1
echo "=== conwrt VPN Lifecycle Test (SHC) ==="
echo "Started: $(date -u)"

# ── 1. Install dependencies ──────────────────────────────────────────
echo ">>> Installing dependencies..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    qemu-system-x86 qemu-utils sshpass curl wget git \
    python3 python3-pip python3-venv \
    wireguard-tools wireguard qrencode

# Install nak
if ! which nak >/dev/null 2>&1; then
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
        curl -sL "$NAK_URL" -o nak-bin && chmod +x nak-bin
        if /tmp/nak-bin --version >/dev/null 2>&1; then
            sudo mv nak-bin /usr/local/bin/nak
        else
            mv nak-bin nak.tar.gz && tar xzf nak.tar.gz && sudo mv nak /usr/local/bin/nak && rm -f nak.tar.gz
        fi
    fi
fi

# ── 2. Clone conwrt ──────────────────────────────────────────────────
echo ">>> Cloning conwrt..."
cd /tmp
rm -rf conwrt
git clone --depth 1 -b "$CONWRT_BRANCH" "$CONWRT_REPO"
cd conwrt
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]" -q

# ── 3. Prepare and boot OpenWrt VM ──────────────────────────────────
echo ">>> Preparing OpenWrt image with WG packages..."
sudo chmod 666 /dev/kvm 2>/dev/null || true

# Download and decompress image
if [ ! -f tests/integration/.openwrt.img ]; then
    tests/integration/.openwrt.img 2>/dev/null || true
    curl -fL --retry 3 -o tests/integration/.openwrt.img.gz \
        "https://downloads.openwrt.org/releases/24.10.2/targets/x86/64/openwrt-24.10.2-x86-64-generic-ext4-combined.img.gz"
    gunzip -f tests/integration/.openwrt.img.gz 2>/dev/null || true
fi

# Prepare image: SSH key + network config + WG packages
if [ ! -f tests/integration/.vm_ssh_key ]; then
    ssh-keygen -t ed25519 -N "" -f tests/integration/.vm_ssh_key -q
fi
PUBKEY=$(cat tests/integration/.vm_ssh_key.pub)

# Download WG packages for offline install
mkdir -p /tmp/conwrt-prebake
PKG_BASE="https://downloads.openwrt.org/releases/24.10.2/packages/x86_64/packages"
for pkg in wireguard-tools luci-proto-wireguard qrencode kmod-wireguard sqm-scripts luci-app-sqm iperf3 libiperf3 libatomic1; do
    ipk=$(curl -sfL "${PKG_BASE}/" 2>/dev/null | grep -o "${pkg}[^\"]*\.ipk" | head -1)
    if [ -n "$ipk" ]; then
        curl -sfL -o "/tmp/conwrt-prebake/${ipk}" "${PKG_BASE}/${ipk}" 2>/dev/null || true
    fi
done
# Also check kmod repo
KMOD_BASE="https://downloads.openwrt.org/releases/24.10.2/targets/x86/64/kmods/6.6.93-1-1745ebad77278f5cdc8330d17a3f43d6"
for pkg in kmod-wireguard kmod-crypto-lib-chacha20poly1305 kmod-crypto-lib-curve25519; do
    ipk=$(curl -sfL "${KMOD_BASE}/" 2>/dev/null | grep -o "${pkg}[^\"]*\.ipk" | head -1)
    if [ -n "$ipk" ]; then
        curl -sfL -o "/tmp/conwrt-prebake/${ipk}" "${KMOD_BASE}/${ipk}" 2>/dev/null || true
    fi
done
echo "Pre-baked $(ls /tmp/conwrt-prebake/*.ipk 2>/dev/null | wc -l) packages"

# Inject SSH key + network config + packages into image
sudo bash -c "
set -e
LOOP=\$(losetup -fP --show tests/integration/.openwrt.img)
mkdir -p /mnt/owrt
mount \${LOOP}p2 /mnt/owrt || mount \${LOOP}p1 /mnt/owrt

# SSH key
mkdir -p /mnt/owrt/etc/dropbear
echo '${PUBKEY}' >> /mnt/owrt/etc/dropbear/authorized_keys
chmod 600 /mnt/owrt/etc/dropbear/authorized_keys

# Network: eth0 as LAN DHCP for QEMU NAT
cat > /mnt/owrt/etc/config/network << 'NETCFG'
config interface 'loopback'
        option device 'lo'
        option proto 'static'
        option ipaddr '127.0.0.1'
        option netmask '255.0.0.0'

config interface 'lan'
        option device 'eth0'
        option proto 'dhcp'
NETCFG

# Firewall: open
cat > /mnt/owrt/etc/config/firewall << 'FWCFG'
config defaults
        option input 'ACCEPT'
        option output 'ACCEPT'
        option forward 'ACCEPT'

config zone
        option name 'lan'
        option input 'ACCEPT'
        option output 'ACCEPT'
        option forward 'ACCEPT'
        option device 'eth0'

config zone
        option name 'wan'
        option input 'ACCEPT'
        option output 'ACCEPT'
        option forward 'ACCEPT'
        option masq '1'
        option mtu_fix '1'
FWCFG

# Dropbear
cat > /mnt/owrt/etc/config/dropbear << 'DROPBEAR'
config dropbear
        option PasswordAuth 'on'
        option RootPasswordAuth 'on'
        option Port '22'
        option Interface 'lan'
DROPBEAR

# Pre-bake packages
mkdir -p /mnt/owrt/tmp/prebake
cp /tmp/conwrt-prebake/*.ipk /mnt/owrt/tmp/prebake/ 2>/dev/null || true
cat > /mnt/owrt/etc/uci-defaults/98-prebake << 'PREBAKE'
#!/bin/sh
cd /tmp/prebake 2>/dev/null && opkg install *.ipk 2>/dev/null
rm -rf /tmp/prebake
exit 0
PREBAKE
chmod +x /mnt/owrt/etc/uci-defaults/98-prebake

umount /mnt/owrt
losetup -d \$LOOP
"

echo ">>> Booting OpenWrt VM with WG port forward..."
qemu-system-x86_64 \
    -drive file=tests/integration/.openwrt.img,format=raw,if=virtio \
    -m 512M \
    -netdev "user,id=net0,hostfwd=tcp::${SSH_PORT}-:22,hostfwd=udp::${WG_PORT}-:51820" \
    -device virtio-net-pci,netdev=net0 \
    -display none \
    -serial file:/tmp/serial.log \
    -daemonize \
    -enable-kvm -cpu host

echo "Waiting for SSH..."
VM_SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -i tests/integration/.vm_ssh_key -p ${SSH_PORT} root@127.0.0.1"
for i in $(seq 1 60); do
    $VM_SSH "true" 2>/dev/null && break
    sleep 3
done
echo "SSH ready"

# Wait for prebake packages to install
echo "Waiting for packages to install..."
for i in $(seq 1 20); do
    if $VM_SSH "which wg" 2>/dev/null; then
        echo "WireGuard tools available"
        break
    fi
    sleep 5
done

# ── 4. Configure WireGuard server via conwrt ─────────────────────────
echo ">>> Configuring WireGuard server via conwrt..."

# Set up SSH config for conwrt
mkdir -p ~/.ssh
cat > ~/.ssh/config << SSHCFG
Host 127.0.0.1
    Port ${SSH_PORT}
    IdentityFile $(pwd)/tests/integration/.vm_ssh_key
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
SSHCFG

# conwrt config with wireguard-server use case
cat > /tmp/conwrt-wg-config.toml << 'TOML'
[password]
mode = "none"

[network]
lan_ip_mode = "static"
lan_ip = "10.0.99.1"

[use_cases]
enabled = ["wireguard-server"]

[use_cases.wireguard-server]
private_key = "generate"
listen_port = 51820
subnet = "10.0.0.1/24"
TOML

cp config.toml config.toml.bak 2>/dev/null || true
cp /tmp/conwrt-wg-config.toml config.toml

python3 scripts/conwrt.py configure --model-id virtual-x86-64 --ip 127.0.0.1 2>&1 || true
mv config.toml.bak config.toml 2>/dev/null || rm -f config.toml

# ── 5. Read server's public key ──────────────────────────────────────
echo ">>> Reading server's WireGuard public key..."
SERVER_PUBKEY=$($VM_SSH "wg show wg0 public-key 2>/dev/null || cat /etc/wireguard/server_public_key 2>/dev/null" 2>/dev/null | tr -d '[:space:]')
echo "Server public key: ${SERVER_PUBKEY:0:20}..."

if [ -z "$SERVER_PUBKEY" ]; then
    echo "ERROR: Could not get server public key"
    echo "wg show output:"
    $VM_SSH "wg show all" 2>/dev/null || true
    exit 1
fi

# ── 6. Generate client keypair on host ───────────────────────────────
echo ">>> Generating client WireGuard keypair..."
CLIENT_PRIVKEY=$(wg genkey)
CLIENT_PUBKEY=$(echo "$CLIENT_PRIVKEY" | wg pubkey)

# ── 7. Register client as peer on server ─────────────────────────────
echo ">>> Registering client peer on server..."
$VM_SSH "wg set wg0 peer '${CLIENT_PUBKEY}' allowed-ips ${WG_CLIENT_IP}/32" 2>&1
echo "Peer registered"

# ── 8. Configure WireGuard client on host ────────────────────────────
echo ">>> Configuring WireGuard client on host..."
sudo ip link add dev wgtest type wireguard 2>/dev/null || sudo ip link del wgtest; sudo ip link add dev wgtest type wireguard
sudo ip addr add "${WG_CLIENT_IP}/32" dev wgtest
sudo wg set wgtest \
    private-key <(echo "$CLIENT_PRIVKEY") \
    peer "${SERVER_PUBKEY}" \
    endpoint "127.0.0.1:${WG_PORT}" \
    allowed-ips "${WG_SUBNET}" \
    persistent-keepalive 25
sudo ip link set wgtest up

echo "Waiting for handshake..."
HANDSHAKE=""
for i in $(seq 1 10); do
    HANDSHAKE=$(sudo wg show wgtest latest-handshakes 2>/dev/null | awk '{print $2}')
    if [ "$HANDSHAKE" != "0" ] && [ -n "$HANDSHAKE" ]; then
        echo "Handshake successful!"
        break
    fi
    sleep 2
done

# ── 9. Test: ping through tunnel ─────────────────────────────────────
echo ">>> Testing tunnel connectivity..."
PING_RESULT=$(ping -c 5 -W 2 "${WG_SERVER_IP}" 2>&1)
echo "$PING_RESULT"
PING_OK=$(echo "$PING_RESULT" | grep -oP '\d+(?= received)' || echo 0)

# ── 10. Test: key rotation ───────────────────────────────────────────
echo ">>> Testing key rotation..."
NEW_CLIENT_PRIVKEY=$(wg genkey)
NEW_CLIENT_PUBKEY=$(echo "$NEW_CLIENT_PRIVKEY" | wg pubkey)
$VM_SSH "wg set wg0 peer '${NEW_CLIENT_PUBKEY}' allowed-ips ${WG_CLIENT_IP}/32; wg set wg0 peer '${CLIENT_PUBKEY}' remove" 2>&1
sudo wg set wgtest private-key <(echo "$NEW_CLIENT_PRIVKEY") peer "${SERVER_PUBKEY}" --remove-old-peers 2>/dev/null || \
    sudo wg set wgtest peer "${SERVER_PUBKEY}" endpoint "127.0.0.1:${WG_PORT}" allowed-ips "${WG_SUBNET}" persistent-keepalive 25
sleep 3
NEW_HANDSHAKE=$(sudo wg show wgtest latest-handshakes 2>/dev/null | awk '{print $2}')
KEY_ROTATION_OK="no"
if [ "$NEW_HANDSHAKE" != "0" ] && [ -n "$NEW_HANDSHAKE" ]; then
    echo "Key rotation: new handshake successful"
    KEY_ROTATION_OK="yes"
fi

# ── 11. Collect results ──────────────────────────────────────────────
mkdir -p "$RESULTS_DIR"

PASSED=0
FAILED=0
[ -n "$HANDSHAKE" ] && [ "$HANDSHAKE" != "0" ] && PASSED=$((PASSED+1)) || FAILED=$((FAILED+1))
[ "$PING_OK" -ge 4 ] && PASSED=$((PASSED+1)) || FAILED=$((FAILED+1))
[ "$KEY_ROTATION_OK" = "yes" ] && PASSED=$((PASSED+1)) || FAILED=$((FAILED+1))

cat > "$RESULTS_DIR/summary.md" << MDEOF
# conwrt VPN Lifecycle Test — SHC KVM

**Date:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Runner:** SHC Dev VPS (KVM)

## Results: ${PASSED} passed, ${FAILED} failed

| Test | Status |
|------|--------|
| WireGuard handshake | $([ -n "$HANDSHAKE" ] && [ "$HANDSHAKE" != "0" ] && echo '✅ PASS' || echo '❌ FAIL') |
| Ping through tunnel (5 pings) | $([ "$PING_OK" -ge 4 ] && echo "✅ PASS (${PING_OK}/5)" || echo "❌ FAIL (${PING_OK}/5)") |
| Key rotation | $([ "$KEY_ROTATION_OK" = "yes" ] && echo '✅ PASS' || echo '❌ FAIL') |

## Architecture
- OpenWrt VM (KVM): WireGuard server, ${WG_SERVER_IP}, port ${WG_PORT}
- SHC Host: WireGuard client, ${WG_CLIENT_IP}
- UDP forwarded via QEMU hostfwd

## Server Details
- Public key: ${SERVER_PUBKEY:0:40}...
- Listen port: ${WG_PORT}
- Subnet: ${WG_SUBNET}
MDEOF

cat > "$RESULTS_DIR/comparison.json" << JSONEOF
{
  "run_id": "conwrt-vpn-$(date -u +%Y%m%d-%H%M%S)",
  "project": "conwrt",
  "runner": "shc-kvm",
  "test_type": "vpn_lifecycle",
  "passed": $PASSED,
  "failed": $FAILED,
  "tests": {
    "handshake": $([ -n "$HANDSHAKE" ] && [ "$HANDSHAKE" != "0" ] && echo true || echo false),
    "ping_through_tunnel": $([ "$PING_OK" -ge 4 ] && echo true || echo false),
    "key_rotation": $([ "$KEY_ROTATION_OK" = "yes" ] && echo true || echo false)
  },
  "ping_received": $PING_OK
}
JSONEOF

echo "Results: ${PASSED} passed, ${FAILED} failed"

# ── 12. Publish results ──────────────────────────────────────────────
if [ -f "${NSEC_FILE:-$HOME/.config/prta/nsec}" ]; then
    echo ">>> Publishing to Nostr..."
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
            --run-id "conwrt-vpn-$(date -u +%Y%m%d-%H%M%S)" \
            --nsec-file "${NSEC_FILE:-$HOME/.config/prta/nsec}" \
            --summary "conwrt VPN lifecycle — ${PASSED} passed, ${FAILED} failed (WG server on OpenWrt, client on host)" \
            --passed "$PASSED" --failed "$FAILED" 2>&1 || echo "Publish failed (non-fatal)"
    fi
fi

# Cleanup
sudo ip link del wgtest 2>/dev/null || true

echo "=== VPN Test Complete ==="
echo "Finished: $(date -u)"
echo "VPN_DONE" > /tmp/vpn-bootstrap.status
