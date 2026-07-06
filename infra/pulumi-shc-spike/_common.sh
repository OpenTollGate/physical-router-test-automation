#!/usr/bin/env bash
# Shared setup for the pulumi-shc-spike wrapper scripts.
# Source this file; do not execute it directly.
#
# Isolation strategy:
#   - Force a LOCAL file backend stored inside this spike directory (.state/).
#     Nothing is written to the Pulumi service backend or to ~/.pulumi.
#   - Set a fixed, non-interactive config passphrase. We avoid storing any
#     Pulumi config secrets (the API key is read from SHC_API_KEY at runtime),
#     so the passphrase only protects stack URNs.
#   - All pulumi commands run from this directory so stack state stays local.

set -euo pipefail

# Resolve the directory this file lives in. Works under bash (wrapper scripts)
# and degrades gracefully if sourced from an interactive zsh.
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi
cd "$SCRIPT_DIR"

export PULUMI_BACKEND_URL="file://${SCRIPT_DIR}/.state"
mkdir -p "$SCRIPT_DIR/.state"

# Fixed passphrase so `pulumi` never blocks on a prompt. Safe because we do not
# store secrets in stack config — the SHC API key is read from the environment
# at program runtime by __main__.py.
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-pulumi-shc-spike-local-dev-only}"
export PULUMI_SKIP_UPDATE_CHECK="${PULUMI_SKIP_UPDATE_CHECK:-true}"

SPIKE_STACK="${SPIKE_STACK:-spike}"

require_shc_api_key() {
    if [[ -z "${SHC_API_KEY:-}" ]]; then
        echo "ERROR: SHC_API_KEY is not set in the environment." >&2
        echo "       Export it (shc_live_...) before running this script." >&2
        echo "       Generate one at https://blesta.sovereignhybridcompute.com/user-api/docs/" >&2
        exit 2
    fi
}

ensure_stack() {
    # Create the stack if it does not already exist in the local backend.
    if ! pulumi stack ls 2>/dev/null | grep -q "^${SPIKE_STACK}\b"; then
        echo ">> creating local stack '${SPIKE_STACK}'"
        pulumi stack init "${SPIKE_STACK}"
    fi
    pulumi stack select "${SPIKE_STACK}"
}

ensure_deps() {
    if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
        echo ">> creating virtualenv (.venv) — one-time setup"
        pulumi install
    fi
}
