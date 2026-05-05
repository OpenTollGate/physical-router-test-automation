#!/usr/bin/env bash
set -euo pipefail

COMMIT="${1:?Usage: $0 <tollgate-commit-hash> [report-dir]}"
REPORT_DIR="${2:-playwright-report}"

REPORT_DIR="$(cd "$(dirname "$REPORT_DIR")" && pwd)/$(basename "$REPORT_DIR")"
if [ ! -d "$REPORT_DIR" ]; then
  echo "ERROR: report dir not found: $REPORT_DIR" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_URL="$(git -C "$REPO_DIR" remote get-url origin)"
WORK=$(mktemp -d /tmp/tollgate-report-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

SHORT="${COMMIT:0:12}"
TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M UTC')

echo "==> Publishing report for commit ${SHORT}..."

cd "$WORK"
git init -b gh-pages
git remote add origin "$REMOTE_URL"

mkdir -p "reports/${COMMIT}"

cp -r "$REPORT_DIR/." "reports/${COMMIT}/"

cat > index.html << EOF
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>TollGate Test Reports</title>
<style>body{font-family:system-ui;max-width:800px;margin:2em auto;padding:0 1em}code{background:#f4f4f4;padding:2px 6px;border-radius:3px}li{margin:.5em 0}</style>
</head>
<body>
<h1>TollGate Router Test Reports</h1>
<ul>
<li><a href="reports/${COMMIT}/index.html"><code>${SHORT}</code></a> — ${TIMESTAMP}</li>
</ul>
</body>
</html>
EOF

git add -A
git commit -m "report: ${SHORT}

TollGate commit: ${COMMIT}
Generated: ${TIMESTAMP}"

git push -f origin gh-pages 2>&1

HTTPS_URL="${REMOTE_URL/git@github.com:/https://github.com/}"
HTTPS_URL="${HTTPS_URL%.git}"

echo ""
echo "==> Report published to gh-pages"
echo "==> View: https://opentollgate.github.io/physical-router-test-automation/reports/${COMMIT}/"
echo "==> Index: https://opentollgate.github.io/physical-router-test-automation/"
