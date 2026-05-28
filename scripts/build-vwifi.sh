#!/usr/bin/env bash
# Build vwifi from source for cross-VM WiFi frame relay.
#
# Produces binaries for three targets:
#   host/    — vwifi-server, vwifi-ctrl (runs on GCP host)
#   debian/  — vwifi-client, vwifi-add-interfaces (runs in Debian QEMU guest)
#   openwrt/ — vwifi-client, vwifi-add-interfaces (runs in OpenWrt QEMU guest)
#
# The Debian and OpenWrt guests are both x86_64 Linux, so the same static
# binaries work for both.  OpenWrt x86_64 uses either glibc or musl; we
# build statically linked binaries to avoid library mismatches.
#
# Usage:
#   ./scripts/build-vwifi.sh [--output-dir DIR]
#
# Idempotent: skips build if all expected binaries already exist.
#
# Dependencies (auto-installed on Debian/Ubuntu):
#   cmake, make, g++, pkg-config, libnl-3-dev, libnl-genl-3-dev
#
# Reference: https://github.com/Raizo62/vwifi

set -euo pipefail

VWIFI_REPO="https://github.com/Raizo62/vwifi.git"
VWIFI_BRANCH="master"
BUILD_DIR="/tmp/vwifi-build"

# Default output directory relative to repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${REPO_ROOT}/binaries/vwifi"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

HOST_DIR="${OUTPUT_DIR}/host"
DEBIAN_DIR="${OUTPUT_DIR}/debian"
OPENWRT_DIR="${OUTPUT_DIR}/openwrt"

echo "=== vwifi build script ==="
echo "Output: ${OUTPUT_DIR}"

# Check if all binaries already exist (idempotent)
check_binaries() {
    local all_exist=true
    for dir in "${HOST_DIR}" "${DEBIAN_DIR}" "${OPENWRT_DIR}"; do
        if [[ ! -d "${dir}" ]]; then
            all_exist=false
            break
        fi
    done

    if [[ "${all_exist}" == true ]]; then
        # Check host binaries
        for bin in vwifi-server vwifi-ctrl; do
            if [[ ! -x "${HOST_DIR}/${bin}" ]]; then
                all_exist=false
                break
            fi
        done
        # Check guest binaries (debian + openwrt)
        for bin in vwifi-client vwifi-add-interfaces; do
            if [[ ! -x "${DEBIAN_DIR}/${bin}" ]] || [[ ! -x "${OPENWRT_DIR}/${bin}" ]]; then
                all_exist=false
                break
            fi
        done
    fi

    if [[ "${all_exist}" == true ]]; then
        echo "All vwifi binaries already exist, skipping build."
        echo "  host:    ${HOST_DIR}/vwifi-server"
        echo "  debian:  ${DEBIAN_DIR}/vwifi-client"
        echo "  openwrt: ${OPENWRT_DIR}/vwifi-client"
        exit 0
    fi
}

check_binaries

# Install build dependencies (Debian/Ubuntu)
echo "Installing build dependencies..."
sudo apt-get update -qq 2>/dev/null || true
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    cmake make g++ pkg-config libnl-3-dev libnl-genl-3-dev git \
    >/dev/null 2>&1 || {
    echo "WARNING: apt install failed, attempting build anyway..." >&2
}

# Clone vwifi repository
echo "Cloning vwifi repository..."
if [[ -d "${BUILD_DIR}/.git" ]]; then
    echo "  Repository already cloned at ${BUILD_DIR}, pulling latest..."
    git -C "${BUILD_DIR}" pull --ff-only 2>/dev/null || \
        echo "  WARNING: git pull failed, using existing checkout"
else
    rm -rf "${BUILD_DIR}"
    git clone --depth 1 --branch "${VWIFI_BRANCH}" "${VWIFI_REPO}" "${BUILD_DIR}"
fi

# Clean stale build dirs (may have wrong permissions from previous runs)
rm -rf "${BUILD_DIR}/build-host" "${BUILD_DIR}/build-guest"

# --- Build host binaries (vwifi-server, vwifi-ctrl) ---
echo ""
echo "Building host binaries (vwifi-server, vwifi-ctrl)..."
HOST_BUILD="${BUILD_DIR}/build-host"
mkdir -p "${HOST_BUILD}"
cd "${HOST_BUILD}"

cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
make -j"$(nproc)" 2>&1 | tail -5

mkdir -p "${HOST_DIR}"
for bin in vwifi-server vwifi-ctrl; do
    if [[ -x "${HOST_BUILD}/${bin}" ]]; then
        cp "${HOST_BUILD}/${bin}" "${HOST_DIR}/${bin}"
        echo "  OK: ${HOST_DIR}/${bin}"
    else
        echo "  WARNING: ${bin} not found in build output" >&2
    fi
done

# --- Build guest binaries (vwifi-client, vwifi-add-interfaces) ---
# Both Debian and OpenWrt guests are x86_64 Linux. We build static binaries
# so they work on both glibc (Debian) and musl (OpenWrt x86_64) systems.
echo ""
echo "Building guest binaries (static, for Debian + OpenWrt x86_64)..."
GUEST_BUILD="${BUILD_DIR}/build-guest"
mkdir -p "${GUEST_BUILD}"
cd "${GUEST_BUILD}"

cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_EXE_LINKER_FLAGS="-static" \
    2>&1 | tail -5
if ! make -j"$(nproc)" 2>&1 | tail -5; then
    echo "  Static build failed, trying dynamic..."
    rm -rf "${GUEST_BUILD}"
    mkdir -p "${GUEST_BUILD}"
    cd "${GUEST_BUILD}"
    cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
    make -j"$(nproc)" 2>&1 | tail -5
fi

mkdir -p "${DEBIAN_DIR}" "${OPENWRT_DIR}"
for bin in vwifi-client vwifi-add-interfaces; do
    if [[ -x "${GUEST_BUILD}/${bin}" ]]; then
        cp "${GUEST_BUILD}/${bin}" "${DEBIAN_DIR}/${bin}"
        cp "${GUEST_BUILD}/${bin}" "${OPENWRT_DIR}/${bin}"
        echo "  OK: ${DEBIAN_DIR}/${bin}"
        echo "  OK: ${OPENWRT_DIR}/${bin}"
    else
        echo "  WARNING: ${bin} not found in build output" >&2
        # Fallback: try non-static build if static fails
        if [[ -x "${HOST_BUILD}/${bin}" ]]; then
            echo "  FALLBACK: using host build of ${bin}" >&2
            cp "${HOST_BUILD}/${bin}" "${DEBIAN_DIR}/${bin}"
            cp "${HOST_BUILD}/${bin}" "${OPENWRT_DIR}/${bin}"
        fi
    fi
done

# --- Verify ---
echo ""
echo "=== Build verification ==="
all_ok=true
for f in "${HOST_DIR}/vwifi-server" "${HOST_DIR}/vwifi-ctrl" \
         "${DEBIAN_DIR}/vwifi-client" "${DEBIAN_DIR}/vwifi-add-interfaces" \
         "${OPENWRT_DIR}/vwifi-client" "${OPENWRT_DIR}/vwifi-add-interfaces"; do
    if [[ -x "${f}" ]]; then
        size=$(stat -f%z "${f}" 2>/dev/null || stat -c%s "${f}" 2>/dev/null || echo "?")
        echo "  OK: ${f} (${size} bytes)"
    else
        echo "  MISSING: ${f}" >&2
        all_ok=false
    fi
done

if [[ "${all_ok}" == true ]]; then
    echo ""
    echo "Build complete. Binaries at:"
    echo "  ${OUTPUT_DIR}/"
    echo "  ${HOST_DIR}/vwifi-server"
    echo "  ${DEBIAN_DIR}/vwifi-client"
    echo "  ${OPENWRT_DIR}/vwifi-client"
else
    echo ""
    echo "WARNING: Some binaries are missing." >&2
    exit 1
fi
