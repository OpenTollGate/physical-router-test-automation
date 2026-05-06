#!/usr/bin/env bash
set -euo pipefail

# Download a CI-built .ipk from GitHub Actions and deploy it to the router.
#
# Usage: deploy-ci.sh <branch> [run-id] [router-ip]
#
# This combines download-ci-artifact.sh + deploy.sh into one step.
# The CI-built artifact is the "production-grade" package — same build
# pipeline that produces release artifacts.
#
# Requires: gh (GitHub CLI), sshpass, router reachable via SSH

BRANCH="${1:?Usage: $0 <branch> [run-id] [router-ip]}"
RUN_ID="${2:-}"
ROUTER_IP="${3:-${TOLLGATE_SSH_HOST:-${TOLLGATE_LUCI_URL#http://} | sed 's/:[0-9]*//'}}"
ROUTER_IP="${ROUTER_IP:-192.168.13.112}"
ROUTER_USER="${TOLLGATE_SSH_USER:-root}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "${TOLLGATE_LUCI_PASSWORD:-}" ] && [ -z "${TOLLGATE_SSH_PASSWORD:-}" ]; then
  echo "ERROR: TOLLGATE_LUCI_PASSWORD or TOLLGATE_SSH_PASSWORD is required" >&2
  exit 1
fi

export SSHPASS="${TOLLGATE_SSH_PASSWORD:-${TOLLGATE_LUCI_PASSWORD}}"

echo "==> Downloading CI artifact for branch '${BRANCH}'..."
IPK_FILE=$("$SCRIPT_DIR/download-ci-artifact.sh" "$BRANCH" "$RUN_ID")
echo "==> Downloaded: $IPK_FILE"

echo "==> Copying to ${ROUTER_USER}@${ROUTER_IP}..."
sshpass -e scp -O \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=10 \
  "$IPK_FILE" \
  "${ROUTER_USER}@${ROUTER_IP}:/tmp/tollgate-wrt.ipk"

echo "==> Installing on router..."
sshpass -e ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=10 \
  "${ROUTER_USER}@${ROUTER_IP}" \
  'opkg install --force-overwrite /tmp/tollgate-wrt.ipk && /etc/init.d/tollgate-wrt restart && /etc/init.d/tollgate-basic restart 2>/dev/null; /etc/init.d/uhttpd restart && rm -f /tmp/tollgate-wrt.ipk && echo DEPLOY_OK'

echo "==> Verifying..."
INSTALLED=$(sshpass -e ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "${ROUTER_USER}@${ROUTER_IP}" \
  'opkg list-installed | grep tollgate-wrt')
HTTP_CODE=$(sshpass -e ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "${ROUTER_USER}@${ROUTER_IP}" \
  'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/')

echo "==> Installed: ${INSTALLED}"
echo "==> LuCI HTTP: ${HTTP_CODE}"
if [ "$HTTP_CODE" != "200" ]; then
  echo "WARNING: LuCI not responding 200 (got ${HTTP_CODE})" >&2
fi

echo "==> Deploy complete."
