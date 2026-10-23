#!/usr/bin/env bash
# =============================================================================
# hw-dispatch/enqueue.sh — drop a bench smoke job into the local queue.
# =============================================================================
# FALLBACK dispatch path (PRTA-REVIVE): when no self-hosted runner can take a
# workflow_dispatch, a bench-side cron (poll-queue.sh) pulls this queue and
# posts the verdict back to the commit via the GitHub statuses API:
#   gh api repos/<repo>/statuses/<sha>
#
# USAGE
#   scripts/hw-dispatch/enqueue.sh <full-sha> [purpose] [host]
#
# ENV
#   HW_QUEUE_SPOOL   spool dir (default ~/.hermes/state/hw-queue)
#   HW_QUEUE_REPO    owner/repo for status posts (default
#                    felixfelix-bot/physical-router-test-automation)
# =============================================================================
set -euo pipefail

SPOOL="${HW_QUEUE_SPOOL:-$HOME/.hermes/state/hw-queue}"
REPO="${HW_QUEUE_REPO:-felixfelix-bot/physical-router-test-automation}"

SHA="${1:?usage: enqueue.sh <full-sha> [purpose] [host]}"
PURPOSE="${2:-bench-smoke}"
HOST="${3:-${TOLLGATE_ROUTER_HOST:-192.168.1.1}}"

mkdir -p "$SPOOL"
ts="$(date -u +%Y%m%dT%H%M%S)"
job="$SPOOL/${ts}-${SHA:0:8}.json"

job_body="$(printf '{
  "sha": "%s",
  "purpose": "%s",
  "host": "%s",
  "repo": "%s",
  "enqueued_at": "%s",
  "enqueued_by": "%s@%s"
}' "$SHA" "$PURPOSE" "$HOST" "$REPO" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${USER:-unknown}" "$(hostname)")"

printf '%s\n' "$job_body" > "$job"
echo "enqueued: $job"
echo "poller will run the zero-secret admin-UI smoke under the bench lease and"
echo "post the verdict to $REPO statuses for ${SHA:0:12}"
