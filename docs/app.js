// TollGate Router Tests — Nostr reader for GitHub Pages
// Fetches kind 30078 (test run summaries) + kind 1063 (NIP-94 file metadata)
// from the bot npub, parses file lists + pass/fail metadata, renders test run
// cards with screenshots from Blossom URLs. Pure vanilla JS, no build step.
//
// Event contract (emitted by lib/result_publisher.py + nostr_publisher.py):
//
//   kind 30078 (parameterized replaceable, d-tag = run_id):
//     tags:
//       ["d", run_id]
//       ["t", "test-run"]
//       ["timestamp", "<unix>"]
//       ["file", blossom_url]            (one per uploaded file)
//     content: JSON string:
//       {
//         "run_id": "...",
//         "timestamp": "ISO-8601",
//         "blossom_server": "https://blossom.psbt.me",
//         "scan_summary": {"blocked": N, "redacted": N, "clean": N, "scanned": N},
//         "files": [{"path","url","sha256","mime","size","redacted"}, ...],
//         "metadata": {"branch","pr","passed","failed","skipped","router",...}
//       }
//
//   kind 1063 (NIP-94 file metadata, per file, BlossomFS):
//     tags: ["url", ...], ["x", sha256], ["m", mime], ["filename", ...], ["size", ...]

// === CONFIGURATION ==========================================================
// Replace with the PRTA bot's npub (hex, 64 chars, no 0x prefix).
const BOT_NPUB_HEX = "9a515b0f08d554b582e54202c7ca0e6ee56d81559957cbf9b40047d391b95fd5"; // shared with bcr-agent

const RELAYS = [
  "wss://relay.cashu.email",
  "wss://relay.tollgate.me",
];
const FETCH_TIMEOUT_MS = 12000;
const FETCH_SINCE_DAYS = 90;

// === STATE ==================================================================
let allRuns = [];
let selectedRunId = null;

// ===========================================================================
// WebSocket: Fetch kind 30078 + 1063 events from multiple relays
// ===========================================================================

function fetchNostrEvents(pubkeyHex, kinds = [30078, 1063], limit = 200) {
  return new Promise((resolve) => {
    const events = new Map(); // dedup by event id
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

      const subId = "prta-" + Math.random().toString(36).slice(2, 8);

      ws.onopen = () => {
        connectedCount++;
        ws.send(JSON.stringify([
          "REQ", subId,
          {
            authors: [pubkeyHex],
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

// Parse kind 30078 -> run object.
// The content field carries a JSON summary with full file metadata; the tags
// carry the d-tag (run_id) and per-file URL list. We prefer the content JSON
// and cross-reference kind 1063 metadata for anything missing.
function parseRunFromKind30078(event, fileMeta) {
  const tags = event.tags || [];
  const runId = getTag(tags, "d") || event.id;

  // Parse the JSON content payload emitted by the orchestrator.
  let payload = null;
  try {
    payload = JSON.parse(event.content || "{}");
  } catch (e) {
    // Older or hand-written events may carry plain text.
  }

  const contentFiles = (payload && Array.isArray(payload.files)) ? payload.files : [];
  const meta = (payload && payload.metadata) ? payload.metadata : {};
  const scanSummary = (payload && payload.scan_summary) ? payload.scan_summary : {};

  // Fallback: build file list from ["file", url] tags if content has none.
  let files = contentFiles;
  if (files.length === 0) {
    files = getAllTags(tags, "file").map((t) => ({ url: t[1] || "" }));
  }

  // Enrich each file with 1063 metadata when available.
  files = files.map((f) => {
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

  const passed = meta.passed != null ? meta.passed : null;
  const failed = meta.failed != null ? meta.failed : null;
  const skipped = meta.skipped != null ? meta.skipped : null;
  const total = meta.total != null ? meta.total : null;

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
    branch: meta.branch || null,
    pr: meta.pr || null,
    commit: meta.commit || null,
    router: meta.router || null,
    backend: meta.backend || null,
    clientType: meta.client_type || null,
    viewport: meta.viewport || null,
    blossomServer: payload ? payload.blossom_server : null,
    scanSummary: scanSummary,
    files: nonScreenshotFiles,
    screenshots,
    content: event.content || "",
    rawEvent: event,
  };
}

// Keep only the latest kind 30078 event per run_id (parameterized replaceable).
function dedupeRuns(events, fileMeta) {
  const k30078 = events.filter((e) => e.kind === 30078);
  const parsed = k30078
    .map((evt) => {
      try {
        return parseRunFromKind30078(evt, fileMeta);
      } catch (e) {
        console.warn("[PRTA] Failed to parse event", evt.id, e);
        return null;
      }
    })
    .filter(Boolean);

  const byRunId = new Map();
  for (const run of parsed) {
    const existing = byRunId.get(run.runId);
    if (!existing || run.timestamp > existing.timestamp) {
      byRunId.set(run.runId, run);
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

function statusIcon(status) {
  if (status === "success") return `<span class="status-dot status-success" title="All tests passed"></span>`;
  if (status === "error") return `<span class="status-dot status-error" title="Failures"></span>`;
  if (status === "partial") return `<span class="status-dot status-partial" title="Partial"></span>`;
  return "";
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

function renderRunsList(runs) {
  const container = document.getElementById("runs-list");
  container.innerHTML = "";

  if (runs.length === 0) {
    container.innerHTML = `
      <div class="runs-empty">
        <div class="runs-empty-icon">\u26A0</div>
        <p>No test runs found from the bot.</p>
        <p class="hint">Make sure the publisher has emitted<br>kind 30078 events.</p>
      </div>`;
    return;
  }

  const countEl = document.createElement("div");
  countEl.className = "runs-count";
  countEl.textContent = runs.length + " run" + (runs.length !== 1 ? "s" : "");
  container.appendChild(countEl);

  runs.forEach((run) => {
    const card = document.createElement("div");
    card.className = "run-card";
    card.dataset.runId = run.runId;
    if (run.runId === selectedRunId) card.classList.add("active");

    const passedTxt = run.passed != null ? run.passed : "?";
    const failedTxt = run.failed != null ? run.failed : "?";

    card.innerHTML = `
      <div class="run-card-header">
        <span class="run-id">${escapeHtml(shortRunId(run.runId))}</span>
        <div class="run-card-pf">
          ${statusIcon(run.status)}
          <span class="pf-text">
            <span class="pf-pass-num">${passedTxt}</span>pass
            <span class="pf-fail-num">${failedTxt}</span>fail
          </span>
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
        <span class="relative">${escapeHtml(formatRelative(run.timestamp))}</span>
      </div>
    `;

    card.addEventListener("click", () => {
      selectRun(run);
      if (window.innerWidth <= 768) {
        document.getElementById("app").classList.remove("sidebar-open");
      }
    });

    container.appendChild(card);
  });
}

// ===========================================================================
// Rendering: detail view
// ===========================================================================

let currentRun = null;

function selectRun(run) {
  selectedRunId = run.runId;
  currentRun = run;
  document.querySelectorAll(".run-card").forEach((el) => {
    el.classList.toggle("active", el.dataset.runId === run.runId);
  });

  const view = document.getElementById("run-view");
  view.scrollTop = 0;

  const metaItems = [];
  if (run.branch) metaItems.push(metaItem("Branch", escapeHtml(run.branch)));
  if (run.pr) metaItems.push(metaItem("PR", "#" + escapeHtml(run.pr)));
  if (run.commit) {
    const c = shortCommit(run.commit);
    metaItems.push(metaItem("Commit", `<code>${escapeHtml(c)}</code>`));
  }
  if (run.router) metaItems.push(metaItem("Router", escapeHtml(run.router)));
  if (run.backend) metaItems.push(metaItem("Backend", escapeHtml(run.backend)));
  if (run.clientType) metaItems.push(metaItem("Client", escapeHtml(run.clientType)));
  if (run.viewport) metaItems.push(metaItem("Viewport", escapeHtml(run.viewport)));

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
      <div class="detail-titles">
        <div class="detail-run">
          ${statusIcon(run.status)}
          <span class="run-id-lg">${escapeHtml(run.runId)}</span>
        </div>
      </div>
      ${metrics.length > 0 ? `<div class="detail-metrics">${metrics.join("")}</div>` : ""}
      ${metaItems.length > 0 ? `<div class="detail-meta-grid">${metaItems.join("")}</div>` : ""}
      <div class="detail-links">
        <a href="https://njump.me/${run.eventId}" target="_blank" class="detail-link" rel="noopener">Nostr event \u2197</a>
      </div>
    </div>
    <div class="detail-body">
      <section class="screenshot-section">
        <h3 class="section-title">Screenshots <span class="section-count">${run.screenshots.length}</span></h3>
        ${renderScreenshots(run.screenshots)}
      </section>
      <section class="files-section">
        <h3 class="section-title">Files <span class="section-count">${run.files.length}</span></h3>
        ${renderFileList(run.files)}
      </section>
    </div>
  `;

  // Wire up screenshot clicks -> lightbox
  view.querySelectorAll(".shot-thumb").forEach((img) => {
    img.addEventListener("click", () => openLightbox(img.dataset.fullUrl, img.dataset.filename));
  });
}

function showRunPlaceholder() {
  const view = document.getElementById("run-view");
  view.innerHTML = `
    <div class="run-placeholder">
      <div class="run-placeholder-icon">\u{1F50E}</div>
      <p>Select a test run to view details</p>
      <p class="hint">Screenshots load on demand to be gentle on Blossom servers.</p>
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
           src="${escapeHtml(s.url)}"
           data-full-url="${escapeHtml(s.url)}"
           data-filename="${escapeHtml(s.path)}"
           loading="lazy"
           alt="${escapeHtml(s.path)}"
           onerror="this.parentElement.classList.add('shot-error')">
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
  const container = document.getElementById("runs-list");
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
  console.log("[PRTA] Bot npub (hex):", BOT_NPUB_HEX);
  console.log("[PRTA] Fetching kinds [30078, 1063]");

  // Lightbox wiring
  const lb = document.getElementById("lightbox");
  lb.querySelector(".lightbox-backdrop").addEventListener("click", closeLightbox);
  lb.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeLightbox();
  });

  // Mobile sidebar toggle
  const menuBtn = document.getElementById("menu-toggle");
  const backdrop = document.getElementById("sidebar-backdrop");
  const app = document.getElementById("app");
  if (menuBtn) {
    menuBtn.addEventListener("click", () => app.classList.toggle("sidebar-open"));
  }
  if (backdrop) {
    backdrop.addEventListener("click", () => app.classList.remove("sidebar-open"));
  }

  // Loading state
  const loadingEl = document.getElementById("runs-loading");
  if (loadingEl) {
    loadingEl.innerHTML = `
      <div class="connecting">
        <div class="spinner"></div>
        <p>Connecting to relays\u2026</p>
        <div class="relay-list">${RELAYS.map((r) => `<span class="relay-chip">${r.replace("wss://", "")}</span>`).join("")}</div>
      </div>`;
  }

  if (BOT_NPUB_HEX === "REPLACE_WITH_PRTA_BOT_NPUB" || BOT_NPUB_HEX.length !== 64) {
    showGlobalError("Bot npub not configured. Set BOT_NPUB_HEX in app.js.");
    console.error("[PRTA] BOT_NPUB_HEX is a placeholder. Replace it with the PRTA bot's 64-char hex npub.");
    return;
  }

  try {
    const { events, connected } = await fetchNostrEvents(BOT_NPUB_HEX, [30078, 1063], 200);

    const n30078 = events.filter((e) => e.kind === 30078).length;
    const n1063 = events.filter((e) => e.kind === 1063).length;
    console.log("[PRTA] Connected to " + connected + "/" + RELAYS.length + " relays");
    console.log("[PRTA] Received " + events.length + " events (30078: " + n30078 + ", 1063: " + n1063 + ")");

    const fileMeta = buildFileMeta(events);
    console.log("[PRTA] File metadata map: " + fileMeta.size + " entries");

    allRuns = dedupeRuns(events, fileMeta);
    console.log("[PRTA] Parsed " + allRuns.length + " test runs");

    if (connected === 0 && events.length === 0) {
      showGlobalError("Could not connect to any relay.");
      return;
    }

    renderRunsList(allRuns);
    if (allRuns.length > 0) {
      showRunPlaceholder();
    }
  } catch (e) {
    console.error("[PRTA] Init error:", e);
    showGlobalError("Initialization failed: " + e.message);
  }
})();
