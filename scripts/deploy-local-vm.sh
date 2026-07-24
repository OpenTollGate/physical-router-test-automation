#!/usr/bin/env bash
set -euo pipefail

# Local VM Deployment Script — deploys TollGate + NDS + captive portal to a QEMU OpenWrt VM
#
# Usage:
#   ./scripts/deploy-local-vm.sh                    # deploy to 10.99.99.1
#   ./scripts/deploy-local-vm.sh 192.168.1.100      # deploy to custom IP
#
# Idempotent: safe to re-run

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
VM_IP="${1:-10.99.99.1}"
PORTAL_BUILD_DIR="/tmp/cw-build/dist"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verify SSH connectivity
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "root@${VM_IP}" "echo 'SSH OK'" > /dev/null 2>&1; then
    log_error "Cannot connect to root@${VM_IP}. Ensure the VM is running and SSH is accessible."
    exit 1
fi

log_info "Connected to VM at ${VM_IP}"

# Function to execute SSH command
ssh_cmd() {
    ssh -o StrictHostKeyChecking=no "root@${VM_IP}" "$@"
}

# Function to copy file via SCP
scp_to_vm() {
    local src="$1"
    local dest="$2"
    scp -o StrictHostKeyChecking=no "$src" "root@${VM_IP}:${dest}"
}

log_info "Step 1: Installing nodogsplash via opkg..."
ssh_cmd "opkg update && opkg install nodogsplash" || {
    log_error "Failed to install nodogsplash"
    exit 1
}
log_success "nodogsplash installed"

log_info "Step 2: Configuring NDS..."
# Configure NDS with TollGate settings
ssh_cmd "uci delete nodogsplash.@nodogsplash[0] 2>/dev/null || true"
ssh_cmd "uci add nodogsplash nodogsplash"
ssh_cmd "uci set nodogsplash.@nodogsplash[0].enabled='1'"
ssh_cmd "uci set nodogsplash.@nodogsplash[0].gatewayname='TollGate Portal'"
ssh_cmd "uci set nodogsplash.@nodogsplash[0].gatewayinterface='br-lan'"
ssh_cmd "uci set nodogsplash.@nodogsplash[0].gatewayport='2050'"
ssh_cmd "uci delete_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 2121' 2>/dev/null || true"
ssh_cmd "uci add_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 2121'"
ssh_cmd "uci delete_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 8080' 2>/dev/null || true"
ssh_cmd "uci add_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 8080'"
ssh_cmd "uci delete_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 2050' 2>/dev/null || true"
ssh_cmd "uci add_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port 2050'"
ssh_cmd "uci commit nodogsplash"
log_success "NDS configured"

log_info "Step 3: Deploying captive portal site..."
if [[ ! -d "$PORTAL_BUILD_DIR" ]]; then
    log_error "Portal build directory not found at $PORTAL_BUILD_DIR"
    log_error "Build the captive portal first: npm run build (in cw-portal repo)"
    exit 1
fi

# Create directories
ssh_cmd "mkdir -p /etc/nodogsplash/htdocs /www/net4sats"

# Copy portal files
if [[ -d "$PORTAL_BUILD_DIR/portal" ]]; then
    ssh_cmd "rm -rf /etc/nodogsplash/htdocs/*"
    scp_to_vm "${PORTAL_BUILD_DIR}/portal/*" "/etc/nodogsplash/htdocs/"
    log_success "Portal files deployed to /etc/nodogsplash/htdocs/"
else
    log_warn "Portal directory not found at ${PORTAL_BUILD_DIR}/portal, skipping"
fi

# Copy balance files
if [[ -d "$PORTAL_BUILD_DIR/balance" ]]; then
    ssh_cmd "rm -rf /www/net4sats/*"
    scp_to_vm "${PORTAL_BUILD_DIR}/balance/*" "/www/net4sats/"
    log_success "Balance files deployed to /www/net4sats/"
else
    log_warn "Balance directory not found at ${PORTAL_BUILD_DIR}/balance, skipping"
fi

log_info "Step 4: Deploying nftables rules from PR #283..."
cat > /tmp/20-nds-enforce.nft << 'NFT'
chain nds_enforce_forward {
    type filter hook forward priority -1; policy accept
    meta nfproto ipv4 iifname "br-lan" meta mark & 0x00030000 == 0x00010000 counter drop
    meta nfproto ipv4 iifname "br-lan" meta mark & 0x00030000 == 0x00020000 counter accept
    meta nfproto ipv4 iifname "br-lan" meta mark & 0x00030000 == 0x00030000 counter accept
    meta nfproto ipv4 iifname "br-lan" oifname { "eth0", "phy0-sta0" } counter reject with icmp type port-unreachable
}
NFT

scp_to_vm "/tmp/20-nds-enforce.nft" "/etc/nftables.d/20-nds-enforce.nft"
log_success "nftables rules deployed"

log_info "Step 5: Configuring uhttpd instances..."
# Configure main uhttpd instance on port 80
ssh_cmd "uci delete uhttpd.@uhttpd[0] 2>/dev/null || true"
ssh_cmd "uci add uhttpd uhttpd"
ssh_cmd "uci set uhttpd.@uhttpd[0].home='/www'"
ssh_cmd "uci set uhttpd.@uhttpd[0].listen_http='80'"
ssh_cmd "uci set uhttpd.@uhttpd[0].listen_https=''"

# Configure net4sats uhttpd instance on port 8090
ssh_cmd "uci set uhttpd.@uhttpd[0].listen_http='80'"
ssh_cmd "uci set uhttpd.@uhttpd[0].listen_http='80 8090'"

# More uhttpd config
ssh_cmd "uci set uhttpd.@uhttpd[0].cgi_prefix='/cgi-bin'"
ssh_cmd "uci set uhttpd.@uhttpd[0].rfc2616_handler='0'"
ssh_cmd "uci commit uhttpd"
log_success "uhttpd configured for ports 80 and 8090"

log_info "Step 6: Activating nft chain and restarting services..."
ssh_cmd "nft 'delete chain inet fw4 nds_enforce_forward' 2>/dev/null || true"
ssh_cmd "nft 'add chain inet fw4 nds_enforce_forward { type filter hook forward priority -1; policy accept; }'"
ssh_cmd "nft 'add rule inet fw4 nds_enforce_forward meta nfproto ipv4 iifname \"br-lan\" meta mark & 0x00030000 == 0x00010000 counter drop'"
ssh_cmd "nft 'add rule inet fw4 nds_enforce_forward meta nfproto ipv4 iifname \"br-lan\" meta mark & 0x00030000 == 0x00020000 counter accept'"
ssh_cmd "nft 'add rule inet fw4 nds_enforce_forward meta nfproto ipv4 iifname \"br-lan\" meta mark & 0x00030000 == 0x00030000 counter accept'"
ssh_cmd "nft 'add rule inet fw4 nds_enforce_forward meta nfproto ipv4 iifname \"br-lan\" oifname { \"eth0\", \"phy0-sta0\" } counter reject with icmp type port-unreachable'"
ssh_cmd "/etc/init.d/nodogsplash restart"
ssh_cmd "/etc/init.d/uhttpd restart"
log_success "Services activated (nft chain added directly, no fw4 reload)"

log_info "Step 7: Verifying services are running..."
# Check NDS on port 2050
if ssh_cmd "netstat -tuln | grep -q ':2050 '" 2>/dev/null || ssh_cmd "ss -tuln | grep -q ':2050 '" 2>/dev/null; then
    log_success "NDS is listening on port 2050"
else
    log_error "NDS is NOT listening on port 2050"
    exit 1
fi

# Check uhttpd on port 80
if ssh_cmd "netstat -tuln | grep -q ':80 '" 2>/dev/null || ssh_cmd "ss -tuln | grep -q ':80 '" 2>/dev/null; then
    log_success "uhttpd is listening on port 80"
else
    log_error "uhttpd is NOT listening on port 80"
    exit 1
fi

# Check uhttpd on port 8090
if ssh_cmd "netstat -tuln | grep -q ':8090 '" 2>/dev/null || ssh_cmd "ss -tuln | grep -q ':8090 '" 2>/dev/null; then
    log_success "uhttpd is listening on port 8090"
else
    log_warn "uhttpd is NOT listening on port 8090 (may be optional)"
fi

# Check if tollgate is running (will be on port 2121)
if ssh_cmd "pgrep -f tollgate" > /dev/null 2>&1; then
    log_success "tollgate process is running"
elif ssh_cmd "netstat -tuln | grep -q ':2121 '" 2>/dev/null || ssh_cmd "ss -tuln | grep -q ':2121 '" 2>/dev/null; then
    log_success "Something is listening on port 2121 (likely tollgate)"
else
    log_warn "tollgate is not running on port 2121 (may need to be deployed separately)"
fi

log_success "Deployment complete! VM at ${VM_IP} is ready."
log_info "Portal: http://${VM_IP}/nodogsplash"
log_info "Balance UI: http://${VM_IP}:8090/"
log_info "TollGate API: http://${VM_IP}:2121/"

# Cleanup temp file
rm -f /tmp/20-nds-enforce.nft