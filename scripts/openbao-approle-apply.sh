#!/usr/bin/env bash
# openbao-approle-apply.sh — create the test-automation policy and AppRole.
#
# Requires an admin/root token in BAO_TOKEN. This only writes policy and role
# metadata; it does not print or store a SecretID.
#
#   BAO_TOKEN=... scripts/openbao-approle-apply.sh
#   BAO_TOKEN=... scripts/openbao-approle-apply.sh --role-id-file ~/.config/prta/role-id
#
# Then create a SecretID (once, on the operator host) and store it root-only:
#   bao write -f -field=secret_id auth/approle/role/physical-router-automation/secret-id \
#     > ~/.config/prta/secret-id
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BAO_BIN="${BAO_BIN:-$(command -v bao || true)}"
ROLE="${BAO_APPROLE:-physical-router-automation}"
POLICY="${BAO_POLICY:-physical-router-automation}"
ROLE_ID_FILE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --role-id-file) ROLE_ID_FILE="${2:-}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$BAO_BIN" ] || { echo "ERROR: bao CLI not found" >&2; exit 1; }
[ -n "${BAO_TOKEN:-}" ] || { echo "ERROR: BAO_TOKEN (admin/root) is required" >&2; exit 1; }

"$BAO_BIN" policy write "$POLICY" "$ROOT/config/openbao-policy.hcl"

if ! "$BAO_BIN" auth list 2>/dev/null | grep -q '^approle/'; then
  "$BAO_BIN" auth enable approle
fi

"$BAO_BIN" write "auth/approle/role/$ROLE" \
  token_policies="$POLICY" \
  token_ttl=15m \
  token_max_ttl=30m \
  secret_id_ttl=24h \
  bind_secret_id=true >/dev/null

ROLE_ID="$("$BAO_BIN" read -field=role_id "auth/approle/role/$ROLE/role-id")"
if [ -n "$ROLE_ID_FILE" ]; then
  umask 077
  mkdir -p "$(dirname "$ROLE_ID_FILE")"
  printf '%s\n' "$ROLE_ID" > "$ROLE_ID_FILE"
  chmod 600 "$ROLE_ID_FILE"
  echo "role-id written to $ROLE_ID_FILE"
else
  echo "role-id: $ROLE_ID"
fi

echo "AppRole $ROLE configured with policy $POLICY."
echo "Create a SecretID once and keep it root-only; never commit it."
