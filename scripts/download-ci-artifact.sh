#!/usr/bin/env bash
set -euo pipefail

# Download a CI-built .ipk artifact from GitHub Actions.
#
# Usage: download-ci-artifact.sh <branch> [run-id] [arch]
#
# Examples:
#   download-ci-artifact.sh feat/luci-admin-ui
#   download-ci-artifact.sh main 25427933255
#   download-ci-artifact.sh feat/luci-admin-ui "" aarch64_cortex-a53
#
# Requires: gh (GitHub CLI), authenticated with repo access
#
# The artifact is downloaded to /tmp/tollgate-build/ and the path is printed.

BRANCH="${1:?Usage: $0 <branch> [run-id] [arch]}"
RUN_ID="${2:-}"
ARCH="${3:-aarch64_cortex-a53}"
REPO="OpenTollGate/tollgate-module-basic-go"
OUTDIR="/tmp/tollgate-build"

mkdir -p "$OUTDIR"

if [ -n "$RUN_ID" ]; then
	echo "==> Using specified run: ${RUN_ID}"
else
	echo "==> Finding latest successful build for branch '${BRANCH}'..."
	RUN_ID=$(gh run list \
		--repo "$REPO" \
		--branch "$BRANCH" \
		--status success \
		--workflow "Build and Publish" \
		--limit 1 \
		--json databaseId \
		--jq '.[0].databaseId')
	if [ -z "$RUN_ID" ]; then
		echo "ERROR: No successful runs found for branch '${BRANCH}'" >&2
		exit 1
	fi
	echo "==> Found run: ${RUN_ID}"
fi

echo "==> Downloading artifacts from run ${RUN_ID}..."
gh run download "$RUN_ID" \
	--repo "$REPO" \
	--dir "$OUTDIR" \
	|| {
		echo "ERROR: Download failed. Check run ID and permissions." >&2
		exit 1
	}

IPK_FILE=$(find "$OUTDIR" -name "*${ARCH}*.ipk" -type f | head -1)
if [ -z "$IPK_FILE" ]; then
	echo "ERROR: No .ipk found for arch ${ARCH} in ${OUTDIR}" >&2
	echo "==> Available files:"
	find "$OUTDIR" -type f || true
	exit 1
fi

echo "==> Artifact: ${IPK_FILE}"
echo "==> Size: $(du -h "$IPK_FILE" | cut -f1)"
echo "${IPK_FILE}"
