"""Obsidian-style knowledge-graph renderer.

Reuses src.graph data builders (build_dictation_graph / concept / notes) and
emits a minimal black-canvas D3 force layout. Hovering a node highlights its
1-hop neighborhood in purple and dims the rest — the Obsidian interaction.

The output is a single self-contained HTML document, suitable for serving
directly from Flask or saving as a file.
"""
from __future__ import annotations

import json
from typing import Any

from .. import graph as _g


def _merge(dict_g: dict, concept_g: dict, notes_g: dict) -> dict[str, Any]:
    """Unify the three graphs into one. Namespaces ids to avoid collisions:
       d:<int> for dictations, c:<name> for concepts, n:<id> for notes.
       Adds bridge edges from each dictation to the concepts it contains."""
    nodes: list[dict] = []
    links: list[dict] = []

    # --- dictations ---
    for n in dict_g.get("nodes", []):
        cnt = int(n.get("count", 1))
        label = n.get("label", "")
        full = n.get("full", n.get("label", ""))
        if cnt > 1:
            # One node stands in for `cnt` identical dictations.
            label = f"{label} ×{cnt}"
            full = f"{full}  (said {cnt}×)"
        nodes.append({
            "id": f"d:{n['id']}",
            "label": label,
            "full": full,
            "group": "dictation",
            "cluster": n.get("cluster", 0),
            "count": cnt,
            "size": 7 + min(6, cnt - 1),  # bigger blob the more it repeats
        })
    for l in dict_g.get("links", []):
        links.append({
            "source": f"d:{l['source']}",
            "target": f"d:{l['target']}",
            "kind": "sim",
            "value": float(l.get("value", 0.6)),
        })

    # --- concepts ---
    for n in concept_g.get("nodes", []):
        freq = n.get("freq", n.get("count", 1))
        nodes.append({
            "id": f"c:{n['id']}",
            "label": n.get("label", n["id"]),
            "full": f"Concept appearing {freq}×",
            "group": "concept",
            "cluster": -1,
            "size": 6 + min(10, freq),
            "freq": freq,
        })
    for l in concept_g.get("links", []):
        links.append({
            "source": f"c:{l['source']}",
            "target": f"c:{l['target']}",
            "kind": "cooc",
            "value": float(l.get("value", 0.4)),
        })

    # --- notes ---
    for n in notes_g.get("nodes", []):
        if n.get("kind") == "dictation":
            # notes graph re-emits the source dictation; skip dupes when present
            continue
        # build_notes_graph emits ids already namespaced as "note:<id>"; strip
        # that prefix so the node id matches the "n:<id>" form the link loop
        # below remaps note edges to. Bare ids (no prefix) pass through.
        raw_id = str(n["id"])
        nid = raw_id[5:] if raw_id.startswith("note:") else raw_id
        nodes.append({
            "id": f"n:{nid}",
            "label": n.get("label", n["id"]),
            "full": n.get("full", ""),
            "group": "note",
            "cluster": -2,
            "size": 10,
        })
    for l in notes_g.get("links", []):
        # bridge note -> dictation; remap to the d: namespace
        src = l["source"]
        tgt = l["target"]
        if isinstance(src, str) and src.startswith("note:"):
            src = f"n:{src[5:]}"
        if isinstance(tgt, str) and tgt.startswith("dict:"):
            tgt = f"d:{tgt[5:]}"
        links.append({
            "source": src,
            "target": tgt,
            "kind": "note",
            "value": float(l.get("value", 0.7)),
        })

    return {"nodes": nodes, "links": links}


_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Graph</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin:0; padding:0; height:100%; background:#0d0e11;
               font: 11px/1.3 -apple-system, "Segoe UI", system-ui, sans-serif;
               color:#cbd5e1; overflow:hidden; }
  svg { width:100vw; height:100vh; display:block; cursor:grab; }
  svg:active { cursor:grabbing; }

  .link { stroke:#4b5260; stroke-opacity:.55; }
  .node circle { fill:#e2e8f0; stroke:#0d0e11; stroke-width:1.5px;
                 transition: r .18s ease, stroke-width .18s ease; }
  .node text  { fill:#e2e8f0; pointer-events:none;
                paint-order:stroke; stroke:#0d0e11; stroke-width:4px;
                font-size:12px; font-weight:500; letter-spacing:.01em;
                transition: opacity .15s ease; }
  /* Group accents — dictation gets a per-cluster tint via inline fill */
  .node.g-concept circle { fill:#fbbf24; }       /* amber */
  .node.g-note    circle { fill:#34d399; }       /* emerald */
  .node.g-note    text   { font-size:13px; font-weight:600; }

  /* Hide labels by default in large graphs / zoomed-out — toggled by JS */
  .labels-hidden .node text { opacity: 0; }
  .labels-hidden .node.lit text,
  .labels-hidden .node.focus text,
  .labels-hidden .node.hover text { opacity: 1; }

  /* Dim everything */
  .dimmed .link { stroke-opacity:.06; }
  .dimmed .node circle { fill-opacity:.18; }
  .dimmed .node text   { fill-opacity:.18; }

  /* Then re-highlight the focused subgraph */
  .link.lit { stroke:#a78bfa; stroke-opacity:.9; }
  .node.lit circle { fill-opacity:1; stroke:#a78bfa; stroke-width:2px; }
  .node.lit text   { fill:#ede9fe; fill-opacity:1; font-weight:600; }
  .node.focus circle { stroke:#c4b5fd; stroke-width:3px; }
  .node.hit circle { stroke:#fde68a; stroke-width:2.5px; }
  .node.hit text   { fill:#fef3c7; }

  /* Pulse animation for search hits — finite so a left-open search doesn't
     loop the compositor forever. Three pulses is enough to draw the eye. */
  @keyframes pulse { 0%,100% { stroke-opacity:1 } 50% { stroke-opacity:.35 } }
  .node.hit circle { animation: pulse 1.4s ease-in-out 3; }

  /* Floating controls (top-right) — Obsidian-minimal */
  .panel { position:fixed; top:12px; right:12px; display:flex; gap:6px;
           background:rgba(20,22,28,.85); backdrop-filter:blur(8px);
           border:1px solid #2a2e38; border-radius:8px; padding:6px;
           font-size:11px; }
  .panel button { background:transparent; color:#cbd5e1; border:0;
                  padding:4px 10px; border-radius:5px; cursor:pointer; }
  .panel button:hover { background:#2a2e38; }
  .panel button.active { background:#3b3354; color:#ddd6fe; }
  .panel input { background:#1a1d24; color:#e2e8f0; border:1px solid #2a2e38;
                 border-radius:5px; padding:4px 8px; width:140px; outline:none; }
  .panel input:focus { border-color:#a78bfa; }
  .panel .icon { width:26px; height:26px; padding:0;
                 display:inline-flex; align-items:center; justify-content:center; }

  .stats { position:fixed; left:12px; bottom:10px; color:#64748b; font-size:10.5px;
           letter-spacing:.02em; }

  /* Minimap (bottom-right) — same glass treatment as .panel */
  .minimap { position:fixed; right:12px; bottom:12px; width:180px; height:120px;
             background:rgba(20,22,28,.85); backdrop-filter:blur(8px);
             border:1px solid #2a2e38; border-radius:8px; overflow:hidden;
             cursor:crosshair; }
  .minimap svg { width:100%; height:100%; display:block; }
  .minimap .mm-dot { fill:#cbd5e1; opacity:.55; }
  .minimap .mm-view { fill:rgba(167,139,250,.14); stroke:#a78bfa;
                      stroke-width:1px; cursor:grab; }
  .minimap .mm-view:active { cursor:grabbing; }

  /* Cluster hulls — soft tinted blobs behind dictation clusters */
  .hulls path { stroke:none; pointer-events:none;
                transition: opacity .2s ease; }
  .hulls.off  { display:none; }

  /* Side detail panel (shown when a node is clicked/pinned) */
  .detail { position:fixed; right:12px; top:56px; width:300px; max-height:75vh;
            background:rgba(20,22,28,.93); backdrop-filter:blur(10px);
            border:1px solid #2a2e38; border-radius:10px; padding:14px 16px;
            font-size:12px; line-height:1.55; color:#cbd5e1;
            display:none; overflow:auto; box-shadow:0 8px 28px rgba(0,0,0,.45); }
  .detail.open { display:block; }
  .detail .pill { display:inline-block; padding:2px 8px; border-radius:999px;
                  font-size:10px; font-weight:600; letter-spacing:.04em;
                  text-transform:uppercase; margin-bottom:8px; }
  .detail .pill.dictation { background:#1e293b; color:#e2e8f0; }
  .detail .pill.concept   { background:#3b2f0c; color:#fbbf24; }
  .detail .pill.note      { background:#0f3a2c; color:#34d399; }
  .detail h3 { margin:4px 0 8px; font-size:14px; font-weight:600; color:#f1f5f9;
               word-break:break-word; }
  .detail .body { color:#94a3b8; white-space:pre-wrap; word-break:break-word; }
  .detail .meta { color:#64748b; font-size:11px; margin-top:10px; }
  .detail .close { position:absolute; top:8px; right:10px; cursor:pointer;
                   color:#64748b; font-size:16px; line-height:1; padding:2px 6px; }
  .detail .close:hover { color:#e2e8f0; }
  .detail ul.neigh { list-style:none; padding:0; margin:6px 0 0;
                     max-height:160px; overflow:auto; }
  .detail ul.neigh li { padding:3px 6px; border-radius:4px; cursor:pointer;
                        color:#cbd5e1; font-size:11px; }
  .detail ul.neigh li:hover { background:#2a2e38; color:#ede9fe; }
</style></head><body>
<svg id="g"></svg>

<div class="panel">
  <button id="m-all"   class="active">All</button>
  <button id="m-dict">Dictations</button>
  <button id="m-conc">Concepts</button>
  <button id="m-note">Notes</button>
  <button id="m-hulls" class="active" title="Toggle cluster hulls">Hulls</button>
  <input id="search" placeholder="Search…" />
  <button id="fit" class="icon" title="Fit to view">⤢</button>
</div>

<div class="minimap" id="minimap">
  <svg id="mm" viewBox="0 0 180 120" preserveAspectRatio="none">
    <g id="mm-dots"></g>
    <rect id="mm-view" class="mm-view" x="0" y="0" width="180" height="120"></rect>
  </svg>
</div>

<div class="stats" id="stats"></div>

<div class="detail" id="detail">
  <span class="close" id="detail-close">×</span>
  <span class="pill" id="detail-pill">node</span>
  <h3 id="detail-title"></h3>
  <div class="body" id="detail-body"></div>
  <div class="meta" id="detail-meta"></div>
  <div id="detail-neigh-wrap" style="display:none">
    <div class="meta" style="margin-top:12px">Neighbors</div>
    <ul class="neigh" id="detail-neigh"></ul>
  </div>
</div>

<script id="__graph_data" type="application/json">__DATA_JSON__</script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const DATA = JSON.parse(document.getElementById('__graph_data').textContent);

// Obsidian-ish per-cluster tint for dictation nodes. Stays muted so the
// canvas reads as one graph, not a stained-glass window.
const CLUSTER_COLORS = [
  '#e2e8f0', '#fca5a5', '#93c5fd', '#fcd34d', '#86efac',
  '#c4b5fd', '#f9a8d4', '#67e8f9', '#fdba74', '#a3e635'
];
function nodeFill(d) {
  if (d.group === 'concept') return '#fbbf24';
  if (d.group === 'note')    return '#34d399';
  return CLUSTER_COLORS[((d.cluster|0) % CLUSTER_COLORS.length + CLUSTER_COLORS.length) % CLUSTER_COLORS.length];
}

const svg = d3.select('#g');
const W = () => window.innerWidth, H = () => window.innerHeight;

// Zoom/pan container (zoom behavior attached lower, after fitToView is defined).
const root = svg.append('g');

const hullLayer = root.append('g').attr('class','hulls');
const linkLayer = root.append('g').attr('class','links');
const nodeLayer = root.append('g').attr('class','nodes');

// Smooth closed-curve generator for cluster hull paths.
const hullLine = d3.line().curve(d3.curveBasisClosed);
let hullsOn = true;
let hullGroups = []; // [{cluster, members:[node,...], color}]
function rebuildHullGroups() {
  const by = new Map();
  for (const n of nodes) {
    if (n.group !== 'dictation') continue;
    const c = (n.cluster|0);
    if (c < 0) continue;
    if (!by.has(c)) by.set(c, []);
    by.get(c).push(n);
  }
  hullGroups = [];
  for (const [c, members] of by) {
    if (members.length < 3) continue;
    const color = CLUSTER_COLORS[(c % CLUSTER_COLORS.length + CLUSTER_COLORS.length) % CLUSTER_COLORS.length];
    hullGroups.push({cluster: c, members, color});
  }
}
function updateHulls() {
  const sel = hullLayer.selectAll('path').data(hullGroups, d => d.cluster);
  sel.exit().remove();
  const enter = sel.enter().append('path')
    .attr('fill', d => d.color)
    .attr('fill-opacity', 0.08);
  enter.merge(sel)
    .attr('fill', d => d.color)
    .attr('d', d => {
      const pts = d.members
        .filter(m => m.x != null && m.y != null)
        .map(m => [m.x, m.y]);
      if (pts.length < 3) return null;
      const hull = d3.polygonHull(pts);
      if (!hull) return null;
      // Expand hull outward slightly so it pads the nodes.
      const cx = d3.mean(hull, p => p[0]);
      const cy = d3.mean(hull, p => p[1]);
      const pad = 28;
      const padded = hull.map(([x, y]) => {
        const dx = x - cx, dy = y - cy;
        const len = Math.hypot(dx, dy) || 1;
        return [x + (dx / len) * pad, y + (dy / len) * pad];
      });
      return hullLine(padded);
    });
}

let nodes = [], links = [], adjacency = new Map(), nodeById = new Map();
let currentMode = 'all';
let pinned = null;          // node currently pinned by click
let currentZoom = 1;        // tracked transform.k

function applyMode(mode) {
  currentMode = mode;
  const ok = n => mode === 'all' ||
                  (mode === 'dict' && n.group === 'dictation') ||
                  (mode === 'conc' && n.group === 'concept')   ||
                  (mode === 'note' && n.group === 'note');
  nodes = DATA.nodes.filter(ok);
  const ids = new Set(nodes.map(n => n.id));
  links = DATA.links
    .filter(l => ids.has(l.source.id || l.source) && ids.has(l.target.id || l.target))
    .map(l => ({source: l.source.id || l.source, target: l.target.id || l.target, kind: l.kind}));

  adjacency = new Map();
  nodeById = new Map();
  nodes.forEach(n => { adjacency.set(n.id, new Set([n.id])); nodeById.set(n.id, n); });
  links.forEach(l => {
    adjacency.get(l.source).add(l.target);
    adjacency.get(l.target).add(l.source);
  });

  // Perf: default to labels-hidden for large graphs; show on hover/zoom-in.
  document.body.classList.toggle('labels-hidden', nodes.length > 500);

  rebuildHullGroups();
  render();
  document.getElementById('stats').textContent =
    `${nodes.length} nodes · ${links.length} edges`;
}

let sim;
function render() {
  const link = linkLayer.selectAll('line').data(links, d => d.source + '|' + d.target);
  link.exit().remove();
  const linkEnter = link.enter().append('line').attr('class','link');
  const linkAll = linkEnter.merge(link)
    // Edge thickness scales with similarity / weight (0..1 → .5..2.6px)
    .attr('stroke-width', d => 0.5 + 2.1 * Math.max(0, Math.min(1, d.value || 0.5)))
    .attr('stroke-opacity', d => 0.35 + 0.4 * Math.max(0, Math.min(1, d.value || 0.5)));

  const node = nodeLayer.selectAll('g.node').data(nodes, d => d.id);
  node.exit().remove();
  const nodeEnter = node.enter().append('g').attr('class','node');
  nodeEnter.append('circle').attr('r', d => d.size || 8);
  nodeEnter.append('text').attr('dy', d => (d.size || 8) + 14).attr('text-anchor','middle')
           .text(d => d.label || d.id);
  const nodeAll = nodeEnter.merge(node);
  nodeAll.attr('class', d => 'node g-' + d.group)
         .select('circle').attr('fill', nodeFill);

  nodeAll.on('mouseenter', (e,d) => { if (!pinned) focus(d, false); })
         .on('mouseleave', () => { if (!pinned) unfocus(); })
         .on('click', (e,d) => { e.stopPropagation(); pin(d); })
         .call(d3.drag()
            .on('start', (e,d) => { if (!e.active) sim.alphaTarget(.25).restart();
                                    d.fx = d.x; d.fy = d.y; })
            .on('drag',  (e,d) => { d.fx = e.x; d.fy = e.y; })
            .on('end',   (e,d) => { if (!e.active) sim.alphaTarget(0);
                                    d.fx = null; d.fy = null; }));

  // Approximate label width so collide separates labels too, not just circles.
  const labelW = d => Math.min(180, 7 * ((d.label || d.id).length));

  // Cheap per-tick work: just push node/link positions to the DOM.
  function applyPositions() {
    linkAll.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
           .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeAll.attr('transform', d => `translate(${d.x},${d.y})`);
  }

  // Hull + minimap recompute is the expensive part. Throttle it to every few
  // ticks (a settling layout doesn't need them at 60fps), then do one final
  // exact pass on 'end'. prefersReduced skips the animated settle entirely.
  const prefersReduced =
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let _tickN = 0;

  sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(110).strength(.35))
    .force('charge', d3.forceManyBody().strength(-650).distanceMax(900))
    .force('center', d3.forceCenter(W()/2, H()/2))
    .force('x', d3.forceX(W()/2).strength(.03))
    .force('y', d3.forceY(H()/2).strength(.03))
    .force('collide', d3.forceCollide()
            .radius(d => Math.max((d.size || 8) + 14, labelW(d) / 2 + 6))
            .strength(.9))
    .alpha(1).alphaDecay(.04)          // settle faster → less total CPU
    .on('tick', () => {
      applyPositions();
      if (_tickN++ % 3 === 0) { updateHulls(); updateMinimap(); }
    })
    .on('end', () => { updateHulls(); updateMinimap(); fitToView(); });

  // Reduced motion → run the layout to completion synchronously, paint once,
  // stop. No animated settle, no per-frame CPU at all.
  if (prefersReduced) {
    sim.stop();
    for (let i = 0; i < 220; i++) sim.tick();
    applyPositions();
    updateHulls();
    updateMinimap();
    fitToView();
  }

  // Pause the simulation timer when the page is backgrounded; resume only if
  // it hadn't finished settling. Keeps CPU at ~0 behind other windows.
  // Registered once (render() may run again on data reload).
  if (!window._efGraphVis) {
    window._efGraphVis = true;
    document.addEventListener('visibilitychange', () => {
      if (!sim) return;
      if (document.hidden) sim.stop();
      else if (!prefersReduced && sim.alpha() > sim.alphaMin()) sim.restart();
    });
  }
}

// Compute bbox of all nodes and transform the zoom so they fit with padding.
const zoomBehavior = d3.zoom().scaleExtent([0.15, 6])
  .on('zoom', e => {
    root.attr('transform', e.transform);
    currentZoom = e.transform.k;
    // Obsidian-style: labels appear when you zoom in close.
    // Auto-hide threshold scales with graph size.
    const threshold = nodes.length > 500 ? 1.4 : (nodes.length > 200 ? 0.9 : 0.55);
    document.body.classList.toggle('labels-hidden', currentZoom < threshold);
    updateMinimap();
  });
svg.call(zoomBehavior);
// Click on empty canvas → unpin
svg.on('click', () => { if (pinned) unpin(); });

function fitToView(padding = 80) {
  if (!nodes.length) return;
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const w = x1 - x0 || 1, h = y1 - y0 || 1;
  const scale = Math.min(1.2, 0.95 / Math.max(w / (W() - padding * 2),
                                              h / (H() - padding * 2)));
  const tx = (W() - scale * (x0 + x1)) / 2;
  const ty = (H() - scale * (y0 + y1)) / 2;
  svg.transition().duration(500).call(
    zoomBehavior.transform,
    d3.zoomIdentity.translate(tx, ty).scale(scale));
}

function focus(d) {
  const neigh = adjacency.get(d.id) || new Set([d.id]);
  svg.classed('dimmed', true);
  nodeLayer.selectAll('g.node')
    .classed('lit',   n => neigh.has(n.id))
    .classed('focus', n => n.id === d.id);
  linkLayer.selectAll('line')
    .classed('lit', l => (l.source.id === d.id || l.target.id === d.id));
}
function unfocus() {
  svg.classed('dimmed', false);
  nodeLayer.selectAll('g.node').classed('lit', false).classed('focus', false);
  linkLayer.selectAll('line').classed('lit', false);
}

// --- pin / detail panel ------------------------------------------------
const detail = document.getElementById('detail');
function pin(d) {
  pinned = d;
  focus(d);
  showDetail(d);
}
function unpin() {
  pinned = null;
  unfocus();
  detail.classList.remove('open');
}
document.getElementById('detail-close').onclick = (e) => { e.stopPropagation(); unpin(); };
detail.onclick = (e) => e.stopPropagation();

function showDetail(d) {
  const pill = document.getElementById('detail-pill');
  pill.className = 'pill ' + d.group;
  pill.textContent = d.group;
  document.getElementById('detail-title').textContent = d.label || d.id;
  document.getElementById('detail-body').textContent = d.full || '';
  const metaParts = [];
  if (d.group === 'concept' && d.freq) metaParts.push(d.freq + ' occurrences');
  if (d.group === 'dictation') metaParts.push('cluster #' + d.cluster);
  document.getElementById('detail-meta').textContent = metaParts.join(' · ');

  // Neighbor list (clickable to re-pin)
  const neigh = adjacency.get(d.id) || new Set();
  const others = [...neigh].filter(id => id !== d.id).slice(0, 30);
  const wrap = document.getElementById('detail-neigh-wrap');
  const ul = document.getElementById('detail-neigh');
  ul.innerHTML = '';
  if (others.length) {
    wrap.style.display = 'block';
    others.forEach(id => {
      const n = nodeById.get(id); if (!n) return;
      const li = document.createElement('li');
      li.textContent = n.label || n.id;
      li.onclick = (e) => { e.stopPropagation(); pin(n); panTo(n); };
      ul.appendChild(li);
    });
  } else {
    wrap.style.display = 'none';
  }
  detail.classList.add('open');
}

function panTo(n, scale) {
  if (n.x == null || n.y == null) return;
  const k = scale != null ? scale : Math.max(currentZoom, 1.2);
  const tx = W()/2 - k * n.x;
  const ty = H()/2 - k * n.y;
  svg.transition().duration(450).call(
    zoomBehavior.transform,
    d3.zoomIdentity.translate(tx, ty).scale(k));
}

// Mode buttons
const MODE_BTNS = [['m-all','all'],['m-dict','dict'],['m-conc','conc'],['m-note','note']];
for (const [id, m] of MODE_BTNS) {
  document.getElementById(id).onclick = () => {
    MODE_BTNS.forEach(([bid]) => document.getElementById(bid).classList.remove('active'));
    document.getElementById(id).classList.add('active');
    applyMode(m);
  };
}

// Search → animated highlight; Enter pans to first hit.
let lastHits = [];
const searchEl = document.getElementById('search');
function runSearch() {
  const q = searchEl.value.trim().toLowerCase();
  nodeLayer.selectAll('g.node').classed('hit', false);
  if (!q) { if (!pinned) unfocus(); lastHits = []; return; }
  const hits = nodes.filter(n => (n.label||'').toLowerCase().includes(q) ||
                                 (n.full||'').toLowerCase().includes(q) ||
                                 n.id.toLowerCase().includes(q));
  lastHits = hits;
  const lit = new Set();
  hits.forEach(h => (adjacency.get(h.id) || new Set([h.id])).forEach(x => lit.add(x)));
  svg.classed('dimmed', true);
  const hitIds = new Set(hits.map(h => h.id));
  nodeLayer.selectAll('g.node')
    .classed('lit',   n => lit.has(n.id))
    .classed('hit',   n => hitIds.has(n.id))
    .classed('focus', false);
  linkLayer.selectAll('line')
    .classed('lit', l => lit.has(l.source.id) && lit.has(l.target.id));
}
searchEl.oninput = runSearch;
searchEl.onkeydown = (e) => {
  if (e.key === 'Enter' && lastHits.length) {
    e.preventDefault();
    panTo(lastHits[0], Math.max(currentZoom, 1.5));
    pin(lastHits[0]);
  } else if (e.key === 'Escape') {
    searchEl.value = '';
    runSearch();
    if (pinned) unpin();
  }
};

// Fit-to-view button → recompute bbox.
document.getElementById('fit').onclick = () => fitToView();

window.addEventListener('resize', () => {
  if (sim) sim.force('center', d3.forceCenter(W()/2, H()/2)).alpha(.3).restart();
});

// --- Hulls toggle ------------------------------------------------------
const hullsBtn = document.getElementById('m-hulls');
hullsBtn.onclick = (e) => {
  e.stopPropagation();
  hullsOn = !hullsOn;
  hullsBtn.classList.toggle('active', hullsOn);
  hullLayer.classed('off', !hullsOn);
};

// --- Minimap -----------------------------------------------------------
const MM_W = 180, MM_H = 120, MM_PAD = 6;
const mmSvg = d3.select('#mm');
const mmDots = d3.select('#mm-dots');
const mmView = d3.select('#mm-view');
let mmTransform = {sx: 1, sy: 1, tx: 0, ty: 0}; // graph→minimap mapping

function computeMinimapTransform() {
  if (!nodes.length) return;
  const xs = nodes.map(n => n.x).filter(v => v != null);
  const ys = nodes.map(n => n.y).filter(v => v != null);
  if (!xs.length) return;
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const w = (x1 - x0) || 1, h = (y1 - y0) || 1;
  const s = Math.min((MM_W - MM_PAD * 2) / w, (MM_H - MM_PAD * 2) / h);
  const tx = MM_PAD + (MM_W - MM_PAD * 2 - w * s) / 2 - x0 * s;
  const ty = MM_PAD + (MM_H - MM_PAD * 2 - h * s) / 2 - y0 * s;
  mmTransform = {sx: s, sy: s, tx, ty};
}

function updateMinimap() {
  computeMinimapTransform();
  const {sx, sy, tx, ty} = mmTransform;
  const dots = mmDots.selectAll('circle').data(nodes, d => d.id);
  dots.exit().remove();
  const enter = dots.enter().append('circle')
    .attr('class', 'mm-dot')
    .attr('r', 1.5);
  enter.merge(dots)
    .attr('cx', d => (d.x || 0) * sx + tx)
    .attr('cy', d => (d.y || 0) * sy + ty);

  // Viewport rect: invert the current zoom transform to find which graph-space
  // box is visible, then project that box into minimap space.
  const t = d3.zoomTransform(svg.node());
  const gx0 = (-t.x) / t.k;
  const gy0 = (-t.y) / t.k;
  const gx1 = gx0 + W() / t.k;
  const gy1 = gy0 + H() / t.k;
  const vx = gx0 * sx + tx;
  const vy = gy0 * sy + ty;
  const vw = (gx1 - gx0) * sx;
  const vh = (gy1 - gy0) * sy;
  mmView.attr('x', vx).attr('y', vy).attr('width', vw).attr('height', vh);
}

// Convert a minimap click point (in minimap viewBox coords) into a graph
// coordinate, then center the main view on it without changing zoom.
function panToMinimapPoint(mx, my) {
  const {sx, sy, tx, ty} = mmTransform;
  if (!sx) return;
  const gx = (mx - tx) / sx;
  const gy = (my - ty) / sy;
  const t = d3.zoomTransform(svg.node());
  const k = t.k;
  const newTx = W() / 2 - k * gx;
  const newTy = H() / 2 - k * gy;
  svg.transition().duration(180).call(
    zoomBehavior.transform,
    d3.zoomIdentity.translate(newTx, newTy).scale(k));
}

// Map a pointer event on the minimap into its viewBox coords.
function mmPointer(event) {
  const rect = document.getElementById('minimap').getBoundingClientRect();
  const mx = ((event.clientX - rect.left) / rect.width) * MM_W;
  const my = ((event.clientY - rect.top) / rect.height) * MM_H;
  return [mx, my];
}

// Click anywhere on the minimap → pan
mmSvg.on('click', (e) => {
  e.stopPropagation();
  const [mx, my] = mmPointer(e);
  panToMinimapPoint(mx, my);
});

// Drag the viewport rect → pan continuously
mmView.call(d3.drag()
  .on('start', (e) => { e.sourceEvent.stopPropagation(); })
  .on('drag',  (e) => {
    const [mx, my] = mmPointer(e.sourceEvent);
    panToMinimapPoint(mx, my);
  }));

applyMode('all');
</script>
</body></html>
"""


def render(db_path: str) -> str:
    """Return the full HTML document as a string."""
    rows = _g._load_rows(db_path)
    merged = _merge(
        _g.build_dictation_graph(rows),
        _g.build_concept_graph(rows),
        _g.build_notes_graph(db_path, rows),
    )
    # Inject as JSON inside a <script type=application/json> tag rather than
    # interpolating into JS, so any label containing </script> or quote-y
    # text can't escape into executable context. Belt-and-suspenders:
    # escape `</` so a malicious `</script>` literal can't close the block.
    payload = json.dumps(merged, ensure_ascii=False).replace("</", "<\\/")
    return _HTML.replace("__DATA_JSON__", payload)
