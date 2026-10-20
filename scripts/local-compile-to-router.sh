#!/bin/bash

# Exit on any error (including SSH timeouts)
set -e

# local-compile-to-router.sh - Build and deploy tollgate-wrt binary to OpenWrt router
#
# PURPOSE:
#   Development/debugging tool for quickly testing changes on a router.
#   NOT intended for official deployments or production use.
#
# DESCRIPTION:
#   This script cross-compiles both the TollGate service and CLI client for the target
#   router architecture and deploys them via SSH/SCP. It handles the service
#   lifecycle by stopping the service before deployment and restarting it after.
#   Designed for rapid iteration during development and debugging.
#
# USAGE:
#   ./local-compile-to-router.sh [ROUTER_IP] [OPTIONS]
#
# ARGUMENTS:
#   ROUTER_IP (optional)    - IP address of the target router
#                            Format: X.X.X.X (e.g., 192.168.1.1)
#                            Default: 192.168.1.1
#                            Must be the first argument if provided
#
# OPTIONS:
#   --device=DEVICE        - Target device model for architecture selection
#                           Supported values:
#                           - gl-mt3000 (ARM64 architecture) [default]
#                           - gl-ar300 (MIPS with soft float)
#   --arch=OPENWRT_ARCH    - Target OpenWrt package arch (e.g. mipsel_24kc,
#                           aarch64_cortex-a53, x86_64). Takes precedence over
#                           --device; also read from the router inventory or
#                           TOLLGATE_ROUTER_ARCH. See lib/openwrt-arch.sh.
#
# EXAMPLES:
#   ./local-compile-to-router.sh                                    # Deploy to 192.168.1.1 for gl-mt3000
#   ./local-compile-to-router.sh 192.168.1.100                     # Deploy to custom IP for gl-mt3000
#   ./local-compile-to-router.sh --device=gl-ar300                 # Deploy to 192.168.1.1 for gl-ar300
#   ./local-compile-to-router.sh 192.168.1.100 --device=gl-ar300  # Custom IP and device
#
# REQUIREMENTS:
#   - Go compiler installed and configured
#   - SSH access to the router (uses root user)
#     * For password-less deployment, set up SSH keys: ssh-copy-id root@router_ip
#   - Router must have the tollgate-basic service configured
echo "Compiling to router"

# Default settings
ROUTER_USERNAME=root
ROUTER_IP=192.168.1.1
DEVICE="gl-mt3000"
ARCH=""
WALLET="gonuts"

# OpenWrt package arch per known device (lib/openwrt-arch.sh does the rest).
device_to_openwrt_arch() {
  case "$1" in
    gl-mt3000) echo "aarch64_cortex-a53" ;;
    gl-ar300)  echo "mips_24kc" ;;
    *) echo "" ;;
  esac
}

# Check for router IP as first argument
if [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  ROUTER_IP="$1"
  shift
fi

# Parse remaining command line arguments
for i in "$@"; do
  case $i in
    --arch=*) ARCH="${i#*=}" ;;
    --arch)   shift; ARCH="$1" ;;
    --device=*)
      DEVICE="${i#*=}"
      shift
      ;;
    --wallet=*)
      WALLET="${i#*=}"
      shift
      ;;
    *)
      ;;
  esac
done

if [[ "$WALLET" == "cdk" ]]; then
  BUILD_TAGS="-tags cdk_wallet"
  CGO_FLAG="CGO_ENABLED=1"
  if [[ "$DEVICE" == "gl-ar300" ]]; then
    echo "ERROR: --wallet=cdk requires CGO, not available for MIPS (gl-ar300)." >&2
    exit 1
  fi
else
  BUILD_TAGS=""
  CGO_FLAG="CGO_ENABLED=0"
fi

# SSH/SCP connection options
SSH_OPTS="-o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
EXECUTABLE_NAME=tollgate-wrt
EXECUTABLE_PATH="/usr/bin/$EXECUTABLE_NAME"

# Anchor to repo root so the script works regardless of CWD.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/src"

# Resolve the target arch: explicit --arch wins, then the device table,
# then the router inventory. Never a silent default (lib/openwrt-arch.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/openwrt-arch.sh"
if [ -z "$ARCH" ]; then
  ARCH="$(device_to_openwrt_arch "$DEVICE")"
fi
if [ -z "$ARCH" ]; then
  ARCH="$(resolve_openwrt_arch "")" || exit 1
fi
GO_ENV="$(go_env_for_openwrt_arch "$ARCH")"
echo "Building for OpenWrt arch $ARCH (${GO_ENV})"

# Build main service
echo "Building TollGate service..."
env $CGO_FLAG GOOS=linux $GO_ENV go build $BUILD_TAGS -o $EXECUTABLE_NAME -trimpath -ldflags="-s -w"

# Build CLI client
CLI_NAME=tollgate
CLI_PATH="/usr/bin/$CLI_NAME"
echo "Building CLI client..."
cd cmd/tollgate-cli

# Clean previous CLI builds
rm -f $CLI_NAME
go clean -cache
go mod tidy

env $CGO_FLAG GOOS=linux $GO_ENV go build $BUILD_TAGS -o $CLI_NAME -trimpath -ldflags="-s -w"

# Verify CLI binary was created
if [[ ! -f $CLI_NAME ]]; then
  echo "ERROR: CLI binary was not created!"
  exit 1
fi

echo "CLI binary created: $(ls -la $CLI_NAME)"
cd ../..

# Stop service, deploy both binaries, start service
echo "Stopping service $EXECUTABLE_NAME on router..."
ssh $SSH_OPTS $ROUTER_USERNAME@$ROUTER_IP "service $EXECUTABLE_NAME stop"
echo "Stopped service $EXECUTABLE_NAME on router"

echo "Copying service binary to router..."
scp -O $SSH_OPTS "$EXECUTABLE_NAME" "$ROUTER_USERNAME@$ROUTER_IP:$EXECUTABLE_PATH"
echo "Service binary copied to router"

echo "Copying CLI binary to router..."
scp -O $SSH_OPTS "cmd/tollgate-cli/$CLI_NAME" "$ROUTER_USERNAME@$ROUTER_IP:$CLI_PATH"
echo "CLI binary copied to router at $CLI_PATH"

if [[ "$WALLET" == "cdk" ]]; then
  # The cdkffi prebuilt follows the Go arch name (linux_arm64, ...). The
  # old hardcode silently shipped an arm64 .so to MIPS routers.
  CDK_GOARCH="$(echo "$GO_ENV" | awk '{print $1}' | cut -d= -f2)"
  CDK_SO_SRC="$(go env GOMODCACHE)/github.com/cashubtc/cdk-go@v0.17.3/bindings/cdkffi/native/linux_${CDK_GOARCH}/libcdk_ffi.so"
  if [[ -f "$CDK_SO_SRC" ]]; then
    echo "Deploying libcdk_ffi.so (cdk_wallet)..."
    scp -O $SSH_OPTS "$CDK_SO_SRC" "$ROUTER_USERNAME@$ROUTER_IP:/usr/lib/libcdk_ffi.so"
    ssh $SSH_OPTS $ROUTER_USERNAME@$ROUTER_IP "ldconfig"
    echo "libcdk_ffi.so deployed to /usr/lib/"
  else
    echo "WARNING: libcdk_ffi.so not found at $CDK_SO_SRC"
    echo "         Run 'go mod download github.com/cashubtc/cdk-go' in src/ first."
  fi
fi

echo "Setting executable permissions..."
ssh $SSH_OPTS $ROUTER_USERNAME@$ROUTER_IP "chmod +x $CLI_PATH"

echo "Starting service $EXECUTABLE_NAME on router..."
ssh $SSH_OPTS $ROUTER_USERNAME@$ROUTER_IP "service $EXECUTABLE_NAME start"
echo "Started service $EXECUTABLE_NAME on router"

echo "Done"