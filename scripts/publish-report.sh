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
# ALL JSON parsing uses inline Python. No grep/sed JSON hacks.
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

inject_pytest_media_ui() {
  local report_html="$1"
  [ -f "$report_html" ] || return 0

  python3 - "$report_html" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
html = path.read_text(errors='replace')

needle = '</body>'
if needle not in html or 'tg-report-explorer' in html or 'data-jsonblob' not in html:
    sys.exit(0)

html = html.replace('<source src="" type="video/mp4">', '<source src="">')

snippet = r'''
<script>
(function() {
  var proto = HTMLSourceElement.prototype;
  var desc = Object.getOwnPropertyDescriptor(proto, 'src');
  if (desc && desc.set) {
    Object.defineProperty(proto, 'src', {
      get: desc.get,
      set: function(v) {
        desc.set.call(this, v);
        var video = this.closest('video');
        if (video && v && v.length > 10) {
          try { video.load(); } catch(e) {}
        }
      },
      configurable: true,
      enumerable: true
    });
  }
})();
</script>
<style>
.tg-report-explorer{margin:16px 0 22px;padding:18px 20px;border:1px solid #dbe7ff;border-radius:12px;background:#f7faff;color:#1a1a2e;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.tg-report-explorer h3{margin:0 0 8px 0;font-size:20px;color:#0f2b5b}
.tg-report-explorer p{margin:0 0 10px 0;color:#334}
.tg-report-explorer .meta{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 14px}
.tg-report-explorer .pill{display:inline-block;padding:4px 10px;border-radius:999px;background:#e8f0fe;color:#1a73e8;font-size:12px;font-weight:600}
.tg-report-explorer .controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.tg-report-explorer button{border:0;border-radius:8px;padding:8px 12px;background:#302b63;color:#fff;cursor:pointer;font-weight:600}
.tg-report-explorer button.secondary{background:#eef3ff;color:#294172}
.tg-report-explorer button.ghost{background:#fff;color:#294172;border:1px solid #cdd9ff}
.tg-report-explorer .sections{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:16px}
.tg-report-explorer .section{background:#fff;border:1px solid #e6edff;border-radius:10px;padding:14px}
.tg-report-explorer .section h4{margin:0 0 8px 0;font-size:14px;color:#19396f;text-transform:uppercase;letter-spacing:.4px}
.tg-report-explorer ul{margin:8px 0 0 18px;padding:0}
.tg-report-explorer li{margin:5px 0}
.tg-report-explorer code{font-size:12px}
.tg-filter-list{display:flex;gap:8px;flex-wrap:wrap}
.tg-filter-list a{display:inline-block;padding:6px 10px;border-radius:999px;background:#eef3ff;color:#294172;text-decoration:none;font-size:12px;font-weight:600}
.tg-filter-list a:hover{background:#dde8ff}
.tg-jump-list{max-height:260px;overflow:auto;padding-right:6px}
.tg-jump-list a{color:#1a73e8;text-decoration:none}
.tg-jump-list a:hover{text-decoration:underline}
.tg-muted{color:#5a6473;font-size:13px}
.results-table-row.tg-hidden-by-explorer{display:none !important}
</style>
<script>
(function(){
  function initReportExplorer(){
    const dataContainer = document.getElementById('data-container');
    if(!dataContainer || !dataContainer.dataset || !dataContainer.dataset.jsonblob){
      return false;
    }

    let data;
    try {
      data = JSON.parse(dataContainer.dataset.jsonblob);
    } catch (_) {
      return false;
    }

    const testEntries = Object.entries(data.tests || {});
    let imageCount = 0;
    let videoCount = 0;
    const mediaTests = [];
    const resultCounts = {};
    const testsByType = {};

    const slugify = (value) => value.replace(/[^a-zA-Z0-9_-]+/g, '-');
    const inferType = (testId) => {
      if (testId.includes('/api/')) return 'api';
      if (testId.includes('/phone/')) return 'phone';
      if (testId.includes('/web/')) return 'web';
      if (testId.includes('/protocol/')) return 'protocol';
      if (testId.includes('/destructive/')) return 'destructive';
      return 'other';
    };

    const assignRowIds = () => {
      const rows = Array.from(document.querySelectorAll('#results-table tbody.results-table-row'));
      rows.forEach((tbody, index) => {
        const testCell = tbody.querySelector('.col-testId');
        const row = tbody.querySelector('tr.collapsible');
        if (!testCell || !row) return;
        const testId = (testCell.textContent || '').trim();
        if (!testId) return;
        const id = 'tg-test-' + slugify(testId) + '-' + index;
        row.id = id;
        tbody.dataset.testId = testId;
        tbody.dataset.tgExplorerId = id;
      });
    };

    const clickCollapseButton = (buttonId) => {
      const btn = document.getElementById(buttonId);
      if (btn) btn.click();
    };

    const expandAll = () => clickCollapseButton('show_all_details');
    const collapseAll = () => clickCollapseButton('hide_all_details');

    const clearHiddenRows = () => {
      document.querySelectorAll('#results-table tbody.results-table-row').forEach((tbody) => {
        tbody.classList.remove('tg-hidden-by-explorer');
      });
    };

    const filterRows = (predicate) => {
      document.querySelectorAll('#results-table tbody.results-table-row').forEach((tbody) => {
        const testId = tbody.dataset.testId || '';
        const resultCell = tbody.querySelector('.col-result');
        const result = resultCell ? (resultCell.textContent || '').trim().toLowerCase() : '';
        tbody.classList.toggle('tg-hidden-by-explorer', !predicate({testId, result, tbody}));
      });
    };

    const scrollToTest = (testId) => {
      const tbody = Array.from(document.querySelectorAll('#results-table tbody.results-table-row')).find((node) => node.dataset.testId === testId);
      if (!tbody) return;
      expandAll();
      setTimeout(() => {
        const rowId = tbody.dataset.tgExplorerId;
        if (rowId) {
          const row = document.getElementById(rowId);
          if (row) row.scrollIntoView({behavior: 'smooth', block: 'center'});
        }
      }, 220);
    };

    for (const [testId, entries] of testEntries) {
      let extrasForTest = 0;
      let result = 'unknown';
      for (const entry of (entries || [])) {
        result = (entry.result || result || 'unknown').toLowerCase();
        for (const extra of (entry.extras || [])) {
          if (extra.format_type === 'image') {
            imageCount += 1;
            extrasForTest += 1;
          } else if (extra.format_type === 'video') {
            videoCount += 1;
            extrasForTest += 1;
          }
        }
      }
      if (extrasForTest > 0) {
        mediaTests.push({testId, extrasForTest, result});
      }
      resultCounts[result] = (resultCounts[result] || 0) + 1;
      const type = inferType(testId);
      testsByType[type] = (testsByType[type] || 0) + 1;
    }

    assignRowIds();

    const explorer = document.createElement('section');
    explorer.className = 'tg-report-explorer';

    const mediaTestsSorted = [...mediaTests].sort((a, b) => b.extrasForTest - a.extrasForTest || a.testId.localeCompare(b.testId));
    const mediaListItems = mediaTestsSorted.slice(0, 20).map(item => '<li><a href="#" data-jump-test="' + item.testId.replace(/"/g, '&quot;') + '"><code>' + item.testId + '</code></a> — ' + item.extrasForTest + ' media item' + (item.extrasForTest === 1 ? '' : 's') + ' (' + item.result + ')</li>').join('');
    const typeLinks = Object.entries(testsByType).sort().map(([type, count]) => '<a href="#" data-filter-type="' + type + '">' + type + ' (' + count + ')</a>').join('');
    const resultLinks = Object.entries(resultCounts).sort().map(([result, count]) => '<a href="#" data-filter-result="' + result + '">' + result + ' (' + count + ')</a>').join('');

    explorer.innerHTML = '<h3>Report explorer</h3><p>This published pytest report already contains all screenshots and videos inline. Use the controls below to quickly navigate the most interesting tests instead of scanning the raw table manually.</p><div class="meta"><span class="pill">' + testEntries.length + ' tests</span><span class="pill">' + imageCount + ' screenshots</span><span class="pill">' + videoCount + ' videos</span><span class="pill">' + mediaTests.length + ' tests with media</span></div><div class="controls"><button type="button" id="tg-expand-media">Expand all details</button><button type="button" class="secondary" id="tg-collapse-media">Hide all details</button><button type="button" class="ghost" id="tg-show-media-only">Show only tests with media</button><button type="button" class="ghost" id="tg-show-all-tests">Show all tests</button></div><div class="sections"><div class="section"><h4>Filter by result</h4><div class="tg-filter-list">' + (resultLinks || '<span class="tg-muted">No result buckets found.</span>') + '</div></div><div class="section"><h4>Filter by test area</h4><div class="tg-filter-list">' + (typeLinks || '<span class="tg-muted">No test areas found.</span>') + '</div></div><div class="section"><h4>Jump to media-rich tests</h4><div class="tg-jump-list"><ul>' + (mediaListItems || '<li class="tg-muted">No embedded media found.</li>') + '</ul></div></div></div>';

    const summaryBlock = document.querySelector('.summary');
    if (summaryBlock && summaryBlock.parentNode) {
      summaryBlock.parentNode.insertBefore(explorer, summaryBlock.nextSibling);
    }

    const expandBtn = document.getElementById('tg-expand-media');
    const collapseBtn = document.getElementById('tg-collapse-media');
    if (expandBtn) expandBtn.addEventListener('click', expandAll);
    if (collapseBtn) collapseBtn.addEventListener('click', collapseAll);

    const mediaOnlyBtn = document.getElementById('tg-show-media-only');
    const showAllBtn = document.getElementById('tg-show-all-tests');
    if (mediaOnlyBtn) mediaOnlyBtn.addEventListener('click', () => {
      filterRows(({testId}) => mediaTests.some((item) => item.testId === testId));
      expandAll();
    });
    if (showAllBtn) showAllBtn.addEventListener('click', () => {
      clearHiddenRows();
    });

    document.querySelectorAll('[data-filter-result]').forEach((link) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        const result = link.getAttribute('data-filter-result');
        filterRows(({result: rowResult}) => rowResult === result);
      });
    });

    document.querySelectorAll('[data-filter-type]').forEach((link) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        const type = link.getAttribute('data-filter-type');
        filterRows(({testId}) => inferType(testId) === type);
      });
    });

    document.querySelectorAll('[data-jump-test]').forEach((link) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        const testId = link.getAttribute('data-jump-test');
        scrollToTest(testId);
      });
    });

    const params = new URLSearchParams(window.location.search);
    if (params.get('mediaOnly') === '1') {
      setTimeout(() => {
        if (mediaOnlyBtn) mediaOnlyBtn.click();
      }, 220);
    }
    if (params.get('showMedia') === '1' || params.has('sort')) {
      setTimeout(expandAll, 250);
    }

    document.addEventListener('click', (e) => {
      const img = e.target.closest('.media-container__viewport img');
      if (img && img.src && img.src.startsWith('data:')) {
        e.preventDefault();
        e.stopPropagation();
        try {
          const parts = img.src.split(',');
          const mime = parts[0].match(/:(.*?);/)[1];
          const bstr = atob(parts[1]);
          const u8arr = new Uint8Array(bstr.length);
          for (let i = 0; i < bstr.length; i++) u8arr[i] = bstr.charCodeAt(i);
          const blob = new Blob([u8arr], {type: mime});
          window.open(URL.createObjectURL(blob), '_blank');
        } catch (_) { /* fallback: do nothing */ }
      }
    }, true);

    return true;
  }

  if (!initReportExplorer()) {
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      if (initReportExplorer() || tries > 20) {
        clearInterval(timer);
      }
    }, 500);
  }
})();
</script>
'''

path.write_text(html.replace(needle, snippet + '\n</body>'))
PY
}

inject_pytest_media_ui "$TARGET_DIR/report/index.html"

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
# Entirely in Python — no grep/sed JSON parsing.

echo "==> Generating dashboard..."

python3 - "$WORK/gh-pages/reports" "$WORK/gh-pages/index.html" <<'PYEOF'
import json
import os
import sys
from collections import OrderedDict
from datetime import datetime


reports_dir = sys.argv[1]
output_file = sys.argv[2]


def get(d, path, default=''):
    """Get nested dict value with dot notation."""
    keys = path.split('.')
    v = d
    for k in keys:
        if isinstance(v, dict):
            v = v.get(k)
        else:
            return default
    if v is None:
        return default
    return v


def read_run(path):
    """Read run.json, supporting both new (schema_version>=1) and old (flat) schemas."""
    with open(path) as f:
        d = json.load(f)

    sv = d.get('schema_version', 0)

    if sv >= 1:
        commit = get(d, 'sut.commit', 'unknown')
        commit_short = get(d, 'sut.commit_short', commit[:7])
        branch = get(d, 'sut.branch', '')
        pr = get(d, 'sut.pr', '')
        backend = get(d, 'sut.backend', '')
        repo = get(d, 'sut.repo', '')
        router_id = get(d, 'lab.router_id', 'unknown')
        client_type = get(d, 'lab.client_type', '')
        viewport = get(d, 'lab.viewport', '')
        test_plan = get(d, 'test_plan', '')
        status = get(d, 'status', '')
        started_at = get(d, 'started_at', '')
        duration_ms = get(d, 'duration_ms', 0)
        counts = get(d, 'counts', {})
        runners = get(d, 'runners', [])
    else:
        # Old schema — flat fields
        commit = d.get('tollgate_commit', 'unknown')
        commit_short = commit[:7]
        branch = d.get('tollgate_branch', '')
        pr = d.get('tollgate_pr', '')
        backend = ''
        repo = d.get('sut_repo', 'OpenTollGate/tollgate-module-basic-go')
        router_id = d.get('router_id', 'unknown')
        client_type = d.get('client_type', '')
        viewport = d.get('viewport', '')
        test_plan = d.get('test_type', 'e2e')
        status = ''
        started_at = d.get('timestamp', '')
        duration_ms = d.get('duration_ms', 0)
        counts = {
            'total': d.get('total', 0),
            'passed': d.get('passed', 0),
            'failed': d.get('failed', 0),
            'errors': 0,
            'skipped': d.get('skipped', 0),
            'flaky': d.get('flaky', 0),
        }
        runners = []

    return {
        'commit': commit,
        'commit_short': commit_short,
        'branch': branch,
        'pr': pr,
        'backend': backend,
        'repo': repo,
        'router_id': router_id,
        'client_type': client_type,
        'viewport': viewport,
        'test_plan': test_plan,
        'status': status,
        'started_at': started_at,
        'duration_ms': int(duration_ms) if duration_ms else 0,
        'counts': counts,
        'runners': runners,
    }


def format_ts(ts):
    """Format ISO timestamp to human-readable."""
    if not ts:
        return 'N/A'
    for fmt in (
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H-%M-%S',
        '%Y-%m-%dT%H-%M-%SZ',
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            months = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            return '%s %d, %d %02d:%02d UTC' % (
                months[dt.month], dt.day, dt.year, dt.hour, dt.minute)
        except ValueError:
            continue
    return ts


def format_duration(ms):
    """Format duration in ms to human-readable."""
    if not ms or ms <= 0:
        return '-'
    seconds = ms / 1000
    if seconds < 60:
        return '%ds' % int(seconds)
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return '%dm%02ds' % (minutes, secs)


def esc(s):
    """HTML-escape a string."""
    return (str(s)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


# ── Collect all runs ─────────────────────────────────────────────────

runs = []
if os.path.isdir(reports_dir):
    for hash_dir_name in os.listdir(reports_dir):
        hash_path = os.path.join(reports_dir, hash_dir_name)
        if not os.path.isdir(hash_path):
            continue
        for ts_dir_name in os.listdir(hash_path):
            ts_path = os.path.join(hash_path, ts_dir_name)
            if not os.path.isdir(ts_path):
                continue
            rj = os.path.join(ts_path, 'run.json')
            if not os.path.isfile(rj):
                continue
            try:
                run = read_run(rj)
                run['hash_dir'] = hash_dir_name
                run['ts_dir'] = ts_dir_name
                run['report_path'] = (
                    'reports/%s/%s/report/index.html'
                    % (hash_dir_name, ts_dir_name))
                runs.append(run)
            except Exception:
                continue

# Sort by started_at descending
runs.sort(key=lambda r: r.get('started_at', '') or '', reverse=True)

# Group by commit (preserving insertion order = newest commit first)
commit_groups = OrderedDict()
for run in runs:
    c = run.get('commit', 'unknown')
    if c not in commit_groups:
        commit_groups[c] = []
    commit_groups[c].append(run)

total_runs = len(runs)
total_commits = len(commit_groups)
last_updated = format_ts(runs[0]['started_at']) if runs else 'N/A'


# ── Build HTML ───────────────────────────────────────────────────────

out = []

out.append('''\
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
.container{max-width:1200px;margin:2rem auto;padding:0 1rem}
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
.badge-passed{background:#e6f4ea;color:#137333}
.badge-failed{background:#fce8e8;color:#d93025}
.badge-errored{background:#fce8e8;color:#d93025}
.badge-partial{background:#fce8b2;color:#b06000}
.runs{padding:.5rem 0}
.run-row{display:grid;grid-template-columns:170px 75px 75px 60px 85px 75px 1fr 55px 65px;align-items:center;padding:.65rem 1.5rem;border-bottom:1px solid #f5f5f5;font-size:.875rem;gap:.4rem}
.run-row:last-child{border-bottom:none}
.run-row:hover{background:#fafbfc}
.run-time{color:#555;font-variant-numeric:tabular-nums}
.run-plan{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.3px;background:#fce8b2;color:#b06000;padding:2px 6px;border-radius:4px;text-align:center}
.run-router,.run-client{color:#444;font-family:"SF Mono",SFMono-Regular,Consolas,monospace;font-size:.8rem}
.runner-summary{font-size:.8rem;color:#555}
.runner-summary .rname{font-weight:600;color:#333}
.runner-summary .rcount{font-variant-numeric:tabular-nums}
.runner-summary .rp{color:#137333}
.runner-summary .rf{color:#d93025}
.runner-summary .rs{color:#80868b}
.run-dur{font-variant-numeric:tabular-nums;color:#555;font-size:.8rem}
.run-link a{color:#1a73e8;text-decoration:none;font-weight:500;font-size:.8rem}
.run-link a:hover{text-decoration:underline}
.run-header{display:grid;grid-template-columns:170px 75px 75px 60px 85px 75px 1fr 55px 65px;padding:.5rem 1.5rem;background:#fafbfc;font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:#666;border-bottom:1px solid #eee;gap:.4rem}
.empty{padding:3rem;text-align:center;color:#888;font-size:1rem}
</style>
</head>
<body>
<div class="header">
<h1>TollGate Test Reports</h1>
<div class="updated">Last updated: %(last_updated)s</div>
</div>
<div class="container">
<div class="summary">
<div class="summary-card"><div class="label">Total Runs</div><div class="value">%(total_runs)d</div></div>
<div class="summary-card"><div class="label">Commits Tested</div><div class="value">%(total_commits)d</div></div>
</div>
''' % {
    'last_updated': esc(last_updated),
    'total_runs': total_runs,
    'total_commits': total_commits,
})

if not runs:
    out.append('<div class="empty">No test reports found.</div>')
else:
    for commit, commit_runs in commit_groups.items():
        # Metadata from the most recent run for this commit
        meta = commit_runs[0]
        short = meta['commit_short']
        branch = meta.get('branch', '')
        pr = meta.get('pr', '')
        repo = (meta.get('repo', '')
                or 'OpenTollGate/tollgate-module-basic-go')

        # Build commit URL
        if repo and '/' in repo:
            commit_url = 'https://github.com/%s/commit/%s' % (repo, commit)
        else:
            commit_url = (
                'https://github.com/OpenTollGate/'
                'tollgate-module-basic-go/commit/%s' % commit)

        out.append('<div class="commit-group">')
        out.append('<div class="commit-header">')
        out.append(
            '<span class="hash">'
            '<a href="%s">%s</a></span>'
            % (esc(commit_url), esc(short)))

        if branch:
            if repo and '/' in repo:
                branch_url = (
                    'https://github.com/%s/tree/%s' % (repo, branch))
            else:
                branch_url = (
                    'https://github.com/OpenTollGate/'
                    'tollgate-module-basic-go/tree/%s' % branch)
            out.append(
                '<a class="badge badge-branch" href="%s">%s</a>'
                % (esc(branch_url), esc(branch)))

        if pr and str(pr) not in ('0', ''):
            if repo and '/' in repo:
                pr_url = 'https://github.com/%s/pull/%s' % (repo, pr)
            else:
                pr_url = (
                    'https://github.com/OpenTollGate/'
                    'tollgate-module-basic-go/pull/%s' % pr)
            out.append(
                '<a class="badge badge-pr" href="%s">#%s</a>'
                % (esc(pr_url), esc(pr)))

        out.append('</div>')

        # Table header
        out.append(
            '<div class="run-header">'
            '<span>Timestamp</span>'
            '<span>Plan</span>'
            '<span>Status</span>'
            '<span>Backend</span>'
            '<span>Router</span>'
            '<span>Client</span>'
            '<span>Runners</span>'
            '<span>Dur.</span>'
            '<span>Report</span>'
            '</div>')
        out.append('<div class="runs">')

        for run in commit_runs:
            display_time = (
                format_ts(run.get('started_at', ''))
                or run.get('ts_dir', ''))
            test_plan = run.get('test_plan', '') or 'e2e'
            status = run.get('status', '')
            backend = run.get('backend', '')
            router_id = run.get('router_id', 'unknown')
            client_type = run.get('client_type', '')
            report_path = run.get('report_path', '')
            duration = format_duration(run.get('duration_ms', 0))

            # ── Runner summary: per-runner pass/fail/skip ─────────
            runners = run.get('runners', [])
            if runners:
                runner_parts = []
                for r in runners:
                    rname = r.get('name', '?')
                    rc = r.get('counts', {})
                    rp = rc.get('passed', 0)
                    rf = rc.get('failed', 0) + rc.get('errors', 0)
                    rs = rc.get('skipped', 0)
                    runner_parts.append(
                        '<span class="rname">%s</span> '
                        '<span class="rcount">'
                        '<span class="rp">%d\u2713</span>'
                        '<span class="rf">%d\u2717</span>'
                        '<span class="rs">%d\u25cb</span>'
                        '</span>'
                        % (esc(rname), rp, rf, rs))
                runner_html = ' &nbsp;/ '.join(runner_parts)
            else:
                # Old schema: show flat counts
                c = run.get('counts', {})
                runner_html = (
                    '<span class="rcount">'
                    '<span class="rp">%d\u2713</span> '
                    '<span class="rf">%d\u2717</span> '
                    '<span class="rs">%d\u25cb</span>'
                    '</span>'
                    % (c.get('passed', 0),
                       c.get('failed', 0),
                       c.get('skipped', 0)))

            # ── Status badge ──────────────────────────────────────
            if status:
                status_html = (
                    '<span class="badge badge-%s">%s</span>'
                    % (esc(status), esc(status)))
            else:
                # Infer from counts (old schema)
                c = run.get('counts', {})
                if c.get('failed', 0) > 0 or c.get('errors', 0) > 0:
                    status_html = (
                        '<span class="badge badge-failed">failed</span>')
                else:
                    status_html = (
                        '<span class="badge badge-passed">passed</span>')

            out.append('<div class="run-row">')
            out.append(
                '<span class="run-time">%s</span>'
                % esc(display_time))
            out.append(
                '<span class="run-plan">%s</span>'
                % esc(test_plan))
            out.append('<span>%s</span>' % status_html)
            out.append(
                '<span class="run-client">%s</span>'
                % esc(backend))
            out.append(
                '<span class="run-router">%s</span>'
                % esc(router_id))
            out.append(
                '<span class="run-client">%s</span>'
                % esc(client_type))
            out.append(
                '<span class="runner-summary">%s</span>'
                % runner_html)
            out.append(
                '<span class="run-dur">%s</span>'
                % esc(duration))
            out.append(
                '<span class="run-link">'
                '<a href="%s">View</a></span>'
                % esc(report_path))
            out.append('</div>')

        out.append('</div>')  # .runs
        out.append('</div>')  # .commit-group

out.append('''\
</div>
</body>
</html>
''')

with open(output_file, 'w') as f:
    f.write('\n'.join(out))

print('==> Dashboard written to %s (%d runs, %d commits)'
      % (output_file, total_runs, total_commits))
PYEOF

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
if [ -n "$TOLLGATE_GH_PAGES_CNAME" ]; then
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
