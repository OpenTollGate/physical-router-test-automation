#!/usr/bin/env bash
# watch-feed-release — poll FreedomTechFeed/packages for new tollgate-wrt
# releases and notify when the newest tag changes. Release-day trigger for
# the official-path validation (venue bring-up + install-and-test.sh with
# the new tag + the payment rejection suite).
#
# Run once by hand to baseline (records the current tag, no alert), then
# install from cron, e.g. every 30 minutes:
#   */30 * * * * $HOME/src/physical-router-test-automation/scripts/watch-feed-release.sh
#
# State: ~/.cache/tollgate-feed-watcher/{state,log,TRIGGERED}
set -u
STATE_DIR="$HOME/.cache/tollgate-feed-watcher"
STATE="$STATE_DIR/state"
LOG="$STATE_DIR/log"
mkdir -p "$STATE_DIR"

NEWEST=$(curl -fsS --max-time 20 \
  'https://api.github.com/repos/FreedomTechFeed/packages/releases?per_page=10' \
  | python3 -c 'import sys,json; rs=[r for r in json.load(sys.stdin) if not r.get("draft")]; print(max(rs, key=lambda r: r["published_at"])["tag_name"])' \
  2>/dev/null)
if [ -z "$NEWEST" ]; then
  echo "$(date -Is) fetch-failed" >>"$LOG"
  exit 0
fi

LAST=$(cat "$STATE" 2>/dev/null || echo "")
if [ -z "$LAST" ]; then
  printf '%s' "$NEWEST" >"$STATE"
  echo "$(date -Is) baseline=$NEWEST" >>"$LOG"
elif [ "$NEWEST" != "$LAST" ]; then
  printf '%s' "$NEWEST" >"$STATE"
  echo "$(date -Is) NEW RELEASE: $LAST -> $NEWEST" >>"$LOG"
  touch "$STATE_DIR/TRIGGERED"
  command -v notify-send >/dev/null 2>&1 && notify-send -u critical \
    "TollGate feed moved: $NEWEST" \
    "$LAST → $NEWEST — re-run the official-path validation (log: $LOG)"
fi
