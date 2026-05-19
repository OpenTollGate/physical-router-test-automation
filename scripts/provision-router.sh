#!/usr/bin/env bash
set -euo pipefail

ROUTER_HOST=${ROUTER_HOST:-10.99.99.1}
ROUTER_PASSWORD=${ROUTER_PASSWORD:-tollgate}
TOLLGATE_BINARY=${TOLLGATE_BINARY:-/tmp/tollgate-wrt}
TOLLGATE_PACKAGING=${TOLLGATE_PACKAGING:-/tmp/tollgate-packaging.tar.gz}
MINT_URL=${MINT_URL:-https://testnut.cashu.exchange}
REDIRECT_URL=${REDIRECT_URL:-https://wallet.cashu.me/welcome}
AUTH_DELAY=${AUTH_DELAY:-8}

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 -o LogLevel=ERROR"
SSH="sshpass -p $ROUTER_PASSWORD ssh $SSH_OPTS root@$ROUTER_HOST"

WELCOME_SEARCH_PATHS=(
    "$(dirname "$0")/../tollgate-module-basic-go/packaging/files/tollgate-captive-portal-site/welcome.html"
    /tmp/welcome.html
)

step() { printf '\n==> %s\n' "$1"; }

die() { printf 'FATAL: %s\n' "$1" >&2; exit 1; }

warn() { printf 'WARN: %s\n' "$1" >&2; }

# --------------------------------------------------------------------------------

step "Verifying SSH access to router at $ROUTER_HOST"
$SSH 'echo SSH_OK' >/dev/null 2>&1 || die "Cannot SSH to root@$ROUTER_HOST — is the VM running and password '$ROUTER_PASSWORD'?"
echo "SSH OK"

# --------------------------------------------------------------------------------

step "Installing nodogsplash on router"
$SSH 'opkg update && opkg install nodogsplash' || warn "nodogsplash install may have failed (possibly already installed)"

# --------------------------------------------------------------------------------

step "Extracting packaging tar.gz on host"
PACKAGING_TMPDIR=$(mktemp -d /tmp/tollgate-provision-XXXXXX)
trap 'rm -rf "$PACKAGING_TMPDIR"' EXIT
tar xzf "$TOLLGATE_PACKAGING" -C "$PACKAGING_TMPDIR" || die "Failed to extract $TOLLGATE_PACKAGING"

PACKAGING_FILES="$PACKAGING_TMPDIR/files"
[ -d "$PACKAGING_FILES" ] || die "Expected packaging/files/ not found after extraction"

# --------------------------------------------------------------------------------

step "Deploying packaging files to router"
tar cf - -C "$PACKAGING_FILES" . | $SSH 'cd / && tar xf -'

# --------------------------------------------------------------------------------

step "Moving captive portal site to /etc/tollgate/"
$SSH 'mkdir -p /etc/tollgate/tollgate-captive-portal-site && cp -rf /tollgate-captive-portal-site/* /etc/tollgate/tollgate-captive-portal-site/ 2>/dev/null; rm -rf /tollgate-captive-portal-site'

# --------------------------------------------------------------------------------

step "Symlinking /etc/nodogsplash/htdocs -> /etc/tollgate/tollgate-captive-portal-site"
$SSH 'rm -rf /etc/nodogsplash/htdocs && ln -sf /etc/tollgate/tollgate-captive-portal-site /etc/nodogsplash/htdocs'

# --------------------------------------------------------------------------------

step "Deploying welcome.html"
WELCOME_SRC=""
for p in "${WELCOME_SEARCH_PATHS[@]}"; do
    if [ -f "$p" ]; then
        WELCOME_SRC="$p"
        break
    fi
done

if [ -n "$WELCOME_SRC" ]; then
    sshpass -p "$ROUTER_PASSWORD" scp -O $SSH_OPTS "$WELCOME_SRC" "root@$ROUTER_HOST:/etc/tollgate/tollgate-captive-portal-site/welcome.html"
    echo "welcome.html deployed from $WELCOME_SRC"
else
    warn "welcome.html not found in any search path — using the one from packaging tarball"
fi

# --------------------------------------------------------------------------------

step "Deploying tollgate-wrt binary to router /tmp"
[ -f "$TOLLGATE_BINARY" ] || die "Binary not found at $TOLLGATE_BINARY"
sshpass -p "$ROUTER_PASSWORD" scp -O $SSH_OPTS "$TOLLGATE_BINARY" "root@$ROUTER_HOST:/tmp/tollgate-wrt"
$SSH 'chmod +x /tmp/tollgate-wrt'
echo "Binary deployed ($(stat -f%z "$TOLLGATE_BINARY" 2>/dev/null || stat -c%s "$TOLLGATE_BINARY" 2>/dev/null) bytes)"

# --------------------------------------------------------------------------------

step "Symlinking /usr/bin/tollgate-wrt -> /tmp/tollgate-wrt"
$SSH 'rm -f /usr/bin/tollgate-wrt && ln -sf /tmp/tollgate-wrt /usr/bin/tollgate-wrt'

# --------------------------------------------------------------------------------

step "Running UCI defaults scripts"

$SSH 'sh /etc/uci-defaults/90-tollgate-captive-portal-symlink' || warn "90-tollgate-captive-portal-symlink exited non-zero"

$SSH 'rm -f /etc/tollgate-setup-done && sh /etc/uci-defaults/99-tollgate-setup' || warn "99-tollgate-setup exited non-zero"

# --------------------------------------------------------------------------------

step "Writing config.json to /tmp/tollgate-main-test/config.json"
$SSH "mkdir -p /tmp/tollgate-main-test && cat > /tmp/tollgate-main-test/config.json <<'CONFIGJSON'
{
  \"config_version\": \"v0.0.7\",
  \"log_level\": \"info\",
  \"accepted_mints\": [
    {
      \"url\": \"$MINT_URL\",
      \"min_balance\": 0,
      \"balance_tolerance_percent\": 0,
      \"payout_interval_seconds\": 999999,
      \"min_payout_amount\": 999999,
      \"price_per_step\": 1,
      \"price_unit\": \"sats\",
      \"purchase_min_steps\": 0
    }
  ],
  \"profit_share\": [
    { \"factor\": 0.79, \"identity\": \"owner\" },
    { \"factor\": 0.21, \"identity\": \"developer\" }
  ],
  \"step_size\": 22020096,
  \"margin\": 0.1,
  \"metric\": \"bytes\",
  \"show_setup\": true,
  \"reseller_mode\": false,
  \"upstream_detector\": {
    \"probe_timeout\": 10000000000,
    \"probe_retry_count\": 3,
    \"probe_retry_delay\": 2000000000,
    \"require_valid_signature\": true,
    \"ignore_interfaces\": [\"lo\", \"docker0\", \"br-lan\", \"hostap0\"],
    \"only_interfaces\": [],
    \"discovery_timeout\": 300000000000
  },
  \"upstream_session_manager\": {
    \"max_price_per_millisecond\": 0.002777777778,
    \"max_price_per_byte\": 0.00003725782414,
    \"trust\": {
      \"default_policy\": \"trust_all\",
      \"allowlist\": [],
      \"blocklist\": []
    },
    \"sessions\": {
      \"preferred_session_increments_milliseconds\": 60000,
      \"preferred_session_increments_bytes\": 131100000,
      \"millisecond_renewal_offset\": 10000,
      \"bytes_renewal_offset\": 131100000
    },
    \"usage_tracking\": {
      \"data_monitoring_interval\": 500000000
    }
  },
  \"redirect_url\": \"$REDIRECT_URL\",
  \"auth_delay_seconds\": $AUTH_DELAY
}
CONFIGJSON"
echo "config.json written (mint=$MINT_URL redirect=$REDIRECT_URL auth_delay=$AUTH_DELAY)"

# --------------------------------------------------------------------------------

step "Enabling and starting services"
$SSH '/etc/init.d/nodogsplash enable 2>/dev/null; /etc/init.d/nodogsplash restart' || warn "nodogsplash restart failed"
$SSH '/etc/init.d/tollgate-wrt enable 2>/dev/null; /etc/init.d/tollgate-wrt restart' || warn "tollgate-wrt restart failed"

# --------------------------------------------------------------------------------

step "Setting up CDK fakewallet mint (port 8085)"

CDK_MINTD_BINARY=${CDK_MINTD_BINARY:-/tmp/cdk-mintd}
if [ -f "$CDK_MINTD_BINARY" ]; then
  scp -O "$CDK_MINTD_BINARY" root@$ROUTER_HOST:/usr/bin/cdk-mintd 2>/dev/null || \
    warn "Failed to copy cdk-mintd binary"

  $SSH 'chmod +x /usr/bin/cdk-mintd && mkdir -p /etc/cdk-mintd'

  $SSH "cat > /etc/cdk-mintd/config.toml <<'CDKCONFIG'
[info]
url = \"http://tollgate.lan:8085\"
name = \"TollGate Test Mint\"
description = \"Local FakeWallet mint for testing\"
listen_host = \"0.0.0.0\"
listen_port = 8085
mnemonic = \"abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about\"

[ln]
ln_backend = \"fakewallet\"

[fake_wallet]
supported_units = [\"sat\"]
fee_percent = 0.0
reserve_fee_min = 0
min_delay_time = 0
max_delay_time = 1

[database]
engine = \"sqlite\"
CDKCONFIG"

  $SSH 'cat > /etc/init.d/cdk-mintd << "INIT"
#!/bin/sh /etc/rc.common
START=99
STOP=15
USE_PROCD=1
start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/cdk-mintd --config /etc/cdk-mintd/config.toml
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
INIT
chmod +x /etc/init.d/cdk-mintd
/etc/init.d/cdk-mintd enable
/etc/init.d/cdk-mintd start' || warn "cdk-mintd setup failed"

  echo "CDK mint configured on port 8085"
else
  warn "cdk-mintd binary not found at $CDK_MINTD_BINARY, skipping mint setup"
fi

# --------------------------------------------------------------------------------

step "Setting up 502 test mint (port 8086)"

$SSH 'cat > /usr/bin/mint-502-responder << "SCRIPT"
#!/bin/sh
read -t 5 LINE
echo "HTTP/1.1 502 Bad Gateway"
echo "Content-Type: application/json"
echo "Connection: close"
echo ""
echo "{\"error\":\"Bad Gateway\",\"code\":502}"
SCRIPT
chmod +x /usr/bin/mint-502-responder

cat > /etc/init.d/mint-502 << "INIT"
#!/bin/sh /etc/rc.common
START=98
STOP=14
USE_PROCD=1
start_service() {
    procd_open_instance
    procd_set_param command socat TCP-LISTEN:8086,reuseaddr,fork EXEC:/usr/bin/mint-502-responder
    procd_set_param respawn
    procd_close_instance
}
INIT
chmod +x /etc/init.d/mint-502
/etc/init.d/mint-502 enable
/etc/init.d/mint-502 start' || warn "502 mint setup failed"

# --------------------------------------------------------------------------------

step "Opening firewall for mint ports"
$SSH 'iptables -C INPUT -p tcp --dport 8085 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 8085 -j ACCEPT'
$SSH 'iptables -C INPUT -p tcp --dport 8086 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 8086 -j ACCEPT'

# --------------------------------------------------------------------------------

step "Waiting for services to bind (up to 20s)"
OK=true
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    sleep 2
    PORTS=$($SSH 'netstat -tlnp 2>/dev/null | grep -E ":(2050|2121)" || true')
    HAS_2050=$(echo "$PORTS" | grep -c ':2050' || true)
    HAS_2121=$(echo "$PORTS" | grep -c ':2121' || true)

    if [ "$HAS_2050" -ge 1 ] && [ "$HAS_2121" -ge 1 ]; then
        echo "PASS: both ports listening"
        echo "$PORTS"
        break
    fi

    if [ "$attempt" -eq 10 ]; then
        OK=false
        echo "TIMEOUT waiting for ports"
        echo "  port 2050 (tollgate backend): $([ "$HAS_2050" -ge 1 ] && echo LISTENING || echo NOT FOUND)"
        echo "  port 2121 (tollgate protocol): $([ "$HAS_2121" -ge 1 ] && echo LISTENING || echo NOT FOUND)"
        echo "--- netstat output ---"
        $SSH 'netstat -tlnp 2>/dev/null' || true
    fi
done

# --------------------------------------------------------------------------------

if $OK; then
    printf '\n========================================\n'
    printf '  PROVISION COMPLETE\n'
    printf '  Router: %s\n' "$ROUTER_HOST"
    printf '  Mint:   %s\n' "$MINT_URL"
    printf '  Redirect: %s\n' "$REDIRECT_URL"
    printf '  Ports: 2050 (backend) + 2121 (protocol)\n'
    printf '========================================\n'
else
    printf '\n========================================\n'
    printf '  PROVISION HAD ISSUES — check logs above\n'
    printf '========================================\n'
    exit 1
fi
