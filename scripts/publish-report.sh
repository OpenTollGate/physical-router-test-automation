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

# Try to fetch existing gh-pages to preserve history
git fetch origin gh-pages 2>/dev/null && git reset origin/gh-pages || true

mkdir -p "reports/${COMMIT}"
cp -r "$REPORT_DIR/." "reports/${COMMIT}/"

# Build index listing all reports
cat > index.html << 'HEADER'
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>TollGate Test Reports</title>
<style>body{font-family:system-ui;max-width:900px;margin:2em auto;padding:0 1em}code{background:#f4f4f4;padding:2px 6px;border-radius:3px}li{margin:.5em 0}.suite{color:#666;font-size:.9em}</style>
</head>
<body>
<h1>TollGate Router Test Reports</h1>
<ul>
HEADER

# List all existing report directories
if [ -d "reports" ]; then
    for dir in reports/*/; do
        [ -d "$dir" ] || continue
        sha=$(basename "$dir")
        short_sha="${sha:0:12}"
        # Detect what's in this report
        suites=""
        [ -f "${dir}report.html" ] && suites="${suitues}Playwright "
        [ -f "${dir}api/report.html" ] && suites="${suites}API "
        [ -f "${dir}phone/report.html" ] && suites="${suites}Phone "
        [ -f "${dir}index.html" ] && suites="${suites}Playwright "
        echo "<li><a href=\"reports/${sha}/index.html\"><code>${short_sha}</code></a> <span class=\"suite\">${suites}</span></li>" >> index.html
    done
fi

cat >> index.html << 'FOOTER'
</ul>
</body>
</html>
FOOTER

# If the report has subdirectories (api/phone from pytest), create a landing page
if [ -d "reports/${COMMIT}/api" ] || [ -d "reports/${COMMIT}/phone" ]; then
    cat > "reports/${COMMIT}/index.html" << 'DASH_HEADER'
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>TollGate Test Report</title>
<style>body{font-family:system-ui;max-width:900px;margin:2em auto;padding:0 1em}h1{border-bottom:1px solid #eee;padding-bottom:.3em}.card{border:1px solid #e0e0e0;border-radius:8px;padding:1em 1.5em;margin:1em 0}.card h2{margin:0 0 .5em}.card a{color:#0366d6}</style>
</head>
<body>
<h1>TollGate Test Report</h1>
DASH_HEADER

    [ -d "reports/${COMMIT}/api" ] && cat >> "reports/${COMMIT}/index.html" << 'API_CARD'
<div class="card">
<h2>API Tests</h2>
<p>Backend API tests — health, discovery, payment structure, token validation, sessions.</p>
<p><a href="api/report.html">View API Report</a></p>
</div>
API_CARD

    [ -d "reports/${COMMIT}/phone" ] && cat >> "reports/${COMMIT}/index.html" << 'PHONE_CARD'
<div class="card">
<h2>Phone Tests</h2>
<p>Android phone tests — payment flows, session lifecycle, metering, edge cases.</p>
<p><a href="phone/report.html">View Phone Report</a></p>
</div>
PHONE_CARD

    [ -f "reports/${COMMIT}/playwright-output.log" ] && cat >> "reports/${COMMIT}/index.html" << 'PW_CARD'
<div class="card">
<h2>Playwright LuCI Tests</h2>
<p>LuCI admin UI tests (Playwright).</p>
<p><a href="playwright-output.log">View Playwright Log</a></p>
</div>
PW_CARD

    cat >> "reports/${COMMIT}/index.html" << 'DASH_FOOTER'
</body>
</html>
DASH_FOOTER
fi

git add -A
git commit -m "report: ${SHORT}

TollGate commit: ${COMMIT}
Generated: ${TIMESTAMP}" || echo "==> No changes to commit"

git push -f origin gh-pages 2>&1

HTTPS_URL="${REMOTE_URL/git@github.com:/https://github.com/}"
HTTPS_URL="${HTTPS_URL%.git}"

echo ""
echo "==> Report published to gh-pages"
echo "==> View: https://opentollgate.github.io/physical-router-test-automation/reports/${COMMIT}/"
echo "==> Index: https://opentollgate.github.io/physical-router-test-automation/"
