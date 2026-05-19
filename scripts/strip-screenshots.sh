#!/usr/bin/env bash
set -euo pipefail

# ── strip-screenshots.sh ──────────────────────────────────────────────
# Strip non-whitelisted screenshots from a Playwright HTML report.
#
# Playwright always takes full screenshots (for local debugging). This
# script removes PNG files from the report's data/ directory unless the
# test that produced them has a `publish-screenshot` annotation.
#
# Usage: ./scripts/strip-screenshots.sh <report-dir>
#
# <report-dir> must contain:
#   report.json   — Playwright JSON reporter output
#   data/         — screenshot PNGs and metadata
#
# Tests are whitelisted by adding an annotation in the test file:
#   test('name', { annotation: { type: 'publish-screenshot', description: '...' } }, ...)
# ──────────────────────────────────────────────────────────────────────

REPORT_DIR="${1:?Usage: $0 <report-dir>}"
REPORT_DIR="$(cd "$(dirname "$REPORT_DIR")" && pwd)/$(basename "$REPORT_DIR")"

if [ ! -f "$REPORT_DIR/report.json" ]; then
	echo "ERROR: report.json not found in $REPORT_DIR" >&2
	echo "Add ['json', { outputFile: 'report/report.json' }] to playwright.config.mjs reporters" >&2
	exit 1
fi

if [ ! -d "$REPORT_DIR/data" ]; then
	echo "ERROR: data/ directory not found in $REPORT_DIR" >&2
	exit 1
fi

# ── Extract whitelisted attachment paths from report.json ─────────────
#
# report.json structure (Playwright):
#   { "suites": [{ "specs": [{ "tests": [{ "annotations": [...], "results": [{ "attachments": [{ "path": "data/abc.png" }] }] }] }] }] }
#
# We find all attachment paths belonging to tests that have a
# publish-screenshot annotation, then delete any PNG in data/ NOT in
# that set.

WHITELIST_FILE=$(mktemp)
KEEP_COUNT=0
STRIP_COUNT=0

# Use python3 to parse JSON (no jq dependency)
python3 -c "
import json, sys

with open('$REPORT_DIR/report.json') as f:
    report = json.load(f)

whitelisted = set()

def find_attachments(obj):
    '''Recursively find attachments from tests with publish-screenshot annotation.'''
    if isinstance(obj, dict):
        # Check if this is a test-level object with annotations
        annotations = obj.get('annotations', [])
        has_publish = any(a.get('type') == 'publish-screenshot' for a in annotations)

        # Process results (which contain attachments)
        for result in obj.get('results', []):
            for att in result.get('attachments', []):
                path = att.get('path', '')
                if path and has_publish:
                    whitelisted.add(path)

        # Recurse into nested structures
        for key, val in obj.items():
            find_attachments(val)
    elif isinstance(obj, list):
        for item in obj:
            find_attachments(item)

find_attachments(report)

for path in sorted(whitelisted):
    print(path)
" > "$WHITELIST_FILE"

# ── Strip non-whitelisted PNGs ────────────────────────────────────────

echo "==> Whitelisted screenshots: $(wc -l < "$WHITELIST_FILE" | tr -d ' ')"

for png in "$REPORT_DIR"/data/*.png; do
	[ -f "$png" ] || continue
	filename=$(basename "$png")

	if grep -q "$filename" "$WHITELIST_FILE" 2>/dev/null; then
		echo "  KEEP  $filename"
		KEEP_COUNT=$((KEEP_COUNT + 1))
	else
		echo "  STRIP $filename"
		rm "$png"
		STRIP_COUNT=$((STRIP_COUNT + 1))
	fi
done

rm -f "$WHITELIST_FILE"

echo "==> Stripped $STRIP_COUNT screenshots, kept $KEEP_COUNT"
