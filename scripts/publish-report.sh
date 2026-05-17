#!/usr/bin/env bash
set -euo pipefail

# ── publish-report.sh ────────────────────────────────────────────────
# Publish a test report to gh-pages, preserving existing reports and
# generating a self-contained HTML dashboard.
#
# Usage: ./scripts/publish-report.sh <run-dir>
#
# <run-dir> is a canonical run directory like results/20260516T172600Z-abc1234
# and must contain:
#   run.json          — canonical run metadata (new or old schema)
#   summary.json      — test summary
#   report/index.html — HTML test report
#
# Delegates to:
#   scripts/render-dashboard.py  — Jinja2 dashboard generation
#   scripts/inject-report-ui.py  — pytest-html video fix + report explorer
#
# Standalone script — does NOT import from lib/.
# ─────────────────────────────────────────────────────────────────────

RUN_DIR="${1:?Usage: $0 <run-dir>}"
RUN_DIR="$(cd "$(dirname "$RUN_DIR")" && pwd)/$(basename "$RUN_DIR")"

if [ ! -d "$RUN_DIR" ]; then
  echo "ERROR: run dir not found: $RUN_DIR" >&2
  exit 1
fi

for required in run.json summary.json report/index.html; do
  if [ ! -f "$RUN_DIR/$required" ]; then
    echo "ERROR: $required not found in $RUN_DIR" >&2
    exit 1
  fi
done

# ── Helper: read a field from run.json using Python ──────────────────
# Supports dot notation for nested keys (e.g., sut.commit, counts.passed).
# Returns empty string for missing/null, 'true'/'false' for booleans.

read_run_json() {
  python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
keys = sys.argv[2].split('.')
v = d
for k in keys:
    if isinstance(v, dict):
        v = v.get(k)
    else:
        v = None
        break
if v is None:
    print('')
elif isinstance(v, bool):
    print('true' if v else 'false')
else:
    print(v)
" "$RUN_DIR/run.json" "$1"
}

# ── Helper: backward-compatible field reader ─────────────────────────
# Try new nested path first, fall back to old flat key, then default.

read_field() {
  local new_path="$1" old_key="${2:-}" default="${3:-}"
  local val
  val="$(read_run_json "$new_path")"
  if [[ -z "$val" && -n "$old_key" ]]; then
    val="$(read_run_json "$old_key")"
  fi
  echo "${val:-$default}"
}

# ── Helper: format ISO timestamp to human-readable ───────────────────

format_timestamp() {
  local ts="$1"
  python3 -c "
import sys
from datetime import datetime
ts = sys.argv[1]
for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H-%M-%S', '%Y-%m-%dT%H-%M-%SZ'):
    try:
        dt = datetime.strptime(ts, fmt)
        months = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
        print('%s %d, %d %02d:%02d UTC' % (months[dt.month], dt.day, dt.year, dt.hour, dt.minute))
        sys.exit(0)
    except ValueError:
        continue
print(ts)
" "$ts" 2>/dev/null
}

# ── Read metadata from run.json ──────────────────────────────────────
# Uses read_field for backward compat: new schema (sut.commit) → old (tollgate_commit)

COMMIT="$(read_field sut.commit tollgate_commit unknown)"
COMMIT_SHORT="$(read_field sut.commit_short "" "${COMMIT:0:7}")"
# shellcheck disable=SC2034
BRANCH="$(read_field sut.branch tollgate_branch "")"
# shellcheck disable=SC2034
PR="$(read_field sut.pr tollgate_pr "")"
# shellcheck disable=SC2034
BACKEND="$(read_field sut.backend "" "")"
# shellcheck disable=SC2034
ROUTER_ID="$(read_field lab.router_id router_id unknown)"
CLIENT_TYPE="$(read_field lab.client_type client_type "")"
# shellcheck disable=SC2034
VIEWPORT="$(read_field lab.viewport viewport desktop)"
# shellcheck disable=SC2034
TEST_PLAN="$(read_field test_plan test_type e2e)"
# shellcheck disable=SC2034
STATUS="$(read_field status "" "")"
# shellcheck disable=SC2034
STARTED_AT="$(read_field started_at timestamp "")"
# shellcheck disable=SC2034
DURATION_MS="$(read_field duration_ms duration_ms 0)"

KEEP="${TOLLGATE_GH_PAGES_KEEP:-50}"

echo "==> Publishing report for commit ${COMMIT_SHORT}..."

# ── Clone or create gh-pages ─────────────────────────────────────────

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_URL="$(git -C "$REPO_DIR" remote get-url origin)"

WORK=$(mktemp -d /tmp/tollgate-report-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

if git clone --single-branch -b gh-pages "$REMOTE_URL" "$WORK/gh-pages" 2>/dev/null; then
  echo "==> Cloned existing gh-pages branch"
else
  echo "==> gh-pages branch not found, creating fresh"
  mkdir -p "$WORK/gh-pages"
  cd "$WORK/gh-pages"
  git init -b gh-pages
  git remote add origin "$REMOTE_URL"
fi

cd "$WORK/gh-pages"

# ── Copy run to gh-pages ─────────────────────────────────────────────

DIR_TIMESTAMP="$(basename "$RUN_DIR")"
TARGET_DIR="reports/${COMMIT_SHORT}/${DIR_TIMESTAMP}"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"

cp -r "$RUN_DIR/report" "$TARGET_DIR/report"
cp "$RUN_DIR/run.json" "$TARGET_DIR/run.json"

# Copy raw artifacts if present
if [[ -d "$RUN_DIR/raw" ]]; then
  cp -r "$RUN_DIR/raw" "$TARGET_DIR/raw"
fi

# ── Sanitization ─────────────────────────────────────────────────────
# Container (VM) mode: skip ALL sanitization. VM test reports contain no PII
#   (random QEMU MACs, local IPs, test tokens, hardcoded "tollgate" password).
#   The report is copied AS-IS — no HTML modification, no img tag stripping,
#   no asset removal. This preserves base64-embedded images (screenshots)
#   that would otherwise be destroyed by the sanitizer, causing about:blank
#   img src bugs in the published report.
# Phone / unknown mode: strip all screenshots, image tags, and asset references
#   to prevent leaking PII from real device test reports.

if [ "$CLIENT_TYPE" = "container" ]; then
  echo "==> Container mode: skipping ALL sanitization, preserving report as-is (no PII in VM tests)"
else
  # Phone / unknown mode: strip all screenshots and XML
  echo "==> Non-container mode: stripping screenshots and XML"

  # Playwright reports: strip non-whitelisted PNGs from data/
  if [ -f "$TARGET_DIR/report/report.json" ]; then
    bash "$REPO_DIR/scripts/strip-screenshots.sh" "$TARGET_DIR/report" 2>/dev/null || true
  fi

  # All reports: remove any stray PNG, XML, TXT asset files
  find "$TARGET_DIR" -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' -o -name '*.gif' -o -name '*.xml' \) -print -delete 2>/dev/null || true

  # Strip image tags and asset references from HTML files (pytest-html embeds them)
  for html_file in "$TARGET_DIR"/*.html "$TARGET_DIR"/report/*.html "$TARGET_DIR"/report/index.html; do
    [ -f "$html_file" ] || continue
    python3 -c "
import re, sys
with open(sys.argv[1], 'r') as f:
    html = f.read()
# Remove <img> tags (screenshots and image embeds)
html = re.sub(r'<img[^>]*/?\s*>', '', html, flags=re.IGNORECASE)
# Remove links to image/text assets
html = re.sub(r'<a[^>]*(?:href|HREF)[^>]*assets/[^>]*\.(?:png|txt|xml)[^>]*>.*?</a>', '', html, flags=re.IGNORECASE | re.DOTALL)
# Remove JS string literals referencing .png/.txt/.xml assets
html = re.sub(r'assets/[^\"'\''\\s<>]*\.(?:png|txt|xml)', '', html, flags=re.IGNORECASE)
with open(sys.argv[1], 'w') as f:
    f.write(html)
" "$html_file" 2>/dev/null || true
  done

  # Call sanitize-results.sh on the target dir if it exists (redacts IPs, passwords, tokens, MACs)
  if [[ -f "$REPO_DIR/scripts/sanitize-results.sh" ]]; then
    SANITIZE_OUT="$(mktemp -d /tmp/tollgate-sanitize-XXXXXX)"
    if bash "$REPO_DIR/scripts/sanitize-results.sh" "$TARGET_DIR" "$SANITIZE_OUT" 2>/dev/null; then
      rm -rf "$TARGET_DIR"
      mv "$SANITIZE_OUT" "$TARGET_DIR"
    else
      rm -rf "$SANITIZE_OUT"
    fi
  fi
fi

find "$TARGET_DIR" -type d -empty -delete 2>/dev/null || true

echo "==> Copied and cleaned report to ${TARGET_DIR}"

# ── Inject report explorer UI + video fix ───────────────────────────
# Adds a navigation dashboard and fixes pytest-html video playback bugs.

echo "==> Injecting report explorer UI..."

python3 "$REPO_DIR/scripts/inject-report-ui.py" "$TARGET_DIR"

# ── Purge old runs ───────────────────────────────────────────────────

purge_old_runs() {
  local reports_dir="$1"
  local keep="$2"

  [ ! -d "$reports_dir" ] && return 0

  # Collect commit directories with their newest timestamp
  local -a dirs=()
  local -a times=()

  for hash_dir in "$reports_dir"/*/; do
    [ ! -d "$hash_dir" ] && continue
    local hash_name
    hash_name="$(basename "$hash_dir")"

    # Find the newest timestamp subdirectory
    local newest=""
    for ts_dir in "$hash_dir"*/; do
      [ ! -d "$ts_dir" ] && continue
      local ts_name
      ts_name="$(basename "$ts_dir")"
      if [ -z "$newest" ] || [ "$ts_name" \> "$newest" ]; then
        newest="$ts_name"
      fi
    done

    if [ -n "$newest" ]; then
      dirs+=("$hash_name")
      times+=("$newest")
    fi
  done

  # If within keep limit, nothing to do
  local count=${#dirs[@]}
  if [ "$count" -le "$keep" ]; then
    return 0
  fi

  # Sort directories by their newest timestamp (ascending = oldest first)
  local to_delete=$((count - keep))

  local sort_file
  sort_file="$(mktemp)"

  local i=0
  for ((i = 0; i < ${#dirs[@]}; i++)); do
    echo "${times[$i]} ${dirs[$i]}" >> "$sort_file"
  done

  # Get the oldest directories (first to_delete lines after sort ascending)
  local deleted=0
  while IFS=' ' read -r _ hash_name; do
    if [ "$deleted" -ge "$to_delete" ]; then
      break
    fi
    echo "==> Purging old report: $hash_name"
    rm -rf "${reports_dir:?}/$hash_name"
    deleted=$((deleted + 1))
  done < <(sort "$sort_file")

  rm -f "$sort_file"
}

purge_old_runs "$WORK/gh-pages/reports" "$KEEP"

# ── Generate dashboard index.html ────────────────────────────────────

echo "==> Generating dashboard..."

python3 "$REPO_DIR/scripts/render-dashboard.py" \
  --reports-dir "$WORK/gh-pages/reports" \
  --output "$WORK/gh-pages/index.html"

# ── Commit and push ──────────────────────────────────────────────────

git add -A
git commit -m "report: ${COMMIT_SHORT} ${DIR_TIMESTAMP}" || true

echo "==> Pushing to gh-pages..."
git push -f origin gh-pages 2>&1

# ── Print URLs ───────────────────────────────────────────────────────

HTTPS_URL="${REMOTE_URL/git@github.com:/https://github.com/}"
HTTPS_URL="${HTTPS_URL%.git}"
REPO_NAME="$(basename "$HTTPS_URL")"
ORG_NAME="$(dirname "$HTTPS_URL" | xargs basename)"

CUSTOM_DOMAIN="${TOLLGATE_GH_PAGES_CNAME:-}"
if [ -z "$CUSTOM_DOMAIN" ] && [ -f "$WORK/gh-pages/CNAME" ]; then
  CUSTOM_DOMAIN="$(tr -d '\r\n' < "$WORK/gh-pages/CNAME")"
fi
if [ -n "${TOLLGATE_GH_PAGES_CNAME:-}" ]; then
  printf '%s\n' "$TOLLGATE_GH_PAGES_CNAME" > "$WORK/gh-pages/CNAME"
  CUSTOM_DOMAIN="$TOLLGATE_GH_PAGES_CNAME"
fi

PAGES_BASE_URL="${TOLLGATE_GH_PAGES_BASE_URL:-}"
if [ -z "$PAGES_BASE_URL" ]; then
  if [ -n "$CUSTOM_DOMAIN" ]; then
    PAGES_BASE_URL="https://${CUSTOM_DOMAIN}"
  else
    PAGES_BASE_URL="https://${ORG_NAME}.github.io/${REPO_NAME}"
  fi
fi

echo ""
echo "==> Report published to gh-pages"
echo "==> Report: ${PAGES_BASE_URL}/reports/${COMMIT_SHORT}/${DIR_TIMESTAMP}/report/index.html"
echo "==> Dashboard: ${PAGES_BASE_URL}/"
