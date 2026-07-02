#!/usr/bin/env bash
# Dry-run: show what `pulumi up` would do without touching SHC.
#
# NOTE: SHC is a dynamic provider. `preview` will still call the SHC API to
# resolve the size -> package_id/pricing_id mapping and to run the credit
# pre-check, but it will NOT create any VM.
set -euo pipefail
source "$(dirname "$0")/_common.sh"

require_shc_api_key
ensure_deps
ensure_stack

echo ">> pulumi preview (dry-run, no resources created)"
pulumi preview --diff 2>&1 | tee last-preview.txt
