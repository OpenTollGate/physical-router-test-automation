#!/usr/bin/env python3
"""fips chaos test results visualizer.

Reads tree/mmp/congestion snapshots + per-node logs from a results directory
and produces:
  - Per-peer link quality bar charts (goodput, RTT, loss)
  - Tree depth distribution comparison
  - Congestion / ECN counter bar chart
  - Topology convergence GIF (warmup vs final spanning tree)
  - Rekey event timeline (from per-node daemon logs)
  - Self-contained interactive HTML report (all images base64-embedded)

Usage:
    python3 visualize.py --results-dir ./results/.../fips-results/
    python3 visualize.py --results-dir ./results/.../fips-results/ --output report.html
"""

import argparse
import base64
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # headless backend
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import networkx as nx
import numpy as np


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_json(path):
    """Load a JSON file, returning None on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def load_text(path):
    """Load a text file, returning None on failure."""
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return None


def build_addr_map(tree_snap):
    """Map node_addr -> node ID (e.g. 'n01') from the tree snapshot.

    The tree snapshot is keyed by node ID and each entry has 'my_node_addr'.
    """
    addr_map = {}
    if not tree_snap:
        return addr_map
    for node_id, data in tree_snap.items():
        addr = data.get("my_node_addr")
        if addr:
            addr_map[addr] = node_id
    return addr_map


def resolve_name(display_name, addr, addr_map):
    """Resolve a display name to a clean node ID.

    Sometimes display_name is a truncated npub (e.g. 'npub1m6al...tkyw').
    If we can resolve the addr via addr_map, prefer the clean ID.
    """
    if addr and addr in addr_map:
        return addr_map[addr]
    if display_name and display_name.startswith("n") and len(display_name) <= 4:
        # Looks like a clean node ID already
        return display_name
    if display_name and display_name.startswith("npub"):
        # Truncated npub — try to shorten for display
        if "..." in display_name:
            return display_name.split("...")[0][-6:]
        return display_name[-8:]
    return display_name or "?"


# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------

def parse_analysis(path):
    """Parse analysis.txt into a dict of key -> value (string)."""
    text = load_text(path)
    if text is None:
        return {}
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("---"):
            continue
        # Lines look like "Panics:               0"
        m = re.match(r"^(.+?):\s*(.+)$", line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            result[key] = val
    return result


def parse_metadata(path):
    """Parse metadata.txt into a dict."""
    text = load_text(path)
    if text is None:
        return {}
    result = {"adjacency": {}, "edge_list": []}
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Key-value pairs at top
        m = re.match(r"^(\w+):\s*(.+)$", stripped)
        if stripped == "adjacency:":
            section = "adjacency"
            continue
        elif stripped == "edges:":
            section = "edges"
            continue
        if section == "adjacency":
            # n01 (172.20.0.10): n02, n04, n05, n10
            m2 = re.match(r"^(\S+)\s+\(([^)]+)\):\s*(.*)$", stripped)
            if m2:
                node = m2.group(1)
                ip = m2.group(2)
                peers = [p.strip() for p in m2.group(3).split(",") if p.strip()]
                result["adjacency"][node] = {"ip": ip, "peers": peers}
        elif section == "edges":
            # n01 -- n02
            m3 = re.match(r"^(\S+)\s+--\s+(\S+)$", stripped)
            if m3:
                result["edge_list"].append((m3.group(1), m3.group(2)))
        elif m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            # Try to convert to int
            try:
                result[key] = int(val)
            except ValueError:
                result[key] = val
    return result


# ---------------------------------------------------------------------------
# Log parsing: rekey events and parent switches
# ---------------------------------------------------------------------------

# Log line format:
#   2026-06-26T21:42:58.654519Z  INFO fips::node::tree: Parent switched, ...
TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+(\w+)\s+(.*)$"
)


def extract_log_events(results_dir, keywords):
    """Scan per-node logs for lines matching any keyword.

    Returns a list of dicts: {node, timestamp, level, message, keyword}.
    """
    events = []
    log_files = sorted(glob.glob(os.path.join(results_dir, "fips-node-*.log")))
    for log_path in log_files:
        basename = os.path.basename(log_path)
        # Extract node ID from filename: fips-node-n01.log -> n01
        m = re.match(r"fips-node-(n\d+)\.log", basename)
        if not m:
            continue
        node_id = m.group(1)
        text = load_text(log_path)
        if text is None:
            continue
        for line in text.splitlines():
            for kw in keywords:
                if kw.lower() in line.lower():
                    ts_match = TS_RE.match(line)
                    if ts_match:
                        ts_str = ts_match.group(1)
                        level = ts_match.group(2)
                        msg = ts_match.group(3)
                        try:
                            ts = datetime.strptime(
                                ts_str.replace("Z", "+0000"),
                                "%Y-%m-%dT%H:%M:%S.%f%z",
                            )
                        except ValueError:
                            ts = None
                        events.append(
                            {
                                "node": node_id,
                                "timestamp": ts,
                                "ts_str": ts_str,
                                "level": level,
                                "message": msg,
                                "keyword": kw,
                            }
                        )
    return events


# ---------------------------------------------------------------------------
# Figure <-> base64 conversion
# ---------------------------------------------------------------------------

def fig_to_base64(fig, format="png", dpi=120):
    """Convert a matplotlib figure to a base64-encoded data URI string."""
    buf = io.BytesIO()
    fig.savefig(buf, format=format, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/{format};base64,{data}"


def file_to_base64(path, mime_type):
    """Read a file and return as base64 data URI."""
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime_type};base64,{data}"
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Topology visualization
# ---------------------------------------------------------------------------

def compute_tree_layout(tree_snap, addr_map):
    """Build a networkx DiGraph from the tree snapshot and compute positions.

    Returns (graph, positions, root_id, depth_map).
    """
    if not tree_snap:
        return None, {}, None, {}

    G = nx.DiGraph()
    depth_map = {}
    root_id = None

    # Add all nodes
    for node_id, data in tree_snap.items():
        depth = data.get("depth", 0)
        depth_map[node_id] = depth
        G.add_node(node_id, depth=depth, is_root=data.get("is_root", False))
        if data.get("is_root"):
            root_id = node_id

    # Add parent->child edges
    for node_id, data in tree_snap.items():
        if data.get("is_root"):
            continue
        parent_addr = data.get("parent")
        parent_id = resolve_name(
            data.get("parent_display_name"), parent_addr, addr_map
        )
        # Only add edge if parent is a known node in our graph
        if parent_id and parent_id in G:
            G.add_edge(parent_id, node_id)

    # Compute positions: hierarchical layout
    # Group nodes by depth
    pos = {}
    nodes_at_depth = {}
    for node_id, depth in depth_map.items():
        nodes_at_depth.setdefault(depth, []).append(node_id)

    max_depth = max(depth_map.values()) if depth_map else 0
    for depth, nodes in sorted(nodes_at_depth.items()):
        nodes_sorted = sorted(nodes)
        n = len(nodes_sorted)
        for i, node_id in enumerate(nodes_sorted):
            # Spread horizontally, stack vertically by depth
            x = (i + 1) / (n + 1) if n > 0 else 0.5
            y = -depth  # root at top (y=0), deeper nodes go down
            pos[node_id] = (x, y)

    return G, pos, root_id, depth_map


def draw_topology_frame(tree_snap, addr_map, title):
    """Draw a single topology frame. Returns the matplotlib figure."""
    G, pos, root_id, depth_map = compute_tree_layout(tree_snap, addr_map)
    if G is None or len(G) == 0:
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(
            0.5, 0.5, "No tree data", ha="center", va="center", fontsize=16
        )
        ax.set_title(title)
        ax.axis("off")
        return fig

    fig, ax = plt.subplots(figsize=(10, 7))

    # Node colors: root is gold, others are steel blue with depth gradient
    node_colors = []
    node_sizes = []
    max_depth = max(depth_map.values()) if depth_map else 1
    for node_id in G.nodes():
        d = depth_map.get(node_id, 0)
        if node_id == root_id:
            node_colors.append("#FFD700")  # gold for root
            node_sizes.append(1500)
        else:
            # Lighter blue for deeper nodes
            intensity = 1.0 - (d / (max_depth + 1)) * 0.5
            node_colors.append((0.27, 0.51, 0.71, intensity))
            node_sizes.append(600 + (max_depth - d) * 150)

    # Draw edges
    nx.draw_networkx_edges(
        G, pos, ax=ax, edge_color="#888888", arrows=True, arrowsize=18,
        node_size=node_sizes, width=1.8, connectionstyle="arc3,rad=0.05",
    )

    # Draw nodes
    nx.draw_networkx_nodes(
        G, pos, ax=ax, node_color=node_colors, node_size=node_sizes,
        edgecolors="#333333", linewidths=1.5, alpha=0.9,
    )

    # Draw labels
    labels = {n: n for n in G.nodes()}
    nx.draw_networkx_labels(
        G, pos, labels, ax=ax, font_size=9, font_weight="bold",
        font_color="#1a1a1a",
    )

    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.axis("off")

    # Add depth annotations on the left
    if depth_map:
        for d in range(max_depth + 1):
            y = -d
            ax.text(
                -0.08, y, f"d={d}", transform=ax.transData,
                fontsize=8, color="#666666", va="center", ha="right",
            )

    plt.tight_layout()
    return fig


def make_topology_gif(tree_warmup, tree_final, addr_map, outpath):
    """Create a 2-frame animated GIF: warmup vs final topology.

    Returns the output path if successful, None otherwise.
    """
    fig = plt.figure(figsize=(10, 7))

    def update(frame_idx):
        fig.clear()
        if frame_idx == 0:
            snap = tree_warmup
            label = "Warmup (post-convergence)"
        else:
            snap = tree_final
            label = "Final (post-stress)"
        ax = fig.add_subplot(111)
        G, pos, root_id, depth_map = compute_tree_layout(snap, addr_map)
        if G is None or len(G) == 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            ax.set_title(label)
            ax.axis("off")
            return

        node_colors = []
        node_sizes = []
        max_depth = max(depth_map.values()) if depth_map else 1
        for node_id in G.nodes():
            d = depth_map.get(node_id, 0)
            if node_id == root_id:
                node_colors.append("#FFD700")
                node_sizes.append(1500)
            else:
                intensity = 1.0 - (d / (max_depth + 1)) * 0.5
                node_colors.append((0.27, 0.51, 0.71, intensity))
                node_sizes.append(600 + (max_depth - d) * 150)

        nx.draw_networkx_edges(
            G, pos, ax=ax, edge_color="#888888", arrows=True, arrowsize=18,
            node_size=node_sizes, width=1.8,
        )
        nx.draw_networkx_nodes(
            G, pos, ax=ax, node_color=node_colors, node_size=node_sizes,
            edgecolors="#333333", linewidths=1.5, alpha=0.9,
        )
        labels = {n: n for n in G.nodes()}
        nx.draw_networkx_labels(
            G, pos, labels, ax=ax, font_size=9, font_weight="bold",
        )
        ax.set_title(label, fontsize=14, fontweight="bold")
        ax.axis("off")

    try:
        anim = animation.FuncAnimation(
            fig, update, frames=2, interval=2000, repeat=True,
        )
        anim.save(outpath, writer="pillow", fps=0.5)
        plt.close(fig)
        return outpath
    except Exception as e:
        plt.close(fig)
        print(f"Warning: GIF creation failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Link quality charts
# ---------------------------------------------------------------------------

def plot_link_quality(mmp_snap, addr_map):
    """Multi-bar chart: goodput, SRTT, loss rate per peer pair.

    Returns a matplotlib figure.
    """
    if not mmp_snap:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No MMP data", ha="center", va="center")
        ax.axis("off")
        return fig

    # Collect unique directed peer pairs
    pairs = []  # (src, dst, goodput, srtt, loss, lqi, etx)
    seen = set()
    for src_id, data in sorted(mmp_snap.items()):
        peers = data.get("peers", [])
        for peer in peers:
            dst_addr = peer.get("peer")
            dst_name = peer.get("display_name")
            dst_id = resolve_name(dst_name, dst_addr, addr_map)
            pair_key = f"{src_id}->{dst_id}"
            if pair_key in seen:
                continue
            seen.add(pair_key)
            ll = peer.get("link_layer", {})
            pairs.append(
                (
                    src_id,
                    dst_id,
                    ll.get("goodput_bps", 0),
                    ll.get("srtt_ms", 0),
                    ll.get("loss_rate", 0),
                    ll.get("lqi", 0),
                    ll.get("etx", 0),
                )
            )

    if not pairs:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No peer links found", ha="center", va="center")
        ax.axis("off")
        return fig

    labels = [f"{p[0]}->{p[1]}" for p in pairs]
    goodputs = [p[2] for p in pairs]
    srtts = [p[3] for p in pairs]
    losses = [p[4] * 100 for p in pairs]  # as percentage

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    x = np.arange(len(labels))
    bar_width = 0.6

    # Goodput
    ax1 = axes[0]
    bars1 = ax1.bar(x, goodputs, bar_width, color="#2196F3", alpha=0.85)
    ax1.set_ylabel("Goodput (bps)", fontsize=11)
    ax1.set_title("Per-Link Goodput", fontsize=13, fontweight="bold")
    ax1.axhline(y=np.mean(goodputs), color="#1565C0", linestyle="--",
                alpha=0.5, label=f"mean={np.mean(goodputs):.1f}")
    ax1.legend(fontsize=9)
    ax1.grid(axis="y", alpha=0.3)

    # SRTT
    ax2 = axes[1]
    bars2 = ax2.bar(x, srtts, bar_width, color="#4CAF50", alpha=0.85)
    ax2.set_ylabel("SRTT (ms)", fontsize=11)
    ax2.set_title("Per-Link Smoothed RTT", fontsize=13, fontweight="bold")
    ax2.axhline(y=np.mean(srtts), color="#2E7D32", linestyle="--",
                alpha=0.5, label=f"mean={np.mean(srtts):.2f}")
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.3)

    # Loss rate
    ax3 = axes[2]
    bars3 = ax3.bar(x, losses, bar_width, color="#F44336", alpha=0.85)
    ax3.set_ylabel("Loss Rate (%)", fontsize=11)
    ax3.set_title("Per-Link Packet Loss", fontsize=13, fontweight="bold")
    if max(losses) > 0:
        ax3.axhline(y=np.mean(losses), color="#B71C1C", linestyle="--",
                     alpha=0.5, label=f"mean={np.mean(losses):.3f}")
        ax3.legend(fontsize=9)
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax3.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    return fig


def plot_tree_depth(tree_warmup, tree_final):
    """Tree depth distribution comparison (warmup vs final).

    Returns a matplotlib figure.
    """
    fig, ax = plt.subplots(figsize=(8, 5))

    def get_depths(snap):
        if not snap:
            return []
        return [d.get("depth", 0) for d in snap.values()]

    depths_w = get_depths(tree_warmup)
    depths_f = get_depths(tree_final)

    if not depths_w and not depths_f:
        ax.text(0.5, 0.5, "No tree data", ha="center", va="center",
                transform=ax.transAxes)
        ax.axis("off")
        return fig

    max_d = max(max(depths_w) if depths_w else 0,
                max(depths_f) if depths_f else 0)
    bins = np.arange(-0.5, max_d + 1.5, 1)

    width = 0.35
    x = np.arange(0, max_d + 1)

    # Count nodes at each depth
    counts_w = [depths_w.count(d) for d in range(max_d + 1)]
    counts_f = [depths_f.count(d) for d in range(max_d + 1)]

    ax.bar(x - width / 2, counts_w, width, label="Warmup",
           color="#FF9800", alpha=0.85)
    ax.bar(x + width / 2, counts_f, width, label="Final",
           color="#2196F3", alpha=0.85)

    ax.set_xlabel("Tree Depth", fontsize=11)
    ax.set_ylabel("Node Count", fontsize=11)
    ax.set_title("Spanning Tree Depth Distribution", fontsize=13,
                 fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([f"d={d}" for d in range(max_d + 1)])
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    return fig


def plot_congestion(cong_snap):
    """Bar chart of ECN / congestion counters per node.

    Returns a matplotlib figure.
    """
    if not cong_snap:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No congestion data", ha="center", va="center")
        ax.axis("off")
        return fig

    nodes = sorted(cong_snap.keys())
    ce_fwd = []
    ce_rcv = []
    cong_det = []
    kern_drops = []

    for node in nodes:
        cong = cong_snap[node].get("congestion", {})
        ce_fwd.append(cong.get("ce_forwarded", 0))
        ce_rcv.append(cong.get("ce_received", 0))
        cong_det.append(cong.get("congestion_detected", 0))
        kern_drops.append(cong.get("kernel_drop_events", 0))

    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(nodes))
    width = 0.2

    ax.bar(x - 1.5 * width, ce_fwd, width, label="CE Forwarded",
           color="#FF9800", alpha=0.85)
    ax.bar(x - 0.5 * width, ce_rcv, width, label="CE Received",
           color="#4CAF50", alpha=0.85)
    ax.bar(x + 0.5 * width, cong_det, width, label="Congestion Detected",
           color="#F44336", alpha=0.85)
    ax.bar(x + 1.5 * width, kern_drops, width, label="Kernel Drops",
           color="#9C27B0", alpha=0.85)

    ax.set_xlabel("Node", fontsize=11)
    ax.set_ylabel("Count", fontsize=11)
    ax.set_title("Congestion / ECN Counters by Node", fontsize=13,
                 fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(nodes, fontsize=9)
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    # If all zeros, annotate
    total = sum(ce_fwd + ce_rcv + cong_det + kern_drops)
    if total == 0:
        ax.text(0.5, 0.95, "No congestion events detected",
                transform=ax.transAxes, ha="center", fontsize=12,
                color="#4CAF50", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#E8F5E9",
                          edgecolor="#4CAF50"))

    plt.tight_layout()
    return fig


def plot_rekey_timeline(rekey_events, parent_events):
    """Timeline/Gantt chart of rekey events (and parent switches as context).

    Returns a matplotlib figure.
    """
    has_rekeys = len(rekey_events) > 0
    has_switches = len(parent_events) > 0

    if not has_rekeys and not has_switches:
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.text(0.5, 0.5, "No rekey or parent-switch events found",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=13, color="#666666")
        ax.set_title("Rekey Event Timeline", fontsize=13, fontweight="bold")
        ax.axis("off")
        return fig

    # Combine all events for time range
    all_events = rekey_events + parent_events
    all_events.sort(key=lambda e: e["timestamp"] or datetime.min)

    # Collect unique nodes
    nodes = sorted(set(e["node"] for e in all_events))
    node_to_y = {n: i for i, n in enumerate(nodes)}

    if has_switches:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7),
                                        gridspec_kw={"height_ratios": [2, 2]})
    else:
        fig, ax1 = plt.subplots(figsize=(14, 4))
        ax2 = None

    if has_rekeys:
        for ev in rekey_events:
            y = node_to_y.get(ev["node"], 0)
            ts = ev["timestamp"]
            if ts is None:
                continue
            ax1.scatter(ts, y, c="#E91E63", s=80, zorder=5,
                        edgecolors="#333", linewidths=0.5)
        ax1.set_yticks(range(len(nodes)))
        ax1.set_yticklabels(nodes, fontsize=9)
        ax1.set_ylabel("Node", fontsize=11)
        ax1.set_title("Rekey Events Timeline", fontsize=13, fontweight="bold")
        ax1.grid(axis="x", alpha=0.3)
        fig.autofmt_xdate(rotation=30)
    else:
        ax1.text(0.5, 0.5, "No rekey events found in node logs",
                 ha="center", va="center", transform=ax1.transAxes,
                 fontsize=13, color="#999999",
                 bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFF3E0",
                           edgecolor="#FF9800", alpha=0.5))
        ax1.set_title("Rekey Events Timeline", fontsize=13, fontweight="bold")
        ax1.axis("off")

    # Plot parent switches
    if has_switches:
        for ev in parent_events:
            y = node_to_y.get(ev["node"], 0)
            ts = ev["timestamp"]
            if ts is None:
                continue
            ax2.scatter(ts, y, c="#FF9800", marker="s", s=60, zorder=5,
                        edgecolors="#333", linewidths=0.5, alpha=0.8)
        ax2.set_yticks(range(len(nodes)))
        ax2.set_yticklabels(nodes, fontsize=9)
        ax2.set_ylabel("Node", fontsize=11)
        ax2.set_title("Parent Switch Events (topology changes)", fontsize=12,
                     fontweight="bold")
        ax2.grid(axis="x", alpha=0.3)
        ax2.set_xlabel("Time", fontsize=10)
        fig.autofmt_xdate(rotation=30)

    plt.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fips Chaos Test Report — {title}</title>
<style>
  :root {{
    --bg: #1a1a2e;
    --card: #16213e;
    --accent: #0f3460;
    --text: #e0e0e0;
    --text-dim: #8888aa;
    --link: #e94560;
    --green: #4CAF50;
    --red: #F44336;
    --orange: #FF9800;
    --border: #233;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 20px;
    max-width: 1200px;
    margin: 0 auto;
  }}
  h1 {{
    font-size: 1.8em;
    margin-bottom: 8px;
    background: linear-gradient(135deg, #e94560, #0f3460);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  h2 {{
    font-size: 1.4em;
    margin: 30px 0 12px;
    padding-bottom: 6px;
    border-bottom: 2px solid var(--accent);
    color: var(--text);
  }}
  h3 {{
    font-size: 1.1em;
    margin: 20px 0 8px;
    color: var(--text-dim);
  }}
  .header-meta {{
    color: var(--text-dim);
    font-size: 0.9em;
    margin-bottom: 20px;
  }}
  .card {{
    background: var(--card);
    border-radius: 10px;
    padding: 20px;
    margin: 16px 0;
    border: 1px solid var(--border);
  }}
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
  }}
  .stat-box {{
    background: var(--accent);
    border-radius: 8px;
    padding: 14px;
    text-align: center;
    transition: transform 0.2s;
  }}
  .stat-box:hover {{ transform: translateY(-3px); }}
  .stat-value {{
    font-size: 1.8em;
    font-weight: bold;
    display: block;
  }}
  .stat-label {{
    font-size: 0.75em;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .stat-ok {{ color: var(--green); }}
  .stat-warn {{ color: var(--orange); }}
  .stat-error {{ color: var(--red); }}
  .chart-container {{
    text-align: center;
    margin: 16px 0;
  }}
  .chart-container img {{
    max-width: 100%;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: #fff;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0;
    font-size: 0.9em;
  }}
  th, td {{
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }}
  th {{
    color: var(--text-dim);
    text-transform: uppercase;
    font-size: 0.8em;
    letter-spacing: 0.5px;
  }}
  tr:hover {{ background: rgba(255,255,255,0.03); }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.8em;
    font-weight: bold;
  }}
  .badge-ok {{ background: rgba(76,175,80,0.2); color: var(--green); }}
  .badge-warn {{ background: rgba(255,152,0,0.2); color: var(--orange); }}
  .badge-error {{ background: rgba(244,67,54,0.2); color: var(--red); }}
  .nav {{
    position: sticky;
    top: 0;
    background: var(--bg);
    padding: 10px 0;
    z-index: 100;
    border-bottom: 1px solid var(--border);
    margin-bottom: 20px;
  }}
  .nav a {{
    color: var(--text-dim);
    text-decoration: none;
    margin-right: 16px;
    font-size: 0.9em;
    transition: color 0.2s;
  }}
  .nav a:hover {{ color: var(--link); }}
  .footer {{
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 0.8em;
  }}
  @media (max-width: 600px) {{
    .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>

<h1>fips Chaos Test Report</h1>
<div class="header-meta">
  Generated {gen_time} &middot; Results dir: <code>{results_dir}</code>
</div>

<div class="nav">
  <a href="#summary">Summary</a>
  <a href="#topology">Topology</a>
  <a href="#links">Link Quality</a>
  <a href="#rekey">Rekey Timeline</a>
  <a href="#congestion">Congestion</a>
</div>

<!-- SUMMARY -->
<h2 id="summary">Summary</h2>
<div class="card">
  <div class="stats-grid">
    {stat_boxes}
  </div>
</div>

{metadata_section}

<!-- TOPOLOGY -->
<h2 id="topology">Topology Convergence</h2>
<div class="card">
  <h3>Spanning Tree: Warmup &rarr; Final</h3>
  <div class="chart-container">
    {topology_media}
  </div>
  <h3>Depth Distribution</h3>
  <div class="chart-container">
    <img src="{depth_chart}" alt="Tree depth distribution">
  </div>
</div>

<!-- LINK QUALITY -->
<h2 id="links">Link Quality</h2>
<div class="card">
  <div class="chart-container">
    <img src="{link_chart}" alt="Per-peer link quality charts">
  </div>
</div>

<!-- REKEY TIMELINE -->
<h2 id="rekey">Rekey Timeline</h2>
<div class="card">
  <div class="chart-container">
    <img src="{rekey_chart}" alt="Rekey event timeline">
  </div>
</div>

<!-- CONGESTION -->
<h2 id="congestion">Congestion</h2>
<div class="card">
  <div class="chart-container">
    <img src="{congestion_chart}" alt="Congestion counters">
  </div>
</div>

<div class="footer">
  Generated by visualize.py &middot; fips chaos test visualizer
</div>

</body>
</html>"""


def make_stat_box(value, label, cls=""):
    return f'<div class="stat-box"><span class="stat-value {cls}">{value}</span><span class="stat-label">{label}</span></div>'


def generate_report(results_dir, output_path):
    """Main entry: load all data, generate charts, write HTML report."""
    print(f"Loading data from {results_dir}...")

    # Load snapshots
    tree_warmup = load_json(os.path.join(results_dir, "tree-snapshot-warmup.json"))
    tree_final = load_json(os.path.join(results_dir, "tree-snapshot-final.json"))
    mmp_final = load_json(os.path.join(results_dir, "mmp-snapshot-final.json"))
    mmp_warmup = load_json(os.path.join(results_dir, "mmp-snapshot-warmup.json"))
    cong_final = load_json(os.path.join(results_dir, "congestion-snapshot-final.json"))
    cong_warmup = load_json(os.path.join(results_dir, "congestion-snapshot-warmup.json"))

    # Load text artifacts
    analysis = parse_analysis(os.path.join(results_dir, "analysis.txt"))
    metadata = parse_metadata(os.path.join(results_dir, "metadata.txt"))

    # Build addr map from whichever tree snapshot is available
    addr_map = build_addr_map(tree_final or tree_warmup)

    # Extract log events
    print("Scanning node logs for rekey/parent-switch events...")
    rekey_events = extract_log_events(results_dir, ["rekey", "rekeying", "key rotation"])
    parent_events = extract_log_events(results_dir, ["parent switched"])
    print(f"  Found {len(rekey_events)} rekey events, {len(parent_events)} parent switches")

    # -----------------------------------------------------------------------
    # Generate charts
    # -----------------------------------------------------------------------

    # 1. Topology GIF
    topology_media = ""
    gif_path = os.path.join(results_dir, "topology-convergence.gif")
    if tree_warmup or tree_final:
        print("Generating topology convergence GIF...")
        gif_result = make_topology_gif(tree_warmup, tree_final, addr_map, gif_path)
        if gif_result:
            gif_uri = file_to_base64(gif_result, "image/gif")
            if gif_uri:
                topology_media = f'<img src="{gif_uri}" alt="Topology convergence" style="max-width:100%;border-radius:8px;">'
            else:
                topology_media = f"<p>GIF created at {gif_result} but could not be embedded.</p>"
        else:
            # Fallback: two side-by-side PNGs
            print("  GIF failed, generating side-by-side PNGs instead...")
            fig_w = draw_topology_frame(
                tree_warmup, addr_map, "Warmup (post-convergence)"
            )
            fig_f = draw_topology_frame(
                tree_final, addr_map, "Final (post-stress)"
            )
            uri_w = fig_to_base64(fig_w)
            uri_f = fig_to_base64(fig_f)
            topology_media = (
                '<div style="display:flex;gap:10px;flex-wrap:wrap;">'
                '<div style="flex:1;min-width:300px;"><h3>Warmup</h3>'
                f'<img src="{uri_w}" style="max-width:100%;border-radius:8px;border:1px solid #233;"></div>'
                '<div style="flex:1;min-width:300px;"><h3>Final</h3>'
                f'<img src="{uri_f}" style="max-width:100%;border-radius:8px;border:1px solid #233;"></div>'
                '</div>'
            )
    else:
        topology_media = "<p>No tree snapshot data available.</p>"

    # 2. Depth distribution
    print("Generating tree depth distribution...")
    depth_fig = plot_tree_depth(tree_warmup, tree_final)
    depth_uri = fig_to_base64(depth_fig)

    # 3. Link quality
    print("Generating link quality charts...")
    link_fig = plot_link_quality(mmp_final, addr_map)
    link_uri = fig_to_base64(link_fig)

    # 4. Rekey timeline
    print("Generating rekey timeline...")
    rekey_fig = plot_rekey_timeline(rekey_events, parent_events)
    rekey_uri = fig_to_base64(rekey_fig)

    # 5. Congestion
    print("Generating congestion chart...")
    cong_fig = plot_congestion(cong_final)
    cong_uri = fig_to_base64(cong_fig)

    # -----------------------------------------------------------------------
    # Build summary stat boxes
    # -----------------------------------------------------------------------
    def get_stat(key, default="—"):
        return analysis.get(key, default)

    panics = get_stat("Panics", "0")
    errors = get_stat("Errors", "0")
    warnings = get_stat("Warnings", "0")
    sessions = get_stat("Sessions established", "0")
    parent_sw = get_stat("Parent switches", "0")
    rekey_cutover = get_stat("Rekey cutovers", "0")
    cong_events = get_stat("Congestion events", "0")
    kernel_drops = get_stat("Kernel drop events", "0")

    stat_boxes = []
    stat_boxes.append(make_stat_box(panics, "Panics",
        "stat-error" if panics != "0" else "stat-ok"))
    stat_boxes.append(make_stat_box(errors, "Errors",
        "stat-error" if errors != "0" else "stat-ok"))
    stat_boxes.append(make_stat_box(warnings, "Warnings",
        "stat-warn" if warnings != "0" else "stat-ok"))
    stat_boxes.append(make_stat_box(sessions, "Sessions"))
    stat_boxes.append(make_stat_box(parent_sw, "Parent Switches"))
    stat_boxes.append(make_stat_box(rekey_cutover, "Rekey Cutovers"))
    stat_boxes.append(make_stat_box(cong_events, "Congestion Events",
        "stat-error" if cong_events not in ("0", "—") else "stat-ok"))
    stat_boxes.append(make_stat_box(kernel_drops, "Kernel Drops",
        "stat-error" if kernel_drops not in ("0", "—") else "stat-ok"))

    # Add scenario metadata if available
    if metadata:
        for key in ("scenario", "nodes", "edges", "seed", "duration_secs"):
            if key in metadata:
                stat_boxes.append(make_stat_box(str(metadata[key]),
                                                key.replace("_", " ").title()))

    stat_boxes_html = "\n    ".join(stat_boxes)

    # Metadata section
    metadata_section = ""
    if metadata and metadata.get("adjacency"):
        adj_rows = []
        for node, info in sorted(metadata["adjacency"].items()):
            peers = ", ".join(info.get("peers", []))
            adj_rows.append(
                "<tr><td><strong>{}</strong></td><td>{}</td><td>{}</td></tr>".format(
                    node, info.get("ip", "—"), peers
                )
            )
        metadata_section = """<div class="card">
<h3>Network Adjacency</h3>
<table>
<thead><tr><th>Node</th><th>IP</th><th>Peers</th></tr></thead>
<tbody>
{}
</tbody>
</table>
</div>""".format("\n".join(adj_rows))

    # Title
    scenario = metadata.get("scenario", "unknown") if metadata else "unknown"
    title = "{} ({})".format(
        scenario,
        os.path.basename(results_dir.rstrip("/"))
    )

    # -----------------------------------------------------------------------
    # Assemble HTML
    # -----------------------------------------------------------------------
    html = HTML_TEMPLATE.format(
        title=title,
        gen_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        results_dir=results_dir,
        stat_boxes=stat_boxes_html,
        metadata_section=metadata_section,
        topology_media=topology_media,
        depth_chart=depth_uri,
        link_chart=link_uri,
        rekey_chart=rekey_uri,
        congestion_chart=cong_uri,
    )

    with open(output_path, "w") as f:
        f.write(html)

    print(f"\nReport written to: {output_path}")
    print("  Embedded: topology GIF, depth chart, link quality, rekey timeline, congestion")
    if gif_path and os.path.exists(gif_path):
        print(f"  Topology GIF also saved as: {gif_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Visualize fips chaos test results as interactive HTML report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Example:
  python3 visualize.py --results-dir ./results/poc-test-3/fips-results/20260626-214254-smoke-10/
  python3 visualize.py --results-dir ./results/.../  --output my-report.html
""",
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="Path to the results directory containing snapshots and logs.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML file path (default: <results-dir>/report.html).",
    )
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    if not os.path.isdir(results_dir):
        print(f"Error: results directory not found: {results_dir}",
              file=sys.stderr)
        sys.exit(1)

    output_path = args.output or os.path.join(results_dir, "report.html")
    output_path = os.path.abspath(output_path)

    generate_report(results_dir, output_path)


if __name__ == "__main__":
    main()
