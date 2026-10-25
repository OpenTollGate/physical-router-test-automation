#!/usr/bin/env bash
# openbao-env.sh — load per-router test credentials from OpenBao.
#
# Source this file from an automation entrypoint:
#   . scripts/openbao-env.sh --router e750-lab
#
# It exports TOLLGATE_* variables without printing secret values. If OpenBao is
# not configured, it leaves the existing environment untouched and returns 0,
# so local `.env`/operator usage still works.
#
# Auth (first match):
#   BAO_TOKEN                 direct token
#   BAO_EXEC                  helper that runs `bao` with a short-lived token
#                             (default ~/hermes-trust-root/scripts/bao-exec.sh)
#
# Secret layout:
#   secret/fleet/test-routers-<router>
#     ssh_password, luci_password, wifi_psk, recovery_password,
#     ssh_key_path, ssh_user, ssh_host, jump_host
#
# The automation policy must NOT grant access to secret/fleet/mt6000-*.
set -u

_openbao_router="${TOLLGATE_ROUTER_ID:-default}"
_openbao_check=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --router) _openbao_router="$2"; shift 2 ;;
    --check) _openbao_check=1; shift ;;
    *) shift ;;
  esac
done
_openbao_secret="${TOLLGATE_SECRET_PATH:-secret/fleet/test-routers-${_openbao_router}}"
_openbao_exec="${BAO_EXEC:-$HOME/hermes-trust-root/scripts/bao-exec.sh}"

_openbao_fetch() {
  if [ -n "${BAO_TOKEN:-}" ] && command -v bao >/dev/null 2>&1; then
    bao kv get -format=json "$_openbao_secret" 2>/dev/null
    return $?
  fi
  if [ -x "$_openbao_exec" ]; then
    "$_openbao_exec" "bao kv get -format=json ${_openbao_secret}" 2>/dev/null
    return $?
  fi
  return 1
}

if ! _openbao_json="$(_openbao_fetch)"; then
  echo "openbao-env: no OpenBao credentials for $_openbao_secret; using existing env" >&2
  unset _openbao_router _openbao_secret _openbao_exec _openbao_json _openbao_check
  return 0 2>/dev/null || exit 0
fi

_openbao_exports="$(
  OPENBAO_JSON="$_openbao_json" python3 - <<'PY'
import json
import os
import shlex

doc = json.loads(os.environ["OPENBAO_JSON"])
data = (doc.get("data") or {}).get("data") or {}
if not isinstance(data, dict):
    data = {}

mapping = {
    "ssh_password": "TOLLGATE_SSH_PASSWORD",
    "luci_password": "TOLLGATE_LUCI_PASSWORD",
    "wifi_psk": "TOLLGATE_ROUTER_PSK",
    "recovery_password": "TOLLGATE_RECOVERY_PASSWORD",
    "ssh_key_path": "TOLLGATE_SSH_KEY",
    "ssh_user": "TOLLGATE_SSH_USER",
    "ssh_host": "TOLLGATE_SSH_HOST",
    "jump_host": "TOLLGATE_SSH_JUMP",
}
for field, env in mapping.items():
    value = data.get(field)
    if value not in (None, ""):
        print(f"export {env}={shlex.quote(str(value))}")
PY
)"
if [ "$_openbao_check" = "1" ]; then
  OPENBAO_JSON="$_openbao_json" python3 - <<'PY'
import json
import os
doc = json.loads(os.environ["OPENBAO_JSON"])
data = (doc.get("data") or {}).get("data") or {}
print("openbao-env fields:", ", ".join(sorted(data))) if data else print("openbao-env: empty secret")
PY
else
  eval "$_openbao_exports"
  echo "openbao-env: loaded $_openbao_secret" >&2
fi

unset _openbao_router _openbao_secret _openbao_exec _openbao_json _openbao_check _openbao_exports
