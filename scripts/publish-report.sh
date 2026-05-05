#!/usr/bin/env bash
set -euo pipefail

REPORT_DIR="${1:?Usage: $0 <report-dir> <branch-name>}"
BRANCH_NAME="${2:?Usage: $0 <report-dir> <branch-name>}"

if [ ! -d "$REPORT_DIR" ]; then
  echo "ERROR: report dir not found: $REPORT_DIR" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMPDIR=$(mktemp -d /tmp/tollgate-report-XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Publishing report to branch: $BRANCH_NAME"

cd "$TMPDIR"
git init -b "$BRANCH_NAME"
git remote add origin "$(git -C "$REPO_DIR" remote get-url origin)"

cat > index.html << 'INDEX'
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>TollGate Test Report</title></head>
<body style="font-family:system-ui;max-width:800px;margin:2em auto;padding:0 1em">
<h1>TollGate Router Test Report</h1>
<p>Open the <a href="playwright-report/index.html">Playwright HTML report</a> to view full results with screenshots and traces.</p>
INDEX
if [[ "$BRANCH_NAME" == report/* ]]; then
  COMMIT="${BRANCH_NAME#report/}"
  echo "<p>Tested commit: <code>${COMMIT}</code></p>" >> index.html
fi
echo "<p>Generated: $(date -u '+%Y-%m-%d %H:%M UTC')</p>" >> index.html
echo "</body></html>" >> index.html

cp -r "$REPORT_DIR" playwright-report

git add -A
git commit -m "test report: $BRANCH_NAME

Generated $(date -u '+%Y-%m-%d %H:%M UTC')
Report contains Playwright HTML output with screenshots and traces."

git push -f origin "$BRANCH_NAME" 2>&1 || \
  git push -u origin "$BRANCH_NAME" 2>&1

REMOTE_URL=$(git remote get-url origin)
HTTPS_URL="${REMOTE_URL/git@github.com:/https://github.com/}"
HTTPS_URL="${HTTPS_URL%.git}"

echo ""
echo "==> Report published to branch: $BRANCH_NAME"
echo "==> Browse: ${HTTPS_URL}/tree/${BRANCH_NAME}"
echo ""
echo "To download and view locally:"
echo "  git fetch origin ${BRANCH_NAME}"
echo "  git checkout ${BRANCH_NAME}"
echo "  open playwright-report/index.html"
echo ""
echo "To delete this report branch:"
echo "  git push origin --delete ${BRANCH_NAME}"
