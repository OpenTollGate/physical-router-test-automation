#!/usr/bin/env bash
set -euo pipefail

# Download a Rust v1 CI-built .ipk artifact from GitHub Actions.
# Prints the local file path to stdout (all other output goes to stderr).
#
# Usage: download-rust-ci-artifact.sh <branch> [run-id] [arch]
# Arch:  TOLLGATE_ROUTER_ARCH (default: aarch64_cortex-a53)
#
# Requires: gh (GitHub CLI), authenticated with repo access

BRANCH="${1:?Usage: $0 <branch> [run-id] [arch]}"
RUN_ID="${2:-}"
ARCH="${3:-${TOLLGATE_ROUTER_ARCH:-aarch64_cortex-a53}}"

exec python3 -c "
import logging, sys
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
from lib.deploy import download_artifact
from lib.backend import BackendConfig
backend = BackendConfig('rust')
p = download_artifact('${BRANCH}', '${ARCH}', run_id='${RUN_ID}' or None,
                      repo=backend.repo, workflow=backend.workflow)
print(p)
"
