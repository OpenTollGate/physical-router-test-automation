#!/usr/bin/env bash
# Run the tollgate-rs (rust-v2) host-venue lane: real tollgated/mintd/merchantd
# processes on this host, native TollGate protocol, control-socket assertions.
#
# Usage: scripts/run-rust-v2.sh [extra pytest args]
#
# Env:
#   TOLLGATE_RS_BIN_DIR  release binaries (default: cargo's shared target dir,
#                        then ~/src/tollgate-rs/target/release)
#   TOLLGATE_RS_REPO     clone to build from if the binaries are missing
set -euo pipefail
cd "$(dirname "$0")/.."

DEFAULT_BIN_DIRS=(
    "/root/.cargo-target/release"
    "$HOME/.cargo-target/release"
    "$HOME/src/tollgate-rs/target/release"
)
for d in "${DEFAULT_BIN_DIRS[@]}"; do
    if [ -z "${TOLLGATE_RS_BIN_DIR:-}" ] && [ -x "$d/tollgated" ]; then
        export TOLLGATE_RS_BIN_DIR="$d"
    fi
done

if [ -z "${TOLLGATE_RS_BIN_DIR:-}" ] || [ ! -x "$TOLLGATE_RS_BIN_DIR/tollgated" ]; then
    REPO="${TOLLGATE_RS_REPO:-$HOME/src/tollgate-rs}"
    if [ ! -d "$REPO" ]; then
        git clone https://github.com/OpenTollGate/tollgate-rs "$REPO"
    fi
    (cd "$REPO" && cargo build --release --bins)
    export TOLLGATE_RS_BIN_DIR="$(cd "$REPO" && cargo metadata --format-version 1 --no-deps 2>/dev/null | jq -r .target_directory)/release"
fi

PY="${TOLLGATE_TEST_PYTHON:-$HOME/.tollgate-test-venv/bin/python3}"
[ -x "$PY" ] || PY=python3

exec "$PY" -m pytest tests/backends/test_tollgate_rs_v2_host.py --backend rust-v2 -v "$@"
