#!/usr/bin/env bash
# build-image.sh <LAN_IP> [PROFILE] [OUTNAME] [IMAGEBUILDER_DIR]
#
# Build a bench lab image with the OpenWrt ImageBuilder. It bakes:
#   * a fixed LAN IP (so the box is reachable at a known address after a flash),
#   * the documented bench root password (so the SSH cycle needs no console),
#   * radios OFF by default  (a late /etc/rc.local hook, so it survives the
#     wifi config being generated later in boot — the uci-defaults run too early),
#   * socat (the serial-into-Linux fallback's transport tool).
#
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

cat > files/etc/uci-defaults/99-bench-lab <<EOF
#!/bin/sh
# Bench lab defaults: fixed LAN IP + documented root password.
uci set network.lan.ipaddr=$LAN_IP
uci set network.lan.netmask=255.255.255.0
uci commit network
printf '%s\n' 'benchpw2026' 'benchpw2026' | passwd root >/dev/null 2>&1
exit 0
EOF
chmod +x files/etc/uci-defaults/99-bench-lab files/etc/rc.local

echo "=== building $PROFILE lan=$LAN_IP ==="
make image PROFILE="$PROFILE" PACKAGES="socat" FILES=files 2>&1 | tail -6
src="$(ls -t bin/targets/mediatek/filogic/*sysupgrade.bin | head -1)"
cp "$src" "$HOME/bench-images/bench-${OUT}-sysupgrade.bin"
ls -l "$HOME/bench-images/bench-${OUT}-sysupgrade.bin"
echo "DONE bench-${OUT}-sysupgrade.bin"
