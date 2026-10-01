#!/usr/bin/env bash
# =============================================================================
# hw-dispatch/poll-queue.sh — bench-side queue consumer (fallback dispatch).
# =============================================================================
# PRTA-REVIVE FALLBACK lane: cron pulls jobs enqueued by enqueue.sh and runs
# the zero-secret admin-UI smoke under the ONE machine-global bench lease,
# then posts the verdict to the commit via the GitHub statuses API.
#
# Composition (deliberately boring):
#   * the poll loop itself is flock'd (one poller at a time);
#   * the RUN goes through scripts/hw-bench-lease exec — the same flock every
#     other agent window takes, plus the session_verify idle gate;
#   * the test payload is `nice -d` so a human on the bench keeps priority;
#   * verdicts: gh api repos/<repo>/statuses/<sha> with context hw-smoke/bench.
#
# Lease-busy (rc 2) is NOT a failure: the job stays queued for the next tick.
#
# USAGE
#   scripts/hw-dispatch/poll-queue.sh [--dry-run] [--all]
#     --dry-run   do everything except the gh status post (and print it)
#     --all       drain the whole queue (default: one job per tick)
#
# ENV
#   HW_QUEUE_SPOOL     spool dir (default ~/.hermes/state/hw-queue)
#   HW_QUEUE_REPO      owner/repo for status posts
#   HW_QUEUE_CHECKOUT  repo checkout to run from (default: this script's repo)
#   TOLLGATE_ROUTER_HOST  bench host default (job file wins)
#
# CRON (install ONLY while the runner path is unavailable — one dispatch
# consumer at a time; see scripts/hw-dispatch/README.md):
#   */10 * * * * ~/physical-router-test-automation/scripts/hw-dispatch/poll-queue.sh >> ~/.hermes/state/hw-queue/poll.log 2>&1
# =============================================================================
set -uo pipefail

SPOOL="${HW_QUEUE_SPOOL:-$HOME/.hermes/state/hw-queue}"
REPO="${HW_QUEUE_REPO:-felixfelix-bot/physical-router-test-automation}"
CHECKOUT="${HW_QUEUE_CHECKOUT:-$(cd "$(dirname "$0")/.." && pwd)}"
LEASE="$CHECKOUT/scripts/hw-bench-lease"

DRY_RUN=0
DRAIN_ALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --all) DRAIN_ALL=1; shift ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$SPOOL" "$SPOOL/done"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# One poller at a time; never block a cron tick.
exec 9>"$SPOOL/.poll.lock"
if ! flock -n 9; then
    log "another poller holds $SPOOL/.poll.lock — skipping this tick"
    exit 0
fi

field() { # field <json-file> <key> — crude but dependency-free
    sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$1" | head -1
}

post_status() { # post_status <sha> <state> <description>
    local sha="$1" state="$2" desc="$3"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "DRY-RUN gh api repos/$REPO/statuses/$sha -f state=$state -f context=hw-smoke/bench -f description=\"$desc\""
        return 0
    fi
    if ! command -v gh >/dev/null 2>&1; then
        log "gh not installed — cannot post status for $sha; job kept in done/ with verdict"
        return 0
    fi
    gh api "repos/$REPO/statuses/$sha" \
        -f state="$state" \
        -f context="hw-smoke/bench" \
        -f description="$desc" >/dev/null || log "gh status post FAILED for $sha"
}

run_one_job() { # run_one_job <job-file>
    local job="$1"
    local sha purpose host
    sha="$(field "$job" sha)"
    purpose="$(field "$job" purpose)"
    host="$(field "$job" host)"
    host="${host:-${TOLLGATE_ROUTER_HOST:-192.168.1.1}}"

    if [ -z "$sha" ]; then
        log "job $job has no sha — quarantining to done/"
        mv "$job" "$SPOOL/done/$(basename "$job").no-sha"
        return 0
    fi

    log "running job $(basename "$job") sha=${sha:0:12} purpose=$purpose host=$host"

    if [ ! -d "$CHECKOUT/node_modules" ]; then
        log "no node_modules in $CHECKOUT — running npm ci --prefer-offline"
        (cd "$CHECKOUT" && npm ci --prefer-offline --no-audit --no-fund) \
            || { post_status "$sha" failure "hw-queue: npm ci failed"; \
                 mv "$job" "$SPOOL/done/"; return 0; }
    fi

    local rc=0
    (cd "$CHECKOUT" && TOLLGATE_ROUTER_HOST="$host" \
        python3 "$LEASE" exec \
            --purpose "hw-queue:$purpose" \
            --check-idle \
            --host "$host" \
            -- nice -n 10 npx playwright test --config=tests/browser/admin-ui.config.mjs) \
        || rc=$?

    case "$rc" in
        0)
            post_status "$sha" success "hw-smoke admin-UI walkthrough green (bench lease, idle-gated)"
            mv "$job" "$SPOOL/done/"
            log "job $(basename "$job") GREEN"
            ;;
        2)
            # Lease busy: leave the job queued, retry next tick. Not a failure
            # of the code under test.
            log "bench lease BUSY — job $(basename "$job") stays queued"
            ;;
        4)
            post_status "$sha" failure "hw-smoke: idle gate refused — a paying session was live"
            mv "$job" "$SPOOL/done/"
            log "job $(basename "$job") IDLE-REFUSED"
            ;;
        5)
            post_status "$sha" failure "hw-smoke: bench unreachable / balance not probeable (fail closed)"
            mv "$job" "$SPOOL/done/"
            log "job $(basename "$job") UNREACHABLE"
            ;;
        *)
            post_status "$sha" failure "hw-smoke admin-UI walkthrough failed (rc=$rc)"
            mv "$job" "$SPOOL/done/"
            log "job $(basename "$job") RED rc=$rc"
            ;;
    esac
    return 0
}

shopt -s nullglob
jobs_list=("$SPOOL"/*.json)
shopt -u nullglob

if [ "${#jobs_list[@]}" -eq 0 ]; then
    log "queue empty"
    exit 0
fi

if [ "$DRAIN_ALL" -eq 1 ]; then
    for job in "${jobs_list[@]}"; do
        run_one_job "$job"
    done
else
    run_one_job "${jobs_list[0]}"
fi
exit 0
