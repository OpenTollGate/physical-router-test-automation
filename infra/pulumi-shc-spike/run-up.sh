#!/usr/bin/env bash
# Create / update the disposable SHC VPS for this spike.
#
# Default tier: dev-1c-4gb (~$0.24/day, the cheapest SHC plan).
# Override with:  pulumi config set size nvme-2c-8gb
#
# The VM is created with auto_cancel=True, so `pulumi destroy` (run-destroy.sh)
# cancels the underlying SHC service immediately.
set -euo pipefail
source "$(dirname "$0")/_common.sh"

require_shc_api_key
ensure_deps
ensure_stack

echo ">> pulumi up — provisioning disposable SHC VPS (stack: ${SPIKE_STACK})"
# --yes skips the interactive confirmation. Remove it for manual runs.
pulumi up --yes 2>&1 | tee last-up.txt

echo
echo ">> outputs:"
pulumi stack output --show-secrets=false 2>&1 | tee -a last-up.txt
