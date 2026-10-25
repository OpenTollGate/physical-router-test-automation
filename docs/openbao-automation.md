# OpenBao integration for physical-router tests

Goal: test credentials are delivered by OpenBao at run time, not by committed
`.env` files or chat. The automation role can read **test-router** secrets only;
it has no access to the MT6000 production gateway or the T14 key.

## Secret layout

```text
secret/fleet/test-routers-<router-id>
  ssh_password
  luci_password
  wifi_psk
  recovery_password
  ssh_key_path        # path on the runner host, not key material
  ssh_user
  ssh_host
  jump_host
```

Public fleet keys are non-secret and live in
`config/fleet_ssh_keys.yaml`; they are installed by
`scripts/install-fleet-keys.sh`. Do not put private keys in git.

## Auth

Create a policy from `config/openbao-policy.hcl`:

```bash
bao policy write physical-router-automation config/openbao-policy.hcl
```

Create an AppRole with a short TTL and deliver a response-wrapped SecretID to
the runner. Alternatively, the runner may use the operator's short-lived
`bao-exec` token. The loader supports both:

```bash
# direct token
export BAO_ADDR=https://openbao.example
export BAO_TOKEN=...
. scripts/openbao-env.sh --router e750-lab

# or operator helper
export BAO_EXEC=$HOME/hermes-trust-root/scripts/bao-exec.sh
. scripts/openbao-env.sh --router e750-lab
```

The loader exports `TOLLGATE_SSH_PASSWORD`, `TOLLGATE_LUCI_PASSWORD`,
`TOLLGATE_ROUTER_PSK`, `TOLLGATE_RECOVERY_PASSWORD`, `TOLLGATE_SSH_KEY`
(path), `TOLLGATE_SSH_HOST`, `TOLLGATE_SSH_USER`, and `TOLLGATE_SSH_JUMP`.
It never prints values.

## Wiring into an entrypoint

```bash
#!/usr/bin/env bash
set -euo pipefail
ROUTER_ID="${TOLLGATE_ROUTER_ID:-e750-lab}"
. scripts/openbao-env.sh --router "$ROUTER_ID"
scripts/hw-bench-lease exec --purpose "test-pr $ROUTER_ID" -- \
  scripts/run-with-quiesce.sh --router "$ROUTER_ID" -- \
  ./scripts/test-pr.sh --pr 42 --router "$ROUTER_ID"
```

`test-pr.sh` also refuses to run without the bench lease
(`TOLLGATE_ALLOW_UNLOCKED=1` is the explicit offline escape hatch) and runs the
quiesce hook on exit.

## Rotation

1. Rotate the router credential (LuCI password, SSH password, WiFi PSK).
2. Write the new values to `secret/fleet/test-routers-<id>`.
3. Run `scripts/openbao-env.sh --check --router <id>` — names only.
4. Re-run the health check and, if SSH is key-only, reinstall the fleet keys.

## Guardrails

- The automation policy must not read `secret/fleet/mt6000-*`, the production
  gateway's SSH key, or fleet-admin credentials.
- `TOLLGATE_LUCI_PASSWORD`/`TOLLGATE_SSH_PASSWORD` from OpenBao are for test
  routers only. The MT6000 remains T14-key-only.
- Sanitize results before publishing; OpenBao values must not land in reports.
- The bench lease is required for physical runs; a context window cannot bypass
  it by calling a script directly (the entrypoints refuse).
