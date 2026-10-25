#!/usr/bin/env bash
# install-bench.sh — install the x280 bench routes and quiesce watchdog.
#
# Run as root on x280 from the automation checkout:
#   sudo ./deploy/x280/install-bench.sh
#
# Override PRTA_ROOT, E750_IFACE, MT3000_IFACE, COVR_HOST, etc. in the
# environment. The generated /etc/prta/bench.env is non-secret config.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRTA_ROOT="${PRTA_ROOT:-/home/c08r4d0r/src/prta-main}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

install -d /usr/local/sbin /etc/prta
install -m 0755 "$ROOT/scripts/prta-bench-routes.sh" /usr/local/sbin/prta-bench-routes.sh
install -m 0755 "$ROOT/scripts/prta-quiesce-watchdog.sh" /usr/local/sbin/prta-quiesce-watchdog.sh
install -m 0644 "$ROOT/deploy/systemd/prta-bench-routes.service" "$UNIT_DIR/prta-bench-routes.service"
install -m 0644 "$ROOT/deploy/systemd/prta-quiesce.service" "$UNIT_DIR/prta-quiesce.service"
install -m 0644 "$ROOT/deploy/systemd/prta-quiesce.timer" "$UNIT_DIR/prta-quiesce.timer"

if [ ! -f /etc/prta/bench.env ]; then
  cat >/etc/prta/bench.env <<EOF
E750_IFACE=${E750_IFACE:-enx9c69d3ca3cfe}
E750_ADDR=${E750_ADDR:-192.168.8.244/24}
MT3000_IFACE=${MT3000_IFACE:-enx6c6e0750c33b}
MT3000_ADDR=${MT3000_ADDR:-10.110.223.2/24}
COVR_HOST=${COVR_HOST:-192.168.50.218}
COVR_IFACE=${COVR_IFACE:-enx6c6e0750cd79}
COVR_SRC=${COVR_SRC:-192.168.50.10}
EOF
  chmod 0644 /etc/prta/bench.env
fi

# Keep the watchdog's checkout path in sync without editing the unit by hand.
if [ "$PRTA_ROOT" != "/home/c08r4d0r/src/prta-main" ]; then
  sed -i "s#^Environment=PRTA_ROOT=.*#Environment=PRTA_ROOT=$PRTA_ROOT#" \
    "$UNIT_DIR/prta-quiesce.service"
fi

systemctl daemon-reload
systemctl enable --now prta-bench-routes.service
systemctl enable --now prta-quiesce.timer
systemctl is-active prta-bench-routes.service prta-quiesce.timer
