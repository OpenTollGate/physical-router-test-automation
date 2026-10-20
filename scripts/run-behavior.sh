#!/usr/bin/env bash
# Run every available TollGate backend against the behaviour stories
# (user-stories.md / stories.yaml) and write the differential report.
#
# A backend runs only when its environment is present; absent backends keep
# their stories.yaml status and the report says why they did not run.
#
# Opt-in env (a backend never runs by accident):
#   TOLLGATE_RS_BIN_DIR      rust-v2 lane (auto if the dir exists)
#   TOLLGATE_BEHAVIOR_GO=1   go lane (needs the physical router env of TEST_SETUP.md)
#   TOLLGATE_BEHAVIOR_RUST=1         V1 fork lane (needs TOLLGATE_BINARY_PATH)
#   TOLLGATE_BEHAVIOR_RUST_BASIC=1   (needs TOLLGATE_BINARY_PATH)
set -euo pipefail
cd "$(dirname "$0")/.."

OUT_DIR=results/behavior
mkdir -p "$OUT_DIR"
JUNIT_ARGS=()
SUMMARY=("$OUT_DIR/run-summary.txt")
: > "${SUMMARY}"

run_rust_v2() {
    local bin_dir="${TOLLGATE_RS_BIN_DIR:-}"
    if [ -z "$bin_dir" ]; then
        for d in /root/.cargo-target/release "$HOME/.cargo-target/release" \
                 "$HOME/src/tollgate-rs/target/release"; do
            [ -x "$d/tollgated" ] && bin_dir="$d" && break
        done
    fi
    if [ -z "$bin_dir" ] || [ ! -x "$bin_dir/tollgated" ]; then
        echo "rust-v2: skipped — no tollgate-rs release binaries (build them or set TOLLGATE_RS_BIN_DIR)" | tee -a "${SUMMARY}"
        return
    fi
    echo "rust-v2: running lane from $bin_dir" | tee -a "${SUMMARY}"
    local py="${TOLLGATE_TEST_PYTHON:-$HOME/.tollgate-test-venv/bin/python3}"
    [ -x "$py" ] || py=python3
    if TOLLGATE_RS_BIN_DIR="$bin_dir" "$py" -m pytest \
        tests/backends/test_tollgate_rs_v2_host.py \
        tests/backends/test_tollgate_rs_v2_limits.py \
        --backend rust-v2 -q \
        --junitxml="$OUT_DIR/rust-v2.xml" > /dev/null; then
        echo "rust-v2: lane finished" | tee -a "${SUMMARY}"
    else
        echo "rust-v2: lane FAILED — junit carries the detail" | tee -a "${SUMMARY}"
    fi
    JUNIT_ARGS+=(--junit "$OUT_DIR/rust-v2.xml")
}

maybe() {  # name, flag env, requirement text
    if [ "${!2:-}" = "1" ]; then
        echo "$1: opted in but this runner does not drive it yet — recorded as not-run" | tee -a "${SUMMARY}"
    else
        echo "$1: not run — opt in with $2=1 ($3)" | tee -a "${SUMMARY}"
    fi
}

run_rust_v2
maybe go       TOLLGATE_BEHAVIOR_GO         "physical router env"
maybe rust     TOLLGATE_BEHAVIOR_RUST       "V1 fork binary"
maybe rust-basic TOLLGATE_BEHAVIOR_RUST_BASIC "tollgate-module-basic-rust binary"

py="${TOLLGATE_TEST_PYTHON:-$HOME/.tollgate-test-venv/bin/python3}"
[ -x "$py" ] || py=python3
exec "$py" lib/behavior_report.py \
    --stories tests/conformance/stories.yaml \
    "${JUNIT_ARGS[@]}" \
    --out "$OUT_DIR/behavior-report.md"
