#!/usr/bin/env bash
# =============================================================================
# Guard: no workflow reachable from an untrusted PR event may touch the bench.
# =============================================================================
# WHY: a `pull_request` trigger plus a `[self-hosted, tollgate-router]` runner
# means any contributor's PR code executes on the machine that flashes routers,
# holds the repo secrets and sits in the bench's L1/L2 neighbourhood. The
# previous state (ci.yml `test-ui` / `test-physical` at `if: false` on a
# pull_request-triggered workflow) was one flipped boolean away from that, and
# the job force-enabled router-mutating, paid-traffic specs besides.
#
# RULES
#   R1  A workflow with a pull_request / pull_request_target trigger must not
#       reference a self-hosted runner, nor any bench-mutating env flag.
#   R2  The hardware workflow (default `hw-smoke.yml`) must exist, must NOT be
#       PR-reachable, and its triggers must be a subset of
#       {workflow_dispatch, schedule} — maintainer-triggered only.
#   R3  No `if: false` job guards anywhere in .github/workflows/. A disabled job
#       parked behind a flag is an invitation to flip it; enablement must be an
#       explicit input / repo variable / environment approval.
#   R4  Any job that references a bench-mutating env flag must declare
#       `environment:` (i.e. sit behind required reviewers).
#   R5  In the hardware workflow, any job that uses `secrets.` must declare
#       `environment:` too — the read-only lane must stay credential-free.
#
# Only effective YAML is scanned: full-line and trailing comments are stripped
# first, so documentation prose (which necessarily names the anti-patterns it
# bans) cannot trip the guard.
#
# USAGE
#   bash scripts/ci/check-workflow-hw-isolation.sh [WORKFLOWS_DIR]
#   Exit 0 = safe. Exit 1 = unsafe (every violation printed as file: R# …).
#
# The guard's own RED/GREEN matrix lives in
# scripts/ci/test-check-workflow-hw-isolation.sh — a guard never seen red is
# decoration, not evidence. It runs in the ci.yml `lint` job on every PR.
# =============================================================================
set -uo pipefail

WF_DIR="${1:-.github/workflows}"
HW_WORKFLOW_NAME="${HW_WORKFLOW_NAME:-hw-smoke.yml}"
HW_TRIGGER_WHITELIST="workflow_dispatch schedule"

if [ ! -d "$WF_DIR" ]; then
    echo "FATAL: workflows dir not found: $WF_DIR" >&2
    exit 2
fi

violations=0
note() { printf '%s\n' "$*"; }
violate() { violations=$((violations + 1)); printf 'VIOLATION %s\n' "$*"; }

# YAML-effective text: drop full-line and trailing comments.
yaml_effective() {
    sed -E 's/(^|[[:space:]])#.*$/\1/' "$1"
}

# The `on:` trigger section (block form) plus any inline list on the `on:` line.
on_block() {
    awk '
        /^on[[:space:]]*:/ {
            f = 1
            inline = $0
            sub(/^on[[:space:]]*:/, "", inline)
            if (inline !~ /^[[:space:]]*$/) print inline
            next
        }
        f && /^[^[:space:]]/ { exit }
        f { print }
    ' "$1"
}

# Trigger names, one per line (block form at exactly 2-space indent, or inline
# list). Scoped to the `on:` section: job names are also 2-space keys, so a
# whole-file scan would mistake them for triggers.
triggers_of() {
    on_block "$1" \
        | sed -E 's/(^|[[:space:]])#.*$/\1/' \
        | sed -n -E 's/^\[(.*)\][[:space:]]*$/\1/p; s/^  ([A-Za-z_][A-Za-z0-9_-]*)[[:space:]]*:.*/\1/p' \
        | tr ',' '\n' \
        | tr -d ' "'"'" \
        | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
        | grep -v '^$' || true
}

is_pr_reachable() {
    triggers_of "$1" | grep -qxE 'pull_request|pull_request_target'
}

echo "Hardware-lane isolation guard"
echo "  workflows dir : $WF_DIR"
echo "  hardware wf   : $HW_WORKFLOW_NAME"
echo

shopt -s nullglob
wf_files=("$WF_DIR"/*.yml "$WF_DIR"/*.yaml)
shopt -u nullglob

if [ "${#wf_files[@]}" -eq 0 ]; then
    echo "FATAL: no workflow files in $WF_DIR" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# R1 + R3 — whole-tree rules
# ---------------------------------------------------------------------------
for f in "${wf_files[@]}"; do
    if is_pr_reachable "$f"; then
        while IFS= read -r hit; do
            [ -n "$hit" ] || continue
            violate "$f:$hit  R1 self-hosted runner reachable from pull_request"
        done < <(yaml_effective "$f" | grep -n 'self-hosted' || true)

        while IFS= read -r hit; do
            [ -n "$hit" ] || continue
            violate "$f:$hit  R1 bench-mutating env flag reachable from pull_request"
        done < <(yaml_effective "$f" | grep -n 'TOLLGATE_ENABLE_' || true)
    fi

    while IFS= read -r hit; do
        [ -n "$hit" ] || continue
        violate "$f:$hit  R3 disabled job parked behind \`if: false\`"
    done < <(yaml_effective "$f" | grep -nE '^[[:space:]]*if:[[:space:]]*false[[:space:]]*$' || true)
done

# ---------------------------------------------------------------------------
# R2 — the hardware workflow is dispatch/schedule only and must exist
# ---------------------------------------------------------------------------
hw_path="$WF_DIR/$HW_WORKFLOW_NAME"
if [ ! -f "$hw_path" ]; then
    violate "$hw_path  R2 hardware workflow missing (bench work must live in its own dispatch-only workflow)"
else
    if is_pr_reachable "$hw_path"; then
        violate "$hw_path  R2 hardware workflow is reachable from pull_request"
    fi
    while IFS= read -r trig; do
        [ -n "$trig" ] || continue
        case " $HW_TRIGGER_WHITELIST " in
            *" $trig "*) : ;;
            *) violate "$hw_path  R2 trigger '$trig' not allowed (whitelist: $HW_TRIGGER_WHITELIST)" ;;
        esac
    done < <(triggers_of "$hw_path")

    # -----------------------------------------------------------------------
    # R4 + R5 — job-scoped rules, hardware workflow only
    # -----------------------------------------------------------------------
    job_rule_report="$(
        awk -v file="$hw_path" '
            function flush() {
                if (job == "") return
                if (mutating && !env)
                    printf "VIOLATION %s  R4 job \"%s\" references bench-mutating env flags without an `environment:` approval gate\n", file, job
                if (uses_secrets && !env)
                    printf "VIOLATION %s  R5 job \"%s\" uses `secrets.` without an `environment:` approval gate\n", file, job
            }
            /^  [A-Za-z0-9_-]+:/ {
                flush()
                job = $1; sub(/:.*/, "", job)
                mutating = 0; env = 0; uses_secrets = 0
            }
            job != "" {
                if ($0 ~ /^    environment:/) env = 1
                if ($0 ~ /TOLLGATE_ENABLE_(WIFI_CLIENT|DATA_ALLOTMENT)_TESTS/) mutating = 1
                if ($0 ~ /secrets\./) uses_secrets = 1
            }
            END { flush() }
        ' "$hw_path"
    )"
    if [ -n "$job_rule_report" ]; then
        while IFS= read -r line; do
            [ -n "$line" ] || continue
            printf '%s\n' "$line"
            violations=$((violations + 1))
        done <<< "$job_rule_report"
    fi
fi

echo
if [ "$violations" -eq 0 ]; then
    note "OK — no workflow reachable from pull_request can reach the bench."
    exit 0
fi
note "FAILED — $violations violation(s). See rules R1-R5 in $(basename "$0")."
exit 1
