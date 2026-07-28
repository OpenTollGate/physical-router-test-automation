#!/usr/bin/env bash
set -euo pipefail

# GCP VM Sweeper — finds and stops stale running instances.
#
# Install as a cron job (every 15 min) to prevent runaway costs:
#   crontab -e
#   */15 * * * * /path/to/prta/scripts/gcp-vm-sweeper.sh >> /tmp/gcp-sweeper.log 2>&1
#
# Usage:
#   ./scripts/gcp-vm-sweeper.sh              # report + stop VMs older than 2h
#   ./scripts/gcp-vm-sweeper.sh --dry-run    # report only, don't stop
#   ./scripts/gcp-vm-sweeper.sh --max-hours 4 # custom threshold
#   ./scripts/gcp-vm-sweeper.sh --delete      # delete instead of stop

MAX_HOURS=2
DRY_RUN=false
DELETE=false
PROJECT="${TOLLGATE_GCP_PROJECT:-tollgate-test-lab}"

for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=true ;;
    --delete)     DELETE=true ;;
    --max-hours)  shift; MAX_HOURS="${1:-2}" ;;
    [0-9]*)       MAX_HOURS="$arg" ;;
  esac
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] GCP VM Sweeper — threshold: ${MAX_HOURS}h  dry-run: ${DRY_RUN}  delete: ${DELETE}"

# Get all running instances with zone, name, and creation timestamp
INSTANCES=$(gcloud compute instances list \
  --filter="status:RUNNING" \
  --format="value(name,zone,creationTimestamp.date(tz=UTC))" \
  2>/dev/null || true)

if [[ -z "$INSTANCES" ]]; then
  echo "  No running instances. Clean."
  exit 0
fi

NOW_EPOCH=$(date -u +%s)
STOPPED=0
WARNED=0

while IFS=$'\t' read -r name zone created; do
  [[ -z "$name" ]] && continue

  # Parse creation timestamp to epoch seconds
  # Format from gcloud: 2026-07-26T21:53:00.000-07:00 or 2026-07-27T04:53:00.000Z
  created_clean=$(echo "$created" | sed 's/\.[0-9]*//' | sed 's/+00:00/Z/')
  created_epoch=$(date -u -d "$created_clean" +%s 2>/dev/null || echo 0)

  if [[ "$created_epoch" -eq 0 ]]; then
    echo "  WARN: $name ($zone) — could not parse creation time: $created"
    continue
  fi

  age_seconds=$((NOW_EPOCH - created_epoch))
  age_hours=$((age_seconds / 3600))
  age_minutes=$(( (age_seconds % 3600) / 60 ))

  # Get machine type for cost awareness
  machine=$(gcloud compute instances describe "$name" --zone="$zone" \
    --format="value(machineType.basename())" 2>/dev/null || echo "unknown")

  if [[ "$age_hours" -ge "$MAX_HOURS" ]]; then
    echo "  STALE: $name ($zone, $machine) — ${age_hours}h${age_minutes}m old — EXCEEDS ${MAX_HOURS}h threshold"

    if [[ "$DRY_RUN" == "true" ]]; then
      echo "    [dry-run] Would stop $name"
    elif [[ "$DELETE" == "true" ]]; then
      echo "    Deleting $name..."
      gcloud compute instances delete "$name" --zone="$zone" --delete-disks=all --quiet 2>&1 | tail -1
      STOPPED=$((STOPPED + 1))
    else
      echo "    Stopping $name..."
      gcloud compute instances stop "$name" --zone="$zone" --quiet 2>&1 | tail -1
      STOPPED=$((STOPPED + 1))
    fi
  elif [[ "$age_hours" -ge 1 ]]; then
    echo "  WARN: $name ($zone, $machine) — ${age_hours}h${age_minutes}m old"
    WARNED=$((WARNED + 1))
  else
    echo "  OK: $name ($zone, $machine) — ${age_minutes}m old"
  fi
done <<< "$INSTANCES"

echo ""
echo "Summary: $WARNED warned, $STOPPED stopped"

# Exit non-zero if any VMs were stale (useful for monitoring/alerting)
[[ $STOPPED -gt 0 ]] && exit 1
exit 0
