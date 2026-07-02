#!/usr/bin/env bash
set -euo pipefail

# Standalone deploy: download CI artifact and install on router.
# For test-integrated deploy, use: pytest --tollgate-branch <branch>
#
# Usage: deploy-ci.sh [--backend go|rust] <branch> [run-id] [router-ip]
#
# Env: TOLLGATE_SSH_KEY or TOLLGATE_SSH_PASSWORD for auth
#      TOLLGATE_ROUTER_ARCH (auto-detected from router if not set)
#      TOLLGATE_BACKEND (default: go)

BACKEND="${TOLLGATE_BACKEND:-go}"
if [[ "${1:-}" == "--backend" ]]; then
  BACKEND="$2"
  shift 2
fi

BRANCH="${1:?Usage: $0 [--backend go|rust] <branch> [run-id] [router-ip]}"
RUN_ID="${2:-}"
ROUTER_IP="${3:-${TOLLGATE_SSH_HOST:-192.168.13.112}}"
ARCH="${TOLLGATE_ROUTER_ARCH:-}"

exec python3 -c "
import os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
from lib.router import Router
from lib.deploy import deploy_branch
from lib.backend import BackendConfig
backend = BackendConfig('${BACKEND}')
r = Router(host='${ROUTER_IP}', phone_ip='', phone_mac='', domain='',
           identity_file=os.environ.get('TOLLGATE_SSH_KEY') or None,
           jump_host=os.environ.get('TOLLGATE_SSH_JUMP_HOST') or None,
           backend=backend)
result = deploy_branch(r, '${BRANCH}', arch='${ARCH}', run_id='${RUN_ID}' or None,
                       reboot=$( [ \"${REBOOT:-}\" = \"1\" ] && echo True || echo False ),
                       backend=backend)
print(f'version={result[\"installed_version\"]} health={result[\"health_code\"]} success={result[\"success\"]}')
sys.exit(0 if result['success'] else 1)
"
