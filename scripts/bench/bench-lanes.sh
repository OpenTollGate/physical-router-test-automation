#!/usr/bin/env bash
# bench-lanes.sh — run every configured bench lane, sequentially, on the gateway.
#
# Reads config/bench-lanes.json (lane -> router image/pkg) and calls bench-cycle
# once per lane. Each lane takes the shared bench lock in turn; each guarantees
# radios-off in its own teardown. Emits a JSON summary and exits non-zero if any
# lane failed, so CI can gate on the aggregate.
#
# Usage: bench-lanes.sh [--config FILE] [--only LANE] [--result FILE]
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CONFIG="${BENCH_LANES_CONFIG:-$REPO/config/bench-lanes.json}"
ONLY=""; RESULT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --only)   ONLY="$2";   shift 2 ;;
    --result) RESULT="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -f "$CONFIG" ] || { echo "bench-lanes: no config at $CONFIG" >&2; exit 2; }

# nb: jq is expected on the gateway (installed with the lab image/tooling).
mapfile -t LANES < <(jq -r '.lanes[] | @base64' "$CONFIG")
[ "${#LANES[@]}" -gt 0 ] || { echo "bench-lanes: config has no lanes" >&2; exit 2; }

agg='{"lanes":['; first=1; rc=0
for b64 in "${LANES[@]}"; do
  row="$(printf '%s' "$b64" | base64 -d)"
  lane="$(printf '%s' "$row" | jq -r .lane)"
  [ -n "$ONLY" ] && [ "$lane" != "$ONLY" ] && continue
  router="$(printf '%s' "$row" | jq -r .router)"
  image="$(printf '%s' "$row" | jq -r '.image // empty')"
  pkg="$(printf '%s' "$row" | jq -r '.pkg // empty')"

  args=(--router "$router" --lane "$lane")
  [ -n "$image" ] && args+=(--image "$image" --confirm WIPE)
  [ -n "$pkg" ] && args+=(--pkg "$pkg")
  log="$(mktemp)"; resf="$(mktemp)"

  echo "[bench-lanes] lane=$lane router=$router" >&2
  if "$HERE/bench-cycle.sh" "${args[@]}" --result "$resf" >"$log" 2>&1; then lrc=0; else lrc=$?; rc=1; fi
  one="$(cat "$resf" 2>/dev/null || echo '{}')"
  [ "$first" = 1 ] || agg+=','; first=0
  agg+="$(printf '%s' "$one" | jq -c --argjson rc "$lrc" '. + {lane_rc:$rc}')"
  tail -3 "$log" >&2
  rm -f "$log" "$resf"
done
agg+=']}'

[ -n "$RESULT" ] && printf '%s\n' "$agg" >"$RESULT"
printf '%s\n' "$agg"
exit "$rc"
