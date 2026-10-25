#!/usr/bin/env bash
# prta-quiesce-watchdog.sh — disable APs when no test holds the bench lease.
#
# Installed as a systemd timer on x280. It is a cleanup path, so it fetches the
# lease-scoped bench key from OpenBao, quiesces, and removes the key. It only
# runs when the bench lease is free.
set -euo pipefail

ROOT="${PRTA_ROOT:-/home/c08r4d0r/src/prta-main}"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

if scripts/hw-bench-lease status 2>/dev/null | grep -q "bench free"; then
  . scripts/openbao-approle-login.sh || exit 0
  KEY_FILE="$(mktemp "${TMPDIR:-/tmp}/prta-quiesce-key.XXXXXX")"
  chmod 600 "$KEY_FILE"
  trap 'rm -f "$KEY_FILE"' EXIT
  BAO_BIN="${BAO_BIN:-$HOME/.local/bin/bao}"
  if "$BAO_BIN" kv get -field=ssh_private_key secret/fleet/test-routers-bench >"$KEY_FILE" 2>/dev/null; then
    TOLLGATE_SSH_KEY="$KEY_FILE" python3 scripts/quiesce-router.py --all --allow-unlocked >/dev/null 2>&1 || true
  fi
fi
