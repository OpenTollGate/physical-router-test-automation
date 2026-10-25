#!/usr/bin/env bash
# prta-bench-run.sh — run a router test with a lease-scoped bench key.
#
# The caller must hold the machine-global bench lease, e.g.:
#   scripts/hw-bench-lease exec --purpose "test-pr 42" -- \
#     scripts/prta-bench-run.sh -- ./scripts/test-pr.sh --pr 42
#
# This wrapper:
#   * refuses to run without the lease;
#   * logs in to OpenBao with the physical-router-automation AppRole;
#   * materializes secret/fleet/test-routers-bench:ssh_private_key in a 0600
#     temporary file and exports TOLLGATE_SSH_KEY;
#   * removes the key and quiesces all test-router APs on exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

if [ "${1:-}" = "--" ]; then
  shift
fi
if [ "$#" -eq 0 ]; then
  echo "usage: $0 -- <command...>" >&2
  exit 2
fi

# Fail closed if the bench lease is not held. The wrapper does not acquire it;
# the enclosing hw-bench-lease exec owns it, so nested entrypoints agree.
python3 - <<'PY'
from lib.hardware_lock import require_hardware_lock
require_hardware_lock()
PY

. "$ROOT/scripts/openbao-approle-login.sh"

if ! command -v bao >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/bao" ]; then
    BAO_BIN="$HOME/.local/bin/bao"
  else
    echo "ERROR: bao CLI not found" >&2
    exit 1
  fi
else
  BAO_BIN="$(command -v bao)"
fi

KEY_FILE="$(mktemp "${TMPDIR:-/tmp}/prta-bench-key.XXXXXX")"
chmod 600 "$KEY_FILE"
# Quiesce while the key still exists, then remove it.
trap 'python3 "$ROOT/scripts/quiesce-router.py" --all --allow-unlocked >/dev/null 2>&1 || true; rm -f "$KEY_FILE"' EXIT

if ! "$BAO_BIN" kv get -field=ssh_private_key secret/fleet/test-routers-bench >"$KEY_FILE" 2>/dev/null; then
  echo "ERROR: secret/fleet/test-routers-bench ssh_private_key is missing" >&2
  exit 1
fi
if [ ! -s "$KEY_FILE" ]; then
  echo "ERROR: bench key is empty" >&2
  exit 1
fi
# OpenBao's -field output drops the trailing newline; OpenSSH rejects that.
if [ -n "$(tail -c 1 "$KEY_FILE" 2>/dev/null)" ]; then
  printf '\n' >>"$KEY_FILE"
fi

export TOLLGATE_SSH_KEY="$KEY_FILE"
"$@"
