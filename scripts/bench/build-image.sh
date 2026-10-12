#!/usr/bin/env bash
# build-image.sh <LAN_IP> [PROFILE] [OUTNAME] [IMAGEBUILDER_DIR]
#
# Build a bench lab image with the OpenWrt ImageBuilder. It bakes:
#   * a fixed LAN IP (applied by /etc/rc.local to the bridge that owns the
#     physical port — br-lan vs br-private differs per board),
#   * the documented bench root password (SSH cycle needs no console),
#   * radios OFF by default (same late rc.local hook),
#   * socat (the serial-into-Linux fallback's transport tool).
# One image per lane (the LAN IP differs by lane/board).
set -euo pipefail
LAN_IP="${1:?usage: build-image.sh <LAN_IP> [PROFILE] [OUTNAME] [IB_DIR]}"
PROFILE="${2:-glinet_gl-mt3000}"
OUT="${3:-bench}"
IB="${4:-$HOME/bench-images/imagebuilder}"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -d "$IB" ] || { echo "imagebuilder not found at $IB" >&2; exit 2; }
cd "$IB"
rm -rf files
mkdir -p files/etc/uci-defaults
cp -r "$HERE/image-files/." files/

# Only the root password here (the IP is applied late by rc.local).
cat > files/etc/uci-defaults/99-bench-lab <<'EOF'
#!/bin/sh
printf '%s\n' 'benchpw2026' 'benchpw2026' | passwd root >/dev/null 2>&1
exit 0
EOF
printf '%s\n' "$LAN_IP" > files/etc/bench-lan-ip
chmod +x files/etc/uci-defaults/99-bench-lab files/etc/rc.local

echo "=== building $PROFILE lan=$LAN_IP ==="
make image PROFILE="$PROFILE" PACKAGES="socat" FILES=files 2>&1 | tail -6
src="$(ls -t bin/targets/mediatek/filogic/*sysupgrade.bin | head -1)"
cp "$src" "$HOME/bench-images/bench-${OUT}-sysupgrade.bin"
ls -l "$HOME/bench-images/bench-${OUT}-sysupgrade.bin"
echo "DONE bench-${OUT}-sysupgrade.bin"
