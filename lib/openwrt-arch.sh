#!/usr/bin/env bash
# OpenWrt arch resolution — shared by every build/deploy path.
#
# One rule (OpenWrt multi-router practice): the package architecture comes
# from the device — the router inventory's `arch` field, or an explicit
# argument — never from a hardcoded default. A wrong-arch .ipk installs
# nothing useful on a mixed bench (aarch64 MT3000s beside mipsel_24kc
# COVR-X1860s), and a silent default is exactly how those get built.
#
# Sourced, not executed. Provides:
#   resolve_openwrt_arch [explicit]   -> echoes the arch; errors with guidance
#                                        if neither explicit nor inventory has it
#   go_env_for_openwrt_arch <arch>    -> echoes "GOARCH=… [GOMIPS=…|GOARM=…]"
#   openwrt_arch_to_rust_target <arch> -> echoes the cargo zigbuild target
#                                        (mirrors tollgate-rs build-ipk.sh)

_resolve_from_inventory() {
    # routers.json carries the OpenWrt package arch per router (the same
    # string `opkg status` / the feed's arch.json uses).
    local inv="${TOLLGATE_ROUTER_INVENTORY:-config/routers.json}"
    [ -f "$inv" ] || return 1
    python3 - "$inv" <<'PY' 2>/dev/null
import json, os, sys
try:
    inv = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
router_id = os.environ.get("TOLLGATE_ROUTER_ID") or inv.get("default")
router = (inv.get("routers") or {}).get(router_id) or {}
arch = router.get("arch")
if arch:
    print(arch)
PY
}

resolve_openwrt_arch() {
    local explicit="${1:-${TOLLGATE_ROUTER_ARCH:-}}"
    if [ -n "$explicit" ]; then
        echo "$explicit"
        return 0
    fi
    local from_inv
    if from_inv=$(_resolve_from_inventory) && [ -n "$from_inv" ]; then
        echo "$from_inv"
        return 0
    fi
    echo "ERROR: no router arch. Pass it explicitly (e.g. mipsel_24kc), set" >&2
    echo "       TOLLGATE_ROUTER_ARCH, or give the router an 'arch' field in" >&2
    echo "       the inventory (config/routers.json) — refusing to guess." >&2
    return 1
}

go_env_for_openwrt_arch() {
    case "$1" in
        aarch64*)                     echo "GOARCH=arm64" ;;
        x86_64)                       echo "GOARCH=amd64" ;;
        arm_cortex-a7*|armv7*)        echo "GOARCH=arm GOARM=7" ;;
        mipsel_24kc|mipsel_24kc_*)    echo "GOARCH=mipsle GOMIPS=softfloat" ;;
        mips_24kc|mips_24kc_*)        echo "GOARCH=mips GOMIPS=softfloat" ;;
        mips64el*)                    echo "GOARCH=mips64le GOMIPS=softfloat" ;;
        *)
            echo "ERROR: no Go mapping for OpenWrt arch '$1' — add it to lib/openwrt-arch.sh" >&2
            return 1
            ;;
    esac
}

openwrt_arch_to_rust_target() {
    case "$1" in
        aarch64*)                     echo "aarch64-unknown-linux-musl" ;;
        x86_64)                       echo "x86_64-unknown-linux-musl" ;;
        mipsel_24kc|mipsel_24kc_*)    echo "mipsel-unknown-linux-musl" ;;
        mips_24kc|mips_24kc_*)        echo "mips-unknown-linux-musl" ;;
        arm_cortex-a7*)               echo "arm-unknown-linux-musleabihf" ;;
        *)
            echo "ERROR: no Rust mapping for OpenWrt arch '$1' — add it to lib/openwrt-arch.sh" >&2
            return 1
            ;;
    esac
}
