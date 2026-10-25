#!/usr/bin/env bash
# openbao-approle-login.sh — source this to get a short-lived OpenBao token.
#
#   . scripts/openbao-approle-login.sh
#
# Reads the AppRole credentials from files (never argv, never git):
#   ~/.config/prta/role-id
#   ~/.config/prta/secret-id
# and the address from BAO_ADDR or ~/.config/prta/bao-addr.
#
# It exports BAO_ADDR and BAO_TOKEN. It never prints the token or SecretID.
# If BAO_TOKEN is already set, it is a no-op.
set -u

_bao_login_token="${BAO_TOKEN:-}"
if [ -n "$_bao_login_token" ]; then
  unset _bao_login_token
  return 0 2>/dev/null || exit 0
fi

_bao_login_bin="${BAO_BIN:-$HOME/.local/bin/bao}"
if [ ! -x "$_bao_login_bin" ]; then
  _bao_login_bin="$(command -v bao 2>/dev/null || true)"
fi
if [ -z "$_bao_login_bin" ] || [ ! -x "$_bao_login_bin" ]; then
  echo "openbao-approle-login: bao CLI not found" >&2
  unset _bao_login_bin _bao_login_token
  return 1 2>/dev/null || exit 1
fi

_bao_login_addr="${BAO_ADDR:-}"
if [ -z "$_bao_login_addr" ]; then
  if [ -f "$HOME/.config/prta/bao-addr" ]; then
    _bao_login_addr="$(cat "$HOME/.config/prta/bao-addr")"
  fi
fi
if [ -z "$_bao_login_addr" ]; then
  echo "openbao-approle-login: BAO_ADDR or ~/.config/prta/bao-addr is required" >&2
  unset _bao_login_bin _bao_login_addr _bao_login_token
  return 1 2>/dev/null || exit 1
fi

_bao_login_role_file="${BAO_ROLE_ID_FILE:-$HOME/.config/prta/role-id}"
_bao_login_secret_file="${BAO_SECRET_ID_FILE:-$HOME/.config/prta/secret-id}"
if [ ! -r "$_bao_login_role_file" ] || [ ! -r "$_bao_login_secret_file" ]; then
  echo "openbao-approle-login: missing $_bao_login_role_file or $_bao_login_secret_file" >&2
  unset _bao_login_bin _bao_login_addr _bao_login_role_file _bao_login_secret_file _bao_login_token
  return 1 2>/dev/null || exit 1
fi

_bao_login_role="$(cat "$_bao_login_role_file")"
_bao_login_secret="$(cat "$_bao_login_secret_file")"
_bao_login_json="$(
  BAO_ADDR="$_bao_login_addr" "$_bao_login_bin" write -format=json \
    auth/approle/login role_id="$_bao_login_role" secret_id="$_bao_login_secret" 2>/dev/null
)" || {
  echo "openbao-approle-login: AppRole login failed" >&2
  unset _bao_login_bin _bao_login_addr _bao_login_role_file _bao_login_secret_file \
        _bao_login_role _bao_login_secret _bao_login_token
  return 1 2>/dev/null || exit 1
}

_bao_login_new_token="$(
  OPENBAO_LOGIN_JSON="$_bao_login_json" python3 - <<'PY'
import json
import os
try:
    doc = json.loads(os.environ["OPENBAO_LOGIN_JSON"])
except Exception:
    print("")
    raise SystemExit(0)
print(((doc.get("auth") or {}).get("client_token") or ""))
PY
)"
if [ -z "$_bao_login_new_token" ]; then
  echo "openbao-approle-login: login response had no token" >&2
  unset _bao_login_bin _bao_login_addr _bao_login_role_file _bao_login_secret_file \
        _bao_login_role _bao_login_secret _bao_login_json _bao_login_token
  return 1 2>/dev/null || exit 1
fi

export BAO_ADDR="$_bao_login_addr"
export BAO_TOKEN="$_bao_login_new_token"
unset _bao_login_bin _bao_login_addr _bao_login_role_file _bao_login_secret_file \
      _bao_login_role _bao_login_secret _bao_login_json _bao_login_new_token _bao_login_token
