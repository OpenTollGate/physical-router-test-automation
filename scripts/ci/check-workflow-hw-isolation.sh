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
#       reach a bench runner. "Reach a bench runner" is decided on the LABEL
#       SET, not on the literal `self-hosted` string: every label used by the
#       hardware workflow's `runs-on` (plus `self-hosted`, plus anything in
#       HW_RUNNER_LABELS_EXTRA) is denied, and an expression-valued `runs-on:`
#       is rejected outright because it cannot be verified by reading. It must
#       also not reference any bench-mutating env flag.
#   R2  The hardware workflow (default `hw-smoke.yml`) must exist, must NOT be
#       PR-reachable, and its triggers must be a subset of
#       {workflow_dispatch, schedule} — maintainer-triggered only.
#   R3  No disabled job parked behind a falsy `if:` anywhere in
#       .github/workflows/ (case-insensitive `false` plus the YAML-1.1 falsy
#       words `no`/`off`). A job parked behind a flag is an invitation to flip
#       it; enablement must be an explicit input / repo variable / environment
#       approval.
#   R4  Any job that references a bench-mutating env flag must declare
#       `environment:` NAMED `$HW_ENVIRONMENT_NAME` (default `bench-hardware`) —
#       presence of the key is not enough, a typo'd environment would silently
#       drop the required-reviewer gate.
#   R5  In the hardware workflow, any job that uses `secrets.` must declare the
#       same named environment too — the read-only lane must stay
#       credential-free.
#
# LIMIT (stated, not hidden): this guard runs from the PR's own checkout, so a
# hostile PR can delete it in the same commit that adds the self-hosted job.
# For that class of PR the mechanical protection is branch protection + the
# `lint` check being REQUIRED, not this script. See docs/hw-lane-isolation.md.
#
# Only effective YAML is scanned: full-line and trailing comments are stripped
# first, so documentation prose (which necessarily names the anti-patterns it
# bans) cannot trip the guard.
#
# USAGE
#   bash scripts/ci/check-workflow-hw-isolation.sh [WORKFLOWS_DIR]
#   Exit 0 = safe. Exit 1 = unsafe (every violation printed as file: R# …).
#   Env knobs: HW_WORKFLOW_NAME, HW_ENVIRONMENT_NAME, HW_RUNNER_LABELS_EXTRA
#              (space-separated extra runner labels to deny).
#
# The guard's own RED/GREEN matrix lives in
# scripts/ci/test-check-workflow-hw-isolation.sh — a guard never seen red is
# decoration, not evidence. It runs in the ci.yml `lint` job on every PR.
# =============================================================================
set -uo pipefail

WF_DIR="${1:-.github/workflows}"
HW_WORKFLOW_NAME="${HW_WORKFLOW_NAME:-hw-smoke.yml}"
HW_ENVIRONMENT_NAME="${HW_ENVIRONMENT_NAME:-bench-hardware}"
HW_RUNNER_LABELS_EXTRA="${HW_RUNNER_LABELS_EXTRA:-}"
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
        | tr -d " \"'" \
        | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
        | grep -v '^$' || true
}

is_pr_reachable() {
    triggers_of "$1" | grep -qxE 'pull_request|pull_request_target'
}

# Lowercased runner labels used by the hardware workflow's `runs-on` lines.
hw_runner_labels() {
    [ -f "$1" ] || return 0
    yaml_effective "$1" \
        | grep -nE '^[[:space:]]*runs-on:' \
        | sed -E 's/^[0-9]+:[[:space:]]*runs-on:[[:space:]]*//' \
        | tr -d "[]\"'" \
        | tr ',' '\n' \
        | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
        | grep -v '^$' \
        | grep -v '\${{' \
        | tr '[:upper:]' '[:lower:]' || true
}

echo "Hardware-lane isolation guard"
echo "  workflows dir : $WF_DIR"
echo "  hardware wf   : $HW_WORKFLOW_NAME"
echo "  approval env  : $HW_ENVIRONMENT_NAME"
echo

shopt -s nullglob
wf_files=("$WF_DIR"/*.yml "$WF_DIR"/*.yaml)
shopt -u nullglob

if [ "${#wf_files[@]}" -eq 0 ]; then
    echo "FATAL: no workflow files in $WF_DIR" >&2
    exit 2
fi

# Denied label set: `self-hosted` always, every label the hardware workflow
# itself uses, plus operator-supplied extras. Compared case-insensitively.
hw_path="$WF_DIR/$HW_WORKFLOW_NAME"
bench_labels="$(printf '%s\n%s\n' "self-hosted" "$(hw_runner_labels "$hw_path")" | sort -u | grep -v '^$' || true)"
if [ -n "$HW_RUNNER_LABELS_EXTRA" ]; then
    bench_labels="$(printf '%s\n%s\n' "$bench_labels" "$(printf '%s' "$HW_RUNNER_LABELS_EXTRA" | tr ' ' '\n')" \
        | sort -u | grep -v '^$' || true)"
fi

is_bench_label() {
    local l
    l="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    [ -n "$l" ] || return 1
    printf '%s\n' "$bench_labels" | grep -qxF "$l"
}

# ---------------------------------------------------------------------------
# R1 + R3 — whole-tree rules
# ---------------------------------------------------------------------------
for f in "${wf_files[@]}"; do
    if is_pr_reachable "$f"; then
        # R1a: no bench runner label, and no unverifiable expression-valued runs-on.
        while IFS= read -r hit; do
            [ -n "$hit" ] || continue
            n="${hit%%:*}"
            lin="${hit#*:}"
            val="${lin#*runs-on:}"
            val="${val%%#*}"
            if printf '%s' "$val" | grep -q '\${{'; then
                violate "$f:$n  R1 expression-valued runs-on:'$val' is not verifiable by reading — PR-reachable bench routing must be a literal label (fail closed)"
                continue
            fi
            for lab in $(printf '%s' "$val" | tr -d "[]\"'" | tr ',' ' '); do
                if is_bench_label "$lab"; then
                    violate "$f:$n  R1 self-hosted runner label '$lab' reachable from pull_request"
                fi
            done
        done < <(yaml_effective "$f" | grep -nE '^[[:space:]]*runs-on:' || true)

        # R1b: no bench-mutating env flag.
        while IFS= read -r hit; do
            [ -n "$hit" ] || continue
            violate "$f:$hit  R1 bench-mutating env flag reachable from pull_request"
        done < <(yaml_effective "$f" | grep -n 'TOLLGATE_ENABLE_' || true)
    fi

    # R3: case-insensitive false + the YAML-1.1 falsy words.
    while IFS= read -r hit; do
        [ -n "$hit" ] || continue
        violate "$f:$hit  R3 disabled job parked behind \`if: false\`"
    done < <(yaml_effective "$f" | grep -nEi '^[[:space:]]*if:[[:space:]]*(false|no|off)[[:space:]]*$' || true)
done

# ---------------------------------------------------------------------------
# R2 — the hardware workflow is dispatch/schedule only and must exist
# ---------------------------------------------------------------------------
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
        awk -v file="$hw_path" -v wantenv="$HW_ENVIRONMENT_NAME" '
            function envdesc() { return (env_seen ? ("found: " env_name) : "found: none") }
            function flush() {
                if (job == "") return
                if (mutating && !env_ok)
                    printf "VIOLATION %s  R4 job \"%s\" references bench-mutating env flags without an `environment: %s` approval gate (%s)\n", file, job, wantenv, envdesc()
                if (uses_secrets && !env_ok)
                    printf "VIOLATION %s  R5 job \"%s\" uses `secrets.` without an `environment: %s` approval gate (%s)\n", file, job, wantenv, envdesc()
            }
            /^  [A-Za-z0-9_-]+:/ {
                flush()
                job = $1; sub(/:.*/, "", job)
                mutating = 0; uses_secrets = 0; env_seen = 0; env_name = ""; env_ok = 0
            }
            job != "" {
                if ($0 ~ /^    environment:/) {
                    env_seen = 1
                    v = $0
                    sub(/^[[:space:]]*environment:[[:space:]]*/, "", v)
                    sub(/[[:space:]]+$/, "", v)
                    if (v != "") { env_name = v; if (v == wantenv) env_ok = 1 }
                } else if (env_seen && env_name == "" && $0 ~ /^      name:/) {
                    v = $0
                    sub(/^[[:space:]]*name:[[:space:]]*/, "", v)
                    sub(/[[:space:]]+$/, "", v)
                    env_name = v
                    if (v == wantenv) env_ok = 1
                }
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
