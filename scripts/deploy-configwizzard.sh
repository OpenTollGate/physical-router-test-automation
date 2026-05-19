#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Deploy configurationwizzard SPA to router
#
# Usage:  ./scripts/deploy-configwizzard.sh <router_host> [repo_path]
#
# Builds admin + portal, deploys to router, installs rpcd plugin, restarts services
# ---------------------------------------------------------------------------

ROUTER="${1:?Usage: $0 <router_host> [repo_path]}"
REPO="${2:-/tmp/configurationwizzard}"
SSH="ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new root@$ROUTER"
SCP="scp -O -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

echo "Building configurationwizzard from $REPO..."
cd "$REPO"
npm run build

echo "Deploying admin SPA..."
$SSH "mkdir -p /www/net4sats"
$SCP -r dist/admin/* root@$ROUTER:/www/net4sats/

echo "Deploying captive portal SPA..."
$SSH "mkdir -p /etc/nodogsplash/htdocs"
$SCP -r dist/portal/* root@$ROUTER:/etc/nodogsplash/htdocs/

echo "Installing rpcd plugin..."
$SSH "mkdir -p /usr/libexec/rpcd /usr/share/rpcd/acl.d"
$SCP openwrt/rpcd/tollgate root@$ROUTER:/usr/libexec/rpcd/tollgate
$SCP openwrt/rpcd/tollgate_acl.json root@$ROUTER:/usr/share/rpcd/acl.d/tollgate.json
$SSH "chmod +x /usr/libexec/rpcd/tollgate"

echo "Restarting rpcd..."
$SSH "/etc/init.d/rpcd restart"

echo "Configuring uhttpd..."
$SCP openwrt/files/etc/config/uhttpd_net4sats root@$ROUTER:/etc/config/uhttpd_net4sats
$SSH "/etc/init.d/uhttpd restart"

echo "Restarting NoDogSplash..."
$SSH "/etc/init.d/nodogsplash restart 2>/dev/null || echo 'nodogsplash not installed'" || true

echo ""
echo "Deploy complete!"
echo "  Admin:  http://$ROUTER/"
echo "  Portal: http://$ROUTER:2050/ (via NoDogSplash)"
