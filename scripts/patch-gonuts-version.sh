#!/usr/bin/env bash
# patch-gonuts-version.sh — Temporary workaround for gonuts-tollgate bolt11 regression
#
# PR #126 (merged as 2cb771f) resolved the gonuts version conflict to v0.7.0,
# which does NOT include the bolt11 FakeWallet tolerance fix. This script patches
# the go.mod files to use the pseudo-version that has the fix.
#
# Usage: ./scripts/patch-gonuts-version.sh <repo-dir>
#
# This is a TEMPORARY workaround. Remove when:
#   - gonuts-tollgate v0.7.1+ is released with the bolt11 fix, OR
#   - the fix is merged into gonuts main and tollgate-module-basic-go picks it up
#
# See: https://github.com/OpenTollGate/tollgate-module-basic-go/issues/156

set -euo pipefail

REPO_DIR="${1:?Usage: $0 <repo-dir>}"

# The version with bolt11 FakeWallet tolerance (commit 9b2b843)
BOLT11_FIX_VERSION="v0.0.0-20260528233401-9b2b84344c3a"

# The broken version (v0.7.0 tag does NOT include the bolt11 fix)
BROKEN_VERSION="v0.7.0"

MOD_FILES=(
    "$REPO_DIR/src/go.mod"
    "$REPO_DIR/src/tollwallet/go.mod"
    "$REPO_DIR/src/merchant/go.mod"
)

patched=0
for mod in "${MOD_FILES[@]}"; do
    if [ ! -f "$mod" ]; then
        echo "patch-gonuts: SKIP $mod (not found)"
        continue
    fi

    if grep -q "gonuts-tollgate.*${BROKEN_VERSION}" "$mod"; then
        sed -i.bak "s|gonuts-tollgate ${BROKEN_VERSION}|gonuts-tollgate ${BOLT11_FIX_VERSION}|g" "$mod"
        rm -f "${mod}.bak"
        patched=$((patched + 1))
        echo "patch-gonuts: PATCHED $mod → ${BOLT11_FIX_VERSION}"
    elif grep -q "gonuts-tollgate.*${BOLT11_FIX_VERSION}" "$mod"; then
        echo "patch-gonuts: OK $mod (already has bolt11-tolerant version)"
    else
        echo "patch-gonuts: SKIP $mod (no v0.7.0 replace directive found — may already be fixed upstream)"
    fi
done

if [ "$patched" -gt 0 ]; then
    echo "patch-gonuts: Patched $patched file(s). Running go mod tidy..."
    (cd "$REPO_DIR/src" && go mod tidy 2>&1) || {
        echo "patch-gonuts: WARNING: go mod tidy failed (non-fatal, build may still work)"
    }
fi

echo "patch-gonuts: Done."
