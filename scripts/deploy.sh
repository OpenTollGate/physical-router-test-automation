#!/usr/bin/env bash
set -euo pipefail

GIT_HASH="${1:?Usage: $0 <git-hash> [router-ip] [router-user] [arch]}"
ROUTER_IP="${2:-192.168.13.112}"
ROUTER_USER="${3:-root}"
ARCH="${4:-aarch64_cortex-a53}"

if [ -z "${TOLLGATE_LUCI_PASSWORD:-}" ]; then
  echo "ERROR: TOLLGATE_LUCI_PASSWORD env var is required" >&2
  exit 1
fi

export SSHPASS="$TOLLGATE_LUCI_PASSWORD"

REPO="https://github.com/OpenTollGate/tollgate-module-basic-go.git"
VERSION="${GIT_HASH:0:12}"
BUILD_TIME=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

TMPDIR=$(mktemp -d /tmp/tollgate-build-XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Cloning tollgate-module-basic-go at ${GIT_HASH}..."
git clone --depth 1 --branch "$GIT_HASH" "$REPO" "$TMPDIR/repo" 2>/dev/null || \
  git clone "$REPO" "$TMPDIR/repo" && git -C "$TMPDIR/repo" checkout "$GIT_HASH"

# Temporary: patch gonuts to bolt11-tolerant version (see #156)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/patch-gonuts-version.sh" "$TMPDIR/repo"

cd "$TMPDIR/repo"
mkdir -p bin

echo "==> Building Go binaries for linux/arm64..."
LDFLAGS="-s -w -X 'github.com/OpenTollGate/tollgate-module-basic-go/src/cli.Version=${VERSION}' -X 'github.com/OpenTollGate/tollgate-module-basic-go/src/cli.GitCommit=${GIT_HASH}' -X 'github.com/OpenTollGate/tollgate-module-basic-go/src/cli.BuildTime=${BUILD_TIME}'"

env GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -C src -o bin/tollgate-wrt -trimpath -ldflags="$LDFLAGS" main.go
env GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -C src/cmd/tollgate-cli -o bin/tollgate -trimpath -ldflags="$LDFLAGS"

echo "==> Building payload tree..."
PAYLOAD="$TMPDIR/payload"
mkdir -p "$PAYLOAD"

install -D -m 0755 bin/tollgate-wrt "$PAYLOAD/usr/bin/tollgate-wrt"
install -D -m 0755 bin/tollgate     "$PAYLOAD/usr/bin/tollgate"
install -D -m 0755 packaging/files/etc/init.d/tollgate-wrt "$PAYLOAD/etc/init.d/tollgate-wrt"
install -D -m 0755 packaging/files/etc/uci-defaults/90-tollgate-captive-portal-symlink "$PAYLOAD/etc/uci-defaults/90-tollgate-captive-portal-symlink"
install -D -m 0755 packaging/files/etc/uci-defaults/99-tollgate-setup "$PAYLOAD/etc/uci-defaults/99-tollgate-setup"
install -D -m 0644 packaging/files/etc/config/firewall-tollgate "$PAYLOAD/etc/config/firewall-tollgate"
install -D -m 0755 packaging/files/usr/local/bin/first-login-setup "$PAYLOAD/usr/local/bin/first-login-setup"
install -D -m 0755 packaging/files/usr/bin/check_package_path "$PAYLOAD/usr/bin/check_package_path"
install -D -m 0644 packaging/files/lib/upgrade/keep.d/tollgate "$PAYLOAD/lib/upgrade/keep.d/tollgate"
install -D -m 0755 packaging/files/etc/hotplug.d/iface/95-tollgate-restart "$PAYLOAD/etc/hotplug.d/iface/95-tollgate-restart"
install -D -m 0644 packaging/files/www/luci-static/resources/view/tollgate-payments/settings.js "$PAYLOAD/www/luci-static/resources/view/tollgate-payments/settings.js"
install -D -m 0644 packaging/files/www/luci-static/resources/tollgate-payments/tg.css "$PAYLOAD/www/luci-static/resources/tollgate-payments/tg.css"
install -D -m 0644 packaging/files/usr/share/luci/menu.d/luci-app-tollgate-payments.json "$PAYLOAD/usr/share/luci/menu.d/luci-app-tollgate-payments.json"
install -D -m 0644 packaging/files/usr/share/rpcd/acl.d/luci-app-tollgate-payments.json "$PAYLOAD/usr/share/rpcd/acl.d/luci-app-tollgate-payments.json"
mkdir -p "$PAYLOAD/etc/tollgate/tollgate-captive-portal-site" "$PAYLOAD/etc/tollgate/ecash" "$PAYLOAD/etc/crontabs"
cp -r packaging/files/tollgate-captive-portal-site/. "$PAYLOAD/etc/tollgate/tollgate-captive-portal-site/"

echo "==> Building ipk..."
PKG_NAME=tollgate-wrt
PKG_VERSION="${VERSION}"
MAINTAINER="TollGate <dev@opentollgate.com>"
LICENSE="MIT"
DEPENDS="libc, libpthread"
DESCRIPTION="TollGate router module"

export PKG_NAME PKG_VERSION ARCH MAINTAINER LICENSE DEPENDS DESCRIPTION

bash packaging/build-ipk.sh "$PAYLOAD" "$TMPDIR"

IPK_FILE=$(ls "$TMPDIR"/*.ipk 2>/dev/null | head -1)
if [ -z "$IPK_FILE" ]; then
  echo "ERROR: No ipk file produced" >&2
  exit 1
fi

echo "==> Deploying ${IPK_FILE##*/} to ${ROUTER_USER}@${ROUTER_IP}..."
# -O forces legacy SCP protocol; OpenWrt busybox lacks sftp-server subsystem
sshpass -e scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 "$IPK_FILE" "${ROUTER_USER}@${ROUTER_IP}:/tmp/tollgate-wrt.ipk"

echo "==> Installing on router..."
# --force-overwrite is the correct OpenWrt opkg flag (--force-replace does not exist)
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${ROUTER_USER}@${ROUTER_IP}" \
  'opkg install --force-overwrite /tmp/tollgate-wrt.ipk && /etc/init.d/tollgate-wrt restart && /etc/init.d/tollgate-basic restart 2>/dev/null; /etc/init.d/uhttpd restart && rm -f /tmp/tollgate-wrt.ipk'

echo "==> Installed version:"
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${ROUTER_USER}@${ROUTER_IP}" \
  'tollgate --version 2>/dev/null || echo "version ${VERSION}"'

echo "==> Deploy complete."
