#!/usr/bin/env bash
# Autonomous cloud lab worker entrypoint (runs on GCP outer VM).
set -euo pipefail

cd /opt/tollgate-test 2>/dev/null || cd "$(dirname "$0")/.."

if [[ -x /opt/tollgate-venv/bin/python3 ]]; then
  exec /opt/tollgate-venv/bin/python3 -m lib.cloud_lab.worker "$@"
fi
exec python3 -m lib.cloud_lab.worker "$@"
