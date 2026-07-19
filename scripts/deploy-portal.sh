#!/usr/bin/env bash
set -euo pipefail

# Deploy a freshly-built captive portal SPA to a running OpenWrt VM.
#
# Usage:
#   ./scripts/deploy-portal.sh [--build-dir <path>] [--shc-ip <ip>]
#                              [--openwrt-ip <ip>] [--openwrt-user <user>]
#                              [--no-build]
#
# Defaults (resolvable from .env / .env.rust-shc):
#   --build-dir     ../tollgate-captive-portal-site  (sibling repo)
#   --shc-ip        $TOLLGATE_SSH_HOST                (SHC Debian host, e.g. 66.92.204.240)
#   --openwrt-ip    10.99.99.1                         (QEMU OpenWrt inside SHC VM)
#   --openwrt-user  root
#
# Required env (one of):
#   TOLLGATE_SSH_KEY         SSH key for the SHC host (e.g. ~/.ssh/id_ed25519)
#   TOLLGATE_LUCI_PASSWORD   OpenWrt root password (used via sshpass inside SHC host)
#
# What it does:
#   1. Builds the SPA (npm run build) unless --no-build
#   2. Tars the build/ output (splash.html, balance.html, assets/, locales/, ...)
#   3. SCRs the tarball to the SHC host
#   4. From the SHC host, scp's it to the OpenWrt VM (/tmp/portal-deploy.tar.gz)
#   5. Extracts into /www/ on the OpenWrt VM (overwrites existing splash.html, assets, etc.)
#   6. Restarts nodogsplash to pick up the new files
#   7. Verifies by curl-ing http://<openwrt-ip>:2050/splash.html from the SHC host
#
# Exit codes:
#   0  success
#   1  build or deploy failure
#   2  argument or environment error

BUILD_DIR=""
SHC_IP=""
OPENWRT_IP="10.99.99.1"
OPENWRT_USER="root"
OPENWRT_PORT=${TOLLGATE_NDS_PORT:-2050}
NO_BUILD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --build-dir)    BUILD_DIR="$2"; shift 2 ;;
    --shc-ip)       SHC_IP="$2"; shift 2 ;;
    --openwrt-ip)   OPENWRT_IP="$2"; shift 2 ;;
    --openwrt-user) OPENWRT_USER="$2"; shift 2 ;;
    --no-build)     NO_BUILD=1; shift ;;
    --help|-h)
      sed -n '3,32p' "$0"; exit 0 ;;
    --*)            echo "ERROR: unknown option: $1" >&2; exit 2 ;;
    *)              echo "ERROR: unexpected positional arg: $1" >&2; exit 2 ;;
  esac
done

# Resolve defaults from environment
[ -z "$BUILD_DIR" ] && BUILD_DIR="${TOLLGATE_PORTAL_DIR:-$(realpath "$(dirname "$0")/../../tollgate-captive-portal-site" 2>/dev/null || echo "")}"
[ -z "$SHC_IP" ]    && SHC_IP="${TOLLGATE_SSH_HOST:-}"

if [ -z "$BUILD_DIR" ] || [ ! -d "$BUILD_DIR" ]; then
  echo "ERROR: build directory not found: ${BUILD_DIR:-<unset>}" >&2
  echo "Pass --build-dir <path> or set TOLLGATE_PORTAL_DIR" >&2
  exit 2
fi

if [ -z "$SHC_IP" ]; then
  echo "ERROR: SHC host IP not set" >&2
  echo "Pass --shc-ip <ip> or set TOLLGATE_SSH_HOST" >&2
  exit 2
fi

SSH_KEY="${TOLLGATE_SSH_KEY:-$HOME/.ssh/id_ed25519}"
OPENWRT_PW="${TOLLGATE_LUCI_PASSWORD:-}"

if [ ! -f "$SSH_KEY" ]; then
  echo "ERROR: SSH key not found: $SSH_KEY" >&2
  echo "Set TOLLGATE_SSH_KEY to a valid private key path" >&2
  exit 2
fi

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15)

echo "== Deploy captive portal SPA =="
echo "  Build dir:   $BUILD_DIR"
echo "  SHC host:    debian@$SHC_IP"
echo "  OpenWrt VM:  $OPENWRT_USER@$OPENWRT_IP (port $OPENWRT_PORT)"
echo "  SSH key:     $SSH_KEY"
echo ""

# Step 1: build the SPA (unless --no-build)
if [ "$NO_BUILD" -eq 0 ]; then
  echo "[1/6] Building SPA in $BUILD_DIR ..."
  (cd "$BUILD_DIR" && npm run build)
  echo "      OK"
else
  echo "[1/6] Skipping build (--no-build)"
fi

# Sanity: splash.html must exist post-build
if [ ! -f "$BUILD_DIR/build/splash.html" ]; then
  echo "ERROR: $BUILD_DIR/build/splash.html not found after build" >&2
  exit 1
fi

# Step 2: tar the build output
TARBALL="/tmp/portal-deploy-$$.tar.gz"
echo "[2/6] Tarring build/ -> $(basename "$TARBALL") ..."
tar -C "$BUILD_DIR/build" -czf "$TARBALL" .
echo "      OK ($(du -h "$TARBALL" | cut -f1))"

# Step 3: scp tarball to SHC host
REMOTE_TARBALL="/tmp/portal-deploy-$$.tar.gz"
echo "[3/6] Uploading tarball to SHC host ..."
scp "${SSH_OPTS[@]}" -i "$SSH_KEY" "$TARBALL" "debian@$SHC_IP:$REMOTE_TARBALL"
echo "      OK"

# Step 4: from SHC host, scp to OpenWrt VM
echo "[4/6] Pushing tarball to OpenWrt VM ($OPENWRT_IP) ..."
if [ -n "$OPENWRT_PW" ]; then
  ssh "${SSH_OPTS[@]}" -i "$SSH_KEY" "debian@$SHC_IP" \
    "sshpass -p '$OPENWRT_PW' scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $REMOTE_TARBALL $OPENWRT_USER@$OPENWRT_IP:/tmp/portal-deploy.tar.gz"
else
  # assume key-based auth on the OpenWrt side too
  ssh "${SSH_OPTS[@]}" -i "$SSH_KEY" "debian@$SHC_IP" \
    "scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $REMOTE_TARBALL $OPENWRT_USER@$OPENWRT_IP:/tmp/portal-deploy.tar.gz"
fi
echo "      OK"

# Step 5: extract on the OpenWrt VM
echo "[5/6] Extracting into /www/ on OpenWrt VM ..."
if [ -n "$OPENWRT_PW" ]; then
  ssh "${SSH_OPTS[@]}" -i "$SSH_KEY" "debian@$SHC_IP" << EOF
sshpass -p '$OPENWRT_PW' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $OPENWRT_USER@$OPENWRT_IP '
  set -e
  cd /www
  # back up the existing portal files (one-time)
  if [ ! -f /www/.portal-backup-done ]; then
    mkdir -p /tmp/portal-backup
    cp -a splash.html balance.html 404.html asset-manifest.json manifest.json assets locales /tmp/portal-backup/ 2>/dev/null || true
    touch /www/.portal-backup-done
  fi
  # extract new files over existing
  tar -xzf /tmp/portal-deploy.tar.gz -C /www/
  rm -f /tmp/portal-deploy.tar.gz
  # restart nodogsplash to pick up new files
  /etc/init.d/nodogsplash restart 2>/dev/null || true
  echo "extract OK"
'
EOF
else
  ssh "${SSH_OPTS[@]}" -i "$SSH_KEY" "debian@$SHC_IP" \
    "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $OPENWRT_USER@$OPENWRT_IP 'cd /www && tar -xzf /tmp/portal-deploy.tar.gz && rm /tmp/portal-deploy.tar.gz && /etc/init.d/nodogsplash restart 2>/dev/null || true; echo extract OK'"
fi
echo "      OK"

# Step 6: verify
echo "[6/6] Verifying deployment (curl :${OPENWRT_PORT}/splash.html) ..."
VERIFY=$(ssh "${SSH_OPTS[@]}" -i "$SSH_KEY" "debian@$SHC_IP" \
  "curl -s -o /dev/null -w '%{http_code}' http://$OPENWRT_IP:$OPENWRT_PORT/splash.html || echo 000")
if [ "$VERIFY" = "200" ]; then
  echo "      OK (HTTP 200 from http://$OPENWRT_IP:$OPENWRT_PORT/splash.html)"
else
  echo "      WARN: verification got HTTP $VERIFY (expected 200)" >&2
  echo "      The deploy may still have succeeded — check manually:" >&2
  echo "        ssh -i $SSH_KEY debian@$SHC_IP 'curl -s http://$OPENWRT_IP:$OPENWRT_PORT/splash.html | head -5'" >&2
fi

# Clean up local tarball
rm -f "$TARBALL"
# Clean up remote SHC tarball (the OpenWrt copy is already cleaned in step 5)
ssh "${SSH_OPTS[@]}" -i "$SSH_KEY" "debian@$SHC_IP" "rm -f $REMOTE_TARBALL" 2>/dev/null || true

echo ""
echo "== Deploy complete =="
echo "Splash URL: http://$SHC_IP:$OPENWRT_PORT/splash.html (via SHC host port forward)"
echo "Direct on OpenWrt VM: http://$OPENWRT_IP:$OPENWRT_PORT/splash.html"
