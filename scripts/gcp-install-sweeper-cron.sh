#!/usr/bin/env bash
set -euo pipefail

# Installs gcp-vm-sweeper.sh as a cron job (every 15 minutes).
# Requires: gcloud CLI authenticated.
#
# Usage:
#   ./scripts/gcp-install-sweeper-cron.sh           # every 15 min, stop after 2h
#   ./scripts/gcp-install-sweeper-cron.sh --dry-run  # report only, never stop
#   TOLLGATE_SWEEPER_HOURS=4 ./scripts/gcp-install-sweeper-cron.sh  # custom threshold

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SWEEPER="$SCRIPT_DIR/gcp-vm-sweeper.sh"
HOURS="${TOLLGATE_SWEEPER_HOURS:-2}"
EXTRA_ARGS=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) EXTRA_ARGS="--dry-run" ;;
  esac
done

CRON_CMD="*/15 * * * * $SWEEPER --max-hours $HOURS $EXTRA_ARGS >> /tmp/gcp-sweeper.log 2>&1"

if ! command -v gcloud &>/dev/null; then
  echo "ERROR: gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
  exit 1
fi

if [[ ! -x "$SWEEPER" ]]; then
  echo "ERROR: $SWEEPER not found or not executable"
  exit 1
fi

echo "Installing GCP VM sweeper cron job:"
echo "  Schedule: every 15 minutes"
echo "  Threshold: ${HOURS}h"
echo "  Mode: $([[ -n "$EXTRA_ARGS" ]] && echo 'dry-run (report only)' || echo 'active (stops stale VMs)')"
echo "  Log: /tmp/gcp-sweeper.log"
echo ""

# Add to crontab (don't duplicate if already present)
CRON_EXISTING=$(crontab -l 2>/dev/null || true)
if echo "$CRON_EXISTING" | grep -q "gcp-vm-sweeper"; then
  echo "Sweeper already in crontab. Updating..."
  echo "$CRON_EXISTING" | grep -v "gcp-vm-sweeper" | { cat; echo "$CRON_CMD"; } | crontab -
else
  (echo "$CRON_EXISTING"; echo "$CRON_CMD") | crontab -
fi

echo "Installed. Current crontab:"
crontab -l 2>/dev/null | grep -A0 "sweeper" || true
echo ""
echo "To test manually:"
echo "  $SWEEPER --dry-run"
echo ""
echo "To uninstall:"
echo "  crontab -l | grep -v gcp-vm-sweeper | crontab -"
