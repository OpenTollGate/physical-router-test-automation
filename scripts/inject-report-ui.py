#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path


SNIPPET = r'''
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


def inject_file(path: Path) -> None:
    html = path.read_text(errors="replace")

    if "</body>" not in html or "tg-report-explorer" in html or "data-jsonblob" not in html:
        print(f"==> Skipped {path} (already injected or not a pytest-html report)")
        return

    html = html.replace('<source src="" type="video/mp4">', '<source src="">')
    path.write_text(html.replace("</body>", SNIPPET + "\n</body>"))
    print(f"==> Injected report UI into {path}")


def iter_html_files(target: Path):
    if target.is_file():
        yield target
        return

    if target.is_dir():
        yield from sorted(path for path in target.rglob("*.html") if path.is_file())
        return

    raise FileNotFoundError(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()

    target = Path(args.path)

    try:
        files = list(iter_html_files(target))
    except FileNotFoundError:
        print(f"error: path not found: {target}", file=sys.stderr)
        return 1

    for path in files:
        inject_file(path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
