#!/usr/bin/env bash
# Destroy the spike stack. This cancels the SHC VPS immediately (auto_cancel).
#
# To remove ALL spike local state afterwards, run:
#   rm -rf .state .venv last-*.txt
set -euo pipefail
source "$(dirname "$0")/_common.sh"

# Destroy does not strictly need SHC_API_KEY for the local state teardown, but
# the dynamic provider's delete() MUST call the SHC API to cancel the VM, so we
# require it here too.
require_shc_api_key
ensure_stack

echo ">> pulumi destroy — cancelling SHC VPS (stack: ${SPIKE_STACK})"
pulumi destroy --yes 2>&1 | tee last-destroy.txt
echo
echo ">> done. SHC service should now be cancelled."
echo ">> local stack state retained in .state/ — remove with: rm -rf .state"
