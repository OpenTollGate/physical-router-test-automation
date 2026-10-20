#!/bin/bash
# cvd-host-setup.sh — one-shot host provisioning for the Cuttlefish lab.
#
# ai-legion runs ufw with default INPUT DROP: TCP SYNs from the Cuttlefish
# guest (guest → OpenWrt AP VM → host tap cvd-wifiap-01) die silently while
# ICMP passes, which looks like a routing bug but is not (issue #121).
# This script adds the interface-scoped ufw allows and the guest return
# route, idempotently.
#
# Usage: sudo tools/cvd-host-setup.sh [--dry-run] [--iface IF] [--subnet CIDR] [--gw IP]
#
# See docs/TESTING.md → "Cuttlefish Lab (ai-legion)" → "Host firewall".
set -euo pipefail

IFACE="cvd-wifiap-01"
SUBNET="192.168.99.0/25"
GATEWAY="192.168.94.2"
PORTS=(80 8090)
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: sudo tools/cvd-host-setup.sh [--dry-run] [--iface IF] [--subnet CIDR] [--gw IP]

Options:
  --dry-run    print the commands without running anything (no root needed)
  --iface IF   host tap of the OpenWrt AP VM   (default: cvd-wifiap-01)
  --subnet S   guest subnet behind the AP VM   (default: 192.168.99.0/25)
  --gw IP      gateway = AP VM IP on the tap   (default: 192.168.94.2)
  -h, --help   show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --iface)  IFACE="${2:?missing value}";  shift ;;
        --subnet) SUBNET="${2:?missing value}"; shift ;;
        --gw)     GATEWAY="${2:?missing value}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: needs root (ufw + ip route). Try: sudo $0" >&2
    exit 1
fi

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] $*"
    else
        echo "+ $*"
        "$@"
    fi
}

# ufw allows are idempotent — re-adding an existing rule is a no-op.
for port in "${PORTS[@]}"; do
    run ufw allow in on "$IFACE" to any port "$port" proto tcp
done

# ip route add fails on duplicates — skip if this exact route exists.
if ip route show dev "$IFACE" 2>/dev/null | grep -qF "${SUBNET} via ${GATEWAY}"; then
    echo "route ${SUBNET} via ${GATEWAY} dev ${IFACE}: already present, skipping"
else
    run ip route add "$SUBNET" via "$GATEWAY" dev "$IFACE"
fi
