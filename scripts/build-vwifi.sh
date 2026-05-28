#!/usr/bin/env bash
# Build vwifi from source for cross-VM WiFi frame relay.
#
# Produces binaries for three targets:
#   host/    — vwifi-server, vwifi-ctrl (runs on GCP host, glibc dynamic)
#   debian/  — vwifi-client, vwifi-add-interfaces (runs in Debian QEMU guest)
#   openwrt/ — vwifi-client, vwifi-add-interfaces (runs in OpenWrt QEMU guest, musl)
#
# Guest binaries are built as STATIC musl binaries inside an Alpine Docker
# container.  This is necessary because OpenWrt x86_64 uses musl libc, not
# glibc.  Alpine provides native musl + static libnl3, producing binaries
# that run on both Debian (glibc) and OpenWrt (musl) since they are fully
# statically linked.
#
# Usage:
#   ./scripts/build-vwifi.sh [--output-dir DIR]
#
# Idempotent: skips build if all expected binaries already exist.
#
# Dependencies (auto-installed):
#   Host build: cmake, make, g++, pkg-config, libnl-3-dev, libnl-genl-3-dev, git
#   Guest build: Docker (for Alpine container with musl toolchain)
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
        # Check guest binaries (debian + openwrt) — must be static
        for bin in vwifi-client vwifi-add-interfaces; do
            if [[ ! -x "${DEBIAN_DIR}/${bin}" ]] || [[ ! -x "${OPENWRT_DIR}/${bin}" ]]; then
                all_exist=false
                break
            fi
        done
        # Verify guest binaries are actually static (not dynamic glibc)
        if [[ "${all_exist}" == true ]]; then
            local guest_type
            guest_type=$(file "${DEBIAN_DIR}/vwifi-client" 2>/dev/null || echo "missing")
            if echo "${guest_type}" | grep -q "dynamically linked"; then
                echo "  Existing guest binaries are dynamically linked — rebuilding as static musl..."
                all_exist=false
            fi
        fi
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
    cmake make g++ pkg-config libnl-3-dev libnl-genl-3-dev git docker.io \
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
rm -rf "${BUILD_DIR}/build-host"

# --- Build host binaries (vwifi-server, vwifi-ctrl) ---
echo ""
echo "Building host binaries (vwifi-server, vwifi-ctrl) — glibc dynamic..."
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

# --- Build guest binaries (static musl via Alpine Docker) ---
# OpenWrt x86_64 uses musl libc, not glibc.  Building static binaries in an
# Alpine container (native musl) ensures compatibility with both musl (OpenWrt)
# and glibc (Debian) guests.  The trick: remove .so symlinks so CMake only
# finds the static .a archives of libnl3.
echo ""
echo "Building guest binaries (static musl, for Debian + OpenWrt x86_64)..."

# Ensure Docker is running
sudo systemctl start docker 2>/dev/null || true
sudo docker info >/dev/null 2>&1 || {
    echo "ERROR: Docker is not available. Cannot build static musl binaries." >&2
    echo "Falling back to dynamic glibc build (WILL NOT WORK ON OPENWRT)..." >&2
    # Fallback: build dynamic (same as before — won't work on musl OpenWrt)
    GUEST_BUILD="${BUILD_DIR}/build-guest"
    rm -rf "${GUEST_BUILD}"
    mkdir -p "${GUEST_BUILD}" && cd "${GUEST_BUILD}"
    cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
    make -j"$(nproc)" vwifi-client vwifi-add-interfaces 2>&1 | tail -5
    mkdir -p "${DEBIAN_DIR}" "${OPENWRT_DIR}"
    for bin in vwifi-client vwifi-add-interfaces; do
        [[ -x "${GUEST_BUILD}/${bin}" ]] && cp "${GUEST_BUILD}/${bin}" "${DEBIAN_DIR}/${bin}" "${OPENWRT_DIR}/${bin}"
    done
    echo "  WARNING: Guest binaries are dynamically linked (glibc) — will NOT work on OpenWrt musl!" >&2
    exit 0
}

sudo docker run --rm \
    -v "$(pwd)/..":/src \
    -v "${OUTPUT_DIR}":/output \
    alpine:latest \
    sh -c '
        set -e
        echo "  Installing Alpine build deps..."
        apk add --no-cache cmake make g++ pkgconf \
            libnl3-dev libnl3-static libstdc++-dev musl-dev linux-headers \
            file 2>&1 | tail -3

        echo "  Building vwifi guest (static musl)..."
        cd /src
        rm -rf build-guest-musl
        mkdir -p build-guest-musl && cd build-guest-musl

        # Remove .so symlinks to force CMake to find only static .a archives
        rm -f /usr/lib/libnl*.so* /usr/lib/libnl-3.so* /usr/lib/libnl-genl-3.so*

        cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXE_LINKER_FLAGS=-static 2>&1 | tail -3
        make -j$(nproc) vwifi-client vwifi-add-interfaces 2>&1 | tail -5

        echo "  Verifying static linkage..."
        file vwifi-client vwifi-add-interfaces
        ldd vwifi-client 2>&1 || true

        mkdir -p /output/debian /output/openwrt
        for bin in vwifi-client vwifi-add-interfaces; do
            cp "${bin}" /output/debian/"${bin}"
            cp "${bin}" /output/openwrt/"${bin}"
            echo "  OK: /output/debian/${bin}"
            echo "  OK: /output/openwrt/${bin}"
        done
        echo GUEST_MUSL_BUILD_SUCCESS
    '

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

# Extra check: guest binaries must be static
if [[ -x "${DEBIAN_DIR}/vwifi-client" ]]; then
    if file "${DEBIAN_DIR}/vwifi-client" | grep -q "dynamically linked"; then
        echo "  WARNING: Guest binaries are dynamically linked — will NOT work on OpenWrt (musl)!" >&2
        all_ok=false
    else
        echo "  Guest binaries: static (musl-compatible) ✓"
    fi
fi

if [[ "${all_ok}" == true ]]; then
    echo ""
    echo "Build complete. Binaries at:"
    echo "  ${OUTPUT_DIR}/"
    echo "  ${HOST_DIR}/vwifi-server"
    echo "  ${DEBIAN_DIR}/vwifi-client (static musl)"
    echo "  ${OPENWRT_DIR}/vwifi-client (static musl)"
else
    echo ""
    echo "WARNING: Some binaries are missing." >&2
    exit 1
fi
