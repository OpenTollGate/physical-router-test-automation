#!/usr/bin/env bash
set -euo pipefail

# Deploy a TollGate build to an OpenWrt router.
#
# Artifact sources (priority order):
#   1. Prebuilt local .ipk  -> TOLLGATE_IPK=/path/to/tollgate-wrt.ipk, OR pass
#                              the .ipk path as the first argument. Skips the
#                              source build so a CI/Blossom/Nostr-fetched or
#                              locally cached .ipk can be installed directly.
#   2. Build from source     -> first argument is a git hash (branch/tag/SHA);
#                              the repo is cloned and cross-compiled here.
#
# Usage: deploy.sh <git-hash|local.ipk> [--restart] [--build-only]
#                  [router-ip] [router-user] [arch]
#
#   --restart     Restart the service after install (default behaviour; accepted
#                 so the pytest --binary path that passes this flag works).
#   --build-only  Do not touch a router: produce (or resolve) the .ipk, print
#                 its path to stdout, and exit 0. Lets CI / dev machines
#                 exercise the build path off-router without SSH credentials.
#
# Env:
#   TOLLGATE_LUCI_PASSWORD   SSH password (required unless --build-only)
#   TOLLGATE_IPK             Prebuilt .ipk path; skips the source build
#   TOLLGATE_ROUTER_IP       Override the router IP
#   TOLLGATE_REPO            Override the source repo URL (fork / local mirror)

RESTART=1
BUILD_ONLY=0
POSITIONAL=()

while [ $# -gt 0 ]; do
  case "$1" in
    --restart)    RESTART=1; shift ;;
    --no-restart) RESTART=0; shift ;;
    --build-only) BUILD_ONLY=1; shift ;;
    --help|-h)    sed -n '3,26p' "$0"; exit 0 ;;
    --*)          echo "ERROR: unknown option: $1" >&2; exit 2 ;;
    *)            POSITIONAL+=("$1"); shift ;;
  esac
done

GIT_HASH="${POSITIONAL[0]:?Usage: $0 <git-hash|local.ipk> [--restart] [--build-only] [router-ip] [router-user] [arch]}"
ROUTER_IP="${POSITIONAL[1]:-${TOLLGATE_ROUTER_IP:-192.168.13.112}}"
ROUTER_USER="${POSITIONAL[2]:-root}"
ARCH="${POSITIONAL[3]:-${TOLLGATE_ROUTER_ARCH:-aarch64_cortex-a53}}"

# --- Resolve artifact source ----------------------------------------------
# A prebuilt .ipk (explicit env, or first arg pointing at an .ipk file) short-
# circuits the source build so the test suite can fetch+install a CI artifact
# (Blossom/Nostr download via lib/deploy.py, or a locally cached .ipk).
LOCAL_IPK=""
if [ -n "${TOLLGATE_IPK:-}" ] && [ -f "${TOLLGATE_IPK:-}" ]; then
  LOCAL_IPK="$TOLLGATE_IPK"
elif [[ "$GIT_HASH" == *.ipk ]] && [ -f "$GIT_HASH" ]; then
  LOCAL_IPK="$GIT_HASH"
  GIT_HASH=""
fi

# SSH password is only needed when actually deploying to a router.
if [ "$BUILD_ONLY" -eq 0 ] && [ -z "${TOLLGATE_LUCI_PASSWORD:-}" ]; then
  echo "ERROR: TOLLGATE_LUCI_PASSWORD env var is required (or use --build-only)" >&2
  exit 1
fi
export SSHPASS="${TOLLGATE_LUCI_PASSWORD:-}"

REPO="${TOLLGATE_REPO:-https://github.com/OpenTollGate/tollgate-module-basic-go.git}"
VERSION="${GIT_HASH:0:12}"
BUILD_TIME=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/tollgate-build-XXXXXX")
trap 'rm -rf "$TMPDIR"' EXIT

if [ -n "$LOCAL_IPK" ]; then
  echo "==> Using prebuilt ipk: $LOCAL_IPK"
  cp "$LOCAL_IPK" "$TMPDIR/tollgate-wrt.ipk"
  IPK_FILE="$TMPDIR/tollgate-wrt.ipk"
else
  echo "==> Cloning tollgate-module-basic-go at ${GIT_HASH}..."
  git clone --depth 1 --branch "$GIT_HASH" "$REPO" "$TMPDIR/repo" 2>/dev/null || \
    { git clone "$REPO" "$TMPDIR/repo" && git -C "$TMPDIR/repo" checkout "$GIT_HASH"; }

  cd "$TMPDIR/repo"

  echo "==> Building Go binaries for linux/arm64..."
  LDFLAGS="-s -w -X 'github.com/OpenTollGate/tollgate-module-basic-go/src/cli.Version=${VERSION}' -X 'github.com/OpenTollGate/tollgate-module-basic-go/src/cli.GitCommit=${GIT_HASH}' -X 'github.com/OpenTollGate/tollgate-module-basic-go/src/cli.BuildTime=${BUILD_TIME}'"

  # Absolute BIN_DIR: `go build -C <dir> -o <rel>` resolves -o relative to the
  # -C dir (NOT the original CWD), so the binaries would otherwise land in
  # src/bin/ and the install step below would miss them under `set -e`. Build
  # straight into an absolute repo-root bin/ directory.
  BIN_DIR="$TMPDIR/repo/bin"
  mkdir -p "$BIN_DIR"
  env GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -C src -o "$BIN_DIR/tollgate-wrt" -trimpath -ldflags="$LDFLAGS" main.go
  env GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -C src/cmd/tollgate-cli -o "$BIN_DIR/tollgate" -trimpath -ldflags="$LDFLAGS"

  echo "==> Building payload tree..."
  PAYLOAD="$TMPDIR/payload"
  mkdir -p "$PAYLOAD"

  install -D -m 0755 "$BIN_DIR/tollgate-wrt" "$PAYLOAD/usr/bin/tollgate-wrt"
  install -D -m 0755 "$BIN_DIR/tollgate"     "$PAYLOAD/usr/bin/tollgate"
  install -D -m 0755 packaging/files/etc/init.d/tollgate-wrt "$PAYLOAD/etc/init.d/tollgate-wrt"
  install -D -m 0755 packaging/files/etc/uci-defaults/90-tollgate-captive-portal-symlink "$PAYLOAD/etc/uci-defaults/90-tollgate-captive-portal-symlink"
  install -D -m 0755 packaging/files/etc/uci-defaults/99-tollgate-setup "$PAYLOAD/etc/uci-defaults/99-tollgate-setup"
  install -D -m 0644 packaging/files/etc/config/firewall-tollgate "$PAYLOAD/etc/config/firewall-tollgate"
  install -D -m 0755 packaging/files/usr/local/bin/first-login-setup "$PAYLOAD/usr/local/bin/first-login-setup"
  install -D -m 0755 packaging/files/usr/bin/check_package_path "$PAYLOAD/usr/bin/check_package_path"
  install -D -m 0644 packaging/files/lib/upgrade/keep.d/tollgate "$PAYLOAD/lib/upgrade/keep.d/tollgate"
  install -D -m 0755 packaging/files/etc/hotplug.d/iface/95-tollgate-restart "$PAYLOAD/etc/hotplug.d/iface/95-tollgate-restart"
  # The v0.5.0 packaging overhaul removed the standalone LuCI JS/CSS
  # (packaging/files/www/...); the captive-portal site below is the only UI
  # shipped by this package. The dead www/* install lines were dropped here.
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

  bash packaging/build-ipk.sh "$PAYLOAD" "$TMPDIR/tollgate-wrt.ipk"

  IPK_FILE="$TMPDIR/tollgate-wrt.ipk"
  if [ ! -f "$IPK_FILE" ]; then
    echo "ERROR: No ipk file produced" >&2
    exit 1
  fi
fi

# Off-router / CI build verification: stop after producing the .ipk.
if [ "$BUILD_ONLY" -eq 1 ]; then
  echo "$IPK_FILE"
  exit 0
fi

echo "==> Deploying ${IPK_FILE##*/} to ${ROUTER_USER}@${ROUTER_IP}..."
# -O forces legacy SCP protocol; OpenWrt busybox lacks sftp-server subsystem
sshpass -e scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 "$IPK_FILE" "${ROUTER_USER}@${ROUTER_IP}:/tmp/tollgate-wrt.ipk"

echo "==> Installing on router..."
if [ "$RESTART" -eq 1 ]; then
  RESTART_CMDS="/etc/init.d/tollgate-wrt restart && /etc/init.d/tollgate-basic restart 2>/dev/null;"
else
  RESTART_CMDS=""
fi
# --force-overwrite is the correct OpenWrt opkg flag (--force-replace does not exist)
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${ROUTER_USER}@${ROUTER_IP}" \
  "opkg install --force-overwrite /tmp/tollgate-wrt.ipk && ${RESTART_CMDS} /etc/init.d/uhttpd restart && rm -f /tmp/tollgate-wrt.ipk"

echo "==> Installed version:"
REMOTE_VER='tollgate --version 2>/dev/null || echo "version '"${VERSION}"'"'
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${ROUTER_USER}@${ROUTER_IP}" "$REMOTE_VER"

echo "==> Deploy complete."
