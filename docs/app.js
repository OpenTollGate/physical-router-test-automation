// TollGate Router Tests — Nostr reader for GitHub Pages
// Fetches DVM-native events (kind 5900/6900/7000) + kind 1063 file metadata
// from ALL pubkeys via NIP-90 DVM protocol, renders test run cards with
// screenshots from Blossom URLs. Pure vanilla JS, no build step.
//
// Event contract (emitted by nostr_publisher.py):
//
//   kind 5900 (DVM job request):
//     tags: ["param", key, value], ["e", request_id]
//
//   kind 6900 (DVM job result — primary run parser):
//     tags: ["param", key, value], ["e", request_id], ["file", url]
//     content: JSON with pass/fail counts, file URLs, metadata
//
//   kind 7000 (DVM job feedback):
//     tags: ["status", "processing|success|error"], ["e", request_id]
//
//   kind 1063 (NIP-94 file metadata, per file, BlossomFS):
//     tags: ["url", ...], ["x", sha256], ["m", mime], ["filename", ...], ["size", ...]

// === CONFIGURATION ==========================================================
const RELAYS = [
  "wss://relay.cashu.email",
  "wss://relay.damus.io",
  "wss://nos.lol",
];
const FETCH_TIMEOUT_MS = 12000;
const FETCH_SINCE_DAYS = 90;

// === STATE ==================================================================
let allRuns = [];
let selectedRunId = null;
let imgObserver = null;
let activeImgLoads = 0;
const MAX_CONCURRENT_IMG_LOADS = 3;
const imgLoadQueue = [];

const CACHE_KEY = "prta:runs:v5";
const filterState = { search: "", status: "all", sort: "newest", runner: "" };
let detailLoadId = 0;
let currentTestFilter = "all";
let currentTestSearch = "";
let currentHierarchy = null;
let liveSockets = [];
let liveConnectedCount = 0;
let displayIdCache = new Map();

// ===========================================================================
// WebSocket: Fetch NIP-90 DVM events (kind 5900/6900/7000) + 1063 from ALL pubkeys
// ===========================================================================

function fetchDvmEvents(kinds = [5900, 6900, 7000, 1063], limit = 200) {
  return new Promise((resolve) => {
    const events = new Map();
    let resolved = false;
    let closedRelays = 0;
    let connectedCount = 0;

    const timeout = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        resolve({ events: [...events.values()], connected: connectedCount });
      }
    }, FETCH_TIMEOUT_MS);

    RELAYS.forEach((relayUrl) => {
      let ws;
      try {
        ws = new WebSocket(relayUrl);
      } catch (e) {
        closedRelays++;
        checkDone();
        return;
      }

      const subId = "prta-dvm-" + Math.random().toString(36).slice(2, 8);

      ws.onopen = () => {
        connectedCount++;
        ws.send(JSON.stringify([
          "REQ", subId,
          {
            kinds,
            limit,
            since: Math.floor(Date.now() / 1000) - 86400 * FETCH_SINCE_DAYS,
          },
        ]));
        updateConnectionStatus(connectedCount, RELAYS.length);
      };

      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data[0] === "EVENT" && data[1] === subId && data[2]) {
            const evt = data[2];
            events.set(evt.id, evt);
          } else if (data[0] === "EOSE" && data[1] === subId) {
            ws.send(JSON.stringify(["CLOSE", subId]));
            ws.close();
          }
        } catch (e) { /* ignore parse errors */ }
      };

      ws.onerror = () => {
        closedRelays++;
        checkDone();
      };

      ws.onclose = () => {
        closedRelays++;
        checkDone();
      };
    });

    function checkDone() {
      if (closedRelays >= RELAYS.length && !resolved) {
        clearTimeout(timeout);
        resolved = true;
        resolve({ events: [...events.values()], connected: connectedCount });
      }
    }
  });
}

// ===========================================================================
// Real-time WebSocket subscription for live DVM events
// ===========================================================================

function subscribeToRealtimeUpdates() {
  liveSockets = [];
  liveConnectedCount = 0;

  RELAYS.forEach((relayUrl) => {
    let ws;
    try {
      ws = new WebSocket(relayUrl);
    } catch (e) {
      return;
    }

    ws.onopen = () => {
      liveSockets.push(ws);
      liveConnectedCount++;
      updateLiveIndicator();
      ws.send(JSON.stringify(["REQ", "prta-live", {
        kinds: [5900, 6900, 7000],
        since: Math.floor(Date.now() / 1000),
      }]));
    };

    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data[0] === "EVENT" && data[1] === "prta-live" && data[2]) {
          handleLiveEvent(data[2]);
        }
      } catch (e) { /* ignore parse errors */ }
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onclose = () => {
      const idx = liveSockets.indexOf(ws);
      if (idx >= 0) liveSockets.splice(idx, 1);
      liveConnectedCount = Math.max(0, liveConnectedCount - 1);
      updateLiveIndicator();
    };
  });
}

function handleLiveEvent(event) {
  if (!event || !event.kind) return;

  if (event.kind === 7000) {
    const tags = event.tags || [];
    const status = getTag(tags, "status") || "processing";
    const requestId = getTag(tags, "e");
    if (!requestId) return;

    const run = allRuns.find(
      (r) => r.runId === requestId || r.eventId === requestId
    );
    if (run && run.feedbackStatus !== status) {
      run.feedbackStatus = status;
      renderRunsList();
    }
    return;
  }

  if (event.kind === 6900) {
    const run = parseRunFromKind6900(event, new Map());
    if (!run) return;
    if (allRuns.find((r) => r.runId === run.runId)) return;

    allRuns.unshift(run);
    displayIdCache.clear();
    saveCachedRuns(allRuns);
    populateRunnerFilter();
    renderRunsList();
    return;
  }
}

function updateLiveIndicator() {
  const el = document.getElementById("live-indicator");
  if (!el) return;
  if (liveConnectedCount > 0) {
    el.classList.add("live");
    el.classList.remove("idle");
    el.title = "Live — " + liveConnectedCount + "/" + RELAYS.length + " relays connected";
  } else {
    el.classList.remove("live");
    el.classList.add("idle");
    el.title = "Connecting\u2026";
  }
}

// ===========================================================================
// Parsing helpers
// ===========================================================================

function getTag(tags, name) {
  const t = (tags || []).find((tg) => tg[0] === name);
  return t ? t[1] : null;
}

function getAllTags(tags, name) {
  return (tags || []).filter((tg) => tg[0] === name);
}

function getTagNum(tags, name) {
  const v = getTag(tags, name);
  if (v == null || v === "") return null;
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? null : n;
}

// Build a URL -> metadata map from kind 1063 NIP-94 events.
function buildFileMeta(events) {
  const meta = new Map();
  for (const evt of events) {
    if (evt.kind !== 1063) continue;
    const tags = evt.tags || [];
    const url = getTag(tags, "url");
    if (!url) continue;
    meta.set(url, {
      url,
      sha256: getTag(tags, "x"),
      mime: getTag(tags, "m") || "application/octet-stream",
      filename: getTag(tags, "filename"),
      size: getTagNum(tags, "size"),
      eventId: evt.id,
    });
  }
  return meta;
}

// ===========================================================================
// NIP-19 npub encoding (bech32) for runner pubkey display
// ===========================================================================

const BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function bech32Polymod(values) {
  const gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const b = chk >> 25;
    chk = (chk & 0x1ffffff) << 5 ^ v;
    for (let i = 0; i < 5; i++) {
      if (((b >> i) & 1) === 1) chk ^= gen[i];
    }
  }
  return chk;
}

function bech32HrpExpand(hrp) {
  return [...hrp].map((c) => c.charCodeAt(0) >> 5)
    .concat([...hrp].map((c) => c.charCodeAt(0) & 31));
}

function bech32CreateChecksum(hrp, data) {
  const values = bech32HrpExpand(hrp).concat(data, [0, 0, 0, 0, 0, 0]);
  const mod = bech32Polymod(values) ^ 1;
  const ret = [];
  for (let i = 0; i < 6; i++) {
    ret.push((mod >> (5 * (5 - i))) & 31);
  }
  return ret;
}

function convertBits(data, fromBits, toBits, pad) {
  let acc = 0, bits = 0;
  const ret = [];
  const maxv = (1 << toBits) - 1;
  for (const v of data) {
    if (v < 0 || (v >> fromBits) !== 0) return null;
    acc = (acc << fromBits) | v;
    bits += fromBits;
    while (bits >= toBits) {
      bits -= toBits;
      ret.push((acc >> bits) & maxv);
    }
  }
  if (pad) {
    if (bits > 0) ret.push((acc << (toBits - bits)) & maxv);
  } else if (bits >= fromBits || ((acc << (toBits - bits)) & maxv)) {
    return null;
  }
  return ret;
}

function hexToBytes(hex) {
  const bytes = [];
  for (let i = 0; i < hex.length; i += 2) {
    bytes.push(parseInt(hex.substr(i, 2), 16));
  }
  return bytes;
}

function hexToNpub(hex) {
  if (!hex || hex.length !== 64) return "";
  const hrp = "npub";
  const data = convertBits(hexToBytes(hex), 8, 5, true);
  if (!data) return "";
  const checksum = bech32CreateChecksum(hrp, data);
  const combined = data.concat(checksum);
  return hrp + "1" + combined.map((v) => BECH32_CHARSET[v]).join("");
}

function shortNpub(hex) {
  const npub = hexToNpub(hex);
  if (!npub) return "";
  return npub.slice(0, 10) + "\u2026";
}

// ===========================================================================
// NIP-90 DVM event parsing (kind 5900/6900/7000)
// ===========================================================================

// Parse kind 6900 (DVM job result) -> run object.
// The event carries test results as a DVM job completion. Content may be
// JSON with pass/fail counts and file URLs. Tags carry param values and
// file references. The linked 5900 request event provides the run_id via
// its d-tag.
function parseRunFromKind6900(event, fileMeta) {
  const tags = event.tags || [];

  let payload = null;
  try {
    payload = JSON.parse(event.content || "{}");
  } catch (e) { /* non-JSON content */ }

  const contentFiles = (payload && Array.isArray(payload.files)) ? payload.files : [];
  const passed = payload && payload.passed != null ? payload.passed : getTagNum(tags, "passed");
  const failed = payload && payload.failed != null ? payload.failed : getTagNum(tags, "failed");
  const skipped = payload && payload.skipped != null ? payload.skipped : getTagNum(tags, "skipped");
  const total = payload && payload.total != null
    ? payload.total
    : (passed != null || failed != null || skipped != null
      ? (passed || 0) + (failed || 0) + (skipped || 0)
      : null);

  const branch = (payload && payload.branch)
    || getParam(tags, "branch")
    || getTag(tags, "branch");
  const backend = (payload && payload.backend)
    || getParam(tags, "backend")
    || getTag(tags, "backend");
  const router = (payload && payload.router)
    || getParam(tags, "router")
    || getTag(tags, "router");
  const pr = (payload && payload.pr) || getParam(tags, "pr") || getTag(tags, "pr");

  let files = contentFiles;
  if (files.length === 0) {
    files = getAllTags(tags, "file").map((t) => ({ url: t[1] || "" }));
  }

  files = files.map((f) => {
    if (typeof f === "string") f = { url: f };
    const fm = fileMeta.get(f.url) || {};
    return {
      path: f.path || fm.filename || "",
      url: f.url,
      sha256: f.sha256 || fm.sha256 || "",
      mime: f.mime || fm.mime || "application/octet-stream",
      size: f.size != null ? f.size : (fm.size != null ? fm.size : null),
      redacted: !!f.redacted,
    };
  });

  const screenshots = files.filter((f) => (f.mime || "").startsWith("image/"));
  const nonScreenshotFiles = files.filter(
    (f) => !(f.mime || "").startsWith("image/")
  );

  let runId = (payload && payload.run_id) || getTag(tags, "d") || event.id;

  let status = "success";
  if (failed != null && failed > 0) status = "error";
  else if (passed != null && passed === 0 && total != null && total > 0) status = "error";

  return {
    id: event.id,
    eventId: event.id,
    runId,
    timestamp: event.created_at,
    status,
    passed,
    failed,
    skipped,
    total,
    branch: branch || null,
    pr: pr || null,
    repo: (payload && payload.repo) || null,
    commit: (payload && payload.commit) || getTag(tags, "commit") || null,
    requestEventId: getTag(tags, "e") || null,
    router: router || null,
    backend: backend || null,
    clientType: (payload && payload.client_type) || getTag(tags, "client_type") || null,
    viewport: (payload && payload.viewport) || getTag(tags, "viewport") || null,
    blossomServer: payload ? payload.blossom_server : null,
    scanSummary: (payload && payload.scan_summary) ? payload.scan_summary : {},
    files: nonScreenshotFiles,
    screenshots,
    content: event.content || "",
    rawEvent: event,
    source: "dvm",
    runnerNpub: event.pubkey,
    feedbackStatus: null,
  };
}

function getParam(tags, name) {
  const t = (tags || []).find((tg) => tg[0] === "param" && tg[1] === name);
  return t ? t[2] : null;
}

// Parse kind 7000 (DVM job feedback) -> status object.
function parseFeedbackFromKind7000(event) {
  const tags = event.tags || [];
  const status = getTag(tags, "status") || "processing";
  const requestId = getTag(tags, "e");
  return {
    status,
    requestId,
    runnerNpub: event.pubkey,
    timestamp: event.created_at,
    eventId: event.id,
  };
}

function dedupeDvmRuns(events, fileMeta, feedback) {
  const k6900 = events.filter((e) => e.kind === 6900);

  const byRunId = new Map();

  for (const evt of k6900) {
    try {
      const run = parseRunFromKind6900(evt, fileMeta);
      const existing = byRunId.get(run.runId);
      if (!existing || run.timestamp > existing.timestamp) {
        byRunId.set(run.runId, run);
      }
    } catch (e) {
      console.warn("[PRTA] Failed to parse 6900", evt.id, e);
    }
  }

  if (feedback && feedback.length > 0) {
    const fbByRun = new Map();
    for (const fb of feedback) {
      const runId = fb.requestId || fb.eventId;
      const prev = fbByRun.get(runId);
      if (!prev || fb.timestamp > prev.timestamp) {
        fbByRun.set(runId, fb);
      }
    }
    for (const run of byRunId.values()) {
      const fb = fbByRun.get(run.requestEventId) || fbByRun.get(run.runId) || fbByRun.get(run.eventId);
      if (fb) run.feedbackStatus = fb.status;
    }
  }

  return [...byRunId.values()].sort((a, b) => b.timestamp - a.timestamp);
}

// ===========================================================================
// Formatting helpers
// ===========================================================================

function formatTimestamp(unixSeconds) {
  if (!unixSeconds) return "Unknown";
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleString("en-US", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  }) + " UTC";
}

function formatDateShort(unixSeconds) {
  if (!unixSeconds) return "?";
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  });
}

function formatRelative(unixSeconds) {
  if (!unixSeconds) return "";
  const diff = Date.now() / 1000 - unixSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return Math.floor(diff / 86400) + "d ago";
}

function shortRunId(runId) {
  if (!runId) return "?";
  if (runId.length <= 20) return runId;
  return runId.slice(0, 16) + "\u2026";
}

function shortCommit(commit) {
  if (!commit) return null;
  const c = commit.replace(/^g/, "");
  return c.slice(0, 7);
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatBytes(bytes) {
  if (bytes == null) return "?";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function formatDuration(ms) {
  if (ms == null) return "";
  if (ms < 1000) return ms + "ms";
  return (ms / 1000).toFixed(1) + "s";
}

function statusIcon(status) {
  if (status === "success") return `<span class="status-dot status-success" title="All tests passed"></span>`;
  if (status === "error") return `<span class="status-dot status-error" title="Failures"></span>`;
  if (status === "partial") return `<span class="status-dot status-partial" title="Partial"></span>`;
  return "";
}

function statusBadge(run) {
  if (run.passed == null && run.failed == null) {
    return `<span class="status-badge unknown">NO DATA</span>`;
  }
  if (run.status === "error") {
    return `<span class="status-badge failed">FAILED</span>`;
  }
  if (run.status === "success" && run.passed != null && run.passed > 0) {
    return `<span class="status-badge passed">PASSED</span>`;
  }
  return `<span class="status-badge unknown">NO DATA</span>`;
}

function passFailBar(run) {
  const passed = run.passed || 0;
  const failed = run.failed || 0;
  const skipped = run.skipped || 0;
  const total = passed + failed + skipped;
  if (total === 0) return "";
  const pct = (val) => (val / total) * 100;
  return `<div class="pf-bar">
    ${passed > 0 ? `<div class="pf-seg pf-pass" style="width:${pct(passed)}%" title="${passed} passed"></div>` : ""}
    ${failed > 0 ? `<div class="pf-seg pf-fail" style="width:${pct(failed)}%" title="${failed} failed"></div>` : ""}
    ${skipped > 0 ? `<div class="pf-seg pf-skip" style="width:${pct(skipped)}%" title="${skipped} skipped"></div>` : ""}
  </div>`;
}

function loadCachedRuns() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const runs = JSON.parse(raw);
    return Array.isArray(runs) ? runs : null;
  } catch (e) {
    return null;
  }
}

function saveCachedRuns(runs) {
  try {
    const stripped = runs.map(({ rawEvent, content, ...rest }) => rest);
    localStorage.setItem(CACHE_KEY, JSON.stringify(stripped));
  } catch (e) {
    console.warn("[PRTA] Cache save failed:", e);
  }
}

// ===========================================================================
// Rendering: sidebar run list
// ===========================================================================

function updateConnectionStatus(connected, total) {
  const el = document.getElementById("conn-status");
  if (!el) return;
  if (connected === 0) {
    el.textContent = "Offline";
    el.className = "conn-badge offline";
  } else if (connected < total) {
    el.textContent = connected + "/" + total + " relays";
    el.className = "conn-badge partial";
  } else {
    el.textContent = connected + "/" + total + " relays";
    el.className = "conn-badge online";
  }
}

function getFilteredRuns() {
  let runs = allRuns.slice();

  if (filterState.runner) {
    runs = runs.filter((r) => r.runnerNpub === filterState.runner);
  }

  if (filterState.search) {
    const q = filterState.search;
    runs = runs.filter((r) => {
      const hay = [r.runId, r.branch, r.router, r.backend, r.pr,
        r.runnerNpub ? hexToNpub(r.runnerNpub) : null,
      ].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    });
  }

  if (filterState.status === "passed") {
    runs = runs.filter((r) => r.status === "success" && r.passed != null && r.passed > 0);
  } else if (filterState.status === "failed") {
    runs = runs.filter((r) => r.status === "error");
  }

  if (filterState.sort === "oldest") {
    runs.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  } else if (filterState.sort === "most") {
    runs.sort((a, b) => {
      const ta = a.total != null ? a.total : (a.passed || 0) + (a.failed || 0) + (a.skipped || 0);
      const tb = b.total != null ? b.total : (b.passed || 0) + (b.failed || 0) + (b.skipped || 0);
      return tb - ta;
    });
  } else {
    runs.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
  }

  return runs;
}

function computeDisplayId(run) {
  if (!run.runnerNpub) return shortRunId(run.runId);
  const cacheKey = run.runId + ":" + run.runnerNpub;
  if (displayIdCache.has(cacheKey)) return displayIdCache.get(cacheKey);

  const runnerRuns = allRuns
    .filter((r) => r.runnerNpub === run.runnerNpub)
    .sort((a, b) => b.timestamp - a.timestamp);
  const idx = runnerRuns.findIndex((r) => r.runId === run.runId);
  const displayId = `${shortNpub(run.runnerNpub)} #${runnerRuns.length - idx}`;
  displayIdCache.set(cacheKey, displayId);
  return displayId;
}

function populateRunnerFilter() {
  const select = document.getElementById("runner-filter");
  if (!select) return;

  const runners = [...new Set(
    allRuns.map((r) => r.runnerNpub).filter(Boolean)
  )].sort((a, b) => {
    const aNewest = allRuns.filter((r) => r.runnerNpub === a).reduce((mx, r) => Math.max(mx, r.timestamp || 0), 0);
    const bNewest = allRuns.filter((r) => r.runnerNpub === b).reduce((mx, r) => Math.max(mx, r.timestamp || 0), 0);
    return bNewest - aNewest;
  });

  const currentValue = filterState.runner;
  select.innerHTML = `<option value="">All runners</option>` +
    runners.map((npub) => {
      const count = allRuns.filter((r) => r.runnerNpub === npub).length;
      const label = shortNpub(npub);
      return `<option value="${escapeHtml(npub)}">${escapeHtml(label)} (${count})</option>`;
    }).join("");

  if (runners.includes(currentValue)) {
    select.value = currentValue;
  } else {
    filterState.runner = "";
    select.value = "";
  }
}

function buildSidebar() {
  const aside = document.getElementById("runs-list");
  aside.innerHTML = `
    <div class="sidebar-controls">
      <input type="text" id="search-input" class="search-input" placeholder="Search runs\u2026" autocomplete="off" />
      <div class="filter-toggles">
        <button class="filter-btn active" data-filter="all" type="button">All</button>
        <button class="filter-btn" data-filter="passed" type="button">Passed</button>
        <button class="filter-btn" data-filter="failed" type="button">Failed</button>
      </div>
      <select id="runner-filter" class="runner-filter">
        <option value="">All runners</option>
      </select>
      <select id="sort-select" class="sort-select">
        <option value="newest">Newest</option>
        <option value="oldest">Oldest</option>
        <option value="most">Most tests</option>
      </select>
    </div>
    <div class="runs-scroll" id="runs-scroll"></div>
  `;
  wireSidebarControls();
}

function wireSidebarControls() {
  const searchInput = document.getElementById("search-input");
  if (searchInput) {
    let timer = null;
    searchInput.addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        filterState.search = e.target.value.toLowerCase().trim();
        renderRunsList();
      }, 200);
    });
  }

  document.querySelectorAll(".filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      filterState.status = btn.dataset.filter;
      renderRunsList();
    });
  });

  const sortSelect = document.getElementById("sort-select");
  if (sortSelect) {
    sortSelect.addEventListener("change", (e) => {
      filterState.sort = e.target.value;
      renderRunsList();
    });
  }

  const runnerFilter = document.getElementById("runner-filter");
  if (runnerFilter) {
    runnerFilter.addEventListener("change", (e) => {
      filterState.runner = e.target.value;
      renderRunsList();
    });
  }
}

function renderRunsList() {
  const container = document.getElementById("runs-scroll");
  if (!container) return;
  container.innerHTML = "";

  if (allRuns.length === 0) {
    container.innerHTML = `
      <div class="runs-empty">
        <div class="runs-empty-icon">\u26A0</div>
        <p>No test runs found.</p>
        <p class="hint">Make sure the publisher has emitted<br>kind 6900 DVM result events.</p>
      </div>`;
    return;
  }

  const runs = getFilteredRuns();

  if (runs.length === 0) {
    container.innerHTML = `<div class="no-match">No runs match your filters.</div>`;
    return;
  }

  const countEl = document.createElement("div");
  countEl.className = "runs-count";
  countEl.textContent = runs.length + " run" + (runs.length !== 1 ? "s" : "");
  container.appendChild(countEl);

  runs.forEach((run, index) => {
    const card = document.createElement("div");
    card.className = "run-card";
    card.dataset.runId = run.runId;
    if (run.runId === selectedRunId) card.classList.add("active");
    if (index < 10) {
      card.style.animationDelay = (index * 30) + "ms";
    } else {
      card.style.animationDelay = "0ms";
      card.style.animationDuration = "0s";
    }

    const noData = run.passed == null && run.failed == null;

    const feedbackBadge = run.feedbackStatus
      ? `<span class="dvm-status-badge dvm-status-${escapeHtml(run.feedbackStatus)}">${escapeHtml(run.feedbackStatus)}</span>`
      : "";

    const npubLabel = run.runnerNpub && run.source === "dvm"
      ? `<span class="runner-npub" title="${escapeHtml(hexToNpub(run.runnerNpub))}">${escapeHtml(shortNpub(run.runnerNpub))}</span>`
      : "";

    const displayId = computeDisplayId(run);

    card.innerHTML = `
      <div class="run-card-header">
        <span class="run-id" title="${escapeHtml(run.runId)}">${escapeHtml(displayId)}</span>
        <div class="run-card-pf">
          ${feedbackBadge}
          ${statusIcon(run.status)}
          ${noData
            ? `<span class="pf-text pf-no-data">No data</span>`
            : `<span class="pf-text"><span class="pf-pass-num">${run.passed != null ? run.passed : "?"}</span>pass <span class="pf-fail-num">${run.failed != null ? run.failed : "?"}</span>fail</span>`
          }
        </div>
      </div>
      ${passFailBar(run)}
      <div class="run-card-meta">
        ${run.router ? `<span class="meta-chip">${escapeHtml(run.router)}</span>` : ""}
        ${run.branch ? `<span class="meta-chip meta-chip-branch">${escapeHtml(run.branch)}</span>` : ""}
        ${run.pr ? `<span class="meta-chip meta-chip-pr">#${escapeHtml(run.pr)}</span>` : ""}
      </div>
      <div class="run-card-footer">
        <span class="timestamp">${escapeHtml(formatDateShort(run.timestamp))}</span>
        <div class="run-card-footer-right">
          ${npubLabel}
          <span class="relative">${escapeHtml(formatRelative(run.timestamp))}</span>
        </div>
      </div>
    `;

    card.addEventListener("click", () => {
      selectRun(run);
    });

    container.appendChild(card);
  });
}

// ===========================================================================
// Lazy image loading (Intersection Observer + request throttle)
// ===========================================================================

function lazyLoadScreenshots(container) {
  if (imgObserver) imgObserver.disconnect();
  imgLoadQueue.length = 0;
  activeImgLoads = 0;

  imgObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        const img = entry.target;
        imgObserver.unobserve(img);
        enqueueImageLoad(img);
      }
    });
  }, { rootMargin: "300px" });

  container.querySelectorAll(".shot-thumb[data-src]").forEach((img) => {
    imgObserver.observe(img);
  });
}

function enqueueImageLoad(img) {
  imgLoadQueue.push(img);
  processImageQueue();
}

function processImageQueue() {
  while (activeImgLoads < MAX_CONCURRENT_IMG_LOADS && imgLoadQueue.length > 0) {
    const img = imgLoadQueue.shift();
    activeImgLoads++;

    img.addEventListener("load", () => {
      img.classList.add("loaded");
      activeImgLoads--;
      processImageQueue();
    }, { once: true });

    img.addEventListener("error", () => {
      img.parentElement.classList.add("shot-error");
      activeImgLoads--;
      processImageQueue();
    }, { once: true });

    img.src = img.dataset.src;
    img.removeAttribute("data-src");
  }
}

function observeNewThumbnails(container) {
  if (!imgObserver) return;
  container.querySelectorAll(".shot-thumb[data-src]").forEach((img) => {
    imgObserver.observe(img);
  });
}

// ===========================================================================
// Rendering: detail view
// ===========================================================================

let currentRun = null;

// ===========================================================================
// Test hierarchy: fetch summary.json, group artifacts by test
// ===========================================================================

async function fetchTestSummary(run) {
  const summaryFile = [...(run.files || []), ...(run.screenshots || [])]
    .find((f) => f.path === "summary.json");
  if (!summaryFile) return null;
  try {
    const resp = await fetch(summaryFile.url);
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

function buildTestHierarchy(run, summary) {
  if (!summary || !Array.isArray(summary.tests)) return null;

  const allFiles = [...(run.screenshots || []), ...(run.files || [])];
  const matchedUrls = new Set();
  let debugLogsUrl = null;

  const testsByName = new Map();
  for (const t of summary.tests) {
    testsByName.set(t.name, t);
  }

  const testArtifacts = new Map();
  const suiteFiles = {};
  for (const file of allFiles) {
    const path = file.path || "";
    if (path === "summary.json") continue;
    let matched = false;

    const m = path.match(/^(?:.*\/)?(.+)-(passed|failed|skipped|error)\.[^.]+$/);
    if (m && testsByName.has(m[1])) {
      const name = m[1];
      if (!testArtifacts.has(name)) {
        testArtifacts.set(name, { screenshots: [], videos: [], html: [] });
      }
      const mime = file.mime || "";
      if (mime.startsWith("image/")) {
        testArtifacts.get(name).screenshots.push(file);
      } else if (mime.startsWith("video/")) {
        testArtifacts.get(name).videos.push(file);
      } else if (mime.includes("html")) {
        testArtifacts.get(name).html.push(file);
      }
      matchedUrls.add(file.url);
      matched = true;
    }

    if (!matched && m) {
      const mime = file.mime || "";
      if (mime.startsWith("video/")) {
        const name = m[1];
        if (!testArtifacts.has(name)) {
          testArtifacts.set(name, { screenshots: [], videos: [], html: [] });
        }
        testArtifacts.get(name).videos.push(file);
        testArtifacts.get(name)._standalone = true;
        matchedUrls.add(file.url);
        matched = true;
      }
    }

    if (!matched) {
      const vm = path.match(/^raw\/([^/]+)\//);
      if (vm) {
        const suiteName = vm[1];
        if (!suiteFiles[suiteName]) suiteFiles[suiteName] = [];
        suiteFiles[suiteName].push(file);
      }
    }
    if (path === "debug-logs.json") {
      debugLogsUrl = file.url;
      matchedUrls.add(file.url);
    }
  }

  const suiteMap = new Map();
  for (const test of summary.tests) {
    const runner = test.runner || "ungrouped";
    if (!suiteMap.has(runner)) {
      const runnerInfo = (summary.runners || []).find((r) => r.name === runner);
      suiteMap.set(runner, {
        name: runner,
        status: runnerInfo ? runnerInfo.status : null,
        counts: runnerInfo ? runnerInfo.counts : null,
        tests: [],
      });
    }
    const artifacts = testArtifacts.get(test.name) || { screenshots: [], videos: [], html: [] };
    suiteMap.get(runner).tests.push({ ...test, artifacts });
  }

  const standaloneArtifacts = [];
  for (const [name, arts] of testArtifacts) {
    if (arts._standalone && (arts.videos.length > 0 || arts.screenshots.length > 0)) {
      standaloneArtifacts.push({
        name,
        outcome: "unknown",
        runner: "artifacts",
        artifacts: arts,
      });
    }
  }
  if (standaloneArtifacts.length > 0) {
    suiteMap.set("artifacts", {
      name: "Artifacts",
      status: null,
      counts: null,
      tests: standaloneArtifacts,
    });
  }

  for (const [suiteName, files] of Object.entries(suiteFiles)) {
    const suite = suiteMap.get(suiteName);
    if (!suite) continue;
    const passedTest = suite.tests.find(t => t.outcome === "passed") || suite.tests[0];
    if (!passedTest) continue;
    for (const file of files) {
      const mime = file.mime || "";
      if (mime.startsWith("image/")) {
        passedTest.artifacts.screenshots.push(file);
      } else if (mime.startsWith("video/")) {
        passedTest.artifacts.videos.push(file);
      } else if (mime.includes("html")) {
        passedTest.artifacts.html.push(file);
      }
      matchedUrls.add(file.url);
    }
  }

  const featuredVideos = [];
  for (const suite of suiteMap.values()) {
    for (const test of suite.tests) {
      if (test.artifacts.videos && test.artifacts.videos.length > 0) {
        featuredVideos.push({
          name: test.name,
          outcome: test.outcome,
          suite: suite.name,
          videos: test.artifacts.videos,
        });
      }
    }
  }

  const generalArtifacts = { screenshots: [], html: [], other: [] };
  const reports = [];

  for (const file of allFiles) {
    if (matchedUrls.has(file.url)) continue;
    const path = file.path || "";
    if (path === "summary.json") continue;

    const mime = file.mime || "";
    const isReport = /\.(json|xml|txt|log|csv)$/i.test(path) || mime.includes("json") || mime.includes("xml");

    if (mime.startsWith("image/")) {
      generalArtifacts.screenshots.push(file);
    } else if (mime.includes("html")) {
      generalArtifacts.html.push(file);
    } else if (isReport) {
      reports.push(file);
    } else {
      generalArtifacts.other.push(file);
    }
  }

  return { suites: [...suiteMap.values()], generalArtifacts, reports, featuredVideos, debugLogsUrl };
}

function outcomeIcon(outcome) {
  switch (outcome) {
    case "passed": return "\u2713";
    case "failed":
    case "error": return "\u2717";
    case "skipped": return "\u2298";
    default: return "?";
  }
}

function suiteStatusClass(suite) {
  const tests = suite.tests || [];
  if (tests.some((t) => t.outcome === "failed" || t.outcome === "error")) return "error";
  if (tests.some((t) => t.outcome === "passed")) return "success";
  return "partial";
}

function renderFilterBar(summary) {
  const counts = summary.counts || {};
  const total = counts.total || 0;
  const passed = counts.passed || 0;
  const failed = counts.failed || 0;
  const skipped = counts.skipped || 0;

  return `
    <div class="filter-bar">
      <div class="filter-buttons">
        <button class="filter-btn active" data-filter="all">All <span class="filter-count">${total}</span></button>
        <button class="filter-btn" data-filter="passed">Passed <span class="filter-count">${passed}</span></button>
        <button class="filter-btn" data-filter="failed">Failed <span class="filter-count">${failed}</span></button>
        <button class="filter-btn" data-filter="skipped">Skipped <span class="filter-count">${skipped}</span></button>
      </div>
      <input type="search" class="test-search" placeholder="Search tests\u2026" />
    </div>
  `;
}

function renderFeaturedVideos(hierarchy) {
  const vids = hierarchy.featuredVideos || [];
  if (vids.length === 0 || vids.length > 5) return "";
  return `
    <section class="featured-videos">
      <h3 class="featured-videos-title">\u{1F4F9} Featured Videos <span class="featured-videos-count">${vids.length}</span></h3>
      <div class="featured-videos-grid">
        ${vids.map((v) => `
          <button class="featured-video-card test-status-${v.outcome}" data-test-name="${escapeHtml(v.name)}">
            <span class="featured-video-play">\u25B6</span>
            <span class="featured-video-name">${escapeHtml(v.name)}</span>
            <span class="featured-video-suite">${escapeHtml(v.suite)}</span>
          </button>
        `).join("")}
      </div>
    </section>
  `;
}

function renderTestTree(hierarchy) {
  if (!hierarchy || hierarchy.suites.length === 0) return "";

  const suiteHtml = hierarchy.suites.map((suite) => {
    const tests = suite.tests || [];
    const passed = tests.filter((t) => t.outcome === "passed").length;
    const failed = tests.filter((t) => t.outcome === "failed" || t.outcome === "error").length;
    const skipped = tests.filter((t) => t.outcome === "skipped").length;

    return `
      <div class="test-suite" data-suite="${escapeHtml(suite.name)}">
        <div class="test-suite-header">
          <span class="test-suite-toggle">\u25BC</span>
          <span class="test-suite-name">${escapeHtml(suite.name)}</span>
          <span class="test-suite-badges">
            ${passed > 0 ? `<span class="suite-badge suite-badge-passed">${passed}</span>` : ""}
            ${failed > 0 ? `<span class="suite-badge suite-badge-failed">${failed}</span>` : ""}
            ${skipped > 0 ? `<span class="suite-badge suite-badge-skipped">${skipped}</span>` : ""}
          </span>
          <span class="status-dot status-${suiteStatusClass(suite)}"></span>
        </div>
        <div class="test-suite-body">
          ${tests.map((test) => renderTestCase(test)).join("")}
        </div>
      </div>
    `;
  }).join("");

  return `<div class="test-tree">${suiteHtml}</div>`;
}

function renderTestCase(test) {
  const outcome = test.outcome || "unknown";

  return `
    <div class="test-case" data-test-name="${escapeHtml(test.name)}" data-outcome="${escapeHtml(outcome)}">
      <div class="test-case-header">
        <span class="test-toggle">\u25B8</span>
        <span class="test-status-icon test-status-${escapeHtml(outcome)}">${outcomeIcon(outcome)}</span>
        <span class="test-name">${escapeHtml(test.name)}</span>
        ${test.duration_ms != null ? `<span class="test-duration">${formatDuration(test.duration_ms)}</span>` : ""}
        ${test.failure_message ? `<span class="test-failure" title="${escapeHtml(test.failure_message)}">\u26A0</span>` : ""}
      </div>
    </div>
  `;
}

function renderTestArtifacts(test) {
  const a = test.artifacts;
  if (!a) return "";
  const parts = [];

  parts.push(`
    <div class="test-detail-meta">
      ${test.description ? `<div class="test-description">${escapeHtml(test.description)}</div>` : ""}
      ${test.human_failure ? `<div class="test-failure-detail">\u26A0 ${escapeHtml(test.human_failure)}</div>` : ""}
      ${test.markers && test.markers.length ? `<div class="test-markers">${test.markers.map(m => `<span class="test-marker">${escapeHtml(m)}</span>`).join("")}</div>` : ""}
      <div class="test-detail-info">
        <span>Framework: ${escapeHtml(test.framework || "unknown")}</span>
        ${test.file ? `<span>File: ${escapeHtml(test.file)}</span>` : ""}
      </div>
    </div>
  `);

  if (a.screenshots.length > 0) {
    parts.push(`
      <div class="test-artifact-group">
        <h4 class="test-artifact-title">Screenshots <span class="test-artifact-count">${a.screenshots.length}</span></h4>
        ${renderScreenshots(a.screenshots)}
      </div>
    `);
  }

  if (a.videos.length > 0) {
    parts.push(`
      <div class="test-artifact-group">
        <h4 class="test-artifact-title">Videos <span class="test-artifact-count">${a.videos.length}</span></h4>
        <div class="test-video-grid">
          ${a.videos.map((v) => `
            <div class="test-video-card">
              <video controls preload="metadata" src="${escapeHtml(v.url)}" class="test-video"></video>
              <div class="test-video-name" title="${escapeHtml(v.path)}">${escapeHtml(v.path)}</div>
            </div>
          `).join("")}
        </div>
      </div>
    `);
  }

  if (a.html.length > 0) {
    parts.push(`
      <div class="test-artifact-group">
        <h4 class="test-artifact-title">HTML Snapshots <span class="test-artifact-count">${a.html.length}</span></h4>
        ${a.html.map((h) => `
          <div class="html-inline-row">
            <button class="html-view-btn" data-url="${escapeHtml(h.url)}" data-name="${escapeHtml(h.path)}">View Inline</button>
            <a href="${escapeHtml(h.url)}" target="_blank" rel="noopener" class="html-open-link">${escapeHtml(h.path)} \u2197</a>
          </div>
        `).join("")}
      </div>
    `);
  }

  if (currentHierarchy && currentHierarchy.debugLogsUrl) {
    parts.push(`
      <div class="test-artifact-group">
        <h4 class="test-artifact-title">Debug Output</h4>
        <details class="debug-log-section">
          <summary class="debug-log-toggle">Show debug output</summary>
          <pre class="debug-log-content" data-debug-url="${escapeHtml(currentHierarchy.debugLogsUrl)}" data-test-name="${escapeHtml(test.name)}">Loading\u2026</pre>
        </details>
      </div>
    `);
  }

  if (a.screenshots.length === 0 && a.videos.length === 0 && a.html.length === 0 && !(currentHierarchy && currentHierarchy.debugLogsUrl)) {
    parts.push(`<p class="test-no-artifacts">No screenshots or videos captured for this test.</p>`);
  }

  return `<div class="test-case-body-inner">${parts.join("")}</div>`;
}

function findTestInHierarchy(hierarchy, testName) {
  if (!hierarchy) return null;
  for (const suite of hierarchy.suites) {
    for (const test of suite.tests) {
      if (test.name === testName) return test;
    }
  }
  return null;
}

function applyTestFilters() {
  const view = document.getElementById("run-view");
  const filter = currentTestFilter;
  const search = currentTestSearch.toLowerCase().trim();

  view.querySelectorAll(".test-case").forEach((el) => {
    const outcome = el.dataset.outcome;
    const name = (el.dataset.testName || "").toLowerCase();

    let visible = true;
    if (filter === "passed") visible = outcome === "passed";
    else if (filter === "failed") visible = outcome === "failed" || outcome === "error";
    else if (filter === "skipped") visible = outcome === "skipped";

    if (visible && search) {
      visible = name.includes(search);
    }

    el.classList.toggle("hidden", !visible);
  });

  view.querySelectorAll(".test-suite").forEach((suite) => {
    const hasVisible = suite.querySelector(".test-case:not(.hidden)");
    suite.classList.toggle("hidden", !hasVisible);
  });
}

function renderGeneralArtifacts(hierarchy) {
  const parts = [];
  const ga = hierarchy.generalArtifacts;

  if (ga.screenshots.length > 0) {
    parts.push(`
      <section class="general-section">
        <h3 class="section-title">General Screenshots <span class="section-count">${ga.screenshots.length}</span></h3>
        ${renderScreenshots(ga.screenshots)}
      </section>
    `);
  }

  if (ga.html.length > 0) {
    parts.push(`
      <section class="general-section">
        <h3 class="section-title">HTML Snapshots <span class="section-count">${ga.html.length}</span></h3>
        <div class="html-list">
          ${ga.html.map((h) => `
            <div class="html-inline-row">
              <button class="html-view-btn" data-url="${escapeHtml(h.url)}" data-name="${escapeHtml(h.path)}">View Inline</button>
              <a href="${escapeHtml(h.url)}" target="_blank" rel="noopener" class="html-open-link">${escapeHtml(h.path)} \u2197</a>
            </div>
          `).join("")}
        </div>
      </section>
    `);
  }

  if (hierarchy.reports.length > 0) {
    parts.push(`
      <details class="advanced-section">
        <summary class="advanced-header">Advanced \u2014 Raw Reports &amp; Data <span class="section-count">${hierarchy.reports.length}</span></summary>
        <div class="advanced-body">
          ${renderFileList(hierarchy.reports)}
        </div>
      </details>
    `);
  }

  if (ga.other.length > 0) {
    parts.push(`
      <section class="general-section">
        <h3 class="section-title">Other Files <span class="section-count">${ga.other.length}</span></h3>
        ${renderFileList(ga.other)}
      </section>
    `);
  }

  return parts.join("");
}

function wireUpTestTree(view) {
  view.querySelectorAll(".filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      view.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentTestFilter = btn.dataset.filter;
      applyTestFilters();
    });
  });

  const search = view.querySelector(".test-search");
  if (search) {
    search.addEventListener("input", () => {
      currentTestSearch = search.value;
      applyTestFilters();
    });
  }

  view.querySelectorAll(".test-suite-header").forEach((header) => {
    header.addEventListener("click", () => {
      header.parentElement.classList.toggle("collapsed");
    });
  });

  view.querySelectorAll(".featured-video-card").forEach((card) => {
    card.addEventListener("click", () => {
      const testName = card.dataset.testName;
      const tc = view.querySelector(`.test-case[data-test-name="${CSS.escape(testName)}"]`);
      if (tc) {
        if (!tc.classList.contains("expanded")) {
          tc.querySelector(".test-case-header")?.click();
        }
        tc.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  });

  view.querySelectorAll(".test-case-header").forEach((header) => {
    header.addEventListener("click", () => {
      const testCase = header.parentElement;

      const expanded = testCase.classList.toggle("expanded");
      if (expanded) {
        const testName = testCase.dataset.testName;
        const test = findTestInHierarchy(currentHierarchy, testName);
        if (test) {
          const body = document.createElement("div");
          body.className = "test-case-body";
          body.innerHTML = renderTestArtifacts(test);
          testCase.appendChild(body);

          body.querySelectorAll(".shot-thumb").forEach((img) => {
            img.addEventListener("click", () => openLightbox(img.dataset.fullUrl, img.dataset.filename));
          });

          body.querySelectorAll(".html-view-btn").forEach((btn) => {
            btn.addEventListener("click", () => openHtmlViewer(btn.dataset.url, btn.dataset.name));
          });

          body.querySelectorAll(".debug-log-section").forEach((details) => {
            details.addEventListener("toggle", async () => {
              if (!details.open) return;
              const pre = details.querySelector(".debug-log-content");
              if (!pre || pre.dataset.loaded) return;
              try {
                const resp = await fetch(pre.dataset.debugUrl);
                const data = await resp.json();
                const testName = pre.dataset.testName;
                const entry = data[testName];
                if (!entry) {
                  pre.textContent = "No debug data for this test.";
                  pre.dataset.loaded = "1";
                  return;
                }
                const lines = [
                  `Outcome: ${entry.outcome || "unknown"}`,
                  `Duration: ${(entry.duration_ms / 1000).toFixed(1)}s`,
                ];
                if (entry.failure_message) lines.push(`\nFailure: ${entry.failure_message}`);
                if (entry.stdout) lines.push(`\n=== stdout ===\n${entry.stdout}`);
                if (entry.stderr) lines.push(`\n=== stderr ===\n${entry.stderr}`);
                if (entry.logs) lines.push(`\n=== logs ===\n${entry.logs}`);
                if (entry.traceback) lines.push(`\n=== traceback ===\n${entry.traceback}`);
                pre.textContent = lines.join("\n") || "No output captured.";
                pre.dataset.loaded = "1";
              } catch {
                pre.textContent = "Failed to load debug data.";
              }
            });
          });

          observeNewThumbnails(body);
        }
      } else {
        const body = testCase.querySelector(".test-case-body");
        if (body) body.remove();
      }
    });
  });

  view.querySelectorAll(".general-section .html-view-btn").forEach((btn) => {
    btn.addEventListener("click", () => openHtmlViewer(btn.dataset.url, btn.dataset.name));
  });

  view.querySelectorAll(".general-section .shot-thumb").forEach((img) => {
    img.addEventListener("click", () => openLightbox(img.dataset.fullUrl, img.dataset.filename));
  });

  lazyLoadScreenshots(view);
}

function renderFlatBody(view, run) {
  const body = view.querySelector(".detail-body");
  if (!body) return;

  body.innerHTML = `
    <section class="screenshot-section">
      <h3 class="section-title">Screenshots <span class="section-count">${run.screenshots.length}</span></h3>
      ${renderScreenshots(run.screenshots)}
    </section>
    <section class="files-section">
      <h3 class="section-title">Files <span class="section-count">${run.files.length}</span></h3>
      ${renderFileList(run.files)}
    </section>
  `;

  view.querySelectorAll(".shot-thumb").forEach((img) => {
    img.addEventListener("click", () => openLightbox(img.dataset.fullUrl, img.dataset.filename));
  });

  lazyLoadScreenshots(view);
}

// ===========================================================================
// HTML viewer modal
// ===========================================================================

async function openHtmlViewer(url, filename) {
  const modal = document.getElementById("html-viewer");
  if (!modal) return;
  const frame = modal.querySelector(".html-viewer-frame");
  const title = modal.querySelector(".html-viewer-title");
  const open = modal.querySelector(".html-viewer-open");

  title.textContent = filename || "";
  open.href = url;

  try {
    const resp = await fetch(url);
    const html = await resp.text();
    const blob = new Blob([html], { type: "text/html" });
    frame.src = URL.createObjectURL(blob);
  } catch {
    frame.src = url;
  }

  modal.hidden = false;
  requestAnimationFrame(() => modal.classList.add("open"));
}

function closeHtmlViewer() {
  const modal = document.getElementById("html-viewer");
  if (!modal) return;
  const frame = modal.querySelector(".html-viewer-frame");
  modal.classList.remove("open");
  setTimeout(() => {
    modal.hidden = true;
    if (frame.src.startsWith("blob:")) URL.revokeObjectURL(frame.src);
    frame.src = "about:blank";
  }, 200);
}

// ===========================================================================
// Detail view: selectRun
// ===========================================================================

function selectRunFromHash() {
  const hashRunId = location.hash.slice(1);
  if (!hashRunId) {
    if (allRuns.length > 0 && !selectedRunId) {
      const newest = [...allRuns].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))[0];
      selectRun(newest);
    }
    return;
  }
  if (selectedRunId === hashRunId) return;
  const run = allRuns.find((r) => r.runId === hashRunId);
  if (run) selectRun(run);
}

function showPlaceholder() {
  selectedRunId = null;
  currentRun = null;
  currentHierarchy = null;
  document.getElementById("app").classList.remove("mobile-view-detail");
  document.querySelectorAll(".run-card").forEach((el) => el.classList.remove("active"));

  const view = document.getElementById("run-view");
  view.scrollTop = 0;
  view.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon empty-icon-arrow">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>
          <line x1="8" y1="21" x2="16" y2="21"/>
          <line x1="12" y1="17" x2="12" y2="21"/>
        </svg>
      </div>
      <p class="empty-title">Select a test run</p>
      <p class="empty-hint empty-hint-desktop">&larr; Select a run from the list</p>
      <p class="empty-hint empty-hint-mobile">Tap a run to view details</p>
      <p class="hint">Runs are fetched live from Nostr relays.<br>
      Screenshots load on demand to be gentle on <a href="https://blossom.psbt.me" target="_blank" rel="noopener">blossom.psbt.me</a></p>
    </div>
  `;
}

async function selectRun(run) {
  selectedRunId = run.runId;
  currentRun = run;
  currentHierarchy = null;
  const myLoadId = ++detailLoadId;

  if (location.hash !== "#" + run.runId) {
    history.pushState({ runId: run.runId }, "", "#" + run.runId);
  }

  document.querySelectorAll(".run-card").forEach((el) => {
    el.classList.toggle("active", el.dataset.runId === run.runId);
  });

  if (window.innerWidth <= 768) {
    document.getElementById("app").classList.add("mobile-view-detail");
  }

  const view = document.getElementById("run-view");
  view.scrollTop = 0;

  const metaItems = [];
  if (run.branch) metaItems.push(metaItem("Branch", escapeHtml(run.branch)));
  if (run.pr) metaItems.push(metaItem("PR", "#" + escapeHtml(run.pr)));

  const repo = run.repo || (run.backend === "rust" ? "Amperstrand/tollgate-rs-ai-research-and-experiments" : "OpenTollGate/tollgate-module-basic-go");
  if (repo) {
    metaItems.push(metaItem("Repo", `<a href="https://github.com/${escapeHtml(repo)}" target="_blank" rel="noopener" class="meta-link">${escapeHtml(repo)} \u2197</a>`));
  }
  if (run.commit && run.commit !== "(branch head)" && run.commit.length >= 7) {
    const c = shortCommit(run.commit);
    metaItems.push(metaItem("Commit", `<a href="https://github.com/${escapeHtml(repo)}/commit/${escapeHtml(run.commit)}" target="_blank" rel="noopener" class="meta-link"><code>${escapeHtml(c)}</code> \u2197</a>`));
  }
  if (run.portal && run.portal !== "builtin") {
    metaItems.push(metaItem("Portal", `<span class="meta-chip meta-chip-portal">${escapeHtml(run.portal)}</span>`));
  }
  if (run.router) metaItems.push(metaItem("Router", escapeHtml(run.router)));
  if (run.backend) metaItems.push(metaItem("Backend", escapeHtml(run.backend)));
  if (run.clientType) metaItems.push(metaItem("Client", escapeHtml(run.clientType)));
  if (run.viewport) metaItems.push(metaItem("Viewport", escapeHtml(run.viewport)));
  if (run.runnerNpub) {
    metaItems.push(metaItem("Runner", `<code class="runner-code">${escapeHtml(shortNpub(run.runnerNpub))}</code>`));
  }

  if (run.scanSummary && run.scanSummary.scanned != null) {
    metaItems.push(metaItem("Scanned", String(run.scanSummary.scanned)));
  }
  if (run.scanSummary && run.scanSummary.blocked) {
    metaItems.push(metaItem("Blocked", String(run.scanSummary.blocked)));
  }

  const metrics = [];
  if (run.total != null) metrics.push(metric(run.total, "Total"));
  if (run.passed != null) metrics.push(metric(run.passed, "Passed", "green"));
  if (run.failed != null) metrics.push(metric(run.failed, "Failed", run.failed > 0 ? "red" : "green"));
  if (run.skipped != null && run.skipped > 0) metrics.push(metric(run.skipped, "Skipped", "yellow"));

  view.innerHTML = `
    <div class="detail-header">
      <button id="back-to-list" class="back-to-list" aria-label="Back to runs list">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 18 9 12 15 6"></polyline>
        </svg>
        <span>Runs</span>
      </button>
      <div class="detail-titles">
        <div class="detail-run">
          ${statusBadge(run)}
          ${statusIcon(run.status)}
          <span class="run-id-lg">${escapeHtml(run.runId)}</span>
        </div>
      </div>
      ${metrics.length > 0 ? `<div class="detail-metrics">${metrics.join("")}</div>` : ""}
      ${metaItems.length > 0 ? `<div class="detail-meta-grid">${metaItems.join("")}</div>` : ""}
      <div class="detail-links">
        <a href="https://njump.me/${run.eventId}" target="_blank" class="detail-link" rel="noopener">Nostr event \u2197</a>
      </div>
      ${renderNostrEventsSection(run)}
    </div>
    <div class="detail-body">
      <div class="test-tree-loading">
        <div class="spinner"></div>
        <p>Loading test results\u2026</p>
      </div>
    </div>
  `;

  const summary = await fetchTestSummary(run);

  const backBtn = view.querySelector("#back-to-list");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      if (history.state && history.state.runId) {
        history.back();
      } else {
        showPlaceholder();
      }
    });
  }

  if (myLoadId !== detailLoadId) return;

  const body = view.querySelector(".detail-body");
  if (!body) return;

  if (summary && summary.tests) {
    const hierarchy = buildTestHierarchy(run, summary);
    if (hierarchy) {
      currentHierarchy = hierarchy;
      currentTestFilter = "all";
      currentTestSearch = "";

      body.innerHTML = `
        ${renderFilterBar(summary)}
        ${renderFeaturedVideos(hierarchy)}
        ${renderTestTree(hierarchy)}
        ${renderGeneralArtifacts(hierarchy)}
      `;

      wireUpTestTree(view);
      return;
    }
  }

  renderFlatBody(view, run);
}

function renderNostrEventsSection(run) {
  const parts = [];

  const k6900 = run.rawEvent || null;
  let requestEvent = null;
  if (k6900) {
    const reqTag = (k6900.tags || []).find((t) => t[0] === "request");
    if (reqTag && reqTag[1]) {
      try { requestEvent = JSON.parse(reqTag[1]); } catch (e) {}
    }
  }
  const reqId = run.requestEventId || (requestEvent && requestEvent.id);

  if (requestEvent) {
    parts.push(renderEventBlock("Kind 5900 \u2014 DVM Job Request", requestEvent, "request"));
  } else if (reqId) {
    parts.push(`<div class="nostr-event-block"><div class="nostr-event-kind">Kind 5900 \u2014 DVM Job Request</div><div class="nostr-event-body"><a href="https://njump.me/${escapeHtml(reqId)}" target="_blank" rel="noopener" class="detail-link">${escapeHtml(reqId.slice(0,16))}\u2026 \u2197</a></div></div>`);
  }

  if (k6900) {
    parts.push(renderEventBlock("Kind 6900 \u2014 DVM Job Result", k6900, "result"));
  }

  if (run.feedbackStatus) {
    parts.push(`<div class="nostr-event-block nostr-event-feedback"><div class="nostr-event-kind">Kind 7000 \u2014 DVM Feedback: <span class="feedback-${escapeHtml(run.feedbackStatus)}">${escapeHtml(run.feedbackStatus)}</span></div>${reqId ? `<div class="nostr-event-body"><a href="https://njump.me/${escapeHtml(reqId)}" target="_blank" rel="noopener" class="detail-link">View on njump \u2197</a></div>` : ""}</div>`);
  }

  if (parts.length === 0) return "";

  return `<details class="nostr-events-section"><summary>Nostr DVM Events (${parts.length})</summary><div class="nostr-events-list">${parts.join("")}</div></details>`;
}

function renderEventBlock(title, event, cls) {
  const time = event.created_at ? new Date(event.created_at * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "?";
  const paramTags = (event.tags || []).filter((t) => t[0] === "param").map((t) => `${t[1]}=${t[2]}`).join(", ");
  let contentPreview = "";
  try {
    const parsed = JSON.parse(event.content || "{}");
    const display = {...parsed};
    if (display.files) display.files = `[${display.files.length} files]`;
    contentPreview = JSON.stringify(display, null, 2);
  } catch (e) {
    contentPreview = event.content || "";
  }
  const eventId = event.id || "?";
  return `<div class="nostr-event-block nostr-event-${cls}">
    <div class="nostr-event-kind">${escapeHtml(title)}</div>
    <div class="nostr-event-meta">
      <span>id: <code>${escapeHtml(eventId.slice(0,16))}\u2026</code></span>
      <span>time: ${escapeHtml(time)}</span>
      ${paramTags ? `<span>params: ${escapeHtml(paramTags)}</span>` : ""}
      <a href="https://njump.me/${escapeHtml(eventId)}" target="_blank" rel="noopener" class="detail-link">njump \u2197</a>
    </div>
    <pre class="nostr-event-content">${escapeHtml(contentPreview)}</pre>
  </div>`;
}

function metric(value, label, cls) {
  return `<div class="metric metric-${cls || ""}">
    <span class="metric-value">${value}</span>
    <span class="metric-label">${escapeHtml(label)}</span>
  </div>`;
}

function metaItem(label, valueHtml) {
  return `<div class="meta-item">
    <span class="meta-label">${escapeHtml(label)}</span>
    <span class="meta-value">${valueHtml}</span>
  </div>`;
}

function renderScreenshots(screenshots) {
  if (screenshots.length === 0) {
    return `<p class="section-empty">No screenshots in this run.</p>`;
  }
  return `<div class="shot-grid">` + screenshots.map((s) => `
    <div class="shot-card">
      <img class="shot-thumb"
           data-src="${escapeHtml(s.url)}"
           data-full-url="${escapeHtml(s.url)}"
           data-filename="${escapeHtml(s.path)}"
           alt="${escapeHtml(s.path)}">
      <div class="shot-name" title="${escapeHtml(s.path)}">${escapeHtml(s.path)}</div>
    </div>
  `).join("") + `</div>`;
}

function renderFileList(files) {
  if (files.length === 0) {
    return `<p class="section-empty">No additional files in this run.</p>`;
  }
  const rows = files.map((f) => {
    const name = (f.path || "").split("/").pop() || f.url;
    const icon = fileIcon(f.mime);
    return `<a class="file-row" href="${escapeHtml(f.url)}" target="_blank" rel="noopener">
      <span class="file-icon">${icon}</span>
      <span class="file-name" title="${escapeHtml(f.path)}">${escapeHtml(name)}</span>
      <span class="file-path">${escapeHtml(f.path)}</span>
      <span class="file-size">${escapeHtml(formatBytes(f.size))}</span>
      <span class="file-ext">${escapeHtml(extOf(f.path || name))}</span>
    </a>`;
  }).join("");
  return `<div class="file-list">${rows}</div>`;
}

function fileIcon(mime) {
  if (!mime) return "\u{1F4C4}";
  if (mime.includes("html")) return "\u{1F4C4}";
  if (mime.includes("json")) return "{}";
  if (mime.includes("xml")) return "\u{1F4C4}";
  if (mime.includes("text")) return "\u{1F4C4}";
  if (mime.includes("image")) return "\u{1F5BC}";
  return "\u{1F4C4}";
}

function extOf(path) {
  const dot = path.lastIndexOf(".");
  return dot >= 0 ? path.slice(dot + 1).toUpperCase() : "?";
}

// ===========================================================================
// Lightbox
// ===========================================================================

function openLightbox(url, filename) {
  const lb = document.getElementById("lightbox");
  const img = lb.querySelector(".lightbox-img");
  const open = lb.querySelector(".lightbox-open");
  img.src = url;
  open.href = url;
  lb.hidden = false;
  requestAnimationFrame(() => lb.classList.add("open"));
}

function closeLightbox() {
  const lb = document.getElementById("lightbox");
  lb.classList.remove("open");
  setTimeout(() => { lb.hidden = true; }, 200);
}

// ===========================================================================
// Error states
// ===========================================================================

function showGlobalError(message) {
  const container = document.getElementById("runs-scroll") || document.getElementById("runs-list");
  if (!container) return;
  container.innerHTML = `
    <div class="runs-empty">
      <div class="runs-empty-icon">\u26A0</div>
      <p>${escapeHtml(message)}</p>
      <p class="hint">Check your connection and try<br>refreshing the page.</p>
    </div>`;
}

// ===========================================================================
// Init
// ===========================================================================

(async function init() {
  console.log("[PRTA] Initializing\u2026");
  console.log("[PRTA] Relays:", RELAYS);
  console.log("[PRTA] Fetching kinds [5900, 6900, 7000, 1063] from all pubkeys");

  const lb = document.getElementById("lightbox");
  lb.querySelector(".lightbox-backdrop").addEventListener("click", closeLightbox);
  lb.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeLightbox();
      closeHtmlViewer();
    }
  });

  const hv = document.getElementById("html-viewer");
  if (hv) {
    hv.querySelector(".html-viewer-backdrop").addEventListener("click", closeHtmlViewer);
    hv.querySelector(".html-viewer-close").addEventListener("click", closeHtmlViewer);
  }

  window.addEventListener("popstate", () => {
    if (location.hash) {
      selectRunFromHash();
    } else {
      showPlaceholder();
    }
  });

  const menuBtn = document.getElementById("menu-toggle");
  const backdrop = document.getElementById("sidebar-backdrop");
  const app = document.getElementById("app");
  if (menuBtn) {
    menuBtn.addEventListener("click", () => app.classList.toggle("sidebar-open"));
  }
  if (backdrop) {
    backdrop.addEventListener("click", () => app.classList.remove("sidebar-open"));
  }

  buildSidebar();

  const scrollEl = document.getElementById("runs-scroll");
  if (scrollEl) {
    scrollEl.innerHTML = `
      <div class="loading">
        <div class="connecting">
          <div class="spinner"></div>
          <p>Connecting to relays\u2026</p>
          <div class="relay-list">${RELAYS.map((r) => `<span class="relay-chip">${r.replace("wss://", "")}</span>`).join("")}</div>
        </div>
      </div>`;
  }

  const cached = loadCachedRuns();
  if (cached && cached.length > 0) {
    allRuns = cached;
    populateRunnerFilter();
    renderRunsList();
    console.log("[PRTA] Rendered " + cached.length + " runs from cache (instant)");
    selectRunFromHash();
  }

  try {
    const { events, connected } = await fetchDvmEvents([5900, 6900, 7000, 1063], 200);

    const n5900 = events.filter((e) => e.kind === 5900).length;
    const n6900 = events.filter((e) => e.kind === 6900).length;
    const n7000 = events.filter((e) => e.kind === 7000).length;
    const n1063 = events.filter((e) => e.kind === 1063).length;
    console.log("[PRTA] Connected to " + connected + "/" + RELAYS.length + " relays");
    console.log("[PRTA] " + events.length + " events (5900: " + n5900 + ", 6900: " + n6900 + ", 7000: " + n7000 + ", 1063: " + n1063 + ")");

    const fileMeta = buildFileMeta(events);
    console.log("[PRTA] File metadata map: " + fileMeta.size + " entries");

    const feedback = events
      .filter((e) => e.kind === 7000)
      .map(parseFeedbackFromKind7000);

    const freshRuns = dedupeDvmRuns(events, fileMeta, feedback);
    console.log("[PRTA] " + freshRuns.length + " test runs");

    if (connected === 0 && events.length === 0) {
      if (!cached || cached.length === 0) {
        showGlobalError("Could not connect to any relay.");
      }
      return;
    }

    if (freshRuns.length > 0 || (!cached || cached.length === 0)) {
      const freshIds = new Set(freshRuns.map((r) => r.runId));
      const keptCached = (cached || []).filter((r) => !freshIds.has(r.runId));
      allRuns = [...freshRuns, ...keptCached];
      displayIdCache.clear();
      saveCachedRuns(allRuns);
      populateRunnerFilter();
      renderRunsList();
      selectRunFromHash();
    }
  } catch (e) {
    console.error("[PRTA] Init error:", e);
    if (!cached || cached.length === 0) {
      showGlobalError("Initialization failed: " + e.message);
    }
  }

  subscribeToRealtimeUpdates();
})();
