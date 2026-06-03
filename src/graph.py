"""Knowledge graph visualization of dictation history.

Two modes (toggle in the rendered HTML):
  - Dictations: each dictation is a node; edges = cosine similarity of embeddings.
                Clusters via k-means on the 384-dim vectors.
  - Concepts:   extracted noun phrases / proper nouns / acronyms as nodes;
                edges = co-occurrence in the same dictation.

Plus a time slider that filters by timestamp so you can see the graph grow
as your history accumulates — the "training process" visualization.

Mirrors the self-contained-HTML pattern used by viewer.py — one file, embedded
JSON, opens in the default browser. No server, no external dependencies at
runtime (D3.js loaded from CDN by the browser).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sqlite3
import webbrowser
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np

from .cleanup import collapse_repeats
from .retrieval import from_blob


def _dedup_key(text: str) -> str:
    """Normalized identity for collapsing duplicate dictations into one node.

    Same phrase, same node — regardless of repeat-stutter, trailing
    punctuation, or casing. Mirrors the cleanup collapse so "Open Browser Open
    Browser" and "Open Browser" map to the same key.
    """
    return re.sub(r"\s+", " ", collapse_repeats(text or "").strip().lower().rstrip(".!?;:, ")).strip()


# ---------------------------------------------------------------------------
# Concept extraction (copied from learn.py — kept independent so this module
# doesn't depend on Learner state)
# ---------------------------------------------------------------------------

_CONCEPT_RE = re.compile(
    r"\b([A-Z][a-z]{2,}|[A-Z]{2,}|[a-z]+_[a-z_]+|[a-z]+[A-Z][a-zA-Z]+)\b"
)
_STOP = {
    "The", "This", "That", "These", "Those", "There", "Then", "They",
    "Their", "When", "Where", "What", "Which", "While", "Who", "Why",
    "How", "And", "But", "Also", "From", "With", "Have", "Has", "Had",
    "Will", "Would", "Could", "Should", "Just", "Like", "About", "Into",
    "Over", "Under", "After", "Before", "Today", "Tomorrow", "Yesterday",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
    "Hello", "Hi", "Yes", "No", "Maybe",
    # Dictation filler / generic words that show up as false concepts.
    "Yeah", "Okay", "Stuff", "Thing", "Things", "Really", "Actually",
    "Basically", "Literally", "Honestly", "Anyway", "Whatever", "Something",
    "Anything", "Nothing", "Everything", "Someone", "Everyone", "Anyone",
}


def _extract_concepts(text: str) -> list[str]:
    if not text:
        return []
    out = []
    for m in _CONCEPT_RE.findall(text):
        if m in _STOP or len(m) < 3:
            continue
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_rows(db_path: str, limit: int = 1500, min_quality: float = 30.0) -> list[dict]:
    """Load recent dictations with embeddings parsed to numpy arrays.

    Rows with quality_score < min_quality are dropped (almost certainly silence
    hallucinations or transcription failures — they pollute the semantic map).
    Rows with quality_score IS NULL (legacy, pre-grading) default to 70 so
    they stay visible.
    """
    if not Path(db_path).exists():
        return []
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                "SELECT id, ts, style, raw_text, cleaned_text, embedding, quality_score "
                "FROM dictations WHERE raw_text IS NOT NULL AND raw_text != '' "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:
        return []
    out = []
    for rid, ts, style, raw, cleaned, blob, q in rows:
        # Legacy rows without grading data → neutral default.
        quality = float(q) if q is not None else 70.0
        if quality < min_quality:
            continue
        vec = None
        if blob:
            try:
                vec = from_blob(blob)
            except Exception:
                vec = None
        out.append({
            "id": rid, "ts": ts, "style": style or "default",
            "raw": raw or "", "cleaned": cleaned or raw or "",
            "vec": vec, "quality": quality,
        })
    # Sort ascending by ts so node insertion order matches time progression
    out.sort(key=lambda r: r["ts"])
    return out


# ---------------------------------------------------------------------------
# Dictation graph (semantic similarity)
# ---------------------------------------------------------------------------

def build_dictation_graph(
    rows: list[dict],
    min_similarity: float = 0.55,
    max_edges_per_node: int = 6,
) -> dict[str, Any]:
    """Each dictation → node. Edges = top-K most similar above threshold.

    Identical dictations (same normalized text) collapse into ONE node carrying
    a `count`, so saying the same thing 3× shows a single "… ×3" node instead of
    three overlapping ones. The earliest occurrence is kept as representative.
    """
    # Collapse exact-duplicate dictations before any matrix/cluster/edge work.
    # rows arrive ts-ascending (see _load_rows), so the first per group is the
    # earliest occurrence.
    grouped: "dict[str, dict]" = {}
    for r in rows:
        key = _dedup_key(r.get("cleaned") or r.get("raw") or "")
        if not key:
            key = f"__empty__{id(r)}"  # never merge truly-empty rows together
        if key in grouped:
            grouped[key]["count"] += 1
        else:
            rep = dict(r)
            rep["count"] = 1
            grouped[key] = rep
    rows = list(grouped.values())

    rows_with_vec = [r for r in rows if r["vec"] is not None]
    n = len(rows_with_vec)
    if n == 0:
        return {"nodes": [], "links": []}

    # Stack into matrix for vectorized similarity
    mat = np.vstack([r["vec"] for r in rows_with_vec]).astype(np.float32)
    # Already L2-normalized, so M @ M.T = pairwise cosine
    sim = mat @ mat.T
    np.fill_diagonal(sim, -1.0)   # exclude self

    # Cluster (k-means)
    clusters = [0] * n
    unique_count = len({tuple(v.round(4)) for v in mat})
    k = max(1, min(8, n // 6, unique_count))
    if k >= 2 and n >= k:
        try:
            from sklearn.cluster import KMeans
            km = KMeans(n_clusters=k, n_init=4, random_state=42)
            clusters = km.fit_predict(mat).tolist()
        except Exception as e:
            import logging
            logging.getLogger("wispr.graph").warning("k-means failed: %s", e)

    # Build top-K edges per node, deduplicated, above threshold
    seen_edges = set()
    links = []
    for i in range(n):
        # argsort descending; take top K candidates
        idxs = np.argsort(-sim[i])[: max_edges_per_node * 2]
        added = 0
        for j in idxs:
            j = int(j)
            if j == i or added >= max_edges_per_node:
                continue
            s = float(sim[i, j])
            if s < min_similarity:
                break
            key = (min(i, j), max(i, j))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            links.append({
                "source": rows_with_vec[key[0]]["id"],
                "target": rows_with_vec[key[1]]["id"],
                "value": round(s, 3),
            })
            added += 1

    nodes = []
    for r, cl in zip(rows_with_vec, clusters):
        label = (r["cleaned"] or r["raw"]).strip()
        if len(label) > 40:
            label = label[:40].rstrip() + "…"
        nodes.append({
            "id": r["id"],
            "label": label,
            "full": r["cleaned"] or r["raw"],
            "raw": r["raw"],
            "ts": r["ts"],
            "style": r["style"],
            "quality": round(r.get("quality", 70.0), 1),
            "cluster": int(cl),
            "count": int(r.get("count", 1)),
        })

    # Auto-label clusters with their most-distinctive concepts.
    cluster_labels = _label_clusters(rows_with_vec, clusters)
    return {
        "nodes": nodes,
        "links": links,
        "kind": "dictation",
        "cluster_labels": cluster_labels,
    }


def _label_clusters(rows: list[dict], clusters: list[int]) -> dict[int, str]:
    """For each cluster, find the top 2 concepts that appear more there than elsewhere.

    Falls back to "" for clusters with fewer than 3 members (not enough signal).
    """
    if not rows or not clusters:
        return {}
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for r, c in zip(rows, clusters):
        text = (r.get("cleaned") or r.get("raw") or "")
        if not text:
            continue
        by_cluster[int(c)].extend(_extract_concepts(text))

    # Try TF-IDF first when we have enough data, fall back to raw frequency.
    if len(rows) >= 30:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            # One "document" per cluster. Concepts are already discrete tokens, so
            # unigrams only — bigrams here just produce noise like "Thank Thank".
            docs = {c: " ".join(toks) for c, toks in by_cluster.items()}
            if len(docs) >= 2:
                cluster_ids = list(docs.keys())
                corpus = [docs[c] for c in cluster_ids]
                vect = TfidfVectorizer(
                    lowercase=False, ngram_range=(1, 1), min_df=1, max_df=0.85,
                )
                X = vect.fit_transform(corpus)
                vocab = vect.get_feature_names_out()
                labels: dict[int, str] = {}
                for i, cid in enumerate(cluster_ids):
                    if len(by_cluster[cid]) < 3:
                        labels[cid] = ""
                        continue
                    row = X[i].toarray().ravel()
                    # Top distinctive terms; take top 2 unique.
                    order = row.argsort()[::-1]
                    picked: list[str] = []
                    for k in order:
                        if row[k] <= 0:
                            break
                        term = vocab[k]
                        if term not in picked:
                            picked.append(term)
                        if len(picked) >= 2:
                            break
                    labels[cid] = " · ".join(picked) if picked else ""
                return labels
        except Exception as e:
            import logging
            logging.getLogger("wispr.graph").warning("TF-IDF cluster labelling failed, falling back: %s", e)

    # Fallback: per-cluster top-2 frequencies, with a "uniqueness" boost.
    cluster_counts: dict[int, Counter[str]] = {c: Counter(toks) for c, toks in by_cluster.items()}
    global_freq: Counter[str] = Counter()
    for cc in cluster_counts.values():
        global_freq.update(cc)
    labels: dict[int, str] = {}
    for cid, counts in cluster_counts.items():
        if sum(counts.values()) < 3:
            labels[cid] = ""
            continue
        scored = [(w, counts[w] / (1 + global_freq[w] - counts[w])) for w in counts]
        scored.sort(key=lambda x: x[1], reverse=True)
        labels[cid] = " · ".join(w for w, _ in scored[:2])
    return labels


# ---------------------------------------------------------------------------
# Concept graph (co-occurrence)
# ---------------------------------------------------------------------------

def build_concept_graph(
    rows: list[dict],
    min_freq: int = 2,
    max_edges: int = 600,
) -> dict[str, Any]:
    """Concepts (proper nouns / acronyms / CamelCase) → nodes.
    Two concepts share an edge if they appear in the same dictation."""
    concept_freq: Counter[str] = Counter()
    concept_ts: dict[str, float] = {}   # first-seen timestamp per concept
    per_dictation: list[tuple[float, set[str]]] = []
    for r in rows:
        text = r["cleaned"] or r["raw"]
        cs = set(_extract_concepts(text))
        if not cs:
            continue
        per_dictation.append((r["ts"], cs))
        for c in cs:
            concept_freq[c] += 1
            if c not in concept_ts or r["ts"] < concept_ts[c]:
                concept_ts[c] = r["ts"]

    # Keep only concepts appearing >= min_freq
    kept = {c for c, n in concept_freq.items() if n >= min_freq}
    if not kept:
        return {"nodes": [], "links": [], "kind": "concept"}

    # Co-occurrence edges
    cooccur: Counter[tuple[str, str]] = Counter()
    pair_ts: dict[tuple[str, str], float] = {}
    for ts, cs in per_dictation:
        present = sorted(c for c in cs if c in kept)
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                pair = (present[i], present[j])
                cooccur[pair] += 1
                if pair not in pair_ts or ts < pair_ts[pair]:
                    pair_ts[pair] = ts

    # Take top edges, normalize weights
    top_edges = cooccur.most_common(max_edges)
    if not top_edges:
        return {"nodes": [], "links": [], "kind": "concept"}
    max_w = max(w for _, w in top_edges)
    links = [
        {
            "source": pair[0],
            "target": pair[1],
            "value": round(w / max_w, 3),
            "count": w,
            "ts": pair_ts[pair],
        }
        for pair, w in top_edges
    ]

    # Restrict node set to those that actually appear in retained edges
    used = {pair[0] for pair, _ in top_edges} | {pair[1] for pair, _ in top_edges}
    nodes = []
    max_freq = max(concept_freq[c] for c in used)
    for c in used:
        nodes.append({
            "id": c,
            "label": c,
            "freq": concept_freq[c],
            "size": round(6 + 14 * (concept_freq[c] / max_freq), 2),
            "ts": concept_ts[c],
        })
    return {"nodes": nodes, "links": links, "kind": "concept"}


# ---------------------------------------------------------------------------
# Notes graph (notes as primary, their dictations as secondary)
# ---------------------------------------------------------------------------

def build_notes_graph(db_path: str, rows: list[dict]) -> dict[str, Any]:
    """Build the Notes view: each Note is a big primary node, its source
    dictation is connected as a small secondary node, and backlinked
    dictations also connect (faint edges).
    """
    if not Path(db_path).exists():
        return {"nodes": [], "links": [], "kind": "notes"}
    tags_by_dict: dict[int, list[str]] = {}
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            notes = conn.execute(
                "SELECT id, dictation_id, title, description, created_at, updated_at "
                "FROM notes ORDER BY updated_at DESC"
            ).fetchall()
            if not notes:
                return {"nodes": [], "links": [], "kind": "notes"}
            # Pull all confirmed tags in one pass (avoids a per-note query and a
            # leaked connection) and index by dictation id.
            try:
                for d_id, name in conn.execute(
                    "SELECT dt.dictation_id, t.name FROM dictation_tags dt "
                    "JOIN tags t ON t.id = dt.tag_id WHERE dt.confirmed = 1"
                ).fetchall():
                    tags_by_dict.setdefault(d_id, []).append(name)
            except Exception as e:
                import logging
                logging.getLogger("wispr.graph").warning("note tag lookup failed: %s", e)
    except Exception:
        return {"nodes": [], "links": [], "kind": "notes"}

    # Build lookup of dictations by id from `rows`
    dict_by_id = {r["id"]: r for r in rows}

    nodes: list[dict] = []
    links: list[dict] = []
    note_ids: set[int] = set()
    linked_dict_ids: set[int] = set()

    for n_id, d_id, title, desc, c_at, u_at in notes:
        note_ids.add(int(n_id))
        # Tags for the note's source dictation (pre-loaded above).
        tag_names = tags_by_dict.get(d_id, [])
        nodes.append({
            "id": f"note:{n_id}",
            "kind": "note",
            "label": title,
            "full": desc or "",
            "ts": u_at,
            "tags": tag_names,
            "dictation_id": d_id,
        })
        # Connect note to its source dictation
        if d_id is not None and d_id in dict_by_id:
            linked_dict_ids.add(d_id)
            links.append({
                "source": f"note:{n_id}",
                "target": f"dict:{d_id}",
                "value": 0.9,
                "ts": u_at,
            })

    # Add the linked dictation nodes (small, dim)
    for d_id in linked_dict_ids:
        r = dict_by_id.get(d_id)
        if not r:
            continue
        label = (r.get("cleaned") or r.get("raw") or "").strip()
        if len(label) > 40:
            label = label[:40].rstrip() + "…"
        nodes.append({
            "id": f"dict:{d_id}",
            "kind": "dictation",
            "label": label,
            "full": r.get("cleaned") or r.get("raw") or "",
            "raw": r.get("raw") or "",
            "ts": r.get("ts"),
            "style": r.get("style") or "default",
            "quality": round(r.get("quality", 70.0), 1),
        })

    return {"nodes": nodes, "links": links, "kind": "notes"}


def _all_confirmed_tags(db_path: str) -> list[tuple[str, int]]:
    """Returns [(tag_name, usage_count), ...] for all confirmed tags."""
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                "SELECT t.name, COUNT(*) AS n FROM tags t "
                "JOIN dictation_tags dt ON dt.tag_id = t.id "
                "WHERE dt.confirmed = 1 GROUP BY t.name ORDER BY n DESC"
            ).fetchall()
        return [(r[0], int(r[1])) for r in rows]
    except Exception:
        return []


def _tags_index(db_path: str) -> dict[int, list[str]]:
    """Returns {dictation_id: [confirmed_tag_names...]}"""
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                "SELECT dt.dictation_id, t.name FROM dictation_tags dt "
                "JOIN tags t ON t.id = dt.tag_id WHERE dt.confirmed = 1"
            ).fetchall()
    except Exception:
        return {}
    out: dict[int, list[str]] = {}
    for did, name in rows:
        out.setdefault(int(did), []).append(name)
    return out


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Echo Flow — Knowledge Graph</title>
<style>
  :root {
    --bg: #0b0e14;
    --panel: #161a23;
    --border: #262c3a;
    --muted: #7a8294;
    --accent: #58c77a;
    --text: #e7e9ee;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: ui-sans-serif, system-ui, sans-serif; overflow: hidden; }
  header { padding: 12px 18px; border-bottom: 1px solid var(--border);
           display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
           background: var(--panel); position: relative; z-index: 5; }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  .stat { color: var(--muted); font-size: 12px; }
  .mode-btn { background: #1f2533; color: var(--text); border: 1px solid var(--border);
              padding: 6px 12px; border-radius: 999px; cursor: pointer; font-size: 12px; }
  .mode-btn.active { background: var(--accent); color: #0b0e14; border-color: var(--accent); }
  #time-controls { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 250px; }
  #time-slider { flex: 1; }
  #time-label { color: var(--muted); font-size: 12px; min-width: 160px; text-align: right; }
  #qual-controls { display: flex; align-items: center; gap: 6px; }
  #qual-slider { width: 100px; }
  #qual-label { min-width: 24px; text-align: right; }
  #search-box { background: #1f2533; color: var(--text); border: 1px solid var(--border);
                padding: 6px 10px; border-radius: 999px; font-size: 12px; width: 160px;
                outline: none; }
  #search-box:focus { border-color: var(--accent); }
  .cluster-label { font-size: 12px; font-weight: 600; fill: #e7e9ee;
                   paint-order: stroke; stroke: #0b0e14; stroke-width: 4;
                   pointer-events: none; opacity: 0.85; }
  .node-dim { opacity: 0.15; }
  .node-hi { stroke: #fff !important; stroke-width: 3 !important; }
  #tag-cloud { padding: 6px 18px; background: #11141c; border-bottom: 1px solid var(--border);
               display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-height: 32px; }
  .tag-chip { background: #232a38; color: #c8d0e0; padding: 3px 10px;
              border-radius: 999px; font-size: 11px; cursor: pointer;
              border: 1px solid transparent; user-select: none; }
  .tag-chip:hover { border-color: var(--accent); }
  .tag-chip.active { background: var(--accent); color: #0b0e14; }
  .note-label { font-size: 13px; font-weight: 700; fill: #f4f6fb;
                paint-order: stroke; stroke: #0b0e14; stroke-width: 4; }
  #graph { width: 100vw; height: calc(100vh - 56px); display: block; }
  .panel { position: fixed; right: 16px; top: 72px; width: 320px;
           background: var(--panel); border: 1px solid var(--border);
           border-radius: 10px; padding: 14px; font-size: 13px; line-height: 1.5;
           display: none; max-height: 70vh; overflow-y: auto; }
  .panel h3 { margin: 0 0 8px; font-size: 14px; }
  .panel .label { color: var(--muted); font-size: 11px; text-transform: uppercase;
                  letter-spacing: 0.5px; margin-top: 8px; }
  .panel pre { white-space: pre-wrap; word-break: break-word; margin: 4px 0;
               font-family: inherit; }
  .panel .close { position: absolute; top: 8px; right: 10px; cursor: pointer;
                  color: var(--muted); }
  text { font-size: 10px; fill: var(--text); pointer-events: none;
         text-shadow: 0 0 3px var(--bg), 0 0 3px var(--bg); }
  .node { cursor: pointer; }
  .nores { padding: 60px; text-align: center; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>🕸 Knowledge Graph</h1>
  <button class="mode-btn" id="btn-notes">📌 Notes</button>
  <button class="mode-btn active" id="btn-dict">Dictations</button>
  <button class="mode-btn" id="btn-concept">Concepts</button>
  <span class="stat" id="stats"></span>
  <div id="time-controls">
    <input type="range" id="time-slider" min="0" max="100" value="100" title="Filter by time">
    <span id="time-label"></span>
  </div>
  <div id="qual-controls" title="Minimum quality score (dictation mode only)">
    <span class="stat">Q≥</span>
    <input type="range" id="qual-slider" min="0" max="100" value="0" step="5">
    <span class="stat" id="qual-label">0</span>
  </div>
  <input type="text" id="search-box" placeholder="🔍 search…" />
  <button class="mode-btn" id="btn-refresh" title="Reload from disk">↻</button>
</header>
<div id="tag-cloud"></div>
<svg id="graph"></svg>
<div class="panel" id="panel">
  <span class="close" id="panel-close">×</span>
  <h3 id="panel-title"></h3>
  <div id="panel-body"></div>
</div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const DICT = __DICT_DATA__;
const CONCEPT = __CONCEPT_DATA__;
const NOTES = __NOTES_DATA__;
const TAG_USAGE = __TAG_USAGE__;       // [{name, count}, ...]
const TAGS_INDEX = __TAGS_INDEX__;     // {dictation_id: [tag, ...]}
const TS_MIN = __TS_MIN__, TS_MAX = __TS_MAX__;
let activeTags = new Set();           // empty = no filter

const svg = d3.select("#graph");
const panel = document.getElementById("panel");
const slider = document.getElementById("time-slider");
const qualSlider = document.getElementById("qual-slider");
const qualLabel = document.getElementById("qual-label");
const searchBox = document.getElementById("search-box");
const timeLabel = document.getElementById("time-label");
const stats = document.getElementById("stats");
let mode = "dict";
let simulation = null;
let currentData = null;

function formatTs(t) {
  if (!t) return "—";
  const d = new Date(t * 1000);
  return d.toLocaleString();
}

function colorFor(d) {
  if (mode === "notes") {
    return d.kind === "note" ? "#58c77a" : "#48526a";
  }
  if (mode === "dict") {
    return d3.schemeTableau10[d.cluster % 10];
  }
  return d3.schemeSet2[0];
}

function strokeFor(d) {
  // Quality ring: green ≥80, amber 50-79, red <50. Concepts get the default border.
  if (mode !== "dict" || d.quality == null) return "#0b0e14";
  if (d.quality >= 80) return "#58c77a";
  if (d.quality >= 50) return "#e0b25a";
  return "#e07070";
}

function opacityFor(d) {
  if (mode !== "dict" || d.quality == null) return 1.0;
  return 0.35 + 0.65 * (d.quality / 100);
}

function radiusFor(d) {
  if (mode === "notes") return d.kind === "note" ? 14 : 5;
  if (mode === "concept") return d.size || 8;
  return 7;
}

function renderTagCloud() {
  const cloud = document.getElementById("tag-cloud");
  cloud.innerHTML = "";
  if (!TAG_USAGE || TAG_USAGE.length === 0) {
    const hint = document.createElement("span");
    hint.style.color = "#666"; hint.style.fontSize = "11px";
    hint.textContent = "No confirmed tags yet — tag dictations via the Edit dialog to enable filtering.";
    cloud.appendChild(hint);
    return;
  }
  TAG_USAGE.forEach(t => {
    const chip = document.createElement("span");
    chip.className = "tag-chip" + (activeTags.has(t.name) ? " active" : "");
    chip.textContent = `${t.name} ${t.count}`;
    chip.onclick = () => {
      if (activeTags.has(t.name)) activeTags.delete(t.name);
      else activeTags.add(t.name);
      renderTagCloud();
      render();
    };
    cloud.appendChild(chip);
  });
}

function render() {
  const cutoff = TS_MIN + (TS_MAX - TS_MIN) * (slider.value / 100);
  timeLabel.textContent = formatTs(cutoff);
  const minQ = +qualSlider.value;
  qualLabel.textContent = minQ;

  const src = mode === "notes" ? NOTES : (mode === "dict" ? DICT : CONCEPT);
  if (!src.nodes.length) {
    svg.selectAll("*").remove();
    svg.append("text").attr("class", "nores")
       .attr("x", window.innerWidth/2).attr("y", window.innerHeight/2)
       .attr("text-anchor", "middle")
       .text(mode === "notes" ? "No Notes yet — pin a dictation via the tray menu or Edit dialog."
                              : mode === "dict" ? "No dictations yet — speak something first."
                              : "No recurring concepts found yet — keep dictating.");
    stats.textContent = "";
    return;
  }

  // Filter by time AND quality AND active tag filter.
  function nodeTags(n) {
    if (n.tags && n.tags.length) return n.tags;
    // For dictation nodes in any mode, look up the index.
    let did = null;
    if (typeof n.id === "string" && n.id.startsWith("dict:")) did = parseInt(n.id.slice(5));
    else if (typeof n.id === "number") did = n.id;
    return (did !== null && TAGS_INDEX[did]) ? TAGS_INDEX[did] : [];
  }
  function matchesTagFilter(n) {
    if (activeTags.size === 0) return true;
    const ts = nodeTags(n);
    for (const t of activeTags) { if (ts.indexOf(t) === -1) return false; }
    return true;
  }
  const nodeIds = new Set(src.nodes.filter(n => {
    if (n.ts > cutoff) return false;
    if (mode === "dict" && n.quality != null && n.quality < minQ) return false;
    if (!matchesTagFilter(n)) return false;
    return true;
  }).map(n => n.id));
  const nodes = src.nodes.filter(n => nodeIds.has(n.id))
                          .map(n => Object.assign({}, n));
  const links = src.links.filter(l => {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    if (l.ts && l.ts > cutoff) return false;
    return nodeIds.has(s) && nodeIds.has(t);
  }).map(l => Object.assign({}, l));

  stats.textContent = `${nodes.length} nodes • ${links.length} edges`;

  svg.selectAll("*").remove();
  const W = window.innerWidth, H = window.innerHeight - 56;

  if (nodes.length === 0) {
    svg.append("text").attr("class", "nores")
       .attr("x", W/2).attr("y", H/2).attr("text-anchor", "middle")
       .text("No nodes match these filters — try lowering the quality slider or extending time.");
    return;
  }

  const g = svg.append("g");

  // Zoom/pan
  svg.call(d3.zoom().scaleExtent([0.2, 4]).on("zoom", (e) => {
    g.attr("transform", e.transform);
  }));

  const link = g.append("g").attr("stroke", "#3a4254").attr("stroke-opacity", 0.5)
    .selectAll("line").data(links).join("line")
    .attr("stroke-width", d => 0.5 + 2.5 * (d.value || 0.5));

  const node = g.append("g").selectAll("g").data(nodes).join("g")
    .attr("class", "node");

  node.append("circle")
    .attr("r", d => radiusFor(d))
    .attr("fill", d => colorFor(d))
    .attr("fill-opacity", d => opacityFor(d))
    .attr("stroke", d => strokeFor(d))
    .attr("stroke-width", d => mode === "dict" ? 2.0 : 1.5);

  node.append("title").text(d => mode === "dict"
    ? `${d.full}\n\nQuality: ${d.quality != null ? d.quality : "n/a"}`
    : `${d.label} (×${d.freq})`);

  node.append("text")
    .attr("class", d => (mode === "notes" && d.kind === "note") ? "note-label" : null)
    .attr("dx", d => radiusFor(d) + 3).attr("dy", "0.32em")
    .text(d => d.label);

  // Cluster centroid labels (dictation mode only).
  let labelG = null;
  if (mode === "dict" && src.cluster_labels) {
    labelG = g.append("g").attr("class", "cluster-label-group");
    const entries = Object.entries(src.cluster_labels).filter(([, v]) => v && v.trim());
    labelG.selectAll("text").data(entries).join("text")
      .attr("class", "cluster-label")
      .attr("text-anchor", "middle")
      .text(([_, label]) => label);
  }

  // Search highlight — runs on every keystroke against the current selection.
  function applySearch() {
    const q = (searchBox.value || "").trim().toLowerCase();
    if (!q) {
      node.classed("node-dim", false);
      node.select("circle").classed("node-hi", false);
      return;
    }
    node.each(function(d) {
      const hay = ((d.full || "") + " " + (d.label || "")).toLowerCase();
      const hit = hay.indexOf(q) !== -1;
      d3.select(this).classed("node-dim", !hit);
      d3.select(this).select("circle").classed("node-hi", hit);
    });
  }
  applySearch();
  searchBox.oninput = applySearch;

  node.on("click", (e, d) => {
    document.getElementById("panel-title").textContent = d.label;
    const body = document.getElementById("panel-body");
    if (mode === "notes" && d.kind === "note") {
      const tagsHtml = (d.tags && d.tags.length)
        ? d.tags.map(t => `<span class="tag-chip">${escape(t)}</span>`).join(" ")
        : '<span style="color:#666">(none)</span>';
      body.innerHTML =
        `<div class="label">Description</div><pre>${escape(d.full) || '<em style="color:#666">(no description)</em>'}</pre>` +
        `<div class="label">Tags</div><div style="margin-top:4px">${tagsHtml}</div>` +
        `<div class="label" style="margin-top:8px">Pinned</div><pre>${formatTs(d.ts)}</pre>`;
    } else if (mode === "dict" || (mode === "notes" && d.kind === "dictation")) {
      const did = (typeof d.id === "string" && d.id.startsWith("dict:"))
                  ? parseInt(d.id.slice(5)) : d.id;
      const dtags = (TAGS_INDEX[did] || []);
      const tagsHtml = dtags.length
        ? dtags.map(t => `<span class="tag-chip">${escape(t)}</span>`).join(" ")
        : '<span style="color:#666">(none)</span>';
      body.innerHTML =
        `<div class="label">Raw</div><pre>${escape(d.raw || "")}</pre>` +
        `<div class="label">Cleaned</div><pre>${escape(d.full)}</pre>` +
        `<div class="label">Tags</div><div style="margin-top:4px">${tagsHtml}</div>` +
        `<div class="label" style="margin-top:8px">When</div><pre>${formatTs(d.ts)}</pre>` +
        `<div class="label">Style</div><pre>${escape(d.style || "")}</pre>` +
        `<div class="label">Quality</div><pre>${d.quality != null ? d.quality : "n/a"}</pre>`;
    } else {
      body.innerHTML =
        `<div class="label">Concept</div><pre>${escape(d.label)}</pre>` +
        `<div class="label">Occurrences</div><pre>${d.freq}</pre>` +
        `<div class="label">First seen</div><pre>${formatTs(d.ts)}</pre>`;
    }
    panel.style.display = "block";
  });

  simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id)
      .distance(d => mode === "dict" ? 80 : 100)
      .strength(d => 0.3 + 0.5 * (d.value || 0.3)))
    .force("charge", d3.forceManyBody().strength(mode === "dict" ? -180 : -250))
    .force("center", d3.forceCenter(W/2, H/2))
    .force("collide", d3.forceCollide().radius(d => radiusFor(d) + 4))
    .on("tick", () => {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
          .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
      // Position cluster labels at the centroid of their member nodes.
      if (labelG) {
        const sums = new Map();   // cluster -> [sumX, sumY, count]
        nodes.forEach(n => {
          const key = String(n.cluster);
          const s = sums.get(key) || [0, 0, 0];
          s[0] += n.x; s[1] += n.y; s[2] += 1;
          sums.set(key, s);
        });
        labelG.selectAll("text")
          .attr("x", ([cid]) => { const s = sums.get(String(cid)); return s ? s[0]/s[2] : 0; })
          .attr("y", ([cid]) => { const s = sums.get(String(cid)); return s ? s[1]/s[2] - 22 : 0; });
      }
    });

  // Drag
  node.call(d3.drag()
    .on("start", (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart();
                              d.fx = d.x; d.fy = d.y; })
    .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on("end",   (e, d) => { if (!e.active) simulation.alphaTarget(0);
                              d.fx = null; d.fy = null; }));
}

function escape(s) {
  return (s||"").replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function setMode(m) {
  mode = m;
  document.getElementById("btn-notes").classList.toggle("active", m === "notes");
  document.getElementById("btn-dict").classList.toggle("active", m === "dict");
  document.getElementById("btn-concept").classList.toggle("active", m === "concept");
  render();
}
document.getElementById("btn-notes").addEventListener("click", () => setMode("notes"));
document.getElementById("btn-dict").addEventListener("click", () => setMode("dict"));
document.getElementById("btn-concept").addEventListener("click", () => setMode("concept"));
// Default to Notes mode if any notes exist
if (NOTES && NOTES.nodes && NOTES.nodes.some(n => n.kind === "note")) {
  setMode("notes");
}
renderTagCloud();
slider.addEventListener("input", render);
qualSlider.addEventListener("input", render);
document.getElementById("btn-refresh").addEventListener("click", () => location.reload());
document.getElementById("panel-close").addEventListener("click", () => {
  panel.style.display = "none";
});
window.addEventListener("resize", render);
render();
</script>
</body>
</html>
"""


def render_graph(
    db_path: str,
    out_path: str | None = None,
    open_browser: bool = True,
) -> str:
    """Mirrors viewer.py:render_history signature. Returns the output file path."""
    rows = _load_rows(db_path)
    dict_graph = build_dictation_graph(rows)
    concept_graph = build_concept_graph(rows)
    notes_graph = build_notes_graph(db_path, rows)
    tag_usage = [{"name": n, "count": c} for n, c in _all_confirmed_tags(db_path)]
    tags_index = _tags_index(db_path)

    ts_values = [r["ts"] for r in rows if r.get("ts")]
    ts_min = min(ts_values) if ts_values else 0
    ts_max = max(ts_values) if ts_values else 1
    # Avoid divide-by-zero on a single-row history
    if ts_max <= ts_min:
        ts_max = ts_min + 1

    out = (_HTML
        .replace("__DICT_DATA__", json.dumps(dict_graph, ensure_ascii=False))
        .replace("__CONCEPT_DATA__", json.dumps(concept_graph, ensure_ascii=False))
        .replace("__NOTES_DATA__", json.dumps(notes_graph, ensure_ascii=False))
        .replace("__TAG_USAGE__", json.dumps(tag_usage, ensure_ascii=False))
        .replace("__TAGS_INDEX__", json.dumps(tags_index, ensure_ascii=False))
        .replace("__TS_MIN__", str(ts_min))
        .replace("__TS_MAX__", str(ts_max)))

    if out_path is None:
        out_path = str(Path(db_path).parent / "graph.html")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    if open_browser:
        webbrowser.open(f"file:///{Path(out_path).resolve().as_posix()}")
    return out_path
