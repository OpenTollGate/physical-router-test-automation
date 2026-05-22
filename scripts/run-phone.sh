#!/usr/bin/env bash
set -euo pipefail
# ── Legacy wrapper: delegates to run-profile.sh ────────────────────────
# Prefer: ./scripts/run-profile.sh --profile <profile>
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE="${TOLLGATE_PROFILE:-physical-phone-captive-portal}"
exec "$SCRIPT_DIR/run-profile.sh" --profile "$PROFILE" "$@"
