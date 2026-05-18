#!/usr/bin/env bash
set -euo pipefail

# Deploy Rust v1 CI artifact to router.
# Usage: deploy-rust-ci.sh <branch> [run-id] [router-ip]
#
# Env: TOLLGATE_SSH_KEY or TOLLGATE_SSH_PASSWORD for auth
#      TOLLGATE_ROUTER_ARCH (auto-detected from router if not set)

BRANCH="${1:?Usage: $0 <branch> [run-id] [router-ip]}"
RUN_ID="${2:-}"
ROUTER_IP="${3:-${TOLLGATE_SSH_HOST:-192.168.13.112}}"
ARCH="${TOLLGATE_ROUTER_ARCH:-}"

exec python3 -c "
import os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
from lib.router import Router
from lib.deploy import deploy_branch
from lib.backend import BackendConfig
backend = BackendConfig('rust')
r = Router(host='${ROUTER_IP}', phone_ip='', phone_mac='', domain='',
           identity_file=os.environ.get('TOLLGATE_SSH_KEY') or None,
           backend=backend)
result = deploy_branch(r, '${BRANCH}', arch='${ARCH}', run_id='${RUN_ID}' or None,
                       reboot=$( [ \"${REBOOT:-}\" = \"1\" ] && echo True || echo False ),
                       backend=backend)
print(f'version={result[\"installed_version\"]} health={result[\"health_code\"]} success={result[\"success\"]}')
sys.exit(0 if result['success'] else 1)
"
