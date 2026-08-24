#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Deploy configurationwizzard RELEASE tarball to router
#
# Downloads the prebuilt v1.0.2 release tarball on the workstation (router
# SSL may be broken on factory images), transfers via ssh stdin-pipe, and
# deploys: admin SPA -> /www/net4sats, rpcd plugin, patched admin JS,
# portal -> nodogsplash htdocs, uhttpd UCI (:80 main, :8080 luci, :8090 net4sats).
#
# Matches wizard deploy.go Step 7 sequence. No npm/build needed.
#
# Usage:  ./scripts/deploy-configwizzard-release.sh <router_ip> [password]
# ---------------------------------------------------------------------------

ROUTER="${1:?Usage: $0 <router_ip> [password] [tarball_url]}"
PW="${2:-tollgate}"
URL="${3:-https://github.com/felixfelix-bot/configurationwizzard/releases/download/v1.0.2/net4sats-configwiz-1.0.2.tar.gz}"
# v1.0.2 tag points at ac84f33 = latest upstream net4sats/configurationwizzard
# main HEAD (verified 2026-08-24). Upstream org's v1.0.0 release tarball is stale
# (predates PRs #20-#23). Pass a different URL as arg 3 to override.
TARBALL="/tmp/cw-release.tar.gz"

SSH="sshpass -p $PW ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive root@$ROUTER"

echo "[1/4] Downloading release tarball (workstation)..."
curl -fsSL "$URL" -o "$TARBALL"
echo "     $(du -h "$TARBALL" | cut -f1) downloaded"

echo "[2/4] Transferring to router..."
"$SSH" 'cat > /tmp/cw-release.tar.gz' < "$TARBALL"

echo "[3/4] Deploying on router..."
# Router-side deploy script (BusyBox ash safe, no command substitution)
cat << 'DEPLOY_EOF' | "$SSH" 'cat > /tmp/cw-deploy.sh && sh /tmp/cw-deploy.sh'
set -e
cd /tmp
rm -rf cw-rel && mkdir cw-rel
tar xzf cw-release.tar.gz -C cw-rel

# --- Layout auto-detection: upstream v1.0.0 (admin/, portal/) vs fork v1.0.2 (dist/admin/, dist/portal/) ---
if [ -d cw-rel/dist/admin ]; then
  ADMIN_SRC=cw-rel/dist/admin
  PORTAL_SRC=cw-rel/dist/portal
  BAL_SRC=cw-rel/dist/balance
else
  ADMIN_SRC=cw-rel/admin
  PORTAL_SRC=cw-rel/portal
  BAL_SRC=""
fi

# --- Admin SPA -> /www/net4sats ---
mkdir -p /www/net4sats
cp -r "$ADMIN_SRC"/* /www/net4sats/

# --- Balance page into admin (fork builds only; upstream v1.0.0 lacks it) ---
if [ -n "$BAL_SRC" ] && [ -f "$BAL_SRC/balance.html" ]; then
  cp "$BAL_SRC/balance.html" /www/net4sats/balance.html
fi
if [ -n "$BAL_SRC" ] && [ -d "$BAL_SRC/assets" ]; then
  mkdir -p /www/net4sats/assets
  cp -r "$BAL_SRC"/assets/. /www/net4sats/assets/ 2>/dev/null || true
fi

# --- rpcd plugin ---
mkdir -p /usr/libexec/rpcd /usr/share/rpcd/acl.d
cp cw-rel/openwrt/rpcd/tollgate /usr/libexec/rpcd/tollgate
chmod +x /usr/libexec/rpcd/tollgate
cp cw-rel/openwrt/rpcd/tollgate_acl.json /usr/share/rpcd/acl.d/tollgate.json

# --- Patch admin JS: dnsmasq 2.90 has no ubus dhcp.ipv4leases ---
for f in /www/net4sats/assets/index-*.js; do
  sed -i 's/`dhcp`,`ipv4leases`/`tollgate`,`clients`/g' "$f" 2>/dev/null || true
done

# --- Captive portal SPA -> nodogsplash htdocs ---
mkdir -p /etc/nodogsplash/htdocs
cp -r "$PORTAL_SRC"/* /etc/nodogsplash/htdocs/

# --- uhttpd UCI: main :80, luci :8080, net4sats :8090 (wizard Step 7c) ---
uci -q del_list uhttpd.main.listen_http='0.0.0.0:80' 2>/dev/null; true
uci -q del_list uhttpd.main.listen_http='[::]:80' 2>/dev/null; true
uci -q del_list uhttpd.main.listen_http='0.0.0.0:8080' 2>/dev/null; true
uci -q del_list uhttpd.main.listen_http='[::]:8080' 2>/dev/null; true
uci -q del_list uhttpd.main.listen_http='0.0.0.0:8090' 2>/dev/null; true
uci -q del_list uhttpd.main.listen_http='[::]:8090' 2>/dev/null; true

uci set uhttpd.net4sats=uhttpd
uci -q del_list uhttpd.net4sats.listen_http='0.0.0.0:8090' 2>/dev/null; true
uci add_list uhttpd.net4sats.listen_http='0.0.0.0:8090'
uci add_list uhttpd.net4sats.listen_http='[::]:8090'
uci set uhttpd.net4sats.home='/www/net4sats'
uci set uhttpd.net4sats.ubus_prefix='/ubus'
uci set uhttpd.net4sats.script_timeout='60'
uci set uhttpd.net4sats.network_timeout='30'
uci set uhttpd.net4sats.max_requests='3'
uci set uhttpd.net4sats.tcp_keepalive='1'
uci set uhttpd.net4sats.error_page='/index.html'

uci set uhttpd.luci=uhttpd
uci -q del_list uhttpd.luci.listen_http='0.0.0.0:8080' 2>/dev/null; true
uci add_list uhttpd.luci.listen_http='0.0.0.0:8080'
uci add_list uhttpd.luci.listen_http='[::]:8080'
uci set uhttpd.luci.home='/www'
uci set uhttpd.luci.cgi_prefix='/cgi-bin'
uci -q del_list uhttpd.luci.lua_prefix='/cgi-bin/luci=/usr/lib/lua/luci/sgi/uhttpd.lua' 2>/dev/null; true
uci add_list uhttpd.luci.lua_prefix='/cgi-bin/luci=/usr/lib/lua/luci/sgi/uhttpd.lua'
uci set uhttpd.luci.ubus_prefix='/ubus'
uci set uhttpd.luci.script_timeout='60'
uci set uhttpd.luci.network_timeout='30'
uci set uhttpd.luci.redirect_https='0'

uci commit uhttpd

# --- Restart services ---
/etc/init.d/rpcd restart 2>/dev/null || true
/etc/init.d/uhttpd restart 2>/dev/null || true
/etc/init.d/nodogsplash restart 2>/dev/null || echo "nodogsplash not installed (portal files staged for later)"

rm -rf /tmp/cw-rel
echo "ROUTER_DEPLOY_OK"
DEPLOY_EOF

echo "[4/4] Verifying..."
"$SSH" 'ls /www/net4sats/index.html && ls /usr/libexec/rpcd/tollgate && uci -q get uhttpd.net4sats.home' || {
  echo "VERIFY FAILED - check output above"
  exit 1
}

echo ""
echo "Deploy complete."
echo "  Admin:  http://$ROUTER:8090/"
echo "  LuCI:   http://$ROUTER:8080/"
echo "  Portal: http://$ROUTER:2050/ (once nodogsplash runs)"
