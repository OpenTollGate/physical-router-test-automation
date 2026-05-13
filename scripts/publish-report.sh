#!/usr/bin/env bash
set -euo pipefail

# ── publish-report.sh ────────────────────────────────────────────────
# Publish a Playwright test report to gh-pages, preserving existing
# reports and generating a self-contained HTML dashboard.
#
# Usage: ./scripts/publish-report.sh <run-dir> [tollgate-commit]
#
# <run-dir> must contain:
#   report/     — Playwright HTML report
#   run.json    — metadata for this run
#
# If tollgate-commit is not provided, it is read from run.json.
# ─────────────────────────────────────────────────────────────────────

RUN_DIR="${1:?Usage: $0 <run-dir> [tollgate-commit]}"
RUN_DIR="$(cd "$(dirname "$RUN_DIR")" && pwd)/$(basename "$RUN_DIR")"

if [ ! -d "$RUN_DIR" ]; then
  echo "ERROR: run dir not found: $RUN_DIR" >&2
  exit 1
fi

if [ ! -f "$RUN_DIR/run.json" ]; then
  echo "ERROR: run.json not found in $RUN_DIR" >&2
  exit 1
fi

if [ ! -d "$RUN_DIR/report" ]; then
  echo "ERROR: report/ directory not found in $RUN_DIR" >&2
  exit 1
fi

# ── JSON helpers (no jq) ────────────────────────────────────────────

json_string() {
  # Extract a string value from JSON by key name.
  # Handles "key": "value" with optional whitespace.
  local file="$1" key="$2"
  grep -o "\"${key}\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$file" \
    | head -1 \
    | sed "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\"\(.*\)\"/\1/"
}

json_number() {
  # Extract a numeric value from JSON by key name.
  local file="$1" key="$2"
  grep -o "\"${key}\"[[:space:]]*:[[:space:]]*[0-9]*" "$file" \
    | head -1 \
    | sed "s/.*\"${key}\"[[:space:]]*:[[:space:]]*//"
}

# ── Read metadata from run.json ──────────────────────────────────────

COMMIT="${2:-}"
if [ -z "$COMMIT" ]; then
  COMMIT="$(json_string "$RUN_DIR/run.json" tollgate_commit)"
  if [ -z "$COMMIT" ]; then
    echo "ERROR: tollgate_commit not found in run.json and not provided as argument" >&2
    exit 1
  fi
fi

TOLLGATE_BRANCH="$(json_string "$RUN_DIR/run.json" tollgate_branch || true)"
TOLLGATE_PR="$(json_number "$RUN_DIR/run.json" tollgate_pr || true)"
TEST_TYPE="$(json_string "$RUN_DIR/run.json" test_type || true)"
ROUTER_ID="$(json_string "$RUN_DIR/run.json" router_id || true)"
ROUTER_IP="$(json_string "$RUN_DIR/run.json" router_ip || true)"
VIEWPORT="$(json_string "$RUN_DIR/run.json" viewport || true)"
RUN_TIMESTAMP="$(json_string "$RUN_DIR/run.json" timestamp || true)"
PASSED="$(json_number "$RUN_DIR/run.json" passed || true)"
FAILED="$(json_number "$RUN_DIR/run.json" failed || true)"
SKIPPED="$(json_number "$RUN_DIR/run.json" skipped || true)"
FLAKY="$(json_number "$RUN_DIR/run.json" flaky || true)"
DURATION_MS="$(json_number "$RUN_DIR/run.json" duration_ms || true)"

# Defaults for missing values
TEST_TYPE="${TEST_TYPE:-e2e}"
ROUTER_ID="${ROUTER_ID:-unknown}"
VIEWPORT="${VIEWPORT:-desktop}"
PASSED="${PASSED:-0}"
FAILED="${FAILED:-0}"
SKIPPED="${SKIPPED:-0}"
FLAKY="${FLAKY:-0}"
DURATION_MS="${DURATION_MS:-0}"

# Use run.json timestamp if available, otherwise generate one
if [ -z "$RUN_TIMESTAMP" ]; then
  RUN_TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
fi

# Generate a filesystem-safe timestamp (no colons)
DIR_TIMESTAMP="$(echo "$RUN_TIMESTAMP" | sed 's/[: ]/-/g' | sed 's/[^0-9T.Z-]//g')"

SHORT="${COMMIT:0:12}"
KEEP="${TOLLGATE_GH_PAGES_KEEP:-50}"

echo "==> Publishing report for commit ${SHORT}..."

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

# ── Add the new run ──────────────────────────────────────────────────

TARGET_DIR="reports/${COMMIT}/${DIR_TIMESTAMP}"
mkdir -p "$TARGET_DIR"

cp -r "$RUN_DIR/report" "$TARGET_DIR/report"
cp "$RUN_DIR/run.json" "$TARGET_DIR/run.json"

# ── Strip screenshots and XML ──────────────────────────────────────────
# Never publish screenshots, XML, or raw debug files to gh-pages.

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

find "$TARGET_DIR" -type d -empty -delete 2>/dev/null || true

echo "==> Copied and cleaned report to ${TARGET_DIR}"

# ── Purge old runs ───────────────────────────────────────────────────

purge_old_runs() {
  local reports_dir="$1"
  local keep="$2"

  [ ! -d "$reports_dir" ] && return 0

  # Collect tollgate-hash directories with their newest timestamp
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
  # Build sortable list, sort, then delete the oldest ones
  local to_delete=$((count - keep))

  # Create temp file for sorting
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
    rm -rf "$reports_dir/$hash_name"
    deleted=$((deleted + 1))
  done < <(sort "$sort_file")

  rm -f "$sort_file"
}

purge_old_runs "$WORK/gh-pages/reports" "$KEEP"

# ── Generate dashboard index.html ────────────────────────────────────

echo "==> Generating dashboard..."

generate_dashboard() {
  local reports_dir="$WORK/gh-pages/reports"
  local dash_file="$WORK/gh-pages/index.html"

  # ── Collect all run data ───────────────────────────────────────────

  # We'll build data structures using temp files.
  # Format: <timestamp_sort>|<hash>|<run_dir_relative>|<field1>|...

  local runs_file
  runs_file="$(mktemp)"

  [ -d "$reports_dir" ] || true

  for hash_dir in "$reports_dir"/*/; do
    [ ! -d "$hash_dir" ] && continue
    local hash_name
    hash_name="$(basename "$hash_dir")"

    for ts_dir in "$hash_dir"*/; do
      [ ! -d "$ts_dir" ] && continue
      local ts_name
      ts_name="$(basename "$ts_dir")"
      local run_json="$ts_dir/run.json"

      [ ! -f "$run_json" ] && continue

      local r_branch r_pr r_testtype r_router r_viewport r_timestamp
      local r_passed r_failed r_skipped r_flaky r_duration
      r_branch="$(json_string "$run_json" tollgate_branch || true)"
      r_pr="$(json_number "$run_json" tollgate_pr || true)"
      r_testtype="$(json_string "$run_json" test_type || true)"
      r_router="$(json_string "$run_json" router_id || true)"
      r_viewport="$(json_string "$run_json" viewport || true)"
      r_timestamp="$(json_string "$run_json" timestamp || true)"
      r_passed="$(json_number "$run_json" passed || true)"
      r_failed="$(json_number "$run_json" failed || true)"
      r_skipped="$(json_number "$run_json" skipped || true)"
      r_flaky="$(json_number "$run_json" flaky || true)"
      r_duration="$(json_number "$run_json" duration_ms || true)"

      # Defaults
      r_testtype="${r_testtype:-e2e}"
      r_router="${r_router:-unknown}"
      r_viewport="${r_viewport:-desktop}"
      r_passed="${r_passed:-0}"
      r_failed="${r_failed:-0}"
      r_skipped="${r_skipped:-0}"
      r_flaky="${r_flaky:-0}"
      r_duration="${r_duration:-0}"

      # Use the directory timestamp if JSON timestamp is missing
      local sort_ts="${r_timestamp:-$ts_name}"
      # Normalize for sorting: strip non-sortable chars
      sort_ts="$(echo "$sort_ts" | tr -d ':Z' | sed 's/T/ /')"

      # Store as pipe-delimited record
      echo "${sort_ts}|${hash_name}|${ts_name}|${r_branch}|${r_pr}|${r_testtype}|${r_router}|${r_viewport}|${r_timestamp}|${r_passed}|${r_failed}|${r_skipped}|${r_flaky}|${r_duration}" >> "$runs_file"
    done
  done

  # ── Compute summary stats ──────────────────────────────────────────

  local total_runs=0 total_commits=0 last_updated=""

  if [ -f "$runs_file" ] && [ -s "$runs_file" ]; then
    total_runs="$(wc -l < "$runs_file" | tr -d ' ')"
    total_commits="$(cut -d'|' -f2 "$runs_file" | sort -u | wc -l | tr -d ' ')"
    # Last updated = newest timestamp
    last_updated="$(sort -r "$runs_file" | head -1 | cut -d'|' -f9)"
  fi

  total_runs="${total_runs:-0}"
  total_commits="${total_commits:-0}"
  last_updated="${last_updated:-N/A}"

  # Format the last_updated for display
  local last_updated_display
  if [ "$last_updated" != "N/A" ] && [ -n "$last_updated" ]; then
    last_updated_display="$(format_timestamp "$last_updated")"
  else
    last_updated_display="N/A"
  fi

  # ── Begin HTML output ──────────────────────────────────────────────

  cat > "$dash_file" <<'DASHHEAD'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TollGate Test Reports</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;background:#f0f2f5;color:#1a1a2e;line-height:1.5}
.header{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#fff;padding:1.5rem 2rem;display:flex;align-items:center;justify-content:space-between;box-shadow:0 2px 8px rgba(0,0,0,.2)}
.header h1{font-size:1.5rem;font-weight:600;letter-spacing:.5px}
.header .updated{font-size:.85rem;opacity:.8}
.container{max-width:1100px;margin:2rem auto;padding:0 1rem}
.summary{display:flex;gap:1rem;margin-bottom:2rem;flex-wrap:wrap}
.summary-card{background:#fff;border-radius:8px;padding:1rem 1.5rem;flex:1;min-width:160px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.summary-card .label{font-size:.8rem;text-transform:uppercase;letter-spacing:.5px;color:#666;margin-bottom:.25rem}
.summary-card .value{font-size:1.75rem;font-weight:700;color:#1a1a2e}
.commit-group{background:#fff;border-radius:8px;margin-bottom:1.5rem;box-shadow:0 1px 3px rgba(0,0,0,.08);overflow:hidden}
.commit-header{padding:1rem 1.5rem;border-bottom:1px solid #eee;display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
.commit-header .hash{font-family:"SF Mono",SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace;font-size:1rem;font-weight:600}
.commit-header .hash a{color:#302b63;text-decoration:none}
.commit-header .hash a:hover{text-decoration:underline}
.badge{display:inline-block;font-size:.75rem;font-weight:500;padding:2px 8px;border-radius:12px;vertical-align:middle}
.badge-branch{background:#e8f0fe;color:#1a73e8;text-decoration:none}
.badge-branch:hover{background:#d2e3fc}
.badge-pr{background:#e6f4ea;color:#137333;text-decoration:none}
.badge-pr:hover{background:#ceead6}
.runs{padding:.5rem 0}
.run-row{display:grid;grid-template-columns:180px 70px 120px 80px 90px 90px 90px 90px 1fr;align-items:center;padding:.65rem 1.5rem;border-bottom:1px solid #f5f5f5;font-size:.875rem;gap:.5rem}
.run-row:last-child{border-bottom:none}
.run-row:hover{background:#fafbfc}
.run-time{color:#555;font-variant-numeric:tabular-nums}
.run-type{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.3px}
.run-type.e2e{background:#fce8b2;color:#b06000;padding:2px 6px;border-radius:4px}
.run-router,.run-viewport{color:#444;font-family:"SF Mono",SFMono-Regular,Consolas,monospace;font-size:.8rem}
.count{font-weight:600;text-align:center}
.count-pass{color:#137333}.count-fail{color:#d93025}.count-skip{color:#80868b}.count-flaky{color:#b06000}
.run-link a{color:#1a73e8;text-decoration:none;font-weight:500;font-size:.8rem}
.run-link a:hover{text-decoration:underline}
.run-header{display:grid;grid-template-columns:180px 70px 120px 80px 90px 90px 90px 90px 1fr;padding:.5rem 1.5rem;background:#fafbfc;font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:#666;border-bottom:1px solid #eee;gap:.5rem}
.empty{padding:3rem;text-align:center;color:#888;font-size:1rem}
</style>
</head>
<body>
<div class="header">
<h1>TollGate Test Reports</h1>
<div class="updated">Last updated: PLACEHOLDER_LAST_UPDATED</div>
</div>
<div class="container">
<div class="summary">
<div class="summary-card"><div class="label">Total Runs</div><div class="value">PLACEHOLDER_TOTAL_RUNS</div></div>
<div class="summary-card"><div class="label">Commits Tested</div><div class="value">PLACEHOLDER_TOTAL_COMMITS</div></div>
</div>
DASHHEAD

  # ── Emit commit groups ─────────────────────────────────────────────

  if [ -f "$runs_file" ] && [ -s "$runs_file" ]; then
    # Get unique commit hashes sorted by newest run first
    local commits_file
    commits_file="$(mktemp)"
    # For each hash, find its newest sort timestamp, then sort hashes by that descending
    cut -d'|' -f1,2 "$runs_file" | awk -F'|' '{print $2 "|" $1}' | sort -t'|' -k2 -r | awk -F'|' '{if(!seen[$1]++){print $1}}' > "$commits_file"

    while IFS= read -r hash_name; do
      [ -z "$hash_name" ] && continue
      local short_hash="${hash_name:0:12}"
      local commit_url="https://github.com/OpenTollGate/tollgate-module-basic-go/commit/${hash_name}"

      # Get metadata from the most recent run for this commit (for branch/pr display)
      local meta_run
      meta_run="$(grep "|${hash_name}|" "$runs_file" | sort -t'|' -k1 -r | head -1)"
      local branch="" pr=""
      if [ -n "$meta_run" ]; then
        branch="$(echo "$meta_run" | cut -d'|' -f4)"
        pr="$(echo "$meta_run" | cut -d'|' -f5)"
      fi

      # Commit group header
      echo '<div class="commit-group">' >> "$dash_file"
      echo '<div class="commit-header">' >> "$dash_file"
      echo "<span class=\"hash\"><a href=\"${commit_url}\">${short_hash}</a></span>" >> "$dash_file"

      if [ -n "$branch" ]; then
        local branch_url="https://github.com/OpenTollGate/tollgate-module-basic-go/tree/${branch}"
        echo "<a class=\"badge badge-branch\" href=\"${branch_url}\">${branch}</a>" >> "$dash_file"
      fi

      if [ -n "$pr" ] && [ "$pr" != "0" ]; then
        local pr_url="https://github.com/OpenTollGate/tollgate-module-basic-go/pull/${pr}"
        echo "<a class=\"badge badge-pr\" href=\"${pr_url}\">#${pr}</a>" >> "$dash_file"
      fi

      echo '</div>' >> "$dash_file"

      # Table header for runs
      echo '<div class="run-header"><span>Timestamp</span><span>Type</span><span>Router</span><span>View</span><span>Pass</span><span>Fail</span><span>Skip</span><span>Flaky</span><span>Report</span></div>' >> "$dash_file"
      echo '<div class="runs">' >> "$dash_file"

      # Get runs for this commit, sorted newest first
      grep "|${hash_name}|" "$runs_file" | sort -t'|' -k1 -r | while IFS='|' read -r _ _ ts_name r_branch _ r_testtype r_router r_viewport r_timestamp r_passed r_failed r_skipped r_flaky r_duration; do
        local display_time
        if [ -n "$r_timestamp" ]; then
          display_time="$(format_timestamp "$r_timestamp")"
        else
          display_time="$ts_name"
        fi

        local report_path="reports/${hash_name}/${ts_name}/report/index.html"

        # Determine test type class
        local type_class="${r_testtype}"
        [ -z "$type_class" ] && type_class="e2e"

        echo "<div class=\"run-row\">" >> "$dash_file"
        echo "<span class=\"run-time\">${display_time}</span>" >> "$dash_file"
        echo "<span class=\"run-type ${type_class}\">${type_class}</span>" >> "$dash_file"
        echo "<span class=\"run-router\">${r_router}</span>" >> "$dash_file"
        echo "<span class=\"run-viewport\">${r_viewport}</span>" >> "$dash_file"
        echo "<span class=\"count count-pass\">${r_passed:-0}</span>" >> "$dash_file"
        echo "<span class=\"count count-fail\">${r_failed:-0}</span>" >> "$dash_file"
        echo "<span class=\"count count-skip\">${r_skipped:-0}</span>" >> "$dash_file"
        echo "<span class=\"count count-flaky\">${r_flaky:-0}</span>" >> "$dash_file"
        echo "<span class=\"run-link\"><a href=\"${report_path}\">View Report</a></span>" >> "$dash_file"
        echo "</div>" >> "$dash_file"
      done

      echo '</div>' >> "$dash_file"
      echo '</div>' >> "$dash_file"
    done < "$commits_file"

    rm -f "$commits_file"
  else
    echo '<div class="empty">No test reports found.</div>' >> "$dash_file"
  fi

  # ── Close HTML ─────────────────────────────────────────────────────

  cat >> "$dash_file" <<'DASHFOOT'
</div>
</body>
</html>
DASHFOOT

  # ── Replace placeholders ───────────────────────────────────────────

  sed -i.bak "s|PLACEHOLDER_LAST_UPDATED|${last_updated_display}|g" "$dash_file"
  sed -i.bak "s|PLACEHOLDER_TOTAL_RUNS|${total_runs}|g" "$dash_file"
  sed -i.bak "s|PLACEHOLDER_TOTAL_COMMITS|${total_commits}|g" "$dash_file"
  rm -f "${dash_file}.bak"

  rm -f "$runs_file"
}

# Format ISO timestamp to human-readable
format_timestamp() {
  local ts="$1"
  # Try python3 for reliable formatting
  if command -v python3 &>/dev/null; then
    python3 -c "
import sys
from datetime import datetime
ts = '${ts}'
for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H-%M-%S', '%Y-%m-%dT%H-%M-%SZ'):
    try:
        dt = datetime.strptime(ts, fmt)
        months = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
        print('%s %d, %d %02d:%02d UTC' % (months[dt.month], dt.day, dt.year, dt.hour, dt.minute))
        sys.exit(0)
    except ValueError:
        continue
print(ts)
" 2>/dev/null && return 0
  fi

  # Fallback: just strip the T and Z
  echo "$ts" | sed 's/T/ /;s/Z/ UTC/'
}

generate_dashboard

# ── Commit and push ──────────────────────────────────────────────────

git add -A
git commit -m "report: ${SHORT} ${DIR_TIMESTAMP}" || true

echo "==> Pushing to gh-pages..."
git push -f origin gh-pages 2>&1

# ── Print URLs ───────────────────────────────────────────────────────

HTTPS_URL="${REMOTE_URL/git@github.com:/https://github.com/}"
HTTPS_URL="${HTTPS_URL%.git}"
REPO_NAME="$(basename "$HTTPS_URL")"
ORG_NAME="$(dirname "$HTTPS_URL" | xargs basename)"
GITHUB_PAGES_BASE="https://${ORG_NAME}.github.io/${REPO_NAME}"

echo ""
echo "==> Report published to gh-pages"
echo "==> Report: ${GITHUB_PAGES_BASE}/reports/${COMMIT}/${DIR_TIMESTAMP}/report/index.html"
echo "==> Dashboard: ${GITHUB_PAGES_BASE}/"
